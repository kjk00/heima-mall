import base64
import sqlite3
import asyncio
from app.async_contract import SyncConnectionAdapter
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time as datetime_time, timedelta, timezone
from pathlib import Path

from app.routes import ROUTES
from app import logic
from app.errors import MallError
from app.schemas import CheckoutInput
from app.security import hash_password, token_hash
from app.server import MallApplication
from tests.client import Client

PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGP4DwQACfsD/fteaysAAAAASUVORK5CYII='


class AdminIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.app = MallApplication(self.path / 'mall.db')
        self.customer = Client(self.app)
        self.customer_credentials = {'name': '商城用户', 'phone': '13800138000', 'password': 'customer123'}
        result = self.customer.post('/api/auth/register', self.customer_credentials)
        self.assertEqual(result['status'], 201)
        self.customer_id = result['json']['id']
        self.manager = Client(self.app)
        self.admin_credentials = {'name': '管理员', 'phone': '13900139000', 'password': 'admin123456'}
        result = self.manager.post('/api/admin/setup', self.admin_credentials)
        self.assertEqual(result['status'], 201, result)
        self.admin_id = result['json']['id']

    def product_payload(self, **changes):
        return dict({'name': '新上架陶瓷杯', 'subtitle': '给生活一点温度', 'category': '新分类', 'price': 6999,
                     'original_price': 9999, 'stock': 50, 'image': 'mug', 'badge': '新品', 'color': '#eae5ed',
                     'description': '380ml 陶瓷马克杯，包装包含一个杯子。', 'is_active': True}, **changes)

    def create_product(self, **changes):
        result = self.manager.post('/api/admin/products', self.product_payload(**changes))
        self.assertEqual(result['status'], 201, result)
        return result['json']

    def update_payload(self, product, **changes):
        columns = ('name', 'subtitle', 'category', 'price', 'original_price', 'stock', 'image', 'badge', 'color', 'description', 'version')
        return dict({column: product[column] for column in columns}, is_active=bool(product['is_active']), **changes)

    def new_order(self, product_id=1, quantity=1, paid=False, key='admin-test-order-key-001'):
        result = self.customer.post('/api/cart', {'product_id': product_id, 'quantity': quantity})
        self.assertEqual(result['status'], 200, result)
        payload = {'recipient': '收货人', 'phone': '13700137000', 'address': '上海市浦东新区测试路 100 号', 'idempotency_key': key}
        result = self.customer.post('/api/orders', payload)
        self.assertEqual(result['status'], 201, result)
        order = result['json']
        if paid:
            result = self.customer.post(f"/api/orders/{order['id']}/pay")
            self.assertEqual(result['status'], 200)
            order = result['json']
        return order

    def set_product_status(self, product_id, active):
        product = self.manager.get(f'/api/admin/products/{product_id}')['json']
        result = self.manager.request('PATCH', f'/api/admin/products/{product_id}/status', {'is_active': active, 'version': product['version']})
        self.assertEqual(result['status'], 200, result)
        return result['json']

    def test_all_admin_operations_reject_guests_and_customers(self):
        guest = Client(self.app)
        for route in ROUTES:
            if not route.admin:
                continue
            path = route.path.replace('{product_id}', '1').replace('{user_id}', str(self.customer_id)).replace('{order_id}', 'unknown')
            for client, expected in [(guest, 401), (self.customer, 403)]:
                result = client.request(route.method, path)
                self.assertEqual(result['status'], expected, (route.method, path, result))

    def test_bootstrap_is_once_only_and_normal_login_cannot_gain_admin(self):
        status = self.manager.get('/api/admin/setup')['json']
        self.assertFalse(status['setup_required'])
        self.assertEqual(self.manager.post('/api/admin/setup', self.admin_credentials)['status'], 409)
        self.assertEqual(self.customer.get('/api/auth/me')['json']['role'], 'customer')
        self.assertEqual(self.customer.login(self.customer_credentials['phone'], self.customer_credentials['password'], admin=True)['status'], 403)
        self.assertEqual(self.customer.post('/api/auth/register', dict(self.customer_credentials, role='admin'))['status'], 422)
        self.assertEqual(self.manager.get('/api/admin/auth/me')['json']['role'], 'admin')
        self.manager.post('/api/auth/logout')
        result = self.manager.login(self.admin_credentials['phone'], self.admin_credentials['password'], admin=True)
        self.assertEqual(result['status'], 200)
        self.assertEqual(result['json']['role'], 'admin')
        self.assertNotIn('password_hash', result['json'])

    def test_bootstrap_requires_local_peer_and_existing_account_password(self):
        app = MallApplication(self.path / 'setup.db')
        client = Client(app)
        self.assertEqual(client.post('/api/admin/setup', self.admin_credentials, remote_addr='192.168.1.10')['status'], 403)
        existing = client.post('/api/auth/register', self.customer_credentials)['json']
        result = client.post('/api/admin/setup', dict(self.customer_credentials, password='wrongpassword'))
        self.assertEqual(result['status'], 401)
        result = client.post('/api/admin/setup', self.customer_credentials)
        self.assertEqual(result['status'], 201)
        self.assertEqual(result['json']['id'], existing['id'])
        self.assertEqual(result['json']['role'], 'admin')

    def test_concurrent_bootstrap_creates_only_one_administrator(self):
        app = MallApplication(self.path / 'race.db')
        barrier = threading.Barrier(2)

        def initialize(phone):
            barrier.wait()
            return Client(app).post('/api/admin/setup', dict(self.admin_credentials, phone=phone))

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(initialize, phone) for phone in ('13900139001', '13900139002')]
            results = [future.result() for future in futures]
        self.assertEqual(sorted(result['status'] for result in results), [201, 409])
        with app.state.database.connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0], 1)

    def test_create_and_edit_product_changes_real_storefront_and_cart(self):
        product = self.create_product()
        product_id = product['id']
        self.assertEqual(self.customer.get(f'/api/products/{product_id}')['json']['price'], 6999)
        self.assertIn('新分类', [category['name'] for category in self.customer.get('/api/categories')['json']])
        self.customer.post('/api/cart', {'product_id': product_id})
        payload = self.update_payload(product, name='改名后的杯子', price=7999, stock=60)
        result = self.manager.request('PUT', f'/api/admin/products/{product_id}', payload)
        self.assertEqual(result['status'], 200, result)
        cart = self.customer.get('/api/cart')['json']
        self.assertEqual(cart['items'][0]['name'], '改名后的杯子')
        self.assertEqual(cart['subtotal'], 7999)
        self.assertEqual(cart['total'], 8999)
        order = self.new_order(product_id, key='updated-product-order')
        self.assertEqual(order['items'][0]['price'], 7999)
        self.assertEqual(order['items'][0]['name'], '改名后的杯子')

    def test_unlisting_hides_product_and_prevents_cart_and_checkout(self):
        product = self.create_product()
        product_id = product['id']
        self.customer.post('/api/cart', {'product_id': product_id})
        self.set_product_status(product_id, False)
        self.assertEqual(self.customer.get(f'/api/products/{product_id}')['status'], 404)
        self.assertEqual(self.customer.get('/api/products?category=新分类')['json']['total'], 0)
        self.assertNotIn('新分类', [category['name'] for category in self.customer.get('/api/categories')['json']])
        self.assertEqual(self.customer.post('/api/cart', {'product_id': product_id})['status'], 409)
        self.assertEqual(self.customer.request('PATCH', f'/api/cart/{product_id}', {'quantity': 2})['status'], 409)
        self.assertEqual(self.customer.get('/api/cart')['json']['items'][0]['is_active'], 0)
        payload = {'recipient': '用户', 'phone': '13800138000', 'address': '上海市测试路 100 号', 'idempotency_key': 'inactive-product-order'}
        self.assertEqual(self.customer.post('/api/orders', payload)['status'], 409)
        self.assertEqual(self.manager.get(f'/api/admin/products/{product_id}')['json']['stock'], 50)
        self.set_product_status(product_id, True)
        self.assertEqual(self.customer.post('/api/orders', payload)['status'], 201)

    def test_stale_admin_save_cannot_overwrite_checkout_inventory(self):
        product = self.manager.get('/api/admin/products/1')['json']
        self.new_order()
        result = self.manager.request('PUT', '/api/admin/products/1', self.update_payload(product, stock=1000))
        self.assertEqual(result['status'], 409)
        fresh = self.manager.get('/api/admin/products/1')['json']
        self.assertEqual(fresh['stock'], product['stock'] - 1)
        result = self.manager.request('PUT', '/api/admin/products/1', self.update_payload(fresh, stock=90))
        self.assertEqual(result['status'], 200)
        self.assertEqual(result['json']['stock'], 90)
        self.assertEqual(self.manager.request('PATCH', '/api/admin/products/1/status', {'is_active': False, 'version': product['version']})['status'], 409)

    def test_product_validation_rejects_bad_prices_images_and_fields(self):
        bad_values = [{'price': 0}, {'price': 1.2}, {'stock': -1}, {'original_price': 1}, {'stock': True},
                      {'color': '#fff;display:none'}, {'image': '../database'}, {'image': 'nonexistent'},
                      {'image': 'favicon'}, {'is_active': 'false'}, {'sales': 999999}]
        for changes in bad_values:
            result = self.manager.post('/api/admin/products', self.product_payload(**changes))
            self.assertEqual(result['status'], 422, (changes, result))

    def test_image_upload_and_new_product_use_same_public_image(self):
        result = self.manager.post('/api/admin/images', {'filename': '杯子.png', 'data_base64': PNG})
        self.assertEqual(result['status'], 201, result)
        image = result['json']['image']
        product = self.create_product(image=image)
        self.assertEqual(self.customer.get(f"/api/products/{product['id']}")['json']['image'], image)
        public_image = Client(self.app).get(image)
        self.assertEqual(public_image['status'], 200)
        self.assertEqual(public_image['body'], base64.b64decode(PNG))
        self.assertIn(('Content-Type', 'image/png'), public_image['headers'])
        repeated = self.manager.post('/api/admin/images', {'filename': 'same.png', 'data_base64': PNG})
        self.assertEqual(repeated['json']['image'], image)
        self.assertEqual(len(list(self.app.state.media_root.iterdir())), 1)
        self.assertEqual(self.customer.get('/media/../../app/database.py')['status'], 404)

    def test_image_upload_rejects_svg_invalid_encoding_and_large_images(self):
        for data in ['not base64', base64.b64encode(b'<svg><script>alert(1)</script></svg>').decode()]:
            result = self.manager.post('/api/admin/images', {'filename': 'image.svg', 'data_base64': data})
            self.assertEqual(result['status'], 422)
        content = b'\x89PNG\r\n\x1a\n' + b'x' * (2 * 1024 * 1024)
        self.assertEqual(self.manager.post('/api/admin/images', {'filename': 'large.png', 'data_base64': base64.b64encode(content).decode()})['status'], 413)

    def test_shipping_and_receipt_flow_updates_customer_order(self):
        order = self.new_order()
        order_id = order['id']
        shipment = {'carrier': '顺丰速运', 'tracking_no': 'SF1234567890'}
        self.assertEqual(self.manager.post(f'/api/admin/orders/{order_id}/ship', shipment)['status'], 409)
        self.assertEqual(self.customer.post(f'/api/orders/{order_id}/receive')['status'], 409)
        self.customer.post(f'/api/orders/{order_id}/pay')
        result = self.manager.post(f'/api/admin/orders/{order_id}/ship', shipment)
        self.assertEqual(result['status'], 200, result)
        for _ in range(2):
            self.assertEqual(self.manager.post(f'/api/admin/orders/{order_id}/ship', shipment)['status'], 200)
        self.assertEqual(self.manager.post(f'/api/admin/orders/{order_id}/ship', dict(shipment, tracking_no='different123'))['status'], 409)
        customer_order = self.customer.get(f'/api/orders/{order_id}')['json']
        self.assertEqual(customer_order['fulfillment_status'], 'shipped')
        self.assertEqual(customer_order['tracking_no'], shipment['tracking_no'])
        self.assertTrue(customer_order['shipped_at'])
        for _ in range(2):
            result = self.customer.post(f'/api/orders/{order_id}/receive')
            self.assertEqual(result['json']['fulfillment_status'], 'completed')
        self.assertTrue(self.manager.get(f'/api/admin/orders/{order_id}')['json']['completed_at'])
        self.assertEqual(self.manager.get('/api/admin/orders?status=completed')['json']['total'], 1)

    def test_other_customer_cannot_confirm_receipt_or_use_shipping_api(self):
        order = self.new_order(paid=True)
        shipment = {'carrier': '顺丰速运', 'tracking_no': 'SF1234567890'}
        self.manager.post(f"/api/admin/orders/{order['id']}/ship", shipment)
        other = Client(self.app)
        other.post('/api/auth/register', dict(self.customer_credentials, phone='13600136000'))
        self.assertEqual(other.post(f"/api/orders/{order['id']}/receive")['status'], 404)
        self.assertEqual(other.post(f"/api/admin/orders/{order['id']}/ship", shipment)['status'], 403)

    def test_admin_closes_pending_order_once_and_restores_stock(self):
        before = self.manager.get('/api/admin/products/1')['json']['stock']
        order = self.new_order(quantity=2)
        for _ in range(2):
            result = self.manager.post(f"/api/admin/orders/{order['id']}/cancel")
            self.assertEqual(result['json']['status'], 'cancelled')
        self.assertEqual(self.manager.get('/api/admin/products/1')['json']['stock'], before)
        self.assertEqual(self.customer.get(f"/api/orders/{order['id']}")['json']['status'], 'cancelled')
        paid = self.new_order(paid=True, key='paid-order-cannot-cancel')
        self.assertEqual(self.manager.post(f"/api/admin/orders/{paid['id']}/cancel")['status'], 409)

    def test_disabling_user_revokes_sessions_and_administrators_are_protected(self):
        path = f'/api/admin/users/{self.customer_id}/status'
        result = self.manager.request('PATCH', path, {'is_active': False})
        self.assertEqual(result['status'], 200)
        self.assertEqual(self.customer.get('/api/auth/me')['status'], 401)
        credentials = {key: self.customer_credentials[key] for key in ('phone', 'password')}
        self.assertEqual(self.customer.login(credentials['phone'], credentials['password'])['status'], 403)
        self.assertEqual(self.manager.request('PATCH', path, {'is_active': True})['status'], 200)
        self.assertEqual(self.customer.login(credentials['phone'], credentials['password'])['status'], 200)
        self.assertEqual(self.manager.request('PATCH', f'/api/admin/users/{self.admin_id}/status', {'is_active': False})['status'], 409)
        self.assertEqual(self.manager.get('/api/admin/auth/me')['status'], 200)

    def test_dashboard_and_user_metrics_are_based_on_real_orders(self):
        empty = self.manager.get('/api/admin/dashboard')['json']
        self.assertEqual(empty['summary']['revenue'], 0)
        self.assertEqual(empty['summary']['orders'], 0)
        self.assertEqual(empty['summary']['customers'], 1)
        self.assertEqual(len(empty['trend']), 7)
        order = self.new_order(product_id=6, paid=True)
        dashboard = self.manager.get('/api/admin/dashboard')['json']
        self.assertEqual(dashboard['summary']['revenue'], order['total'])
        self.assertEqual(dashboard['summary']['orders'], 1)
        self.assertEqual(dashboard['summary']['awaiting_shipment'], 1)
        self.assertEqual(sum(day['orders'] for day in dashboard['trend']), 1)
        customers = self.manager.get('/api/admin/users?q=13800138000')['json']['items']
        self.assertEqual(customers[0]['spent'], order['total'])
        self.assertEqual(customers[0]['order_count'], 1)
        self.assertNotIn('password_hash', customers[0])
        self.assertEqual(self.manager.get('/api/admin/orders?q=13800138000')['json']['total'], 1)

    def test_dashboard_uses_china_time_for_today_and_trend(self):
        order = self.new_order()
        china_time = timezone(timedelta(hours=8))
        today = datetime.now(china_time).date()
        created = datetime.combine(today, datetime_time(0, 30), tzinfo=china_time).astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        with self.app.state.database.connection() as connection:
            connection.execute('UPDATE orders SET created_at=? WHERE id=?', (created, order['id']))
        dashboard = self.manager.get('/api/admin/dashboard')['json']
        self.assertEqual(dashboard['summary']['today_orders'], 1)
        self.assertEqual(dashboard['trend'][-1]['day'], str(today))
        self.assertEqual(dashboard['trend'][-1]['orders'], 1)

    def test_disabled_account_cannot_checkout_with_previously_authenticated_identity(self):
        user = self.customer.get('/api/auth/me')['json']
        self.customer.post('/api/cart', {'product_id': 1})
        self.manager.request('PATCH', f'/api/admin/users/{user["id"]}/status', {'is_active': False})
        payload = CheckoutInput(recipient='用户', phone='13800138000', address='上海市测试路100号', idempotency_key='stale-identity-order-key')
        with self.app.state.database.connection() as connection:
            with self.assertRaises(MallError) as error:
                asyncio.run(logic.checkout(payload, user, SyncConnectionAdapter(connection)))
            self.assertEqual(error.exception.status_code, 403)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM orders').fetchone()[0], 0)
            self.assertEqual(connection.execute('SELECT stock FROM products WHERE id=1').fetchone()[0], 80)

    def test_admin_lists_filters_pages_literal_search_and_documentation(self):
        self.create_product(name='100% 好物', stock=3)
        self.assertEqual(self.manager.get('/api/admin/products?q=%25')['json']['total'], 1)
        self.assertEqual(self.manager.get('/api/admin/products?status=low_stock')['json']['total'], 1)
        page = self.manager.get('/api/admin/products?page=2&page_size=5')['json']
        self.assertEqual(len(page['items']), 5)
        for path in ['/api/admin/products?page=0', '/api/admin/orders?status=invalid', '/api/admin/users?page_size=51']:
            self.assertEqual(self.manager.get(path)['status'], 422)
        for path in ['/admin', '/admin/', '/static/admin.css', '/static/admin.js']:
            self.assertEqual(self.manager.get(path)['status'], 200)
        spec = self.manager.get('/openapi.json')['json']
        self.assertIn('put', spec['paths']['/api/admin/products/{product_id}'])
        self.assertEqual(spec['components']['schemas']['ProductStatusInput']['properties']['is_active']['type'], 'boolean')

    def test_product_updates_keep_historical_order_snapshots(self):
        order = self.new_order(paid=True)
        product = self.manager.get('/api/admin/products/1')['json']
        result = self.manager.request('PUT', '/api/admin/products/1', self.update_payload(product, name='更新后的耳机', image='mug', price=10000))
        self.assertEqual(result['status'], 200)
        saved = self.customer.get(f"/api/orders/{order['id']}")['json']
        self.assertEqual(saved['items'][0]['name'], order['items'][0]['name'])
        self.assertEqual(saved['items'][0]['image'], order['items'][0]['image'])
        self.assertEqual(saved['items'][0]['price'], order['items'][0]['price'])


