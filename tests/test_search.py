"""ES搜索路由与outbox同步链路单元测试：全部使用可控stub，不依赖真实ES/RabbitMQ。"""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from app import logic, search, search_sync
from app.async_contract import SyncConnectionAdapter, SyncDatabaseAdapter
from app.database import Database

SYNC_ENV = {'ELASTICSEARCH_URL': 'http://127.0.0.1:9200', 'RABBITMQ_URL': 'amqp://guest:guest@127.0.0.1:5672/'}
PRODUCT_COLUMNS = ('name,subtitle,category,price,original_price,stock,image,badge,color,description,sales,is_active,version,store_id')


def run(coroutine):
    return asyncio.run(coroutine)


def LegacyDatabase(path):
    return SyncDatabaseAdapter(Database(path))


def insert_product(connection, product_id, name, store_id=1):
    connection.execute(f'INSERT INTO products(id,{PRODUCT_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (product_id, name, '副标题', '数码科技', 19900, 59900, 10,
                        '/static/images/headphones.svg', '', '#224466', '描述', 0, 1, 1, store_id))


class SearchArgumentsTests(unittest.TestCase):
    def arguments(self, **overrides):
        base = dict(q='', category='', sort='recommended', page=1, page_size=12, on_sale=False, store_id=None)
        return base | overrides

    def test_filters_sort_and_pagination_mirror_sql(self):
        body = search.search_arguments(self.arguments(category='数码科技', sort='price_desc', on_sale=True,
                                                      store_id=2, page=3, page_size=20), [1, 2, 3], [])
        filters = body['query']['bool']['filter']
        self.assertIn({'term': {'is_active': True}}, filters)
        self.assertIn({'terms': {'store_id': [1, 2, 3]}}, filters)
        self.assertIn({'term': {'store_id': 2}}, filters)
        self.assertIn({'term': {'on_sale': True}}, filters)
        self.assertIn({'term': {'category': '数码科技'}}, filters)
        self.assertEqual(body['sort'], [{'price': 'desc'}, {'id': 'asc'}])
        self.assertEqual(body['from_'], 40)
        self.assertEqual(body['size'], 20)
        self.assertTrue(body['track_total_hits'])
        self.assertEqual(body['source'], ['id'])
        self.assertNotIn('must', body['query']['bool'])

    def test_keyword_wildcards_escape_special_characters(self):
        body = search.search_arguments(self.arguments(q='a*b?c\\d'), [1], [])
        should = body['query']['bool']['must'][0]['bool']['should']
        self.assertEqual(should[0]['wildcard']['name']['value'], '*a\\*b\\?c\\\\d*')
        self.assertTrue(should[0]['wildcard']['name']['case_insensitive'])
        self.assertEqual(should[1]['wildcard']['subtitle']['value'], '*a\\*b\\?c\\\\d*')
        self.assertEqual(body['query']['bool']['must'][0]['bool']['minimum_should_match'], 1)
        self.assertEqual(len(should), 5)
        for index, field in ((2, 'description'), (3, 'detail_text'), (4, 'params_text')):
            self.assertEqual(should[index]['wildcard'][field]['value'], '*a\\*b\\?c\\\\d*')

    def test_store_name_matches_join_as_should_clause(self):
        body = search.search_arguments(self.arguments(q='黑马'), [1, 2], [2])
        should = body['query']['bool']['must'][0]['bool']['should']
        self.assertIn({'terms': {'store_id': [2]}}, should)

    def test_document_computes_sale_and_boolean_flags(self):
        row = {'id': 1, 'name': 'n', 'subtitle': 's', 'category': 'c', 'store_id': 2, 'price': 100,
               'original_price': 200, 'sales': 3, 'version': 4, 'is_active': 1}
        doc = search.document(row)
        self.assertTrue(doc['on_sale'])
        self.assertIs(doc['is_active'], True)
        self.assertEqual(doc['version'], 4)
        row['original_price'] = 100
        self.assertFalse(search.document(row)['on_sale'])

    def test_photo_search_uses_active_catalog_filters_and_bounded_index_hits(self):
        body = search.photo_search_arguments(['耳机', '音箱'], [1, 2, 3])
        filters = body['query']['bool']['filter']
        self.assertIn({'term': {'is_active': True}}, filters)
        self.assertIn({'terms': {'store_id': [1, 2, 3]}}, filters)
        clauses = body['query']['bool']['must'][0]['bool']['should']
        self.assertEqual(len(clauses), 6)
        self.assertEqual(body['size'], 13)
        self.assertFalse(body['track_total_hits'])
        self.assertEqual(body['sort'], [{'sales': 'desc'}, {'id': 'asc'}])


class OutboxTriggerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'outbox.db'

    def test_enabled_writes_enqueue_insert_update_delete(self):
        with patch.dict(os.environ, SYNC_ENV):
            database = Database(self.path)
            database.initialize()
            with database.connection() as connection:
                connection.execute('DELETE FROM product_sync_outbox')
                insert_product(connection, 9001, '同步商品')
                connection.execute("UPDATE products SET name='改名商品' WHERE id=9001")
                connection.execute('DELETE FROM products WHERE id=9001')
                rows = connection.execute('SELECT seq,product_id FROM product_sync_outbox ORDER BY seq').fetchall()
            self.assertEqual([row['product_id'] for row in rows], [9001, 9001, 9001])
            self.assertEqual([row['seq'] for row in rows], sorted(row['seq'] for row in rows))

    def test_disabled_drops_triggers_and_clears_outbox(self):
        with patch.dict(os.environ, SYNC_ENV):
            Database(self.path).initialize()
            with Database(self.path).connection() as connection:
                insert_product(connection, 9001, '同步商品')
        with patch.dict(os.environ, {'ELASTICSEARCH_URL': '', 'RABBITMQ_URL': ''}):
            Database(self.path).initialize()
            with Database(self.path).connection() as connection:
                triggers = connection.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'product_sync_%'").fetchall()
                self.assertEqual(triggers, [])
                self.assertEqual(connection.execute('SELECT COUNT(*) FROM product_sync_outbox').fetchone()[0], 0)
                insert_product(connection, 9002, '普通商品')
                self.assertEqual(connection.execute('SELECT COUNT(*) FROM product_sync_outbox').fetchone()[0], 0)


class FakeExchange:
    def __init__(self, fail=False):
        self.published, self.fail = [], fail

    async def publish(self, message, routing_key):
        if self.fail:
            raise RuntimeError('broker不可用')
        self.published.append((json.loads(message.body), routing_key, message.delivery_mode))


class FakeChannel:
    def __init__(self, fail=False):
        self.default_exchange = FakeExchange(fail)


class FakeES:
    def __init__(self, errors=False):
        self.operations, self.errors = [], errors

    async def bulk(self, operations):
        self.operations.append(operations)
        if self.errors:
            return {'errors': True, 'items': [{'index': {'error': {'type': 'mapper_parsing_exception'}}}]}
        return {'errors': False, 'items': []}


class FakeDeliveryMode:
    PERSISTENT = 2


class FakeAioPika(types.ModuleType):
    DeliveryMode = FakeDeliveryMode

    class Message:
        def __init__(self, body, delivery_mode=None):
            self.body, self.delivery_mode = body, delivery_mode


class RelayAndConsumerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'relay.db'
        self.environment = patch.dict(os.environ, SYNC_ENV)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.modules = patch.dict(sys.modules, {'aio_pika': FakeAioPika('aio_pika')})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.database = LegacyDatabase(self.path)
        self.database.database.initialize()
        with self.database.database.connection() as connection:
            connection.execute('DELETE FROM product_sync_outbox')

    def test_relay_drains_coalesces_and_deletes_only_after_publish(self):
        with self.database.database.connection() as connection:
            insert_product(connection, 9001, '甲')
            insert_product(connection, 9002, '乙')
            connection.execute("UPDATE products SET name='甲改' WHERE id=9001")
        channel = FakeChannel()
        drained = run(search_sync.relay_once(self.database, channel, 'queue-test'))
        self.assertEqual(drained, 3)
        self.assertEqual(len(channel.default_exchange.published), 1)
        payload, routing_key, delivery_mode = channel.default_exchange.published[0]
        self.assertEqual(payload['product_ids'], [9001, 9002])
        self.assertEqual(routing_key, 'queue-test')
        self.assertEqual(delivery_mode, FakeDeliveryMode.PERSISTENT)
        with self.database.database.connection() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM product_sync_outbox').fetchone()[0], 0)
        self.assertEqual(run(search_sync.relay_once(self.database, channel, 'queue-test')), 0)
        self.assertEqual(len(channel.default_exchange.published), 1)

    def test_relay_keeps_outbox_when_publish_fails(self):
        with self.database.database.connection() as connection:
            insert_product(connection, 9001, '甲')
        with self.assertRaises(RuntimeError):
            run(search_sync.relay_once(self.database, FakeChannel(fail=True), 'queue-test'))
        with self.database.database.connection() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM product_sync_outbox').fetchone()[0], 1)

    def test_fetch_documents_upserts_current_state_and_deletes_vanished(self):
        with self.database.database.connection() as connection:
            insert_product(connection, 9001, '甲')
            connection.execute("UPDATE products SET name='最新名字',version=7 WHERE id=9001")
        async def scenario():
            async with self.database.connection() as connection:
                return await search_sync.fetch_documents(connection, [9001, 9999])
        documents, deletes = run(scenario())
        self.assertEqual(documents[0]['name'], '最新名字')
        self.assertEqual(documents[0]['version'], 7)
        self.assertEqual(deletes, [9999])

    def test_bulk_apply_builds_index_and_delete_actions(self):
        es = FakeES()
        run(search_sync.bulk_apply(es, 'alias-test', [{'id': 1, 'name': 'n'}], [2]))
        self.assertEqual(es.operations[0], [
            {'index': {'_index': 'alias-test', '_id': 1}}, {'id': 1, 'name': 'n'},
            {'delete': {'_index': 'alias-test', '_id': 2}}])
        with self.assertRaises(RuntimeError):
            run(search_sync.bulk_apply(FakeES(errors=True), 'alias-test', [{'id': 1}], []))

    def test_consume_once_writes_current_rows(self):
        with self.database.database.connection() as connection:
            insert_product(connection, 9001, '甲')
        es = FakeES()
        message = type('Message', (), {'body': json.dumps({'product_ids': [9001, 9999]}).encode()})()
        run(search_sync.consume_once(message, self.database, es, 'alias-test'))
        actions = es.operations[0]
        self.assertEqual(actions[0], {'index': {'_index': 'alias-test', '_id': 9001}})
        self.assertEqual(actions[1]['name'], '甲')
        self.assertEqual(actions[2], {'delete': {'_index': 'alias-test', '_id': 9999}})

    def test_consumer_loop_acks_success_and_nacks_failure(self):
        class FakeMessage:
            def __init__(self, body):
                self.body, self.acked, self.nacked = body, False, False

            async def ack(self):
                self.acked = True

            async def nack(self, requeue):
                self.nacked = requeue

        class FakeIterator:
            def __init__(self, messages):
                self.messages = iter(messages)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            def __aiter__(self):
                return self

            async def __anext__(self):
                try:
                    return next(self.messages)
                except StopIteration:
                    raise StopAsyncIteration

        class FakeQueue:
            def __init__(self, messages):
                self.messages = messages

            def iterator(self):
                return FakeIterator(self.messages)

        good = FakeMessage(json.dumps({'product_ids': []}).encode())
        bad = FakeMessage(json.dumps({'product_ids': []}).encode())
        es = FakeES()
        run(search_sync.consumer_loop(FakeQueue([good]), self.database, es, 'alias-test'))
        self.assertTrue(good.acked)
        with patch.object(search_sync, 'consume_once', side_effect=RuntimeError('ES故障')):
            run(search_sync.consumer_loop(FakeQueue([bad]), self.database, es, 'alias-test'))
        self.assertTrue(bad.nacked)
        self.assertFalse(bad.acked)


class SearchEngineReadTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'engine.db'
        self.database = LegacyDatabase(self.path)
        self.database.database.initialize()
        self.engine = search.SearchEngine('http://unused', 'alias-test')
        self.arguments = dict(q='', category='', sort='recommended', page=1, page_size=12, on_sale=False, store_id=None)

    def stub_client(self, response=None, error=None):
        calls = []

        class Client:
            async def search(self, **kwargs):
                calls.append(kwargs)
                if error is not None:
                    raise error
                return response

        client = Client()
        self.engine._client = lambda connection: (client, False)
        return calls

    def test_read_returns_total_and_ids_in_order(self):
        calls = self.stub_client(response={'hits': {'total': {'value': 7}, 'hits': [
            {'_source': {'id': 5}}, {'_source': {'id': 3}}]}})
        async def scenario():
            async with self.database.connection() as connection:
                return await self.engine.read(connection, self.arguments)
        result = run(scenario())
        self.assertEqual(result, {'ids': [5, 3]})
        self.assertEqual(calls[0]['index'], 'alias-test')
        self.assertTrue(calls[0]['track_total_hits'])

    def test_failure_trips_breaker_and_falls_back(self):
        calls = self.stub_client(error=RuntimeError('连接失败'))
        async def scenario():
            async with self.database.connection() as connection:
                first = await self.engine.read(connection, self.arguments)
                second = await self.engine.read(connection, self.arguments)
                return first, second
        first, second = run(scenario())
        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertEqual(len(calls), 1)

    def test_deep_page_beyond_window_uses_sql_without_calling_es(self):
        calls = self.stub_client(response={'hits': {'total': {'value': 0}, 'hits': []}})
        async def scenario():
            async with self.database.connection() as connection:
                return await self.engine.read(connection, self.arguments | {'page': 201, 'page_size': 50})
        self.assertIsNone(run(scenario()))
        self.assertEqual(calls, [])

    def test_keyword_resolves_store_names_from_live_database(self):
        calls = self.stub_client(response={'hits': {'total': {'value': 0}, 'hits': []}})
        async def scenario():
            async with self.database.connection() as connection:
                return await self.engine.read(connection, self.arguments | {'q': '黑马'})
        run(scenario())
        should = calls[0]['query']['bool']['must'][0]['bool']['should']
        store_clause = [clause for clause in should if 'terms' in clause]
        self.assertEqual(len(store_clause), 1)
        self.assertEqual(sorted(store_clause[0]['terms']['store_id']), [1, 2])

    def test_read_photo_returns_bounded_ids_without_total_count_query(self):
        hits = [{'_source': {'id': item_id}} for item_id in range(13)]
        calls = self.stub_client(response={'hits': {'hits': hits}})
        async def scenario():
            async with self.database.connection() as connection:
                return await self.engine.read_photo(connection, ['耳机', '数码科技'])
        self.assertEqual(run(scenario()), {'ids': list(range(13))})
        self.assertEqual(calls[0]['size'], 13)
        self.assertFalse(calls[0]['track_total_hits'])
        fields = [next(iter(clause['wildcard'])) for clause in
                  calls[0]['query']['bool']['must'][0]['bool']['should']]
        self.assertEqual(fields, ['name', 'subtitle', 'category'] * 2)


class ProductsRoutingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'routing.db'
        self.database = LegacyDatabase(self.path)
        self.database.database.initialize()

    def test_es_order_and_total_are_used_with_fresh_rows(self):
        class FakeSearch:
            async def read(self, connection, arguments):
                return {'total': 2, 'ids': [2, 1]}
        async def scenario():
            async with self.database.connection() as connection:
                return await logic.products(connection, search=FakeSearch())
        result = run(scenario())
        self.assertEqual([item['id'] for item in result['items']], [2, 1])
        self.assertEqual(result['total'], 2)
        self.assertIn('store_name', result['items'][0])

    def test_inactive_rows_are_filtered_from_stale_es_hits(self):
        with self.database.database.connection() as connection:
            connection.execute('UPDATE products SET is_active=0 WHERE id=2')
        class FakeSearch:
            async def read(self, connection, arguments):
                return {'total': 2, 'ids': [2, 1]}
        async def scenario():
            async with self.database.connection() as connection:
                return await logic.products(connection, search=FakeSearch())
        result = run(scenario())
        self.assertEqual([item['id'] for item in result['items']], [1])
        self.assertEqual(result['total'], 2)

    def test_none_result_falls_back_to_sql(self):
        class FakeSearch:
            async def read(self, connection, arguments):
                return None
        async def scenario():
            async with self.database.connection() as connection:
                routed = await logic.products(connection, q='耳机', search=FakeSearch())
                direct = await logic.products(connection, q='耳机')
                return routed, direct
        routed, direct = run(scenario())
        self.assertEqual(routed, direct)
        self.assertGreater(routed['total'], 0)


class ReconcileTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'reconcile.db'
        self.database = LegacyDatabase(self.path)
        self.database.database.initialize()

    class FakeES:
        def __init__(self, alias_exists, count, checksum):
            self.alias_exists, self.count_value, self.checksum = alias_exists, count, checksum
            self.indices = self

        async def exists_alias(self, name):
            return self.alias_exists

        async def count(self, index, query):
            return {'count': self.count_value}

        async def search(self, **kwargs):
            return {'aggregations': {'checksum': {'value': float(self.checksum)}}}

    def test_matching_count_and_checksum_skip_reindex(self):
        with self.database.database.connection() as connection:
            total, checksum = connection.execute('SELECT COUNT(*),COALESCE(SUM(version),0) FROM products').fetchone()
        es = self.FakeES(True, total, checksum)
        with patch.object(search_sync, 'full_reindex') as reindex:
            self.assertFalse(run(search_sync.reconcile_once(self.database, es, 'alias-test')))
            reindex.assert_not_called()

    def test_checksum_drift_triggers_reindex(self):
        with self.database.database.connection() as connection:
            total, checksum = connection.execute('SELECT COUNT(*),COALESCE(SUM(version),0) FROM products').fetchone()
        es = self.FakeES(True, total, checksum + 1)
        with patch.object(search_sync, 'full_reindex') as reindex:
            self.assertTrue(run(search_sync.reconcile_once(self.database, es, 'alias-test')))
            reindex.assert_called_once()

    def test_missing_alias_triggers_reindex(self):
        es = self.FakeES(False, 0, 0)
        with patch.object(search_sync, 'full_reindex') as reindex:
            self.assertTrue(run(search_sync.reconcile_once(self.database, es, 'alias-test')))
            reindex.assert_called_once()


if __name__ == '__main__':
    unittest.main()
