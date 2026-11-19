"""A synchronized buyer wave through real ASGI/AsyncSession; no TCP traffic.

SQLite uses a temporary file. MySQL always uses a newly named heima_test_* DB.
Fixtures bypass login/password work; each request still authenticates a session.
"""
import argparse
import asyncio
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import importlib.util
import json
import math
import os
from pathlib import Path
import platform
import secrets
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@contextmanager
def environment(values):
    original = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def distribution(values):
    if not values:
        return None
    values = sorted(values)
    return {'mean_ms': round(statistics.mean(values), 3),
            **{f'p{p}_ms': round(values[max(0, math.ceil(len(values)*p/100)-1)], 3) for p in (50,95,99)},
            'max_ms': round(values[-1], 3)}


async def prepare(database, users, stock, product_id):
    from app.security import SESSION_SECONDS, hash_password_async, token_hash
    password = await hash_password_async('isolated-async-stress-only')
    tokens = [secrets.token_urlsafe(32) for _ in range(users)]
    async with database.connection() as connection:
        product = (await connection.execute('SELECT * FROM products WHERE id=?', (product_id,))).fetchone()
        if not product:
            raise ValueError('测试商品不存在')
        # Bound setup transactions independently of the measured request wave.
        async with connection.atomic():
            await connection.execute('UPDATE products SET stock=?,sales=0,version=1 WHERE id=?', (stock,product_id))
        for start in range(0, users, 250):
            batch = range(start, min(start+250,users))
            async with connection.atomic():
                await connection.executemany('INSERT INTO users(id,phone,name,password_hash) VALUES (?,?,?,?)',
                    [(i+1,f'139{i+1:08d}',f'异步压测{i+1}',password) for i in batch])
                await connection.executemany('INSERT INTO sessions(token_hash,user_id,expires_at) VALUES (?,?,?)',
                    [(token_hash(tokens[i]),i+1,int(time.time())+SESSION_SECONDS) for i in batch])
                await connection.executemany('INSERT INTO cart_items(user_id,product_id,quantity) VALUES (?,?,1)',
                    [(i+1,product_id) for i in batch])
    return tokens, product


async def verify(database, rows, users, initial_stock, product_id, price, shipping):
    async with database.connection() as connection:
        product = (await connection.execute('SELECT stock,sales FROM products WHERE id=?', (product_id,))).fetchone()
        orders = (await connection.execute('SELECT id,user_id,status,subtotal,total FROM orders')).fetchall()
        units = (await connection.execute('SELECT COALESCE(SUM(quantity),0) FROM order_items WHERE product_id=?', (product_id,))).fetchone()[0]
        carts = (await connection.execute('SELECT COUNT(*) FROM cart_items WHERE product_id=?', (product_id,))).fetchone()[0]
        paid = sum(order['status']=='paid' for order in orders)
        returned_orders = {row['order_id'] for row in rows if row.get('checkout_status')==201}
        checks = {
            'all_buyers_completed':len(rows)==users,
            'no_overselling':0 <= units <= initial_stock and product['stock'] >= 0,
            'inventory_conserved':product['stock']+units==initial_stock,
            'one_unit_per_order':units==len(orders),
            'one_order_per_buyer':len({order['user_id'] for order in orders})==len(orders),
            'orders_match_responses':{order['id'] for order in orders}==returned_orders,
            'sales_match_payments':product['sales']==paid==sum(row.get('payment_status')==200 for row in rows),
            'carts_match_failed_orders':carts==users-len(orders),
            'prices_match_snapshot':all(order['subtotal']==price and order['total']==price+shipping for order in orders),
        }
        if database.backend == 'sqlite':
            checks['foreign_keys_valid'] = not (await connection.execute('PRAGMA foreign_key_check')).fetchall()
            checks['database_integrity'] = (await connection.execute('PRAGMA integrity_check')).fetchone()[0]=='ok'
        else:
            checks['no_orphan_items'] = (await connection.execute('SELECT COUNT(*) FROM order_items i LEFT JOIN orders o ON o.id=i.order_id WHERE o.id IS NULL')).fetchone()[0]==0
        return {'orders_created':len(orders),'paid_orders':paid,'reserved_units':units,
                'final_stock':product['stock'],'sales':product['sales'],'remaining_carts':carts,
                'checks':checks,'consistency_passed':all(checks.values())}