class LegacyMigrationTests(unittest.TestCase):
    def test_old_database_is_upgraded_without_losing_existing_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'old.db'
            with sqlite3.connect(path) as connection:
                connection.executescript("""
                    CREATE TABLE users(id INTEGER PRIMARY KEY,phone TEXT UNIQUE NOT NULL,name TEXT NOT NULL,password_hash TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00Z');
                    CREATE TABLE products(id INTEGER PRIMARY KEY,name TEXT NOT NULL,subtitle TEXT NOT NULL,category TEXT NOT NULL,price INTEGER NOT NULL,original_price INTEGER NOT NULL,stock INTEGER NOT NULL,image TEXT NOT NULL,badge TEXT NOT NULL DEFAULT '',color TEXT NOT NULL,description TEXT NOT NULL,sales INTEGER NOT NULL DEFAULT 0);
                    CREATE TABLE sessions(token_hash TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),expires_at INTEGER NOT NULL);
                    CREATE TABLE orders(id TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),status TEXT NOT NULL CHECK(status IN ('pending','paid','cancelled')),subtotal INTEGER NOT NULL,shipping_fee INTEGER NOT NULL,total INTEGER NOT NULL,recipient TEXT NOT NULL,phone TEXT NOT NULL,address TEXT NOT NULL,idempotency_key TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00Z',UNIQUE(user_id,idempotency_key));
                """)
                connection.execute('INSERT INTO users(id,phone,name,password_hash) VALUES (7,?,?,?)', ('13800138000', '老用户', hash_password('existingpassword')))
                connection.execute("INSERT INTO products VALUES (1,'保留原商品','旧介绍','旧分类',12345,20000,17,'mug','','#eae5ed','已有商品详情',9)")
                connection.execute('INSERT INTO sessions VALUES (?,?,?)', (token_hash('legacy-session'), 7, int(time.time()) + 3600))
                connection.execute("INSERT INTO orders(id,user_id,status,subtotal,shipping_fee,total,recipient,phone,address,idempotency_key) VALUES ('OLD_ORDER',7,'pending',12345,1000,13345,'老用户','13800138000','上海市旧地址100号','old-order-unique-key')")
            app = MallApplication(path)
            client = Client(app)
            client.cookies['heima_session'] = 'legacy-session'
            self.assertEqual(client.get('/api/auth/me')['json']['role'], 'customer')
            self.assertEqual(client.get('/api/products/1')['json']['stock'], 17)
            self.assertEqual(client.get('/api/products/1')['json']['name'], '保留原商品')
            order = client.get('/api/orders/OLD_ORDER')['json']
            self.assertEqual(order['total'], 13345)
            self.assertEqual(order['fulfillment_status'], 'unfulfilled')
            MallApplication(path)
            self.assertEqual(client.get('/api/products/1')['json']['stock'], 17)


if __name__ == '__main__':
    unittest.main()
