"""Independent acceptance gates; failures describe defects, never mask them.

Run separately from the existing regression suite. All fixtures use disposable
databases and synthetic accounts; no application data or production code changes.
"""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import io
import json
import logging
import os
from pathlib import Path
import random
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.routes import ROUTES
from app.security import hash_password, token_hash
from app.server import MallApplication
from tests.client import Client


class AcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password_hash = hash_password('enterprise-test-only')

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='heima-acceptance-')
        self.addCleanup(self.directory.cleanup)
        self.db = Path(self.directory.name) / 'quality.db'
        self.app = MallApplication(self.db)
        self.client = self.fixture_client(1)
        self.other = self.fixture_client(2)
        self.checkout = {'recipient': '测试顾客', 'phone': '13900000001',
                         'address': '独立数据库测试路100号', 'store_id': 2,
                         'idempotency_key': 'enterprise-checkout-0001'}

    def fixture_client(self, user_id, admin=False):
        token = secrets.token_urlsafe(32)
        with self.app.state.database.connection() as connection:
            connection.execute('INSERT INTO users(id,phone,name,password_hash,role,is_super_admin) VALUES (?,?,?,?,?,?)',
                (user_id, f'139{user_id:08d}', f'测试用户{user_id}', self.password_hash,
                 'admin' if admin else 'customer', int(admin)))
            connection.execute('INSERT INTO sessions VALUES (?,?,?)',
                               (token_hash(token), user_id, int(time.time()) + 3600))
        client = Client(self.app)
        client.cookies['heima_session'] = token
        return client

    def order(self, key='enterprise-checkout-0001', quantity=1):
        self.assertEqual(self.client.post('/api/cart', {'product_id': 1, 'quantity': quantity})['status'], 200)
        response = self.client.post('/api/orders', dict(self.checkout, idempotency_key=key))
        self.assertEqual(response['status'], 201, response.get('json'))
        return response['json']['id']

    def race(self, *operations):
        gate = threading.Barrier(len(operations))
        def call(operation):
            gate.wait(timeout=10)
            return operation()
        with ThreadPoolExecutor(max_workers=len(operations)) as pool:
            return list(pool.map(call, operations))

    def test_csrf_every_write_route(self):
        guest = Client(self.app)
        for route in ROUTES:
            if route.method in {'POST', 'PUT', 'PATCH', 'DELETE'}:
                path = route.path
                for name in ('product_id', 'user_id', 'address_id', 'coupon_id', 'refund_id', 'comment_id', 'conversation_id', 'store_id', 'order_id'):
                    path = path.replace('{' + name + '}', '1')
                for headers in ({'X-Requested-With': ''}, {'Origin': 'https://attacker.invalid'}):
                    with self.subTest(method=route.method, path=path, headers=headers):
                        self.assertEqual(guest.request(route.method, path, {}, headers=headers)['status'], 403)

    def test_admin_route_guest_and_customer_isolation(self):
        for route in ROUTES:
            if not route.admin:
                continue
            path = route.path
            for name in ('product_id', 'user_id', 'address_id', 'coupon_id', 'refund_id', 'comment_id', 'conversation_id', 'store_id', 'order_id'):
                path = path.replace('{' + name + '}', '1')
            for client, expected in ((Client(self.app), 401), (self.client, 403)):
                with self.subTest(method=route.method, path=path, expected=expected):
                    self.assertEqual(client.request(route.method, path, {})['status'], expected)

    def test_invalid_json_types_and_encoding_are_client_errors(self):
        for raw in (b'null', b'[]', b'"text"', b'123', b'{', b'\xff', b'{"quantity":NaN}', b'{"quantity":true}'):
            with self.subTest(raw=repr(raw)):
                response = self.client.request('PATCH', '/api/cart/1', raw=raw)
                self.assertIn(response['status'], {400, 422}, response.get('json'))

    def test_deep_json_does_not_cause_server_error(self):
        response = self.client.post('/api/cart', raw=b'[' * 1500 + b'0' + b']' * 1500)
        self.assertIn(response['status'], {400, 413, 422}, response.get('json'))

    def test_request_size_content_type_and_query_limits(self):
        self.assertEqual(self.client.post('/api/cart', raw=b' ' * 16385)['status'], 413)
        self.assertEqual(self.client.post('/api/cart', {'product_id': 1}, headers={'Content-Type': 'text/plain'})['status'], 415)
        self.assertEqual(self.client.get('/api/products?' + '&'.join(f'q{i}=a' for i in range(21)))['status'], 422)
        for query in ('page=0', 'page=-1', 'page=2147483648', 'page_size=51', 'sort=price;DROP TABLE users', 'store_id=9223372036854775808'):
            with self.subTest(query=query):
                self.assertEqual(self.client.get('/api/products?' + query)['status'], 422)

    def test_strict_numeric_fields_reject_coercion_and_extra_amounts(self):
        for product_id in (True, '1', 1.0, None, -1, 2**63):
            with self.subTest(product_id=product_id):
                self.assertEqual(self.client.post('/api/cart', {'product_id': product_id})['status'], 422)
        self.assertEqual(self.client.post('/api/orders', dict(self.checkout, total=1, role='admin'))['status'], 422)

    def test_session_cookie_secure_flags_and_logout_revocation(self):
        with patch.dict(os.environ, {'COOKIE_SECURE': '1'}):
            response = self.client.login('13900000001', 'enterprise-test-only')
        self.assertEqual(response['status'], 200)
        cookie = next(v for k, v in response['headers'] if k.lower() == 'set-cookie').lower()
        for attribute in ('httponly', 'secure', 'samesite=lax', 'path=/'):
            self.assertIn(attribute, cookie)
        replay = Client(self.app)
        replay.cookies = dict(self.client.cookies)
        self.assertEqual(self.client.post('/api/auth/logout')['status'], 200)
        self.assertEqual(replay.get('/api/auth/me')['status'], 401)

    def test_login_throttle_returns_429(self):
        for _ in range(20):
            self.assertEqual(self.client.login('13900000001', 'incorrect-password')['status'], 401)
        self.assertEqual(self.client.login('13900000001', 'incorrect-password')['status'], 429)

    def test_admin_setup_revokes_preexisting_customer_session(self):
        replay = Client(self.app)
        replay.cookies = dict(self.client.cookies)
        response = self.client.post('/api/admin/setup', {'name': '初始化管理员', 'phone': '13900000001', 'password': 'enterprise-test-only'})
        self.assertEqual(response['status'], 201)
        self.assertEqual(self.client.get('/api/admin/auth/me')['status'], 200)
        self.assertEqual(replay.get('/api/admin/auth/me')['status'], 401,
                         '升级前的顾客Cookie获得了管理员权限；应撤销旧会话')

    def test_checkout_idempotency_replay_same_payload(self):
        order_id = self.order()
        self.assertEqual(self.client.post('/api/orders', self.checkout)['json']['id'], order_id)
        with self.app.state.database.connection() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM orders').fetchone()[0], 1)

    def test_checkout_idempotency_rejects_changed_payload(self):
        self.order()
        response = self.client.post('/api/orders', dict(self.checkout, address='完全不同的收货地址200号'))
        self.assertEqual(response['status'], 409, '相同幂等键用于不同收货请求仍返回201，未检测冲突')

    def test_parallel_pay_and_cancel_preserve_stock_and_sales(self):
        with self.app.state.database.connection() as connection:
            connection.execute('UPDATE products SET sales=0 WHERE id=1')
            initial = connection.execute('SELECT stock FROM products WHERE id=1').fetchone()[0]
        paid = 0
        for index in range(8):
            order_id = self.order(f'enterprise-race-order-{index:04d}')
            responses = self.race(lambda: self.client.post(f'/api/orders/{order_id}/pay'),
                                  lambda: self.client.post(f'/api/orders/{order_id}/cancel'))
            self.assertEqual(sorted(r['status'] for r in responses), [200, 409])
            paid += self.client.get(f'/api/orders/{order_id}')['json']['status'] == 'paid'
        with self.app.state.database.connection() as connection:
            row = connection.execute('SELECT stock,sales FROM products WHERE id=1').fetchone()
            self.assertEqual((row['stock'], row['sales']), (initial - paid, paid))
            self.assertFalse(connection.execute('PRAGMA foreign_key_check').fetchall())

    def test_parallel_address_cap_and_single_default(self):
        with self.app.state.database.connection() as connection:
            connection.executemany('INSERT INTO addresses(user_id,recipient,phone,address,is_default) VALUES (1,?,?,?,?)',
                                   [('测试', '13900000001', f'测试地址{n}号', int(n == 0)) for n in range(49)])
        payload = {'recipient': '测试', 'phone': '13900000001', 'address': '并发新增测试地址', 'is_default': True}
        responses = self.race(*[lambda: self.client.post('/api/addresses', payload) for _ in range(4)])
        self.assertEqual(sorted(r['status'] for r in responses), [200, 409, 409, 409])
        rows = self.client.get('/api/addresses')['json']
        self.assertEqual(len(rows), 50)
        self.assertEqual(sum(r['is_default'] for r in rows), 1)

    def test_checkout_mid_transaction_failure_rolls_back_every_write(self):
        with self.app.state.database.connection() as connection:
            connection.execute('UPDATE products SET store_id=2 WHERE id=2')
            before = [tuple(r) for r in connection.execute('SELECT id,stock,version FROM products WHERE id IN (1,2) ORDER BY id')]
            connection.execute("CREATE TRIGGER inject_failure BEFORE INSERT ON order_items WHEN NEW.product_id=2 BEGIN SELECT RAISE(ABORT,'test fault after first item'); END")
        self.assertEqual(self.client.post('/api/cart', {'product_id': 1})['status'], 200)
        self.assertEqual(self.client.post('/api/cart', {'product_id': 2})['status'], 200)
        self.assertEqual(self.client.post('/api/coupons/1/claim')['status'], 200)
        response = self.client.post('/api/orders', dict(self.checkout, coupon_id=1, save_address=True))
        self.assertEqual(response['status'], 500)
        with self.app.state.database.connection() as connection:
            self.assertEqual([tuple(r) for r in connection.execute('SELECT id,stock,version FROM products WHERE id IN (1,2) ORDER BY id')], before)
            for table in ('orders', 'order_items', 'tracking_events', 'addresses'):
                self.assertEqual(connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM cart_items').fetchone()[0], 2)
            self.assertIsNone(connection.execute('SELECT used_order_id FROM user_coupons').fetchone()[0])
            self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_process_crash_rolls_back_uncommitted_inventory(self):
        with self.app.state.database.connection() as connection:
            initial = connection.execute('SELECT stock FROM products WHERE id=1').fetchone()[0]
        code = "import sqlite3,os,sys; c=sqlite3.connect(sys.argv[1],isolation_level=None); c.execute('BEGIN IMMEDIATE'); c.execute('UPDATE products SET stock=0 WHERE id=1'); os._exit(23)"
        result = subprocess.run([sys.executable, '-c', code, str(self.db)], capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 23, result.stderr.decode())
        with self.app.state.database.connection() as connection:
            self.assertEqual(connection.execute('SELECT stock FROM products WHERE id=1').fetchone()[0], initial)
            self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.order()

    def test_online_backup_restore_preserves_order_session_and_inventory(self):
        import sqlite3
        order_id = self.order()
        self.assertEqual(self.client.post(f'/api/orders/{order_id}/pay')['status'], 200)
        backup = Path(self.directory.name) / 'restored' / 'mall.db'
        backup.parent.mkdir()
        with self.app.state.database.connection() as source, sqlite3.connect(backup) as target:
            source.backup(target)
        restored = Client(MallApplication(backup))
        restored.cookies = dict(self.client.cookies)
        self.assertEqual(restored.get('/api/auth/me')['status'], 200)
        self.assertEqual(restored.get(f'/api/orders/{order_id}')['json'], self.client.get(f'/api/orders/{order_id}')['json'])
        self.assertEqual(restored.get('/api/products/1')['json']['stock'], self.client.get('/api/products/1')['json']['stock'])

    def test_randomized_order_lifecycle_conserves_inventory_and_sales(self):
        admin = self.fixture_client(3, admin=True)
        with self.app.state.database.connection() as connection:
            connection.execute('UPDATE products SET stock=500,sales=0 WHERE id=1')
        expected_stock, expected_sales = 500, 0
        rng = random.Random(20261117)
        for index in range(30):
            quantity, action = rng.randint(1, 3), rng.randrange(5)
            order_id = self.order(f'enterprise-lifecycle-{index:04d}', quantity)
            expected_stock -= quantity
            if action == 0:
                self.assertEqual(self.client.post(f'/api/orders/{order_id}/cancel')['status'], 200)
                self.assertEqual(self.client.post(f'/api/orders/{order_id}/cancel')['status'], 200)
                expected_stock += quantity
            elif action != 1:
                self.assertEqual(self.client.post(f'/api/orders/{order_id}/pay')['status'], 200)
                expected_sales += quantity
                if action in (3, 4):
                    if action == 4:
                        self.assertEqual(admin.post(f'/api/admin/orders/{order_id}/ship', {'carrier': '测试物流', 'tracking_no': f'TEST{index:08d}'})['status'], 200)
                    refund = self.client.post(f'/api/orders/{order_id}/refund', {'reason': '测试退款', 'idempotency_key': f'enterprise-refund-{index:04d}'})['json']
                    response = admin.post(f"/api/admin/refunds/{refund['id']}/decision", {'approve': True, 'restock': False})
                    self.assertEqual(response['status'], 200)
                    expected_sales -= quantity
                    if action == 3:
                        expected_stock += quantity
            with self.app.state.database.connection() as connection:
                product = connection.execute('SELECT stock,sales FROM products WHERE id=1').fetchone()
                self.assertEqual((product['stock'], product['sales']), (expected_stock, expected_sales), f'round={index},action={action}')
                self.assertFalse(connection.execute('PRAGMA foreign_key_check').fetchall())

    def test_truncated_images_are_rejected(self):
        admin = self.fixture_client(3, admin=True)
        png = b'\x89PNG\r\n\x1a\n' + (13).to_bytes(4, 'big') + b'IHDR' + (1).to_bytes(4, 'big') * 2 + b'\x08\x02\x00\x00\x00' + b'\x00' * 4
        jpeg = b'\xff\xd8\xff' + b'\x00' * 16 + b'\xff\xd9'
        webp = b'RIFF' + (22).to_bytes(4, 'little') + b'WEBPVP8 ' + b'\x00' * 14
        for kind, content in (('png', png), ('jpeg', jpeg), ('webp', webp)):
            with self.subTest(kind=kind):
                response = admin.post('/api/admin/images', {'filename': f'broken.{kind}', 'data_base64': base64.b64encode(content).decode()})
                self.assertEqual(response['status'], 422, '仅伪造文件头的不可解码图片被接受')

    def test_public_assets_and_api_security_headers(self):
        for path in ('/', '/admin', '/docs', '/api/health', '/api/products', '/api/cart'):
            response = self.client.get(path)
            headers = dict(response['headers'])
            for header in ('Content-Security-Policy', 'X-Frame-Options', 'X-Content-Type-Options', 'Referrer-Policy'):
                self.assertIn(header, headers)
            if path.startswith('/api/'):
                self.assertEqual(headers['Cache-Control'], 'no-store')
        for path in ('/static/../database.py', '/static/../../data/mall.db', '/media/../mall.db'):
            self.assertEqual(self.client.get(path)['status'], 404)


class JSONResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []

    def startTest(self, test):
        super().startTest(test)
        self.record = {'test': test.id().rsplit('.', 1)[-1], 'status': 'passed', 'details': []}
        self.started = time.perf_counter()

    def addFailure(self, test, error):
        super().addFailure(test, error)
        self.record['status'] = 'failed'
        self.record['details'].append(self._exc_info_to_string(error, test))

    def addError(self, test, error):
        super().addError(test, error)
        self.record['status'] = 'error'
        self.record['details'].append(self._exc_info_to_string(error, test))

    def addSubTest(self, test, subtest, error):
        super().addSubTest(test, subtest, error)
        if error:
            self.record['status'] = 'failed'
            self.record['details'].append(str(subtest) + '\n' + self._exc_info_to_string(error, test))

    def stopTest(self, test):
        self.record['seconds'] = round(time.perf_counter() - self.started, 4)
        self.records.append(self.record)
        super().stopTest(test)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'reports/enterprise-quality-results.json')
    parser.add_argument('--pattern', default='', help='只运行名称包含此文本的验收用例')
    args = parser.parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(AcceptanceTests)
    if args.pattern:
        suite = unittest.TestSuite(test for test in suite if args.pattern in test.id())
    if suite.countTestCases() == 0:
        parser.error('没有匹配的验收用例，请检查 --pattern')
    # Expected fault injection and rejected malformed requests are captured,
    # while assertions and response evidence remain visible in the JSON report.
    logs = io.StringIO()
    handler = logging.StreamHandler(logs)
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    try:
        result = unittest.TextTestRunner(verbosity=2, resultclass=JSONResult).run(suite)
    finally:
        root_logger.removeHandler(handler)
    report = {'timestamp_utc': datetime.now(timezone.utc).isoformat(),
              'python': sys.version.split()[0], 'mode': 'isolated in-process WSGI acceptance',
              'tests_run': result.testsRun,
              'passed': sum(r['status'] == 'passed' for r in result.records),
              'failed': sum(r['status'] != 'passed' for r in result.records),
              'successful': result.wasSuccessful(), 'tests': result.records,
              'application_logs': logs.getvalue()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'验收原始结果：{args.output}')
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
