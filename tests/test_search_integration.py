"""真实 Elasticsearch + RabbitMQ 集成测试（独立临时SQLite，结束清理索引与队列）。

运行：SEARCH_INTEGRATION_TESTS=1 python -m unittest tests.test_search_integration -v
默认跳过；需要本地 ES(9200) 与 RabbitMQ(5672)，可用 ELASTICSEARCH_URL/RABBITMQ_URL 覆盖。
"""
import asyncio
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

ENABLED = os.getenv('SEARCH_INTEGRATION_TESTS') == '1'
ES_URL = os.getenv('ELASTICSEARCH_URL', 'http://127.0.0.1:9200')
MQ_URL = os.getenv('RABBITMQ_URL', 'amqp://guest:guest@127.0.0.1:5672/')
ENV = {'ELASTICSEARCH_URL': ES_URL, 'RABBITMQ_URL': MQ_URL, 'REDIS_URL': '',
       'SEARCH_SYNC_SECONDS': '0.3', 'SEARCH_RECONCILE_SECONDS': '3600'}


async def wait_for(check, timeout=30, interval=0.3):
    deadline = time.monotonic() + timeout
    result = None
    while time.monotonic() < deadline:
        result = await check()
        if result:
            return result
        await asyncio.sleep(interval)
    raise AssertionError(f'等待超时，最后结果：{result!r}')


