"""Release a large virtual arrival wave with bounded real WSGI workers.

Every buyer is submitted before release. Queue time counts toward user latency.
This does not simulate TCP connections or claim every buyer executes at once.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import platform
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.server import MallApplication
from scripts.stress_purchase import ErrorLog, InFlight, distribution, prepare, verify


def run_scenario(users, stock, workers, product_id=1):
    with tempfile.TemporaryDirectory(prefix='heima-purchase-wave-') as directory:
        app = MallApplication(Path(directory) / 'stress.db')
        clients, store_id, price = prepare(app, users, stock, product_id)
        with app.state.database.connection() as connection:
            product_name = connection.execute('SELECT name FROM products WHERE id=?', (product_id,)).fetchone()[0]
        gate, ready = threading.Event(), threading.Event()
        ready_lock, ready_count = threading.Lock(), 0
        workers = min(workers, users)
        in_flight, errors = InFlight(), ErrorLog()
        root_logger = logging.getLogger()
        handlers, level = root_logger.handlers[:], root_logger.level
        root_logger.handlers, root_logger.level = [errors], logging.ERROR
        released = 0

        def purchase(user_id, phone, client):
            nonlocal ready_count
            row = {'user': user_id, 'checkout_status': 0}
            with ready_lock:
                ready_count += 1
                if ready_count == workers:
                    ready.set()
            gate.wait()
            started = time.perf_counter()
            row['queue_ms'] = (started - released) * 1000
            try:
                with in_flight:
                    response = client.post('/api/orders', {
                        'recipient': f'压测用户{user_id}', 'phone': phone,
                        'address': '压力测试独立数据库测试路100号', 'store_id': store_id,
                        'idempotency_key': f'stress-checkout-{user_id:08d}',
                    })
                finished = time.perf_counter()
                row['checkout_service_ms'] = (finished - started) * 1000
                row['checkout_ms'] = (finished - released) * 1000
                row['checkout_status'] = response['status']
                if response['status'] == 201:
                    row['order_id'] = response['json']['id']
                    pay_started = time.perf_counter()
                    with in_flight:
                        payment = client.post(f"/api/orders/{row['order_id']}/pay")
                    row['payment_ms'] = (time.perf_counter() - pay_started) * 1000
                    row['payment_status'] = payment['status']
                    if payment['status'] != 200:
                        row['payment_error'] = (payment.get('json') or {}).get('detail')
                else:
                    row['checkout_error'] = (response.get('json') or {}).get('detail')
                row['total_ms'] = (time.perf_counter() - released) * 1000
            except Exception as error:
                row['exception'] = f'{type(error).__name__}: {error}'
            return row

        rows, launch_error = [], None
        submission_started = time.perf_counter()
        try:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='wave-buyer') as pool:
                futures = []
                try:
                    for user_id, phone, client in clients:
                        futures.append(pool.submit(purchase, user_id, phone, client))
                    if not ready.wait(timeout=30):
                        raise RuntimeError('工作线程未在30秒内就绪')
                except RuntimeError as error:
                    launch_error = str(error)
                submitted = len(futures)
                submission_seconds = time.perf_counter() - submission_started
                released = time.perf_counter()
                gate.set()
                for completed, future in enumerate(as_completed(futures), 1):
                    rows.append(future.result())
                    if completed % 1000 == 0 or completed == submitted:
                        print(f'库存 {stock}：完成 {completed}/{users}', flush=True)
                elapsed = time.perf_counter() - released
        finally:
            root_logger.handlers, root_logger.level = handlers, level
        unexpected = [r for r in rows if r['checkout_status'] not in {201, 409}
                      or 'exception' in r or r.get('payment_status', 200) != 200]
        validation = verify(app, rows, stock, product_id, users, price)
        # Failure to consume all stock describes admission/capacity, not corrupt
        # inventory. Keep this gate separate from conservation and integrity.
        fully_allocated = validation['checks'].pop('available_inventory_fully_reserved', True)
        validation['capacity_checks'] = {'available_inventory_fully_reserved': fully_allocated}
        validation['capacity_passed'] = fully_allocated
        validation['consistency_passed'] = all(validation['checks'].values())
        latencies = {}
        for key in ('queue_ms', 'checkout_service_ms', 'checkout_ms', 'payment_ms', 'total_ms'):
            latencies[key] = distribution([r[key] for r in rows if key in r])
        return {
            'users': users, 'initial_stock': stock, 'product_id': product_id,
            'product_name': product_name, 'worker_limit': workers,
            'buyers_submitted_before_release': submitted,
            'submission_seconds': round(submission_seconds, 4),
            'launch_completed': launch_error is None, 'launch_error': launch_error,
            'peak_in_flight_requests': in_flight.peak,
            'completion_seconds': round(elapsed, 4),
            'buyers_per_second': round(len(rows) / elapsed, 2),
            'checkout_statuses': dict(sorted(Counter(r['checkout_status'] for r in rows).items())),
            'payment_statuses': dict(sorted(Counter(r['payment_status'] for r in rows
                                                    if 'payment_status' in r).items())),
            'unexpected_failures': len(unexpected), 'error_samples': unexpected[:10],
            'latencies': latencies,
            'application_error_logs': {'count': errors.count, 'samples': errors.samples},
            'validation': validation,
            'passed': launch_error is None and not unexpected and validation['consistency_passed'] and validation['capacity_passed'],
            'requests': sorted(rows, key=lambda r: r['user']),
        }


def write_report(report, output):
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    lines = [f"# {report['users']} 人同时到达下单压测", '',
        f"测试时间：{report['timestamp_utc']}。Python {report['environment']['python']}，{report['environment']['os']}。", '',
        f"每个场景使用独立临时数据库、{report['users']} 个已登录账号，每人购物车预放同一商品1件。放行前提交全部买家，事件统一释放。工作线程上限为 {report['worker_limit']}，其余买家排队。成功下单后立即进行演示支付。", '',
        '测量真实WSGI请求入口、认证、校验、事务及SQLite；不含TCP连接、反向代理、FastAPI、真实支付、浏览器或注册登录成本，不修改商城数据库。虚拟到达并发与实际处理并发分别报告。', '',
        '下单及用户总延迟从统一放行开始计时，包含排队；实际处理延迟及支付延迟不含执行队列等待。409库存不足是预期业务结果，500/503/异常及支付失败属于非预期失败。通过仅表示服务无非预期失败且数据一致，响应时间另行评估。', '',
        '| 库存 | 用户 | 处理并发峰值 | 下单成功 | 库存不足 | 非预期失败 | 支付成功 | 剩余库存 | 全部完成(s) | 下单P95含排队(s) | 下单P95处理(ms) | 一致性 |',
        '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    for r in report['scenarios']:
        counts = r['checkout_statuses']
        count = lambda status: counts.get(status, counts.get(str(status), 0))
        lat = r['latencies']
        lines.append(f"| {r['initial_stock']} | {r['users']} | {r['peak_in_flight_requests']} | {count(201)} | {count(409)} | {r['unexpected_failures']} | {r['validation']['paid_orders']} | {r['validation']['final_stock']} | {r['completion_seconds']} | {lat['checkout_ms']['p95_ms']/1000:.3f} | {lat['checkout_service_ms']['p95_ms']:.3f} | {'通过' if r['validation']['consistency_passed'] else '失败'} |")
    for r in report['scenarios']:
        lines += ['', f"## 库存 {r['initial_stock']} 件", '',
                  f"放行前提交：{r['buyers_submitted_before_release']}；准备提交耗时：{r['submission_seconds']}秒；每秒完成买家：{r['buyers_per_second']}；应用错误日志：{r['application_error_logs']['count']}条。", '',
                  '延迟分布（毫秒）：', '', '```json', json.dumps(r['latencies'], ensure_ascii=False, indent=2), '```', '']
        lines += [f"- {'通过' if passed else '失败'}：{name}" for name, passed in r['validation']['checks'].items()]
        lines += ['', f"承载验收：{'通过' if r['passed'] else '失败'}；库存全部分配：{'是' if r['validation']['capacity_passed'] else '否'}。"]
        if r['error_samples'] or r['launch_error']:
            lines += ['', '异常样本：', '', '```json', json.dumps({'launch_error': r['launch_error'], 'requests': r['error_samples']}, ensure_ascii=False, indent=2), '```']
    lines += ['', '## 复现命令', '', '```bash',
              f"python3 scripts/stress_purchase_wave.py --users {report['users']} --workers {report['worker_limit']} --stocks {' '.join(str(r['initial_stock']) for r in report['scenarios'])} --output {output}",
              '```', '', f"逐用户请求结果见 `{output.name}`。"]
    output.with_suffix('.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description='独立数据库：大量虚拟用户同时到达，有限线程处理')
    parser.add_argument('--users', type=int, default=10000)
    parser.add_argument('--workers', type=int, default=256)
    parser.add_argument('--stocks', type=int, nargs='+', default=[10000, 100, 1])
    parser.add_argument('--product-id', type=int, default=1)
    parser.add_argument('--output', type=Path, default=ROOT / 'reports/stress-purchase-10000.json')
    args = parser.parse_args()
    if not 1 <= args.users <= 100000 or not 1 <= args.workers <= 1000 or any(s < 0 for s in args.stocks):
        parser.error('用户数量需为1–100000，线程为1–1000，库存不得为负数')
    report = {'timestamp_utc': datetime.now(timezone.utc).isoformat(),
              'mode': 'simultaneous virtual arrival; bounded in-process WSGI workers',
              'users': args.users, 'worker_limit': args.workers,
              'environment': {'python': platform.python_version(), 'os': platform.platform(),
                              'cpu_count': os.cpu_count(), 'sqlite_busy_timeout_seconds': 10},
              'scenarios': []}
    for stock in args.stocks:
        print(f'开始：{args.users} 位虚拟买家同时到达，{args.workers} 工作线程，库存 {stock}', flush=True)
        result = run_scenario(args.users, stock, args.workers, args.product_id)
        report['scenarios'].append(result)
        write_report(report, args.output)
        print(json.dumps({k: result[k] for k in ('initial_stock', 'checkout_statuses', 'payment_statuses', 'unexpected_failures', 'completion_seconds', 'passed')}, ensure_ascii=False), flush=True)
        if not result['launch_completed']:
            break
    print(f'报告：{args.output.with_suffix(".md")}', flush=True)
    return 0 if len(report['scenarios']) == len(args.stocks) and all(r['passed'] for r in report['scenarios']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
