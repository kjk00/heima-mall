"""Optional smoke tests for the FastAPI adapter when its dependencies are installed."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

AVAILABLE = all(importlib.util.find_spec(name) for name in ('fastapi', 'uvicorn', 'jinja2', 'httpx', 'sqlalchemy', 'aiomysql', 'aiosqlite', 'greenlet'))


@unittest.skipUnless(AVAILABLE, 'FastAPI/httpx 未安装；标准库后端测试仍可运行')
class FastAPIAdapterTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from app.main import create_app
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.client = self.enterContext(TestClient(create_app(Path(self.directory.name) / 'test.db')))
        self.headers = {'X-Requested-With': 'HeimaMall'}

    def test_registration_cart_order_and_demo_payment(self):
        response = self.client.post('/api/auth/register', json={'name': '小马', 'phone': '13800138000', 'password': 'test123456'}, headers=self.headers)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(self.client.get('/api/auth/me').status_code, 200)
        self.assertEqual(self.client.post('/api/cart', json={'product_id': 1}, headers=self.headers).status_code, 200)
        payload = {'recipient': '小马', 'phone': '13800138000', 'address': '上海市测试路 100 号', 'idempotency_key': 'fastapi-order-unique-001'}
        response = self.client.post('/api/orders', json=payload, headers=self.headers)
        self.assertEqual(response.status_code, 201, response.text)
        order_id = response.json()['id']
        self.assertEqual(self.client.post('/api/orders', json=payload, headers=self.headers).json()['id'], order_id)
        self.assertEqual(self.client.post(f'/api/orders/{order_id}/pay', headers=self.headers).json()['status'], 'paid')
        self.assertEqual(self.client.post(f'/api/orders/{order_id}/cancel', headers=self.headers).status_code, 409)

    def test_authentication_validation_and_csrf(self):
        self.assertEqual(self.client.get('/api/cart').status_code, 401)
        self.assertEqual(self.client.get('/api/products?page=0').status_code, 422)
        self.assertEqual(self.client.post('/api/auth/register', json={}, headers=self.headers).status_code, 422)
        self.assertEqual(self.client.post('/api/auth/logout').status_code, 403)
        self.assertEqual(self.client.post('/api/auth/logout', headers=dict(self.headers, Origin='https://other.example')).status_code, 403)

    def test_offline_docs_and_storefront(self):
        self.assertEqual(self.client.get('/').status_code, 200)
        for path in ('/products/1', '/stores/2'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn('/static/pages.js', response.text)
        self.assertEqual(self.client.get('/docs').status_code, 200)
        self.assertIn('/api/orders', self.client.get('/openapi.json').json()['paths'])
        self.assertEqual(self.client.get('/static/images/headphones.svg').status_code, 200)

    def test_admin_permissions_product_updates_and_frontend_visibility(self):
        credentials = {'name': '管理员', 'phone': '13800138000', 'password': 'test123456'}
        self.assertEqual(self.client.post('/api/auth/register', json=credentials, headers=self.headers).status_code, 201)
        self.assertEqual(self.client.get('/api/admin/products').status_code, 403)
        # Provision a fixture directly; bootstrap's loopback checks are exercised by WSGI tests.
        async def promote():
            async with self.client.app.state.database.connection() as connection:
                await connection.execute("UPDATE users SET role='admin',is_super_admin=1 WHERE phone=?", (credentials['phone'],))
        self.client.portal.call(promote)
        self.assertEqual(self.client.get('/admin').status_code, 200)
        self.assertEqual(self.client.get('/api/admin/products?page=0').status_code, 422)
        from app.routes import EXAMPLES
        response = self.client.post('/api/admin/products', json=EXAMPLES['AdminProductInput'], headers=self.headers)
        self.assertEqual(response.status_code, 201, response.text)
        product = response.json()
        path = f"/api/admin/products/{product['id']}"
        updated = dict(EXAMPLES['AdminProductInput'], name='更新后的商品', version=product['version'])
        response = self.client.put(path, json=updated, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get(f"/api/products/{product['id']}").json()['name'], '更新后的商品')
        response = self.client.patch(path + '/status', json={'is_active': False, 'version': response.json()['version']}, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get(f"/api/products/{product['id']}").status_code, 404)
