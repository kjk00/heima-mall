"""Payment deadlines, reservation cleanup, restart recovery, and races."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import importlib.util
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from app.async_contract import SyncConnectionAdapter
from app.order_expiration import expire_due_orders, expire_orders_once, legacy_expiration_worker, payment_deadline
from app.server import MallApplication
from tests.client import Client


class OrderExpirationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.enterContext(patch.dict(os.environ, {'REDIS_URL':''}))
        self.app = MallApplication(Path(directory.name)/'mall.db')
        self.client = Client(self.app)
        self.assertEqual(self.client.post('/api/auth/register', {
            'name':'过期测试','phone':'13800138000','password':'test123456'})['status'], 201)
        self.initial = self.client.get('/api/products/1')['json']
        self.assertEqual(self.client.post('/api/coupons/1/claim')['status'], 200)
        self.assertEqual(self.client.post('/api/cart', {'product_id':1,'quantity':2})['status'], 200)
        response = self.client.post('/api/orders', {'recipient':'测试','phone':'13800138000',
            'address':'上海市测试路100号','coupon_id':1,'idempotency_key':'expiry-order-00000001'})
        self.assertEqual(response['status'], 201, response)
        self.order = response['json']
        self.deadline = payment_deadline(self.order['created_at'])

    def sweep(self):
        with self.app.state.database.connection() as raw:
            return asyncio.run(expire_due_orders(SyncConnectionAdapter(raw)))

    def assert_reservation(self, status, restored):
        with self.app.state.database.connection() as raw:
            order = raw.execute('SELECT status FROM orders WHERE id=?', (self.order['id'],)).fetchone()
            product = raw.execute('SELECT stock,sales FROM products WHERE id=1').fetchone()
            coupon = raw.execute('SELECT used_order_id FROM user_coupons WHERE coupon_id=1').fetchone()
            count = raw.execute("SELECT COUNT(*) FROM tracking_events WHERE order_id=? AND stage='expired'",
                                (self.order['id'],)).fetchone()[0]
        self.assertEqual(order['status'], status)
        self.assertEqual(product['stock'], self.initial['stock'] if restored else self.initial['stock']-2)
        self.assertEqual(product['sales'], self.initial['sales'])
        self.assertEqual(coupon['used_order_id'], None if restored else self.order['id'])
        self.assertEqual(count, int(status == 'cancelled' and restored))

    def test_deadline_and_payment_before_fifteen_minutes(self):
        self.assertEqual(self.order['payment_expires_at'], self.deadline.isoformat())
        self.assertFalse(self.order['is_expired'])
        with patch('app.order_expiration.utc_now', return_value=self.deadline-timedelta(seconds=1)):
            self.assertEqual(self.sweep(), 0)
            self.assertEqual(self.client.post(f"/api/orders/{self.order['id']}/pay")['status'], 200)
        with patch('app.order_expiration.utc_now', return_value=self.deadline+timedelta(seconds=1)):
            self.assertEqual(self.sweep(), 0)
            self.assertEqual(self.client.post(f"/api/orders/{self.order['id']}/pay")['status'], 200)
            saved = self.client.get(f"/api/orders/{self.order['id']}")['json']
        self.assertEqual(saved['status'], 'paid')
        self.assertFalse(saved['is_expired'])

    def test_late_payment_commits_cleanup_before_returning_conflict(self):
        with patch('app.order_expiration.utc_now', return_value=self.deadline):
            for _ in range(2):
                response = self.client.post(f"/api/orders/{self.order['id']}/pay")
                self.assertEqual(response['status'], 409, response)
                self.assertIn('超过15分钟', response['json']['detail'])
            self.assertEqual(self.sweep(), 0)
            saved = self.client.post(f"/api/orders/{self.order['id']}/cancel")['json']
        self.assertTrue(saved['is_expired'])
        self.assert_reservation('cancelled', True)

    def test_sweep_releases_stock_and_coupon_without_an_http_request(self):
        with patch('app.order_expiration.utc_now', return_value=self.deadline):
            self.assertEqual(self.sweep(), 1)
            self.assertEqual(self.sweep(), 0)
        self.assert_reservation('cancelled', True)

    def test_reading_an_expired_order_closes_it_immediately(self):
        with patch('app.order_expiration.utc_now', return_value=self.deadline):
            response = self.client.get(f"/api/orders/{self.order['id']}")
        self.assertEqual(response['status'], 200, response)
        self.assertTrue(response['json']['is_expired'])
        self.assert_reservation('cancelled', True)

    def test_manual_cancellation_stays_distinct_from_expiration(self):
        response = self.client.post(f"/api/orders/{self.order['id']}/cancel")
        self.assertEqual(response['status'], 200)
        with patch('app.order_expiration.utc_now', return_value=self.deadline):
            self.assertEqual(self.sweep(), 0)
            saved = self.client.get(f"/api/orders/{self.order['id']}")['json']
        self.assertEqual(saved['status'], 'cancelled')
        self.assertFalse(saved['is_expired'])
        self.assertEqual(self.client.get('/api/products/1')['json']['stock'], self.initial['stock'])

    def test_cleanup_rolls_back_all_changes_on_failure(self):
        original = SyncConnectionAdapter.execute
        async def fail_stock(connection, sql, parameters=()):
            if sql.startswith('UPDATE products SET stock=stock+'):
                raise RuntimeError('injected cleanup failure')
            return await original(connection, sql, parameters)
        with patch('app.order_expiration.utc_now', return_value=self.deadline):
            with patch.object(SyncConnectionAdapter, 'execute', fail_stock):
                with self.assertRaisesRegex(RuntimeError, 'injected'):
                    self.sweep()
            self.assert_reservation('pending', False)
            self.assertEqual(self.sweep(), 1)
        self.assert_reservation('cancelled', True)

    def test_two_sweepers_and_payment_cannot_restore_stock_twice(self):
        gate = threading.Barrier(3)
        def sweep():
            gate.wait(timeout=5)
            return self.sweep()
        def pay():
            gate.wait(timeout=5)
            return self.client.post(f"/api/orders/{self.order['id']}/pay")
        with patch('app.order_expiration.utc_now', return_value=self.deadline):
            with ThreadPoolExecutor(3) as pool:
                futures = [pool.submit(sweep), pool.submit(sweep), pool.submit(pay)]
                results = [future.result(timeout=10) for future in futures]
        self.assertEqual(results[-1]['status'], 409, results)
        self.assert_reservation('cancelled', True)

    def test_legacy_worker_recovers_overdue_orders_on_start(self):
        with patch('app.order_expiration.utc_now', return_value=self.deadline):
            with legacy_expiration_worker(self.app.state.database):
                self.assert_reservation('cancelled', True)


ASYNC_AVAILABLE = all(importlib.util.find_spec(name) for name in (
    'fastapi','sqlalchemy','aiomysql','aiosqlite','greenlet','httpx','jinja2'))


@unittest.skipUnless(ASYNC_AVAILABLE, '异步ORM测试依赖未安装')
class AsyncOrderExpirationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import httpx
        from app.main import create_app
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.enterContext(patch.dict(os.environ, {'REDIS_URL':''}))
        self.path = Path(directory.name)/'async.db'
        self.app = create_app(self.path)
        await self.enterAsyncContext(self.app.router.lifespan_context(self.app))
        self.client = await self.enterAsyncContext(httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
            base_url='http://testserver', headers={'X-Requested-With':'HeimaMall'}))
        self.assertEqual((await self.client.post('/api/auth/register', json={
            'name':'过期测试','phone':'13800138000','password':'test123456'})).status_code, 201)
        self.initial = (await self.client.get('/api/products/1')).json()
        self.assertEqual((await self.client.post('/api/coupons/1/claim')).status_code, 200)
        self.assertEqual((await self.client.post('/api/cart', json={'product_id':1,'quantity':2})).status_code, 200)
        response = await self.client.post('/api/orders', json={'recipient':'测试','phone':'13800138000',
            'address':'上海市测试路100号','coupon_id':1,'idempotency_key':'async-expiry-order-00001'})
        self.assertEqual(response.status_code, 201, response.text)
        self.order = response.json()
        self.deadline = payment_deadline(self.order['created_at'])

    async def assert_expired(self):
        async with self.app.state.database.connection() as connection:
            row = (await connection.execute('SELECT status FROM orders WHERE id=?', (self.order['id'],))).fetchone()
            product = (await connection.execute('SELECT stock,sales FROM products WHERE id=1')).fetchone()
            coupon = (await connection.execute('SELECT used_order_id FROM user_coupons WHERE coupon_id=1')).fetchone()
            events = (await connection.execute("SELECT COUNT(*) FROM tracking_events WHERE order_id=? AND stage='expired'",
                                               (self.order['id'],))).fetchone()[0]
        self.assertEqual(row['status'], 'cancelled')
        self.assertEqual((product['stock'],product['sales']), (self.initial['stock'],self.initial['sales']))
        self.assertIsNone(coupon['used_order_id'])
        self.assertEqual(events, 1)

    async def test_orm_late_payment_commits_expiration(self):
        with patch('app.order_expiration.utc_now', return_value=self.deadline):
            response = await self.client.post(f"/api/orders/{self.order['id']}/pay")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn('超过15分钟', response.json()['detail'])
        await self.assert_expired()

    async def test_payment_rechecks_deadline_after_waiting_for_product_locks(self):
        from app.async_database import AsyncConnection
        clock = self.enterContext(patch('app.order_expiration.utc_now', return_value=self.deadline-timedelta(seconds=1)))
        original = AsyncConnection.lock_products
        async def late_products(connection, product_ids):
            products = await original(connection, product_ids)
            clock.return_value = self.deadline
            return products
        with patch.object(AsyncConnection, 'lock_products', late_products):
            response = await self.client.post(f"/api/orders/{self.order['id']}/pay")
        self.assertEqual(response.status_code, 409, response.text)
        await self.assert_expired()

    async def test_orm_payment_cancel_and_two_sweepers_compete(self):
        with patch('app.order_expiration.utc_now', return_value=self.deadline):
            results = await asyncio.gather(
                self.client.post(f"/api/orders/{self.order['id']}/pay"),
                self.client.post(f"/api/orders/{self.order['id']}/cancel"),
                expire_orders_once(self.app.state.database), expire_orders_once(self.app.state.database))
        self.assertEqual(results[0].status_code, 409, results[0].text)
        self.assertEqual(results[1].status_code, 200, results[1].text)
        await self.assert_expired()

    async def test_background_expires_orders_without_requests(self):
        from app.order_expiration import run_order_expiration
        self.app.state.order_expiration_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await self.app.state.order_expiration_task
        with patch('app.order_expiration.utc_now', return_value=self.deadline):
            task = asyncio.create_task(run_order_expiration(self.app.state.database, interval=0.01))
            try:
                async with asyncio.timeout(2):
                    while True:
                        async with self.app.state.database.connection() as connection:
                            row = (await connection.execute('SELECT status FROM orders WHERE id=?', (self.order['id'],))).fetchone()
                        if row['status'] == 'cancelled':
                            break
                        await asyncio.sleep(0.01)
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        await self.assert_expired()

    async def test_startup_recovers_orders_expired_while_stopped(self):
        from app.main import create_app
        with patch('app.order_expiration.utc_now', return_value=self.deadline):
            restarted = create_app(self.path)
            async with restarted.router.lifespan_context(restarted):
                await self.assert_expired()
            self.assertTrue(restarted.state.order_expiration_task.cancelled())


if __name__ == '__main__':
    unittest.main()
