"""Event-loop, cache cancellation, and optional real async ORM/ASGI tests."""
import asyncio
import importlib.util
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from app.async_cache import AsyncCatalogCache
from app.async_contract import transaction
from app.cache import CacheUnavailable
from app.security import hash_password_async
from app.request_guard import RequestGuard


class StateConnection:
    def __init__(self):
        self.revision = 1
    async def execute(self, sql):
        await asyncio.sleep(0)
        class Result:
            def fetchone(inner):
                return {'generation':'test-instance','revision':self.revision}
        return Result()


class AsyncMemoryBackend:
    def __init__(self):
        self.values, self.fail = {}, False
    async def get(self, key):
        await asyncio.sleep(0)
        if self.fail: raise CacheUnavailable()
        return self.values.get(key)
    async def put(self, key, value, ttl):
        await asyncio.sleep(0)
        if self.fail: raise CacheUnavailable()
        self.values[key] = value
    async def acquire(self, key, token):
        await asyncio.sleep(0)
        if self.fail: raise CacheUnavailable()
        if key in self.values: return False
        self.values[key] = token
        return True
    async def release(self, key, token):
        if self.values.get(key) == token:
            self.values.pop(key)


class AsyncControlTests(unittest.IsolatedAsyncioTestCase):
    async def guarded_request(self, chunks, query=b'', path='/api/cart'):
        responses, forwarded = [], []
        async def endpoint(scope, receive, send):
            forwarded.append((await receive())['body'])
            await send({'type':'http.response.start','status':200,'headers':[]})
            await send({'type':'http.response.body','body':b'{}'})
        messages = iter({'type':'http.request','body':chunk,'more_body':index < len(chunks)-1}
                        for index,chunk in enumerate(chunks))
        async def receive(): return next(messages)
        async def send(message): responses.append(message)
        await RequestGuard(endpoint)({'type':'http','path':path,'method':'POST','headers':[(b'content-type',b'application/json')],
                                      'query_string':query}, receive, send)
        return responses[0]['status'], forwarded

    async def test_chunked_body_limits_prevent_unbounded_allocation(self):
        status, forwarded = await self.guarded_request([b'x'*8000]*3)
        self.assertEqual(status,413)
        self.assertFalse(forwarded)

    async def test_raw_numbers_reject_coercion_before_fastapi(self):
        for body in (b'{"product_id":"1"}', b'{"product_id":true}', b'{"product_id":1.0}', b'{"product_id":1,"price":0}'):
            status, forwarded = await self.guarded_request([body])
            self.assertEqual(status,422,body)
            self.assertFalse(forwarded)

    async def test_guard_replays_valid_stream_and_rejects_bad_json(self):
        status, forwarded = await self.guarded_request([b'{"product_',b'id":1}'])
        self.assertEqual((status,forwarded),(200,[b'{"product_id":1}']))
        self.assertEqual((await self.guarded_request([b'\xff']))[0],400)
        self.assertIn((await self.guarded_request([b'['*1500+b'0'+b']'*1500]))[0],{400,422})

    async def test_query_field_limit_applies_to_asgi(self):
        self.assertEqual((await self.guarded_request([b'{"product_id":1}'],query=b'&'.join([b'a=1']*21)))[0],422)

    async def test_hot_key_coalesces_concurrent_builders_across_instances(self):
        backend, state = AsyncMemoryBackend(), StateConnection()
        caches = [AsyncCatalogCache(Path('/tmp/async-cache-test'),backend) for _ in range(2)]
        builds = 0
        async def load():
            nonlocal builds
            builds += 1
            await asyncio.sleep(0.02)
            return {'stock':80}
        results = await asyncio.gather(*(caches[i%2].read(state,'product',{'product_id':1},load) for i in range(32)))
        self.assertEqual(builds,1)
        self.assertTrue(all(value=={'stock':80} for value in results))

    async def test_canceled_builder_releases_locks_for_the_next_request(self):
        backend, state = AsyncMemoryBackend(), StateConnection()
        cache = AsyncCatalogCache(Path('/tmp/async-cache-cancel'),backend)
        entered = asyncio.Event()
        async def never_finishes():
            entered.set()
            await asyncio.Event().wait()
        task = asyncio.create_task(cache.read(state,'product',{'product_id':1},never_finishes))
        await asyncio.wait_for(entered.wait(),1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertFalse(any(key.endswith(':lock') for key in backend.values))
        async def load(): return {'stock':1}
        self.assertEqual(await asyncio.wait_for(cache.read(state,'product',{'product_id':1},load),1),{'stock':1})

    async def test_outage_and_recovery_do_not_reuse_old_database_version(self):
        backend, state = AsyncMemoryBackend(), StateConnection()
        cache = AsyncCatalogCache(Path('/tmp/async-cache-outage'),backend)
        async def load(): return {'revision':state.revision}
        self.assertEqual((await cache.read(state,'product',{},load))['revision'],1)
        backend.fail, state.revision = True, 2
        self.assertEqual((await cache.read(state,'product',{},load))['revision'],2)
        backend.fail, cache.disabled_until = False, 0
        self.assertEqual((await cache.read(state,'product',{},load))['revision'],2)

    async def test_slow_password_hash_does_not_block_event_loop(self):
        started, release = threading.Event(), threading.Event()
        def blocking_hash(password):
            started.set()
            release.wait(timeout=2)
            return 'test-hash'
        with patch('app.security.hash_password',side_effect=blocking_hash):
            task = asyncio.create_task(hash_password_async('test-only'))
            try:
                for _ in range(100):
                    if started.is_set(): break
                    await asyncio.sleep(0.001)
                self.assertTrue(started.is_set())
                for _ in range(5): await asyncio.sleep(0.001)
                self.assertFalse(task.done())
            finally:
                release.set()
            self.assertEqual(await task,'test-hash')

    async def test_transaction_cancellation_rolls_back(self):
        class Connection:
            def __init__(self): self.calls=[]
            async def begin_transaction(self): self.calls.append('begin')
            async def commit(self): self.calls.append('commit')
            async def rollback(self): self.calls.append('rollback')
        connection, entered = Connection(), asyncio.Event()
        async def work():
            async with transaction(connection):
                entered.set()
                await asyncio.Event().wait()
        task=asyncio.create_task(work())
        await entered.wait(); task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(connection.calls,['begin','rollback'])


ASYNC_AVAILABLE = all(importlib.util.find_spec(name) for name in ('fastapi','sqlalchemy','aiomysql','aiosqlite','greenlet','httpx','jinja2'))


@unittest.skipUnless(ASYNC_AVAILABLE,'异步ORM/ASGI依赖未安装；事件循环与缓存控制测试仍可执行')
class AsyncORMTests(unittest.IsolatedAsyncioTestCase):
    def build_app(self):
        from app.main import create_app
        return create_app(Path(self.directory.name)/'async-test.db')

    async def asyncSetUp(self):
        import httpx
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.app = self.build_app()
        await self.enterAsyncContext(self.app.router.lifespan_context(self.app))
        self.httpx=httpx
        self.client=await self.enterAsyncContext(httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://testserver',headers={'X-Requested-With':'HeimaMall'}))
        response=await self.client.post('/api/auth/register',json={'name':'异步测试','phone':'13900000001','password':'async-test-only'})
        self.assertEqual(response.status_code,201,response.text)
        self.payload={'recipient':'测试','phone':'13900000001','address':'异步ORM独立测试地址','idempotency_key':'async-order-unique-0001'}

    async def test_public_comments_and_reviews_load_with_pagination(self):
        guest = await self.enterAsyncContext(self.httpx.AsyncClient(
            transport=self.httpx.ASGITransport(app=self.app), base_url='http://testserver'))
        for kind in ('comments', 'reviews'):
            response = await guest.get(f'/api/products/1/{kind}')
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['items'], [])
            self.assertEqual(response.json()['total'], 0)

        ids = []
        for content in ('第一条评论', '第二条评论', '第三条评论'):
            response = await self.client.post('/api/products/1/comments', json={'content':content})
            self.assertEqual(response.status_code, 200, response.text)
            ids.append(response.json()['id'])
        for page, expected_ids in ((1, list(reversed(ids[1:]))), (2, [ids[0]]), (3, [])):
            response = await guest.get(f'/api/products/1/comments?page={page}&page_size=2')
            self.assertEqual(response.status_code, 200, response.text)
            data = response.json()
            self.assertEqual((data['total'], data['page'], data['page_size']), (3, page, 2))
            self.assertEqual([item['id'] for item in data['items']], expected_ids)
            for item in data['items']:
                self.assertNotIn('phone', item)
                self.assertNotIn('user_id', item)
        response = await guest.get('/api/products/2/comments')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['total'], 0)
        async with self.app.state.database.connection() as connection:
            await connection.execute('UPDATE comments SET is_visible=0 WHERE id=?', (ids[-1],))
        response = await guest.get('/api/products/1/comments')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['total'], 2)
        self.assertEqual([item['id'] for item in response.json()['items']], list(reversed(ids[:-1])))

        self.assertEqual((await self.client.post('/api/cart', json={'product_id':1})).status_code, 200)
        response = await self.client.post('/api/orders', json=self.payload)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual((await self.client.post(f"/api/orders/{response.json()['id']}/pay")).status_code, 200)
        response = await self.client.put('/api/products/1/reviews', json={'rating':4,'content':'购买后评价'})
        self.assertEqual(response.status_code, 200, response.text)
        response = await guest.get('/api/products/1/reviews')
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual((data['total'], data['average_rating']), (1, 4))
        self.assertEqual(data['items'][0]['content'], '购买后评价')

    async def test_async_order_payment_cancel_and_fingerprint(self):
        self.assertEqual((await self.client.post('/api/cart',json={'product_id':1})).status_code,200)
        response=await self.client.post('/api/orders',json=self.payload)
        self.assertEqual(response.status_code,201,response.text)
        order_id=response.json()['id']
        self.assertEqual((await self.client.post('/api/orders',json=self.payload)).json()['id'],order_id)
        self.assertEqual((await self.client.post('/api/orders',json=dict(self.payload,address='完全不同的收货地址'))).status_code,409)
        self.assertEqual((await self.client.post(f'/api/orders/{order_id}/pay')).status_code,200)
        self.assertEqual((await self.client.post(f'/api/orders/{order_id}/cancel')).status_code,409)
        self.assertNotIn('request_hash',(await self.client.get(f'/api/orders/{order_id}')).json())

    async def test_concurrent_buyers_cannot_oversell_one_item(self):
        async with self.app.state.database.connection() as connection:
            await connection.execute('UPDATE products SET stock=1,sales=0 WHERE id=1')
        buyers=[]
        for index in range(6):
            client=await self.enterAsyncContext(self.httpx.AsyncClient(transport=self.httpx.ASGITransport(app=self.app),base_url='http://testserver',headers={'X-Requested-With':'HeimaMall'}))
            self.assertEqual((await client.post('/api/auth/register',json={'name':'并发测试','phone':f'139{index+2:08d}','password':'async-test-only'})).status_code,201)
            self.assertEqual((await client.post('/api/cart',json={'product_id':1})).status_code,200)
            buyers.append(client)
        results=await asyncio.gather(*(client.post('/api/orders',json=dict(self.payload,idempotency_key=f'async-competition-{index:04d}')) for index,client in enumerate(buyers)))
        self.assertEqual(sorted(r.status_code for r in results),[201]+[409]*5)
        async with self.app.state.database.connection() as connection:
            self.assertEqual((await connection.execute('SELECT stock FROM products WHERE id=1')).fetchone()[0],0)
            self.assertEqual((await connection.execute('SELECT COUNT(*) FROM orders')).fetchone()[0],1)

    async def test_real_async_transaction_cancel_restores_inventory(self):
        entered=asyncio.Event()
        async def work():
            async with self.app.state.database.connection() as connection:
                async with connection.atomic():
                    await connection.execute('UPDATE products SET stock=0 WHERE id=1')
                    entered.set()
                    await asyncio.Event().wait()
        task=asyncio.create_task(work())
        await entered.wait(); task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual((await self.client.get('/api/products/1')).json()['stock'],80)

    async def test_transaction_timeout_rolls_back_inventory(self):
        import sqlite3
        async with self.app.state.database.connection() as connection:
            connection.timeout = 0.02
            with self.assertRaises(sqlite3.OperationalError):
                async with connection.atomic():
                    await connection.execute('UPDATE products SET stock=0 WHERE id=1')
                    await asyncio.sleep(0.1)
        self.assertEqual((await self.client.get('/api/products/1')).json()['stock'],80)

    async def test_admin_cancel_competes_with_payment_without_double_stock(self):
        await self.client.post('/api/cart',json={'product_id':1})
        response=await self.client.post('/api/orders',json=self.payload)
        self.assertEqual(response.status_code,201,response.text)
        order_id=response.json()['id']
        manager=await self.enterAsyncContext(self.httpx.AsyncClient(transport=self.httpx.ASGITransport(app=self.app),base_url='http://testserver',headers={'X-Requested-With':'HeimaMall'}))
        self.assertEqual((await manager.post('/api/auth/register',json={'name':'测试管理员','phone':'13900000002','password':'async-test-only'})).status_code,201)
        async with self.app.state.database.connection() as connection:
            await connection.execute("UPDATE users SET role='admin',is_super_admin=1 WHERE phone='13900000002'")
        async with self.app.state.database.connection() as connection:
            base_sales=(await connection.execute('SELECT sales FROM products WHERE id=1')).fetchone()[0]
        results=await asyncio.gather(self.client.post(f'/api/orders/{order_id}/pay'),manager.post(f'/api/admin/orders/{order_id}/cancel'))
        self.assertEqual(sorted(r.status_code for r in results),[200,409])
        async with self.app.state.database.connection() as connection:
            status=(await connection.execute('SELECT status FROM orders WHERE id=?',(order_id,))).fetchone()[0]
            product=(await connection.execute('SELECT stock,sales FROM products WHERE id=1')).fetchone()
        self.assertEqual((product['stock'],product['sales']),(79,base_sales+1) if status=='paid' else (80,base_sales))

    async def test_raw_json_rejections_are_enforced_by_asgi_and_have_headers(self):
        for body, status in ((b'{"product_id":"1"}',422),(b'x'*16385,413),(b'\xff',400)):
            response=await self.client.post('/api/cart',content=body,headers={'Content-Type':'application/json'})
            self.assertEqual(response.status_code,status)
            self.assertEqual(response.headers['cache-control'],'no-store')
            self.assertIn('content-security-policy',response.headers)

    async def test_feature_routes_save_address_and_apply_coupon(self):
        response=await self.client.post('/api/addresses',json={'recipient':'测试','phone':'13900000001','address':'测试异步地址管理接口','is_default':True})
        self.assertEqual(response.status_code,200,response.text)
        address_id=response.json()['id']
        coupons=(await self.client.get('/api/coupons')).json()
        coupon_id=next(c['id'] for c in coupons if c['code']=='WELCOME10')
        self.assertEqual((await self.client.post(f'/api/coupons/{coupon_id}/claim')).status_code,200)
        self.assertEqual((await self.client.post('/api/cart',json={'product_id':1})).status_code,200)
        response=await self.client.post('/api/orders',json=dict(self.payload,address_id=address_id,coupon_id=coupon_id))
        self.assertEqual(response.status_code,201,response.text)
        self.assertEqual(response.json()['discount'],1000)

    async def test_cart_update_remove_and_guard_branches(self):
        c=self.client
        self.assertEqual((await c.post('/api/cart',json={'product_id':1,'quantity':1})).status_code,200)
        self.assertEqual((await c.patch('/api/cart/1',json={'quantity':2})).status_code,200)
        self.assertEqual((await c.patch('/api/cart/1',json={'quantity':99})).status_code,409)
        self.assertEqual((await c.patch('/api/cart/2',json={'quantity':1})).status_code,404)
        self.assertEqual((await c.delete('/api/cart/1')).status_code,200)
        self.assertEqual((await c.delete('/api/cart/1')).status_code,200)
        async with self.app.state.database.connection() as conn:
            await conn.execute('UPDATE products SET is_active=0 WHERE id=2')
        self.assertEqual((await c.post('/api/cart',json={'product_id':2})).status_code,409)
        await c.post('/api/cart',json={'product_id':1})
        async with self.app.state.database.connection() as conn:
            await conn.execute('UPDATE products SET is_active=0 WHERE id=1')
        self.assertEqual((await c.patch('/api/cart/1',json={'quantity':1})).status_code,409)
        async with self.app.state.database.connection() as conn:
            await conn.execute('UPDATE products SET is_active=1 WHERE id=1')
            await conn.execute('UPDATE stores SET is_active=0 WHERE id=2')
        self.assertEqual((await c.post('/api/cart',json={'product_id':1})).status_code,409)
        async with self.app.state.database.connection() as conn:
            await conn.execute('UPDATE stores SET is_active=1 WHERE id=2')
            await conn.execute("UPDATE users SET is_active=0 WHERE phone='13900000001'")
        self.assertEqual((await c.post('/api/cart',json={'product_id':1})).status_code,403)

    async def test_cancel_pending_and_receive_after_shipment(self):
        c=self.client
        coupons=(await c.get('/api/coupons')).json()
        coupon_id=next(x['id'] for x in coupons if x['code']=='WELCOME10')
        await c.post(f'/api/coupons/{coupon_id}/claim')
        await c.post('/api/cart',json={'product_id':1,'quantity':1})
        order=await c.post('/api/orders',json=dict(self.payload,coupon_id=coupon_id,idempotency_key='async-cancel-coupon-0001'))
        self.assertEqual(order.status_code,201,order.text)
        oid=order.json()['id']
        async with self.app.state.database.connection() as conn:
            stock_after_order=(await conn.execute('SELECT stock FROM products WHERE id=1')).fetchone()[0]
        cancel=await c.post(f'/api/orders/{oid}/cancel')
        self.assertEqual(cancel.status_code,200,cancel.text)
        self.assertEqual(cancel.json()['status'],'cancelled')
        async with self.app.state.database.connection() as conn:
            stock=(await conn.execute('SELECT stock FROM products WHERE id=1')).fetchone()[0]
            used=(await conn.execute('SELECT used_order_id FROM user_coupons WHERE coupon_id=?',(coupon_id,))).fetchone()[0]
        self.assertEqual(stock,stock_after_order+1)
        self.assertIsNone(used)
        await c.post('/api/cart',json={'product_id':1,'quantity':1})
        oid2=(await c.post('/api/orders',json=dict(self.payload,idempotency_key='async-receive-flow-0001'))).json()['id']
        self.assertEqual((await c.post(f'/api/orders/{oid2}/receive')).status_code,409)
        self.assertEqual((await c.post(f'/api/orders/{oid2}/pay')).status_code,200)
        self.assertEqual((await c.post(f'/api/orders/{oid2}/receive')).status_code,409)
        manager=await self.enterAsyncContext(self.httpx.AsyncClient(transport=self.httpx.ASGITransport(app=self.app),base_url='http://testserver',headers={'X-Requested-With':'HeimaMall'}))
        await manager.post('/api/auth/register',json={'name':'管理员','phone':'13900000002','password':'async-test-only'})
        async with self.app.state.database.connection() as conn:
            await conn.execute("UPDATE users SET role='admin',is_super_admin=1 WHERE phone='13900000002'")
        ship=await manager.post(f'/api/admin/orders/{oid2}/ship',json={'carrier':'顺丰','tracking_no':'SF1234567890'})
        self.assertEqual(ship.status_code,200,ship.text)
        receive=await c.post(f'/api/orders/{oid2}/receive')
        self.assertEqual(receive.status_code,200,receive.text)
        self.assertEqual(receive.json()['fulfillment_status'],'completed')
        async with self.app.state.database.connection() as conn:
            other=(await conn.execute('SELECT id FROM products WHERE store_id<>2 AND is_active=1 LIMIT 1')).fetchone()[0]
        await c.post('/api/cart',json={'product_id':1})
        await c.post('/api/cart',json={'product_id':other})
        multi=await c.post('/api/orders',json=dict(self.payload,idempotency_key='async-multi-store-0001'))
        self.assertEqual(multi.status_code,409,multi.text)

    async def test_every_api_endpoint_is_a_coroutine(self):
        import inspect
        endpoints=[r.endpoint for r in self.app.routes if getattr(r,'path','').startswith('/api/')]
        self.assertTrue(endpoints)
        self.assertTrue(all(inspect.iscoroutinefunction(endpoint) for endpoint in endpoints))


@unittest.skipUnless(ASYNC_AVAILABLE and os.getenv('MYSQL_ASYNC_TESTS')=='1',
                     '真实MySQL异步集成需要依赖、可连接本地服务及MYSQL_ASYNC_TESTS=1')
class MySQLAsyncORMTests(AsyncORMTests):
    """Run the same ORM/API invariants against a newly created local MySQL DB."""
    def build_app(self):
        import secrets
        from app.main import create_app
        from sqlalchemy.ext.asyncio import create_async_engine
        name='heima_test_async_'+secrets.token_hex(8)
        self.enterContext(patch.dict(os.environ,{'DATABASE_BACKEND':'mysql','MYSQL_DATABASE':name,
                                               'MYSQL_INITIALIZE_SCHEMA':'1','REDIS_URL':''}))
        app=create_app()
        async def drop_database():
            engine=create_async_engine(app.state.database.engine.url.set(database=None),connect_args={'connect_timeout':3})
            try:
                async with engine.begin() as connection:
                    await connection.exec_driver_sql(f'DROP DATABASE IF EXISTS `{name}`')
            finally:
                await engine.dispose()
        self.addAsyncCleanup(drop_database)
        return app


if __name__=='__main__': unittest.main()
