import asyncio
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from app.server import MallApplication
from app import logic
from app.security import token_hash, verify_password
from tests.client import Client


class MallTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database_path = Path(self.directory.name) / 'test.db'
        self.app = MallApplication(self.database_path)
        self.client = Client(self.app)
        self.credentials = {'phone': '13800138000', 'password': 'test123456', 'name': '测试用户'}
        response = self.client.post('/api/auth/register', self.credentials)
        self.assertEqual(response['status'], 201)
        self.user = response['json']

    def checkout_payload(self, key='unique-order-key-001'):
        return {'recipient': '小马', 'phone': '13800138000', 'address': '上海市浦东新区测试路 100 号', 'idempotency_key': key}

    def add(self, product_id=1, quantity=1, client=None):
        response = (client or self.client).post('/api/cart', {'product_id': product_id, 'quantity': quantity})
        self.assertEqual(response['status'], 200, response)
        return response['json']

    def make_order(self, client=None, key='unique-order-key-001'):
        response = (client or self.client).post('/api/orders', self.checkout_payload(key))
        self.assertEqual(response['status'], 201, response)
        return response['json']

    def second_user(self):
        client = Client(self.app)
        result = client.post('/api/auth/register', dict(self.credentials, phone='13900139000', name='另一个用户'))
        self.assertEqual(result['status'], 201)
        return client

    def product(self, product_id=1):
        return self.client.get(f'/api/products/{product_id}')['json']

    def test_catalog_search_categories_sort_and_pagination(self):
        result = self.client.get('/api/products')['json']
        self.assertEqual(result['total'], 20)
        self.assertEqual(len(result['items']), 12)
        self.assertEqual(len(self.client.get('/api/categories')['json']), 6)
        self.assertEqual(self.client.get('/api/products?q=耳机')['json']['items'][0]['id'], 1)
        self.assertEqual(self.client.get('/api/products?category=家居生活')['json']['total'], 4)
        items = self.client.get('/api/products?sort=price_asc')['json']['items']
        self.assertEqual([p['price'] for p in items], sorted(p['price'] for p in items))
        self.assertEqual(self.client.get('/api/products?page=2&page_size=5')['json']['items'][0]['id'], 6)
        self.assertEqual(self.client.get('/api/products?q=%25')['json']['total'], 0)
        self.assertEqual(self.client.get('/api/products?q=%27%20OR%201%3D1--')['json']['total'], 0)
        self.assertEqual(self.client.get('/api/products?sort=unknown')['status'], 422)
        self.assertEqual(self.client.get('/api/products?page=0')['status'], 422)
        self.assertEqual(self.client.get('/api/products?page_size=51')['status'], 422)
        self.assertEqual(self.client.get('/api/products/999')['status'], 404)

    def test_password_session_login_and_logout(self):
        token = self.client.cookies['heima_session']
        with self.app.state.database.connection() as connection:
            row = connection.execute('SELECT password_hash FROM users WHERE id=?', (self.user['id'],)).fetchone()
            self.assertNotEqual(row['password_hash'], self.credentials['password'])
            self.assertTrue(verify_password(self.credentials['password'], row['password_hash']))
            self.assertIsNotNone(connection.execute('SELECT 1 FROM sessions WHERE token_hash=?', (token_hash(token),)).fetchone())
            self.assertIsNone(connection.execute('SELECT 1 FROM sessions WHERE token_hash=?', (token,)).fetchone())
        self.add()
        self.assertEqual(self.client.post('/api/auth/logout')['status'], 200)
        self.assertEqual(self.client.get('/api/auth/me')['status'], 401)
        self.assertEqual(self.client.login(self.credentials['phone'], 'badpassword')['status'], 401)
        login = self.client.login(self.credentials['phone'], self.credentials['password'])
        self.assertEqual(login['status'], 200)
        cookie = next(value for key, value in login['headers'] if key == 'Set-Cookie')
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=lax', cookie)
        self.assertEqual(self.client.get('/api/cart')['json']['count'], 1)

    def test_login_requires_single_use_captcha(self):
        self.client.post('/api/auth/logout')
        credentials = {key: self.credentials[key] for key in ('phone', 'password')}
        self.assertEqual(self.client.post('/api/auth/login', credentials)['status'], 422)
        self.assertEqual(self.client.post('/api/auth/login', dict(credentials, captcha_id='x', captcha_code='AAAA'))['status'], 422)
        captcha = self.client.get('/api/auth/captcha')['json']
        self.assertTrue(captcha['image'].startswith('data:image/png;base64,'))
        with self.app.state.database.connection() as connection:
            code = connection.execute('SELECT code FROM login_captchas WHERE id=?', (captcha['captcha_id'],)).fetchone()['code']
        wrong = 'AAAA' if code != 'AAAA' else 'BBBB'
        self.assertEqual(self.client.post('/api/auth/login', dict(credentials, captcha_id=captcha['captcha_id'], captcha_code=wrong))['status'], 400)
        # Failed attempts consume the captcha, so replaying the same id is rejected.
        self.assertEqual(self.client.post('/api/auth/login', dict(credentials, captcha_id=captcha['captcha_id'], captcha_code=code))['status'], 400)
        captcha = self.client.get('/api/auth/captcha')['json']
        with self.app.state.database.connection() as connection:
            connection.execute('UPDATE login_captchas SET expires_at=0 WHERE id=?', (captcha['captcha_id'],))
            code = connection.execute('SELECT code FROM login_captchas WHERE id=?', (captcha['captcha_id'],)).fetchone()['code']
        self.assertEqual(self.client.post('/api/auth/login', dict(credentials, captcha_id=captcha['captcha_id'], captcha_code=code))['status'], 400)
        captcha = self.client.get('/api/auth/captcha')['json']
        with self.app.state.database.connection() as connection:
            code = connection.execute('SELECT code FROM login_captchas WHERE id=?', (captcha['captcha_id'],)).fetchone()['code']
        self.assertEqual(self.client.post('/api/auth/login', dict(credentials, captcha_id=captcha['captcha_id'], captcha_code=code.lower()))['status'], 200)

    def test_concurrent_login_attempts_consume_captcha_only_once(self):
        captcha = self.client.get('/api/auth/captcha')['json']
        with self.app.state.database.connection() as connection:
            code = connection.execute('SELECT code FROM login_captchas WHERE id=?', (captcha['captcha_id'],)).fetchone()['code']
        clients = [Client(self.app), Client(self.app)]
        start = threading.Barrier(2)
        payload = {'phone': self.credentials['phone'], 'password': self.credentials['password'],
                   'captcha_id': captcha['captcha_id'], 'captcha_code': code}
        def login(client):
            start.wait()
            return client.post('/api/auth/login', payload)
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(login, clients))
        self.assertEqual(sorted(result['status'] for result in results), [200, 400])
        self.assertEqual(sorted(client.get('/api/auth/me')['status'] for client in clients), [200, 401])

    def test_login_verified_before_password_change_cannot_create_session_afterward(self):
        login_client = Client(self.app)
        captcha = login_client.get('/api/auth/captcha')['json']
        with self.app.state.database.connection() as connection:
            code = connection.execute('SELECT code FROM login_captchas WHERE id=?', (captcha['captcha_id'],)).fetchone()['code']
        verified = threading.Event()
        resume = threading.Event()
        verify = logic.verify_password_async
        async def delayed_verify(password, encoded):
            result = await verify(password, encoded)
            if password == self.credentials['password']:
                verified.set()
                await asyncio.to_thread(resume.wait, 10)
            return result
        payload = {'phone': self.credentials['phone'], 'password': self.credentials['password'],
                   'captcha_id': captcha['captcha_id'], 'captcha_code': code}
        with ThreadPoolExecutor(1) as pool, patch.object(logic, 'verify_password_async', delayed_verify):
            pending = pool.submit(login_client.post, '/api/auth/login', payload)
            self.assertTrue(verified.wait(10), 'login did not reach its password-verification checkpoint')
            changed = self.client.post('/api/settings/password',
                                       {'old_password': self.credentials['password'], 'new_password': 'updatedpass123'})
            self.assertEqual(changed['status'], 200, changed)
            resume.set()
            self.assertEqual(pending.result(timeout=10)['status'], 401)
        self.assertEqual(login_client.get('/api/auth/me')['status'], 401)
        self.assertEqual(self.client.get('/api/auth/me')['status'], 200)
        with self.app.state.database.connection() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM sessions WHERE user_id=?', (self.user['id'],)).fetchone()[0], 1)

    def test_captcha_issue_throttle_returns_429(self):
        for _ in range(30):
            self.assertEqual(self.client.get('/api/auth/captcha')['status'], 200)
        self.assertEqual(self.client.get('/api/auth/captcha')['status'], 429)

    def test_registration_and_request_validation(self):
        self.assertEqual(self.client.post('/api/auth/register', self.credentials)['status'], 409)
        for payload in [dict(self.credentials, phone='123'), dict(self.credentials, password='short'), dict(self.credentials, name='   '), dict(self.credentials, admin=True)]:
            self.assertEqual(self.client.post('/api/auth/register', payload)['status'], 422)
        self.assertEqual(self.client.post('/api/cart', {'product_id': 1, 'quantity': 0})['status'], 422)
        self.assertEqual(self.client.post('/api/cart', {'product_id': True})['status'], 422)
        self.assertEqual(self.client.post('/api/cart', {'product_id': 1, 'quantity': 1.5})['status'], 422)
        self.assertEqual(self.client.post('/api/cart', {'product_id': 999})['status'], 404)
        self.assertEqual(self.client.post('/api/cart', raw=b'not json')['status'], 400)
        self.assertEqual(self.client.post('/api/cart', raw=b'[]')['status'], 422)
        self.assertEqual(self.client.post('/api/cart', raw=b'x' * 17000)['status'], 413)
        self.assertEqual(self.client.post('/api/cart', {'product_id': 1}, headers={'Content-Type': 'text/plain'})['status'], 415)

    def test_authentication_csrf_and_expired_session(self):
        guest = Client(self.app)
        self.assertEqual(guest.get('/api/cart')['status'], 401)
        self.assertEqual(guest.get('/api/orders')['status'], 401)
        self.assertEqual(self.client.post('/api/cart', {'product_id': 1}, headers={'X-Requested-With': ''})['status'], 403)
        self.assertEqual(self.client.post('/api/cart', {'product_id': 1}, headers={'Origin': 'https://other.example'})['status'], 403)
        self.assertEqual(self.client.post('/api/cart', {'product_id': 1}, headers={'Origin': 'http://testserver'})['status'], 200)
        with self.app.state.database.connection() as connection:
            connection.execute('UPDATE sessions SET expires_at=?', (int(time.time()) - 1,))
        self.assertEqual(self.client.get('/api/auth/me')['status'], 401)

    def test_cart_quantity_shipping_and_stock_limits(self):
        cart = self.add(6)
        self.assertEqual((cart['subtotal'], cart['shipping_fee'], cart['total']), (6900, 1000, 7900))
        cart = self.add(6, 2)
        self.assertEqual((cart['count'], cart['shipping_fee'], cart['total']), (3, 0, 20700))
        cart = self.client.request('PATCH', '/api/cart/6', {'quantity': 1})['json']
        self.assertEqual(cart['count'], 1)
        self.assertEqual(self.client.request('PATCH', '/api/cart/1', {'quantity': 1})['status'], 404)
        self.assertEqual(self.client.post('/api/cart', {'product_id': 1, 'quantity': 81})['status'], 409)
        self.assertEqual(self.client.request('DELETE', '/api/cart/6')['json']['count'], 0)
        self.assertEqual(self.client.get('/api/cart')['json']['shipping_fee'], 0)

    def test_checkout_snapshot_idempotency_and_cancel(self):
        before = self.product()
        self.add(1, 2)
        order = self.make_order()
        self.assertEqual(order['total'], before['price'] * 2)
        self.assertEqual(order['status'], 'pending')
        self.assertEqual(self.product()['stock'], before['stock'] - 2)
        self.assertEqual(self.client.get('/api/cart')['json']['count'], 0)
        duplicate = self.make_order()
        self.assertEqual(order['id'], duplicate['id'])
        self.assertEqual(self.client.get('/api/orders')['json']['total'], 1)
        with self.app.state.database.connection() as connection:
            connection.execute('UPDATE products SET price=1,name=? WHERE id=1', ('改过的商品名称',))
        saved = self.client.get('/api/orders/' + order['id'])['json']
        self.assertEqual(saved['items'][0]['price'], before['price'])
        self.assertEqual(saved['items'][0]['name'], before['name'])
        for _ in range(2):
            self.assertEqual(self.client.post(f"/api/orders/{order['id']}/cancel")['json']['status'], 'cancelled')
        self.assertEqual(self.product()['stock'], before['stock'])
        self.assertEqual(self.client.post(f"/api/orders/{order['id']}/pay")['status'], 409)

    def test_checkout_failure_rolls_back_and_keeps_cart(self):
        before = self.product()
        self.add(1)
        self.add(2, 2)
        with self.app.state.database.connection() as connection:
            connection.execute('UPDATE products SET stock=1,store_id=(SELECT store_id FROM products WHERE id=1) WHERE id=2')
        self.assertEqual(self.client.post('/api/orders', self.checkout_payload())['status'], 409)
        self.assertEqual(self.product()['stock'], before['stock'])
        self.assertEqual(self.client.get('/api/orders')['json']['total'], 0)
        self.assertEqual(self.client.get('/api/cart')['json']['count'], 3)

    def test_empty_cart_cannot_checkout(self):
        self.assertEqual(self.client.post('/api/orders', self.checkout_payload())['status'], 400)

    def test_payment_is_idempotent_and_paid_order_cannot_cancel(self):
        before = self.product()
        self.add()
        order = self.make_order()
        for _ in range(2):
            response = self.client.post(f"/api/orders/{order['id']}/pay")
            self.assertEqual(response['json']['status'], 'paid')
            self.assertIn('未发生真实扣款', response['json']['message'])
        self.assertEqual(self.product()['sales'], before['sales'] + 1)
        self.assertEqual(self.product()['stock'], before['stock'] - 1)
        self.assertEqual(self.client.post(f"/api/orders/{order['id']}/cancel")['status'], 409)

    def test_users_cannot_read_or_modify_others_orders_and_carts(self):
        self.add()
        order = self.make_order()
        other = self.second_user()
        self.assertEqual(other.get('/api/cart')['json']['count'], 0)
        self.assertEqual(other.get('/api/orders')['json']['total'], 0)
        self.assertEqual(other.get(f"/api/orders/{order['id']}")['status'], 404)
        self.assertEqual(other.post(f"/api/orders/{order['id']}/pay")['status'], 404)
        self.assertEqual(other.post(f"/api/orders/{order['id']}/cancel")['status'], 404)

    def test_concurrent_checkout_cannot_oversell_last_item(self):
        other = self.second_user()
        self.add(client=self.client)
        self.add(client=other)
        with self.app.state.database.connection() as connection:
            connection.execute('UPDATE products SET stock=1 WHERE id=1')
        barrier = threading.Barrier(2)

        def purchase(client, key):
            barrier.wait()
            return client.post('/api/orders', self.checkout_payload(key))

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(purchase, self.client, 'concurrent-user-one'), pool.submit(purchase, other, 'concurrent-user-two')]
            results = [future.result() for future in futures]
        self.assertEqual(sorted(result['status'] for result in results), [201, 409])
        self.assertEqual(self.product()['stock'], 0)
        with self.app.state.database.connection() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM orders').fetchone()[0], 1)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM cart_items').fetchone()[0], 1)

    def test_concurrent_duplicate_checkout_creates_one_order(self):
        self.add()
        before = self.product()['stock']
        barrier = threading.Barrier(2)

        def purchase():
            client = Client(self.app)
            client.cookies = self.client.cookies.copy()
            barrier.wait()
            return client.post('/api/orders', self.checkout_payload())

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(purchase) for _ in range(2)]
            results = [future.result() for future in futures]
        self.assertEqual([result['status'] for result in results], [201, 201])
        self.assertEqual(results[0]['json']['id'], results[1]['json']['id'])
        self.assertEqual(self.product()['stock'], before - 1)

    def test_restart_preserves_cart_inventory_and_sessions(self):
        self.add(6, 2)
        with self.app.state.database.connection() as connection:
            connection.execute('UPDATE products SET stock=17 WHERE id=1')
        restarted = MallApplication(self.database_path)
        client = Client(restarted)
        client.cookies = self.client.cookies.copy()
        self.assertEqual(client.get('/api/cart')['json']['count'], 2)
        self.assertEqual(client.get('/api/products/1')['json']['stock'], 17)
        self.assertEqual(client.get('/api/products')['json']['total'], 20)

    def test_pages_assets_api_docs_and_path_traversal(self):
        for path in ['/', '/docs', '/static/style.css', '/static/app.js', '/static/images/headphones.svg']:
            response = self.client.get(path)
            self.assertEqual(response['status'], 200, path)
            self.assertTrue(response['body'])
        self.assertEqual(self.client.get('/api/health')['json'], {'status': 'ok'})
        spec = self.client.get('/openapi.json')['json']
        self.assertEqual(spec['openapi'], '3.1.0')
        self.assertIn('/api/orders/{order_id}/pay', spec['paths'])
        self.assertEqual(self.client.get('/static/../database.py')['status'], 404)
        self.assertEqual(self.client.get('/static/missing.svg')['status'], 404)
        self.assertEqual(self.client.request('PUT', '/api/products')['status'], 405)
        self.assertEqual(self.client.get('/api/missing')['status'], 404)
        self.assertEqual(self.client.request('HEAD', '/')['body'], b'')


if __name__ == '__main__':
    unittest.main()
