"""MongoDB parameter consistency regressions and isolated, opt-in driver tests."""
import asyncio
import copy
import inspect
import json
import os
from pathlib import Path
import secrets
import tempfile
import unittest
from unittest.mock import Mock, patch

from app import mongodb, search_sync


OLD = [{'name': '材质', 'value': '塑料'}]
CURRENT = [{'name': '材质', 'value': '陶瓷'}]


class MemoryParams:
    def __init__(self, documents=None):
        self.documents = copy.deepcopy(documents or {})
        self.reads = []
        self.writes = []
        self.read_error = self.write_error = False

    async def get_documents(self, product_ids):
        self.reads.append(list(product_ids))
        if self.read_error:
            raise RuntimeError('read unavailable')
        return {key: copy.deepcopy(self.documents[key]) for key in product_ids if key in self.documents}

    async def get_many(self, product_ids):
        return {key: doc.get('params', []) for key, doc in (await self.get_documents(product_ids)).items()}

    async def set(self, product_id, params, version=None):
        if self.write_error:
            raise RuntimeError('write unavailable')
        self.writes.append((product_id, copy.deepcopy(params), version))
        self.documents[product_id] = {'_id': product_id, 'params': copy.deepcopy(params), 'version': version}


class MongoDBConsistencyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.path = Path('/tmp/mongodb-consistency.db')
        self.row = {'id': 1, 'version': 3, 'params_json': json.dumps(CURRENT)}
        self.replica = MemoryParams({1: {'_id': 1, 'version': 2, 'params': OLD}})
        self.store = self.enterContext(patch.object(mongodb, 'store', return_value=self.replica))
        self.trip = self.enterContext(patch.object(mongodb, '_trip'))

    async def test_stale_detail_returns_sql_params_and_repairs_replica(self):
        self.assertEqual(await mongodb.read_params(self.row, self.path), CURRENT)
        self.assertEqual(self.replica.documents[1], {'_id': 1, 'version': 3, 'params': CURRENT})

    async def test_recovered_replica_cannot_restore_old_params(self):
        self.replica.read_error = True
        self.assertEqual(await mongodb.read_params(self.row, self.path), CURRENT)
        self.replica.read_error = False
        self.assertEqual(await mongodb.read_params(self.row, self.path), CURRENT)
        self.assertEqual(self.replica.documents[1]['params'], CURRENT)

    async def test_clearing_params_does_not_restore_old_nonempty_values(self):
        self.row['params_json'] = '[]'
        self.assertEqual(await mongodb.read_params(self.row, self.path), [])
        self.assertEqual(self.replica.documents[1]['params'], [])

    async def test_missing_legacy_and_malformed_documents_are_repaired(self):
        for doc in (None, {'_id': 1, 'params': CURRENT},
                    {'_id': 1, 'version': 'invalid', 'params': CURRENT},
                    {'_id': 1, 'version': 3, 'params': 'invalid'},
                    {'_id': 1, 'version': 3, 'params': OLD}):
            with self.subTest(doc=doc):
                self.replica.documents = {} if doc is None else {1: copy.deepcopy(doc)}
                self.assertEqual(await mongodb.read_params(self.row, self.path), CURRENT)
                self.assertEqual(self.replica.documents[1], {'_id': 1, 'version': 3, 'params': CURRENT})

    async def test_matching_document_does_not_write(self):
        self.replica.documents[1] = {'_id': 1, 'version': 3, 'params': CURRENT}
        self.assertEqual(await mongodb.read_params(self.row, self.path), CURRENT)
        self.assertEqual(self.replica.writes, [])

    async def test_old_sql_snapshot_does_not_replace_a_newer_mongo_document(self):
        self.replica.documents[1] = {'_id': 1, 'version': 4, 'params': OLD}
        self.assertEqual(await mongodb.read_params(self.row, self.path), CURRENT)
        self.assertEqual(self.replica.writes, [])

    async def test_read_repair_failure_keeps_sql_values(self):
        self.replica.write_error = True
        self.assertEqual(await mongodb.read_params(self.row, self.path), CURRENT)
        self.trip.assert_called_once()

    async def test_search_batch_never_indexes_stale_or_malformed_replica_params(self):
        rows = [self.row, {'id': 2, 'version': 7, 'params_json': '[]'},
                {'id': 3, 'version': 2, 'params_json': json.dumps(CURRENT)}]
        self.replica.documents[2] = {'_id': 2, 'version': 6, 'params': OLD}
        self.replica.documents[3] = {'_id': 3, 'version': 2, 'params': 'invalid'}
        self.assertEqual(await search_sync.params_for_rows(rows, self.path), {1: CURRENT, 2: [], 3: CURRENT})
        self.assertEqual(self.replica.reads, [[1, 2, 3]])
        self.assertEqual(len(self.replica.writes), 3)

    async def test_disabled_replica_and_missing_database_path_use_sql(self):
        self.store.return_value = None
        self.assertEqual(await mongodb.read_params(self.row, self.path), CURRENT)
        self.store.reset_mock()
        self.assertEqual(await search_sync.params_for_rows([self.row], None), {1: CURRENT})
        self.store.assert_not_called()


