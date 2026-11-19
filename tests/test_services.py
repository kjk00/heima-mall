"""Opt-in integration tests against local services using isolated namespaces."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import secrets
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from app.cache import CatalogCache, RedisBackend
from app.mysql import MySQLDatabase
from app.server import MallApplication
from app.security import hash_password, token_hash
from tests.client import Client


@unittest.skipUnless(os.getenv('MYSQL_TESTS') == '1', '设置MYSQL_TESTS=1并配置本地MySQL后运行真实集成测试')
class MySQLIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.settings = patch.dict(os.environ, {'MYSQL_DATABASE': 'heima_test_' + secrets.token_hex(8), 'REDIS_URL': ''})
        self.settings.start()
        self.addCleanup(self.settings.stop)
        self.database = MySQLDatabase()
        with patch('app.server.Database', return_value=self.database):
            self.app = MallApplication()
        self.addCleanup(self.cleanup_database)
        self.client = Client(self.app)

    def cleanup_database(self):
        with self.database.connection(include_database=False) as connection:
            connection.execute(f'DROP DATABASE `{self.database.name}`')

    def test_order_coupon_payment_refund_and_dashboard(self):
        customer = {'name':'集成测试','phone':'13900000001','password':'integration-test-only'}
        self.assertEqual(self.client.post('/api/auth/register', customer)['status'], 201)
        self.assertEqual(self.client.post('/api/cart', {'product_id':1})['status'], 200)
        self.assertEqual(self.client.post('/api/coupons/1/claim')['status'], 200)
        payload = {'recipient':'测试','phone':customer['phone'],'address':'MySQL集成测试独立地址',
                   'coupon_id':1,'idempotency_key':'mysql-integration-order-0001'}
        response = self.client.post('/api/orders', payload)
        self.assertEqual(response['status'], 201, response.get('json'))
        order_id = response['json']['id']
        self.assertEqual(self.client.post('/api/orders', payload)['json']['id'], order_id)
        self.assertEqual(self.client.post(f'/api/orders/{order_id}/pay')['status'], 200)
        refund = self.client.post(f'/api/orders/{order_id}/refund', {'reason':'测试退款','idempotency_key':'mysql-integration-refund-0001'})
        self.assertEqual(refund['status'], 200, refund.get('json'))
        admin = Client(self.app)
        self.assertEqual(admin.post('/api/admin/setup', dict(customer, phone='13900000002'))['status'], 201)
        self.assertEqual(admin.post(f"/api/admin/refunds/{refund['json']['id']}/decision", {'approve':True})['status'], 200)
        dashboard = admin.get('/api/admin/dashboard')
        self.assertEqual(dashboard['status'], 200, dashboard.get('json'))
        self.assertEqual(dashboard['json']['summary']['revenue'], 0)
        self.assertEqual(sum(day['orders'] for day in dashboard['json']['trend']), 1)
        self.assertEqual(self.client.get('/api/products?q=%25_%3F')['json']['total'], 0)

    def test_competing_buyers_do_not_oversell_last_item(self):
        clients = []
        with self.database.connection() as connection:
            connection.execute('UPDATE products SET stock=1,sales=0 WHERE id=1')
            password = hash_password('integration-test-only')
            for user_id in range(1,17):
                token = secrets.token_urlsafe(32)
                connection.execute('INSERT INTO users(id,name,phone,password_hash) VALUES (?,?,?,?)',
                    (user_id, '测试', f'139{user_id:08d}', password))
                connection.execute('INSERT INTO sessions VALUES (?,?,?)', (token_hash(token),user_id,int(time.time())+3600))
                connection.execute('INSERT INTO cart_items VALUES (?,?,?)', (user_id,1,1))
                client = Client(self.app)
                client.cookies['heima_session'] = token
                clients.append(client)
        gate = threading.Barrier(16)
        def checkout(index):
            gate.wait(timeout=10)
            return clients[index].post('/api/orders', {'recipient':'测试','phone':f'139{index+1:08d}',
                'address':'MySQL并发测试独立地址','idempotency_key':f'mysql-competition-{index:04d}'})
        with ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(checkout, range(16)))
        self.assertEqual(sorted(r['status'] for r in results), [201] + [409]*15)
        with self.database.connection() as connection:
            self.assertEqual(connection.execute('SELECT stock FROM products WHERE id=1').fetchone()[0], 0)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM orders').fetchone()[0], 1)


@unittest.skipUnless(os.getenv('REDIS_TEST_URL'), '设置REDIS_TEST_URL后运行真实Redis集成测试')
class RedisIntegrationTests(unittest.TestCase):
    def test_catalog_hits_and_committed_write_invalidation(self):
        with tempfile.TemporaryDirectory() as directory:
            app = MallApplication(Path(directory)/'redis-test.db')
            backend = RedisBackend(os.environ['REDIS_TEST_URL'])
            cache = CatalogCache(app.state.database.path, backend)
            app.state.catalog_cache = cache
            try:
                client = Client(app)
                self.assertEqual(client.get('/api/products/1')['status'], 200)
                self.assertEqual(client.get('/api/products/1')['status'], 200)
                self.assertTrue(list(backend.client.scan_iter(match=cache.namespace+':*')))
                with app.state.database.connection() as connection:
                    connection.execute('UPDATE products SET stock=3 WHERE id=1')
                self.assertEqual(client.get('/api/products/1')['json']['stock'], 3)
            finally:
                keys = list(backend.client.scan_iter(match=cache.namespace+':*'))
                if keys:
                    backend.client.delete(*keys)
                cache.close()


@unittest.skipUnless(os.getenv('PRODUCT_SERVICES_TESTS') == '1',
                     '设置PRODUCT_SERVICES_TESTS=1并启动本地MongoDB/MinIO/ES后运行商品内容链路集成测试')
class ProductContentServicesTests(unittest.TestCase):
    """Write -> MySQL+MongoDB+MinIO, sync -> ES text, read -> assembled detail. Isolated namespaces."""

    def setUp(self):
        import asyncio
        from app import mongodb
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'content.db'
        self.bucket = 'heima-test-' + secrets.token_hex(6)
        self.env = patch.dict(os.environ, {
            'DATABASE_BACKEND': 'sqlite', 'REDIS_URL': '', 'RABBITMQ_URL': '',
            'MONGODB_URL': os.getenv('MONGODB_URL') or 'mongodb://127.0.0.1:27017',
            'MONGODB_DATABASE': '',
            'OSS_ENDPOINT': os.getenv('OSS_ENDPOINT') or '127.0.0.1:9000',
            'OSS_ACCESS_KEY': os.getenv('OSS_ACCESS_KEY') or 'minioadmin',
            'OSS_SECRET_KEY': os.getenv('OSS_SECRET_KEY') or 'minioadmin',
            'OSS_BUCKET': self.bucket,
            'ELASTICSEARCH_URL': os.getenv('ELASTICSEARCH_URL') or 'http://127.0.0.1:9200',
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.app = MallApplication(self.path)
        self.client = Client(self.app)
        self.admin = Client(self.app)
        self.assertEqual(self.admin.post('/api/admin/setup',
            {'name': '内容管理员', 'phone': '13700000001', 'password': 'integration-test-only'})['status'], 201)
        self.addCleanup(self.cleanup_services)

    def cleanup_services(self):
        import asyncio
        from app import mongodb
        from app.search import index_alias
        async def drop_mongo():
            instance = mongodb.store(self.app.state.database.path)
            if instance is not None:
                await instance.client.drop_database(instance.collection.database.name)
                await mongodb.close()
        asyncio.run(drop_mongo())
        storage = self.app.state.storage
        if storage.primary is not None:
            try:
                for obj in storage.primary.client.list_objects(self.bucket, recursive=True):
                    storage.primary.client.remove_object(self.bucket, obj.object_name)
                storage.primary.client.remove_bucket(self.bucket)
            except Exception:
                pass
        try:
            from elasticsearch import Elasticsearch
            es = Elasticsearch(os.environ['ELASTICSEARCH_URL'], request_timeout=10)
            alias = index_alias(self.app.state.database.path)
            if es.indices.exists_alias(name=alias):
                for index in es.indices.get_alias(name=alias).keys():
                    es.indices.delete(index=index)
            es.close()
        except Exception:
            pass

    def png_bytes(self):
        import io
        from PIL import Image
        buffer = io.BytesIO()
        Image.new('RGB', (4, 4), (120, 90, 160)).save(buffer, format='PNG')
        return buffer.getvalue()

    def test_publish_sync_search_and_detail_assembly(self):
        import asyncio
        import base64
        from app import mongodb
        from app.search import SearchEngine, index_alias
        from app.search_sync import full_reindex
        upload = self.admin.post('/api/admin/images',
            {'filename': 'test.png', 'data_base64': base64.b64encode(self.png_bytes()).decode()})
        self.assertEqual(upload['status'], 201, upload.get('json'))
        image = upload['json']['image']
        key = image.removeprefix('/media/')
        # Image landed in MinIO (primary), and is served back through /media/.
        self.assertIsNotNone(self.app.state.storage.primary.get(key))
        self.assertEqual(self.client.get(image)['status'], 200)
        params = [{'name': '品牌', 'value': '黑马集成'}, {'name': '材质', 'value': '陶瓷'}]
        detail_html = '<p>舒适佩戴</p><script>alert(1)</script><table><tr><td>容量</td><td>350ml</td></tr></table>'
        created = self.admin.post('/api/admin/products', {
            'name': '集成测试马克杯', 'subtitle': '链路验证', 'category': '家居生活',
            'price': 6900, 'original_price': 9900, 'stock': 20, 'image': image,
            'badge': '', 'color': '#eae5ed', 'description': '一只用于集成测试的杯子',
            'detail_html': detail_html, 'params': params, 'is_active': True})
        self.assertEqual(created['status'], 201, created.get('json'))
        product_id = created['json']['id']
        # MySQL keeps the durable params_json mirror.
        with self.app.state.database.connection() as connection:
            row = connection.execute('SELECT params_json,detail_html FROM products WHERE id=?', (product_id,)).fetchone()
        self.assertIn('黑马集成', row['params_json'])
        self.assertNotIn('script', row['detail_html'])
        # MongoDB holds the read-optimized params document.
        async def read_mongo():
            instance = mongodb.store(self.app.state.database.path)
            self.assertIsNotNone(instance, 'MongoDB应当可用')
            return await instance.get_many([product_id])
        found = asyncio.run(read_mongo())
        self.assertEqual(found[product_id], params)
        # Detail read assembles MySQL detail_html (sanitized) + MongoDB params.
        detail = self.client.get(f'/api/products/{product_id}')['json']
        self.assertEqual(detail['params'], params)
        self.assertNotIn('script', detail['detail_html'])
        self.assertIn('舒适佩戴', detail['detail_html'])
        # ES indexes the text fields; searching a param keyword finds the product.
        async def index_and_search():
            from app.async_contract import SyncDatabaseAdapter
            engine = SearchEngine.from_environment(self.app.state.database.path)
            self.assertIsNotNone(engine)
            from elasticsearch import AsyncElasticsearch
            es = AsyncElasticsearch(os.environ['ELASTICSEARCH_URL'], request_timeout=30)
            alias = index_alias(self.app.state.database.path)
            database = SyncDatabaseAdapter(self.app.state.database)
            try:
                await full_reindex(database, es, alias)
                async with database.connection() as connection:
                    return await engine.read(connection, dict(q='陶瓷', category='', sort='recommended',
                                                              page=1, page_size=12, on_sale=False, store_id=None))
            finally:
                await es.close()
        result = asyncio.run(index_and_search())
        self.assertIsNotNone(result, 'ES搜索应当可用')
        self.assertIn(product_id, result['ids'])


if __name__ == '__main__':
    unittest.main()