@unittest.skipUnless(ENABLED, '未设置SEARCH_INTEGRATION_TESTS=1；需要本地Elasticsearch与RabbitMQ')
class SearchPipelineIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'search.db'
        self.environment = patch.dict(os.environ, ENV)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        from app.async_database import AsyncDatabase
        from app.search import SearchEngine, index_alias, sync_queue
        self.database = AsyncDatabase(self.path)
        self.alias, self.queue_name = index_alias(self.path), sync_queue(self.path)
        self.engine = SearchEngine.from_environment(self.path)
        self.arguments = dict(q='', category='', sort='recommended', page=1, page_size=12, on_sale=False, store_id=None)
        self.task = None

    def tearDown(self):
        async def cleanup():
            if self.task is not None:
                self.task.cancel()
                try:
                    await self.task
                except asyncio.CancelledError:
                    pass
            import aio_pika
            from elasticsearch import AsyncElasticsearch
            es = AsyncElasticsearch(ES_URL, request_timeout=10)
            try:
                if await es.indices.exists_alias(name=self.alias):
                    for index in (await es.indices.get_alias(name=self.alias)).keys():
                        await es.indices.delete(index=index)
                # An interrupted full_reindex can leave a target index with no alias;
                # sweep the whole per-database namespace so tests leave no residue.
                await es.indices.delete(index=self.alias + '-*', ignore_unavailable=True)
            finally:
                await es.close()
            broker = await aio_pika.connect_robust(MQ_URL)
            try:
                channel = await broker.channel()
                try:
                    queue = await channel.declare_queue(self.queue_name, durable=True, passive=True)
                    await queue.delete(if_empty=False, if_unused=False)
                except aio_pika.exceptions.ChannelNotFoundEntity:
                    pass
            finally:
                await broker.close()
            await self.engine.close()
            await self.database.close()
        asyncio.run(cleanup())

    async def start_worker(self):
        from app.search_sync import run_search_sync
        await self.database.initialize()
        self.task = asyncio.create_task(run_search_sync(self.database))

    async def search(self, **overrides):
        async with self.database.connection() as connection:
            return await self.engine.read(connection, self.arguments | overrides)

    async def insert(self, product_id, name):
        async with self.database.connection() as connection:
            await connection.execute(
                'INSERT INTO products(id,name,subtitle,category,price,original_price,stock,image,badge,color,description,sales,is_active,version,store_id) '
                'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (product_id, name, '集成测试副标题', '数码科技', 19900, 59900, 10,
                 '/static/images/headphones.svg', '', '#224466', '集成测试商品', 0, 1, 1, 2))

    def test_incremental_insert_update_delete_reaches_es(self):
        async def scenario():
            await self.start_worker()
            await self.insert(9001, '集成验证耳机 ABC')
            await wait_for(lambda: self.has_hit('集成验证', 9001))
            async with self.database.connection() as connection:
                await connection.execute("UPDATE products SET name='集成验证音箱 XYZ',version=version+1 WHERE id=9001")
            await wait_for(lambda: self.has_hit('音箱 XYZ', 9001))
            result = await self.search(q='耳机 ABC')
            self.assertNotIn(9001, result['ids'])
            async with self.database.connection() as connection:
                await connection.execute('DELETE FROM products WHERE id=9001')
            await wait_for(lambda: self.gone('集成验证', 9001))
            async with self.database.connection() as connection:
                row = (await connection.execute('SELECT COUNT(*) FROM product_sync_outbox')).fetchone()
            self.assertEqual(row[0], 0)
        asyncio.run(scenario())

    async def has_hit(self, keyword, product_id):
        result = await self.search(q=keyword)
        return result is not None and product_id in result['ids']

    async def gone(self, keyword, product_id):
        result = await self.search(q=keyword)
        return result is not None and product_id not in result['ids']

    def test_reconcile_repairs_missing_documents(self):
        async def scenario():
            await self.start_worker()
            await self.insert(9001, '对账验证商品')
            await wait_for(lambda: self.has_hit('对账验证', 9001))
            from elasticsearch import AsyncElasticsearch
            from app.search_sync import reconcile_once
            es = AsyncElasticsearch(ES_URL, request_timeout=10)
            try:
                await es.delete(index=self.alias, id=9001, refresh=True)
                repaired = await reconcile_once(self.database, es, self.alias)
                self.assertTrue(repaired)
            finally:
                await es.close()
            await wait_for(lambda: self.has_hit('对账验证', 9001))
        asyncio.run(scenario())

    def test_full_reindex_swaps_alias_and_drops_old_index(self):
        async def scenario():
            await self.database.initialize()
            from elasticsearch import AsyncElasticsearch
            from app.search_sync import full_reindex
            es = AsyncElasticsearch(ES_URL, request_timeout=30)
            try:
                first = await full_reindex(self.database, es, self.alias)
                second = await full_reindex(self.database, es, self.alias)
                self.assertNotEqual(first, second)
                self.assertFalse(await es.indices.exists(index=first))
                mapping = await es.indices.get_alias(name=self.alias)
                self.assertEqual(list(mapping.keys()), [second])
                async with self.database.connection() as connection:
                    total = (await connection.execute('SELECT COUNT(*) FROM products')).fetchone()[0]
                count = await es.count(index=self.alias, query={'match_all': {}})
                self.assertEqual(count['count'], total)
            finally:
                await es.close()
        asyncio.run(scenario())

    def test_stdlib_http_search_uses_sync_es_client(self):
        from app.server import MallApplication
        from app.search_sync import legacy_search_sync_worker
        from tests.client import Client
        application = MallApplication(self.path)
        with legacy_search_sync_worker(application.state.database):
            client = Client(application)
            with application.state.database.connection() as connection:
                connection.execute(
                    'INSERT INTO products(id,name,subtitle,category,price,original_price,stock,image,badge,color,description,sales,is_active,version,store_id) '
                    'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (9001, '标准库验证耳机 ABC', '集成测试副标题', '数码科技', 19900, 59900, 10,
                     '/static/images/headphones.svg', '', '#224466', '集成测试商品', 0, 1, 1, 2))
            deadline = time.monotonic() + 30
            response = None
            while time.monotonic() < deadline:
                response = client.get('/api/products?q=标准库验证')
                if response['status'] == 200 and response['json']['total'] == 1:
                    break
                time.sleep(0.3)
            self.assertEqual(response['status'], 200)
            self.assertEqual(response['json']['total'], 1)
            self.assertEqual(response['json']['items'][0]['id'], 9001)
            self.assertIsNotNone(application.state.search_engine._sync_client)
            self.assertIsNone(application.state.search_engine._async_client)
            from elasticsearch import Elasticsearch
            es = Elasticsearch(ES_URL, request_timeout=10)
            try:
                deadline = time.monotonic() + 30
                document = None
                while time.monotonic() < deadline:
                    try:
                        document = es.get(index=application.state.search_engine.alias, id=9001)
                        break
                    except Exception:
                        time.sleep(0.3)
                self.assertIsNotNone(document, '同步线程未把商品写入ES')
                self.assertEqual(document['_source']['name'], '标准库验证耳机 ABC')
            finally:
                es.close()

    def test_fastapi_http_search_uses_async_es_client(self):
        import httpx
        from app.main import create_app
        application = create_app(self.path)
        async def scenario():
            async with application.router.lifespan_context(application):
                await self.insert(9001, '异步验证耳机 ABC')
                transport = httpx.ASGITransport(app=application)
                async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
                    async def check():
                        response = await client.get('/api/products', params={'q': '异步验证'},
                                                    headers={'X-Requested-With': 'HeimaMall'})
                        return response if response.status_code == 200 and response.json()['total'] == 1 else None
                    response = await wait_for(check)
                self.assertEqual(response.json()['items'][0]['id'], 9001)
                self.assertIsNotNone(application.state.search_engine._async_client)
        asyncio.run(scenario())


if __name__ == '__main__':
    unittest.main()