class MongoDBLifecycleTests(unittest.TestCase):
    def test_close_actually_releases_the_motor_client(self):
        instance = mongodb.ProductParams.__new__(mongodb.ProductParams)
        instance.client = Mock()
        result = instance.close()
        if inspect.iscoroutine(result):
            result.close()
        instance.client.close.assert_called_once()


class MongoDBProductFlowTests(unittest.TestCase):
    def setUp(self):
        from app.server import MallApplication
        from tests.client import Client
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.app = MallApplication(Path(self.directory.name) / 'params.db')
        self.admin = Client(self.app)
        self.customer = Client(self.app)
        self.assertEqual(self.admin.post('/api/admin/setup', {
            'name': '参数测试', 'phone': '13700000001', 'password': 'integration-test-only',
        })['status'], 201)
        self.replica = MemoryParams()
        self.enterContext(patch.object(mongodb, 'store', return_value=self.replica))
        self.enterContext(patch.object(mongodb, '_trip'))
        self.payload = dict(name='一致性测试商品', subtitle='参数测试', category='家居', price=100,
                            original_price=200, stock=10, image='mug', badge='', color='#eae5ed',
                            description='商品参数一致性测试', params=OLD, is_active=True)

    def create_product(self):
        result = self.admin.post('/api/admin/products', self.payload)
        self.assertEqual(result['status'], 201, result)
        return result['json']

    def test_committed_mirrors_carry_the_corresponding_sql_version(self):
        product = self.create_product()
        result = self.admin.request('PUT', f"/api/admin/products/{product['id']}",
                                    dict(self.payload, params=CURRENT, version=product['version']))
        self.assertEqual(result['status'], 200, result)
        self.assertEqual(self.replica.writes, [
            (product['id'], OLD, product['version']),
            (product['id'], CURRENT, result['json']['version']),
        ])

    def test_committed_update_survives_outage_and_repairs_detail_and_index(self):
        from app.async_contract import SyncDatabaseAdapter
        product = self.create_product()
        self.replica.read_error = self.replica.write_error = True
        result = self.admin.request('PUT', f"/api/admin/products/{product['id']}",
                                    dict(self.payload, params=CURRENT, version=product['version']))
        self.assertEqual(result['status'], 200, result)
        self.assertEqual(result['json']['params'], CURRENT)
        self.assertEqual(self.replica.documents[product['id']]['params'], OLD)
        self.replica.read_error = self.replica.write_error = False
        detail = self.customer.get(f"/api/products/{product['id']}")
        self.assertEqual(detail['json']['params'], CURRENT)
        self.assertEqual(self.replica.documents[product['id']]['version'], result['json']['version'])
        # Reintroduce drift to exercise the actual indexing fetch path separately.
        self.replica.documents[product['id']] = {'params': OLD, 'version': product['version']}
        async def index_documents():
            async with SyncDatabaseAdapter(self.app.state.database).connection() as connection:
                return await search_sync.fetch_documents(connection, [product['id']])
        docs, deleted = asyncio.run(index_documents())
        self.assertEqual(deleted, [])
        self.assertEqual(docs[0]['params_text'], '材质 陶瓷')
        self.assertEqual(self.replica.documents[product['id']]['params'], CURRENT)

    def test_rejected_save_does_not_mirror_its_params(self):
        product = self.create_product()
        self.replica.writes.clear()
        result = self.admin.request('PUT', f"/api/admin/products/{product['id']}",
                                    dict(self.payload, params=CURRENT, version=product['version'] + 1))
        self.assertEqual(result['status'], 409, result)
        self.assertEqual(self.replica.writes, [])
        self.assertEqual(self.replica.documents[product['id']]['params'], OLD)