async def run_scenario(arguments, stock):
    import httpx
    from sqlalchemy.ext.asyncio import create_async_engine
    from app.main import create_app
    name = 'heima_test_async_' + secrets.token_hex(8)
    values = {'DATABASE_BACKEND':arguments.backend,'MYSQL_DATABASE':name,
              'MYSQL_INITIALIZE_SCHEMA':'1','REDIS_URL':arguments.redis_url or ''}
    with tempfile.TemporaryDirectory(prefix='heima-async-wave-') as directory, environment(values):
        app = create_app(Path(directory)/'stress.db' if arguments.backend=='sqlite' else None)
        database = app.state.database
        try:
            async with app.router.lifespan_context(app):
                tokens, product = await prepare(database,arguments.users,stock,arguments.product_id)
                async with database.connection() as connection:
                    store = (await connection.execute('SELECT * FROM stores WHERE id=?', (product['store_id'],))).fetchone()
                shipping = 0 if product['price'] >= store['free_shipping_threshold'] else store['shipping_fee']
                semaphore, gate = asyncio.Semaphore(arguments.concurrency), asyncio.Event()
                active, peak = 0, 0
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver',
                    headers={'X-Requested-With':'HeimaMall'}) as client:
                    async def buy(index):
                        nonlocal active, peak
                        await gate.wait()
                        row = {'buyer_id':index+1}
                        started = None
                        try:
                            async with asyncio.timeout(arguments.buyer_timeout):
                                async with semaphore:
                                    started = time.perf_counter()
                                    active += 1
                                    peak = max(peak,active)
                                    try:
                                        headers={'Cookie':'heima_session='+tokens[index]}
                                        if arguments.hot_read:
                                            row['catalog_status']=(await client.get(f'/api/products/{arguments.product_id}',headers=headers)).status_code
                                        response = await client.post('/api/orders',headers=headers,json={
                                            'recipient':'压测买家','phone':f'139{index+1:08d}','address':'独立压测收货地址',
                                            'store_id':product['store_id'],'idempotency_key':f'async-wave-order-{index:08d}'})
                                        row['checkout_status']=response.status_code
                                        if response.status_code==201:
                                            row['order_id']=response.json()['id']
                                            if not arguments.no_pay:
                                                row['payment_status']=(await client.post(f'/api/orders/{row["order_id"]}/pay',headers=headers)).status_code
                                        elif response.status_code != 409:
                                            row['detail']=response.text[:200]
                                    finally:
                                        active -= 1
                        except Exception as error:
                            row['client_error']=type(error).__name__
                        ended = time.perf_counter()
                        row['arrival_ms']=(ended-released)*1000
                        row['queue_ms']=((started or ended)-released)*1000
                        row['processing_ms']=(ended-started)*1000 if started else 0
                        return row
                    tasks = [asyncio.create_task(buy(i)) for i in range(arguments.users)]
                    await asyncio.sleep(0)
                    released = time.perf_counter()
                    gate.set()
                    rows = await asyncio.gather(*tasks)
                    elapsed = time.perf_counter()-released
                consistency = await verify(database,rows,arguments.users,stock,arguments.product_id,product['price'],shipping)
                successes = sum(row.get('checkout_status')==201 for row in rows)
                service_errors = sum('client_error' in row or row.get('checkout_status') not in {201,409}
                    or row.get('payment_status') not in {None,200} or row.get('catalog_status') not in {None,200} for row in rows)
                return {'stock':stock,'virtual_users':arguments.users,'configured_client_concurrency':arguments.concurrency,
                    'peak_active_buyers':peak,'seconds':round(elapsed,3),
                    'completed_buyers_per_second':round(len(rows)/elapsed,2),'successful_orders_per_second':round(successes/elapsed,2),
                    'checkout_statuses':dict(Counter(str(row.get('checkout_status','client_error')) for row in rows)),
                    'payment_statuses':dict(Counter(str(row['payment_status']) for row in rows if 'payment_status' in row)),
                    'service_error_buyers':service_errors,'arrival_latency':distribution([row['arrival_ms'] for row in rows]),
                    'client_queue_latency':distribution([row['queue_ms'] for row in rows]),
                    'processing_latency':distribution([row['processing_ms'] for row in rows]),'consistency':consistency,
                    'passed':consistency['consistency_passed'] and not service_errors and successes==min(stock,arguments.users),
                    'error_samples':[row for row in rows if 'client_error' in row or row.get('checkout_status') not in {201,409}
                                     or row.get('payment_status') not in {None,200}][:10]}
        finally:
            # The generated name is never the user's application database.
            if arguments.backend=='mysql':
                engine=create_async_engine(database.engine.url.set(database=None),connect_args={'connect_timeout':3})
                try:
                    async with engine.begin() as connection:
                        await connection.exec_driver_sql(f'DROP DATABASE IF EXISTS `{name}`')
                finally:
                    await engine.dispose()


