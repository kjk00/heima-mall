"""Cache consistency and stampede tests use real isolated database requests."""
from concurrent.futures import ThreadPoolExecutor
import json
import inspect
import asyncio
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from app import logic
from app.cache import CatalogCache, CacheUnavailable, RedisBackend
from app.database import transaction
from app.server import MallApplication
from tests.client import Client


class MemoryBackend:
    def __init__(self):
        self.values, self.lock, self.fail = {}, threading.Lock(), False

    def get(self, key):
        if self.fail:
            raise CacheUnavailable()
        with self.lock:
            row = self.values.get(key)
            return row[0] if row and row[1] > time.monotonic() else None

    def put(self, key, value, ttl):
        if self.fail:
            raise CacheUnavailable()
        with self.lock:
            self.values[key] = (value, time.monotonic() + ttl)

    def acquire(self, key, token):
        if self.fail:
            raise CacheUnavailable()
        with self.lock:
            row = self.values.get(key)
            if row and row[1] > time.monotonic():
                return False
            self.values[key] = (token, time.monotonic() + 5)
            return True

    def release(self, key, token):
        if self.fail:
            raise CacheUnavailable()
        with self.lock:
            if key in self.values and self.values[key][0] == token:
                del self.values[key]


class CatalogCacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'cache.db'
        self.app = MallApplication(self.path)
        self.backend = MemoryBackend()
        self.app.state.catalog_cache = CatalogCache(self.path, self.backend)
        self.client = Client(self.app)

    def test_hot_product_hit_avoids_product_loader(self):
        original = logic.product
        with patch('app.logic.product', wraps=original) as loader:
            loader.__signature__ = inspect.signature(original)
            first = self.client.get('/api/products/1')
            second = self.client.get('/api/products/1')
        self.assertEqual(first['status'], 200)
        self.assertEqual(second['status'], 200)
        self.assertEqual(first['json'], second['json'])
        self.assertEqual(loader.call_count, 1)

    def test_committed_price_stock_and_store_changes_make_old_keys_unreachable(self):
        self.client.get('/api/products/1')
        self.client.get('/api/products')
        with self.app.state.database.connection() as connection, transaction(connection):
            connection.execute('UPDATE products SET price=12345,stock=7 WHERE id=1')
            connection.execute("UPDATE stores SET name='更新后的店铺' WHERE id=2")
        product = self.client.get('/api/products/1')['json']
        self.assertEqual((product['price'], product['stock'], product['store_name']), (12345, 7, '更新后的店铺'))
        products = self.client.get('/api/products')['json']['items']
        self.assertEqual(next(p for p in products if p['id'] == 1)['price'], 12345)
        with self.app.state.database.connection() as connection:
            connection.execute('UPDATE stores SET is_active=0 WHERE id=2')
        self.assertEqual(self.client.get('/api/products/1')['status'], 404)
        self.assertFalse(any(p['store_id'] == 2 for p in self.client.get('/api/products')['json']['items']))

    def test_redis_outage_falls_back_and_recovery_cannot_serve_old_revision(self):
        self.client.get('/api/products/1')
        self.backend.fail = True
        with self.app.state.database.connection() as connection:
            connection.execute('UPDATE products SET stock=2 WHERE id=1')
        self.assertEqual(self.client.get('/api/products/1')['json']['stock'], 2)
        self.backend.fail = False
        self.app.state.catalog_cache._disabled_until = 0
        self.assertEqual(self.client.get('/api/products/1')['json']['stock'], 2)

    def test_invalid_redis_utf8_reply_falls_back_to_database(self):
        class BrokenClient:
            def get(self, key):
                raise UnicodeDecodeError('utf-8', b'\xff', 0, 1, 'invalid cache data')
        backend = RedisBackend.__new__(RedisBackend)
        backend.errors = type('RedisError', (Exception,), {})
        backend.client = BrokenClient()
        self.app.state.catalog_cache = CatalogCache(self.path, backend)
        self.assertEqual(self.client.get('/api/products/1')['status'], 200)

    def test_transaction_rollback_does_not_invalidate_valid_cache(self):
        first = self.client.get('/api/products/1')['json']
        with self.app.state.database.connection() as connection:
            with self.assertRaises(RuntimeError), transaction(connection):
                connection.execute('UPDATE products SET price=1 WHERE id=1')
                raise RuntimeError('injected rollback')
        with patch('app.logic.product', wraps=logic.product) as loader:
            self.assertEqual(self.client.get('/api/products/1')['json'], first)
        self.assertEqual(loader.call_count, 0)

    def test_local_and_cross_instance_cold_reads_build_once(self):
        second_app = MallApplication(self.path)
        second_app.state.catalog_cache = CatalogCache(self.path, self.backend)
        original = logic.product
        async def slow_loader(**kwargs):
            await asyncio.sleep(0.04)
            return await original(**kwargs)
        gate = threading.Barrier(12)
        def read(index):
            gate.wait(timeout=10)
            return Client(self.app if index % 2 else second_app).get('/api/products/1')
        with patch('app.logic.product', side_effect=slow_loader) as loader, ThreadPoolExecutor(max_workers=12) as pool:
            loader.__signature__ = inspect.signature(original)
            responses = list(pool.map(read, range(12)))
        self.assertTrue(all(r['status'] == 200 for r in responses))
        self.assertEqual(loader.call_count, 1)

    def test_builder_started_before_write_cannot_poison_new_revision(self):
        started, release = threading.Event(), threading.Event()
        old = logic.product
        async def delayed(**kwargs):
            value = await old(**kwargs)
            started.set()
            self.assertTrue(release.wait(timeout=5))
            return value
        with ThreadPoolExecutor(max_workers=1) as pool, patch('app.logic.product', side_effect=delayed) as loader:
            loader.__signature__ = inspect.signature(old)
            future = pool.submit(Client(self.app).get, '/api/products/1')
            self.assertTrue(started.wait(timeout=5))
            with self.app.state.database.connection() as connection:
                connection.execute('UPDATE products SET stock=3 WHERE id=1')
            release.set()
            self.assertEqual(future.result()['status'], 200)
        self.assertEqual(self.client.get('/api/products/1')['json']['stock'], 3)

    def test_corrupt_cache_payload_is_rebuilt_and_private_responses_are_bypassed(self):
        self.client.get('/api/products/1')
        key = next(key for key in self.backend.values if not key.endswith(':lock'))
        self.backend.put(key, '{bad json', 30)
        self.assertEqual(self.client.get('/api/products/1')['status'], 200)
        self.assertEqual(json.loads(self.backend.get(key))['format'], 1)
        count = len(self.backend.values)
        for path in ('/api/auth/me', '/api/cart', '/api/orders', '/api/search/suggestions'):
            self.client.get(path)
        self.assertEqual(len(self.backend.values), count)

    def test_new_database_generation_cannot_reuse_old_database_cache(self):
        with self.app.state.database.connection() as connection:
            connection.execute('UPDATE products SET stock=1 WHERE id=1')
        self.client.get('/api/products/1')
        self.path.unlink()
        for suffix in ('-wal', '-shm'):
            Path(str(self.path) + suffix).unlink(missing_ok=True)
        recreated = MallApplication(self.path)
        recreated.state.catalog_cache = CatalogCache(self.path, self.backend)
        self.assertGreater(Client(recreated).get('/api/products/1')['json']['stock'], 1)


if __name__ == '__main__':
    unittest.main()