@unittest.skipUnless(os.getenv('MONGODB_TESTS') == '1',
                     '设置 MONGODB_TESTS=1 后运行真实 MongoDB 一致性测试（独立临时库）')
class MongoDBDriverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.instance = mongodb.ProductParams(os.getenv('MONGODB_TEST_URL', 'mongodb://127.0.0.1:27017'),
                                              'heima_params_test_' + secrets.token_hex(10))
        self.addCleanup(self.instance.client.close)
        await self.instance.client.admin.command('ping')
        self.addAsyncCleanup(self.instance.client.drop_database, self.instance.collection.database.name)

    async def test_late_write_cannot_replace_newer_params(self):
        await self.instance.set(1, CURRENT, 3)
        await self.instance.set(1, OLD, 2)
        doc = await self.instance.collection.find_one({'_id': 1})
        self.assertEqual((doc['version'], doc['params']), (3, CURRENT))

    async def test_concurrent_upserts_converge_to_highest_version(self):
        await asyncio.gather(*(self.instance.set(1, [{'name': '版本', 'value': str(version)}], version)
                               for version in range(20, 0, -1)))
        doc = await self.instance.collection.find_one({'_id': 1})
        self.assertEqual((doc['version'], doc['params']), (20, [{'name': '版本', 'value': '20'}]))

    async def test_legacy_document_and_equal_version_drift_can_be_repaired(self):
        for product_id, version in enumerate((None, 'invalid', True), start=1):
            with self.subTest(version=version):
                await self.instance.collection.insert_one({'_id': product_id, 'version': version, 'params': OLD})
                await self.instance.set(product_id, CURRENT, 3)
                await self.instance.collection.update_one({'_id': product_id}, {'$set': {'params': OLD}})
                await self.instance.set(product_id, CURRENT, 3)
                self.assertEqual((await self.instance.get_many([product_id]))[product_id], CURRENT)

    async def test_dollar_prefixed_values_are_data_not_aggregation_expressions(self):
        params = [{'name': '$version', 'value': '$$ROOT'}]
        await self.instance.set(1, params, 1)
        self.assertEqual((await self.instance.get_many([1]))[1], params)

    async def test_old_read_repair_preserves_newer_replica_version(self):
        await self.instance.set(1, CURRENT, 3)
        row = {'id': 1, 'version': 2, 'params_json': json.dumps(OLD)}
        with patch.object(mongodb, 'store', return_value=self.instance):
            self.assertEqual(await mongodb.read_params(row, Path('/tmp/snapshot.db')), OLD)
        self.assertEqual((await self.instance.get_many([1]))[1], CURRENT)

    async def test_concurrent_write_between_read_and_repair_is_not_lost(self):
        await self.instance.set(1, OLD, 1)
        get_documents = self.instance.get_documents
        async def read_then_write_newer(ids):
            docs = await get_documents(ids)
            await self.instance.set(1, OLD, 4)
            return docs
        row = {'id': 1, 'version': 3, 'params_json': json.dumps(CURRENT)}
        with patch.object(mongodb, 'store', return_value=self.instance), \
                patch.object(self.instance, 'get_documents', side_effect=read_then_write_newer):
            self.assertEqual(await mongodb.read_params(row, Path('/tmp/race.db')), CURRENT)
        doc = await self.instance.collection.find_one({'_id': 1})
        self.assertEqual((doc['version'], doc['params']), (4, OLD))
