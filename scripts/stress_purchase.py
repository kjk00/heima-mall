"""Synchronize independent logged-in buyers against the real WSGI request pipeline."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import logging
import math
import os
from pathlib import Path
import platform
import secrets
import statistics
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.database import transaction
from app.security import SESSION_SECONDS, hash_password, token_hash
from app.server import MallApplication
from tests.client import Client


class ErrorLog(logging.Handler):
    def __init__(self):
        super().__init__()
        self.count = 0
        self.samples = []

    def emit(self, record):
        self.count += 1
        if len(self.samples) < 10:
            self.samples.append(record.getMessage() + (': ' + str(record.exc_info[1]) if record.exc_info else ''))


class InFlight:
    def __init__(self):
        self.lock = threading.Lock()
        self.current = 0
        self.peak = 0

    def __enter__(self):
        with self.lock:
            self.current += 1
            self.peak = max(self.peak, self.current)

    def __exit__(self, *args):
        with self.lock:
            self.current -= 1


def distribution(values):
    if not values:
        return None
    values = sorted(values)
    percentile = lambda p: round(values[max(0, math.ceil(len(values)*p)-1)], 3)
    return {'min_ms':round(values[0],3),'mean_ms':round(statistics.mean(values),3),
            'p50_ms':percentile(.5),'p95_ms':percentile(.95),'p99_ms':percentile(.99),
            'max_ms':round(values[-1],3)}


def prepare(app, users, stock, product_id):
    clients = []
    # Provision login sessions ahead of the spike, avoiding registration cost and login throttles.
    password = hash_password('isolated-stress-test-only')
    expires = int(time.time()) + SESSION_SECONDS
    with app.state.database.connection() as connection, transaction(connection):
        product = connection.execute('SELECT * FROM products WHERE id=?',(product_id,)).fetchone()
        if not product:
            raise ValueError('测试商品不存在')
        connection.execute('UPDATE products SET stock=?,sales=0,version=1 WHERE id=?',(stock,product_id))
        for user_id in range(1,users+1):
            phone = f'139{user_id:08d}'
            connection.execute('INSERT INTO users(id,phone,name,password_hash) VALUES (?,?,?,?)',
                               (user_id,phone,f'压测用户{user_id}',password))
            token = secrets.token_urlsafe(32)
            connection.execute('INSERT INTO sessions(token_hash,user_id,expires_at) VALUES (?,?,?)',
                               (token_hash(token),user_id,expires))
            connection.execute('INSERT INTO cart_items(user_id,product_id,quantity) VALUES (?,?,1)',(user_id,product_id))
            client = Client(app)
            client.cookies['heima_session'] = token
            clients.append((user_id,phone,client))
    return clients, product['store_id'], product['price']


def verify(app, rows, stock, product_id, users, price):
    with app.state.database.connection() as connection:
        product = connection.execute('SELECT stock,sales FROM products WHERE id=?',(product_id,)).fetchone()
        orders = [dict(r) for r in connection.execute('SELECT id,user_id,status,subtotal,total FROM orders')]
        units = connection.execute('SELECT COALESCE(SUM(quantity),0) FROM order_items WHERE product_id=?',(product_id,)).fetchone()[0]
        carts = connection.execute('SELECT COUNT(*) FROM cart_items WHERE product_id=?',(product_id,)).fetchone()[0]
        item_count = connection.execute('SELECT COUNT(*) FROM order_items').fetchone()[0]
        checkouts = [r for r in rows if r['checkout_status'] == 201]
        paid_rows = [r for r in rows if r.get('payment_status') == 200]
        paid_count = sum(o['status']=='paid' for o in orders)
        checks = {
            'all_buyers_completed':len(rows)==users,
            'inventory_never_negative':product['stock']>=0,
            'no_overselling':units<=stock,
            'inventory_matches_reserved_units':product['stock']+units==stock,
            'orders_match_successful_responses':{o['id'] for o in orders}=={r['order_id'] for r in checkouts},
            'one_order_per_buyer':len({o['user_id'] for o in orders})==len(orders),
            'one_unit_per_order':units==item_count==len(orders),
            'payment_responses_match_paid_orders':paid_count==len(paid_rows),
            'sales_match_paid_orders':product['sales']==paid_count,
            'remaining_carts_match_failed_checkouts':carts==users-len(orders),
            'prices_match_snapshot':all(o['subtotal']==price and o['total']==price for o in orders),
            'no_foreign_key_errors':not connection.execute('PRAGMA foreign_key_check').fetchall(),
            'database_integrity':connection.execute('PRAGMA integrity_check').fetchone()[0]=='ok',
        }
        if stock <= users:
            checks['available_inventory_fully_reserved'] = units == stock
        return {'orders_created':len(orders),'paid_orders':paid_count,'reserved_units':units,
                'final_stock':product['stock'],'sales':product['sales'],'remaining_carts':carts,
                'checks':checks,'consistency_passed':all(checks.values())}


def run_scenario(users, stock, product_id=1):
    with tempfile.TemporaryDirectory(prefix='heima-purchase-stress-') as directory:
        app = MallApplication(Path(directory)/'stress.db')
        clients, store_id, price = prepare(app,users,stock,product_id)
        gate = threading.Barrier(users+1,timeout=60)
        in_flight = InFlight()
        errors = ErrorLog()
        root_logger = logging.getLogger()
        handlers, level = root_logger.handlers[:], root_logger.level
        root_logger.handlers = [errors]
        root_logger.setLevel(logging.ERROR)
        released = 0

        def purchase(user_id, phone, client):
            row = {'user':user_id,'checkout_status':0}
            try:
                gate.wait()
                started = time.perf_counter()
                row['started_at_ms'] = (started-released)*1000
                with in_flight:
                    response = client.post('/api/orders', {'recipient':f'压测用户{user_id}','phone':phone,
                        'address':'压力测试独立数据库测试路100号','store_id':store_id,
                        'idempotency_key':f'stress-checkout-{user_id:08d}'})
                row['checkout_ms'] = (time.perf_counter()-started)*1000
                row['checkout_status'] = response['status']
                if response['status'] == 201:
                    row['order_id'] = response['json']['id']
                    pay_started = time.perf_counter()
                    with in_flight:
                        payment = client.post(f"/api/orders/{row['order_id']}/pay")
                    row['payment_ms'] = (time.perf_counter()-pay_started)*1000
                    row['payment_status'] = payment['status']
                    if payment['status'] != 200:
                        row['payment_error'] = payment.get('json',{}).get('detail')
                else:
                    row['checkout_error'] = response.get('json',{}).get('detail')
                row['total_ms'] = (time.perf_counter()-started)*1000
            except Exception as error:
                row['exception'] = f'{type(error).__name__}: {error}'
            return row

        rows = []
        launch_error = None
        try:
            with ThreadPoolExecutor(max_workers=users,thread_name_prefix='buyer') as pool:
                futures = []
                try:
                    for user_id,phone,client in clients:
                        futures.append(pool.submit(purchase,user_id,phone,client))
                    released = time.perf_counter()
                    gate.wait()
                except (RuntimeError,threading.BrokenBarrierError) as error:
                    launch_error = f'{type(error).__name__}: {error}'
                    gate.abort()
                for completed,future in enumerate(as_completed(futures),1):
                    rows.append(future.result())
                    if completed % 200 == 0:
                        print(f'库存 {stock}：完成 {completed}/{users}',flush=True)
                elapsed = time.perf_counter()-released if released else 0
        finally:
            root_logger.handlers, root_logger.level = handlers, level
        checkout_counts = dict(sorted(Counter(r['checkout_status'] for r in rows).items()))
        payment_counts = dict(sorted(Counter(r['payment_status'] for r in rows if 'payment_status' in r).items()))
        unexpected = [r for r in rows if r['checkout_status'] not in {201,409} or 'exception' in r
                      or r.get('payment_status',200)!=200]
        validation = verify(app,rows,stock,product_id,users,price)
        return {'users':users,'initial_stock':stock,'product_id':product_id,'product_name':'AirSound Pro 降噪耳机' if product_id==1 else str(product_id),
                'launch_completed':launch_error is None,'launch_error':launch_error,
                'peak_in_flight_requests':in_flight.peak,'completion_seconds':round(elapsed,4),
                'buyers_per_second':round(len(rows)/elapsed,2) if elapsed else 0,
                'checkout_statuses':checkout_counts,'payment_statuses':payment_counts,
                'unexpected_failures':len(unexpected),'error_samples':unexpected[:10],
                'checkout_latency':distribution([r['checkout_ms'] for r in rows if 'checkout_ms' in r]),
                'payment_latency':distribution([r['payment_ms'] for r in rows if 'payment_ms' in r]),
                'buyer_latency':distribution([r['total_ms'] for r in rows if 'total_ms' in r]),
                'dispatch_offsets':distribution([r['started_at_ms'] for r in rows if 'started_at_ms' in r]),
                'application_error_logs':{'count':errors.count,'samples':errors.samples},
                'validation':validation,
                'passed':launch_error is None and not unexpected and validation['consistency_passed'],
                'requests':sorted(rows,key=lambda r:r['user'])}


def write_report(report, output):
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    markdown = output.with_suffix('.md')
    lines = ['# 1000 人同时购买压力测试','',
        f"测试时间：{report['timestamp_utc']}。Python {report['environment']['python']}，{report['environment']['os']}，{report['environment']['cpu_count']} 核。",'',
        '每个场景使用全新的临时 SQLite 数据库；1000 个独立账号已登录，每人购物车预放 1 件同一商品。所有线程在屏障处等待，一起释放，成功下单后立即进行演示支付。测试使用真实 WSGI 请求入口，保留请求校验、会话认证和业务事务；不包含 TCP、反向代理、前端或注册登录阶段，不修改运行中的商城数据库。','',
        '| 初始库存 | 参与用户 | 下单成功 | 库存不足 | 下单服务异常 | 支付成功 | 剩余库存 | 全部完成（秒） | 下单 P95（毫秒） | 一致性 |',
        '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    for result in report['scenarios']:
        counts = result['checkout_statuses']
        lookup = lambda key: counts.get(key,counts.get(str(key),0))
        lines.append(f"| {result['initial_stock']} | {result['users']} | {lookup(201)} | {lookup(409)} | {sum(v for k,v in counts.items() if int(k) not in (201,409))} | {result['validation']['paid_orders']} | {result['validation']['final_stock']} | {result['completion_seconds']} | {(result['checkout_latency'] or {}).get('p95_ms','—')} | {'通过' if result['validation']['consistency_passed'] else '失败'} |")
    lines += ['','库存不足（409）是预期业务结果；500、503、异常或支付失败算作服务失败。库存一致性与服务承载能力分别判断。','']
    for result in report['scenarios']:
        lines += [f"## 库存 {result['initial_stock']} 件",'',
            f"同步放行：{'完成' if result['launch_completed'] else '未完成'}；观测到的峰值并行请求：{result['peak_in_flight_requests']}；非预期失败：{result['unexpected_failures']}。",'',
            f"下单延迟：{json.dumps(result['checkout_latency'],ensure_ascii=False)}。",'',
            f"每个用户下单到支付完成的延迟：{json.dumps(result['buyer_latency'],ensure_ascii=False)}。",'',
            f"同步放行后的实际请求启动偏移：{json.dumps(result['dispatch_offsets'],ensure_ascii=False)}。",'']
        lines += [f"- {'通过' if passed else '失败'}：{name}" for name,passed in result['validation']['checks'].items()]
        if result['launch_error']:
            lines += ['',f"启动失败：{result['launch_error']}"]
        if result['application_error_logs']['count']:
            lines += ['',f"应用错误日志 {result['application_error_logs']['count']} 条："] + result['application_error_logs']['samples']
        lines.append('')
    lines += ['## 复现命令','',f"```bash\npython3 scripts/stress_purchase.py --users {report['users']} --stocks {' '.join(str(r['initial_stock']) for r in report['scenarios'])} --output reports/stress-purchase.json\n```",'',
              f'逐用户请求结果见 `{output.name}`。']
    markdown.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return markdown


def main():
    parser = argparse.ArgumentParser(description='独立数据库：模拟用户同时购买同一商品')
    parser.add_argument('--users',type=int,default=1000)
    parser.add_argument('--stocks',type=int,nargs='+',default=[1000,100,1])
    parser.add_argument('--product-id',type=int,default=1)
    parser.add_argument('--output',type=Path,default=ROOT/'reports/stress-purchase.json')
    args = parser.parse_args()
    if not 1<=args.users<=1000 or any(stock<0 for stock in args.stocks):
        parser.error('用户数量需为 1–1000，库存不得为负数')
    report = {'timestamp_utc':datetime.now(timezone.utc).isoformat(),'mode':'in-process WSGI',
        'users':args.users,'environment':{'python':platform.python_version(),'os':platform.platform(),'cpu_count':os.cpu_count(),
        'sqlite_busy_timeout_seconds':10},'scenarios':[]}
    for stock in args.stocks:
        print(f'开始：{args.users} 个同步买家，库存 {stock} 件',flush=True)
        result = run_scenario(args.users,stock,args.product_id)
        report['scenarios'].append(result)
        markdown = write_report(report,args.output)
        print(json.dumps({key:result[key] for key in ('initial_stock','checkout_statuses','payment_statuses','unexpected_failures','completion_seconds','passed')},ensure_ascii=False),flush=True)
        if not result['launch_completed']:
            break
    print(f'报告：{markdown}',flush=True)
    return 0 if len(report['scenarios'])==len(args.stocks) and all(r['passed'] for r in report['scenarios']) else 1


if __name__=='__main__':
    raise SystemExit(main())