def main():
    parser=argparse.ArgumentParser(description='异步ASGI万人下单测试（独立数据库，不含网络）')
    parser.add_argument('--users',type=int,default=10000)
    parser.add_argument('--concurrency',type=int,default=256)
    parser.add_argument('--stocks',type=int,nargs='+',default=[10000,100,1])
    parser.add_argument('--backend',choices=['sqlite','mysql'],default='sqlite')
    parser.add_argument('--product-id',type=int,default=1)
    parser.add_argument('--redis-url',default='')
    parser.add_argument('--hot-read',action='store_true',help='下单前请求同一个商品的缓存详情')
    parser.add_argument('--no-pay',action='store_true')
    parser.add_argument('--buyer-timeout',type=float,default=120)
    parser.add_argument('--output',type=Path,default=ROOT/'reports/async-stress-purchase-10000.json')
    arguments=parser.parse_args()
    if not 1 <= arguments.users <= 100000 or not 1 <= arguments.concurrency <= 10000 or any(stock<0 for stock in arguments.stocks) or arguments.buyer_timeout<=0:
        parser.error('用户数、并发数、库存或超时配置不正确')
    required=['fastapi','jinja2','sqlalchemy','greenlet','aiomysql','httpx']
    if arguments.backend=='sqlite': required.append('aiosqlite')
    if arguments.redis_url: required.append('redis')
    missing=[name for name in required if importlib.util.find_spec(name) is None]
    if missing:
        parser.exit(2,'未执行异步压测：缺少'+', '.join(missing)+'。请安装requirements-dev.txt。\n')
    from app.config import load_environment
    load_environment()
    async def run():
        return [await run_scenario(arguments,stock) for stock in arguments.stocks]
    report={'timestamp_utc':datetime.now(timezone.utc).isoformat(),'python':platform.python_version(),
            'runtime':'FastAPI / AsyncSession / ASGITransport','database_backend':arguments.backend,
            'network_included':False,'redis_enabled':bool(arguments.redis_url),'hot_read':arguments.hot_read,
            'pool_size':int(os.getenv('DB_POOL_SIZE','32')),'application_admission':int(os.getenv('MAX_IN_FLIGHT_REQUESTS','128')),
            'note':'统一释放虚拟买家；实际请求并发受客户端信号量、应用准入和数据库连接池限制。',
            'scenarios':asyncio.run(run())}
    arguments.output.parent.mkdir(parents=True,exist_ok=True)
    arguments.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('异步压测报告：'+str(arguments.output))
    return 0 if all(scenario['passed'] for scenario in report['scenarios']) else 1


if __name__=='__main__':
    raise SystemExit(main())
