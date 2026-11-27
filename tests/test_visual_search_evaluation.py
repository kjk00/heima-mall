"""Dataset validation and live-catalog metrics for visual-search evaluation."""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
from scripts import evaluate_visual_search


class EvaluationDatasetTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / 'queries').mkdir()
        (self.root / 'queries' / 'mug.jpg').write_bytes(b'query image')

    def write_dataset(self, rows):
        path = self.root / 'labels.jsonl'
        path.write_text('\n'.join(rows), encoding='utf-8')
        return path

    def test_reads_relative_image_paths_and_skips_blank_lines(self):
        path = self.write_dataset([
            '',
            json.dumps({'image': 'queries/mug.jpg', 'relevant_product_ids': [12, 18],
                        'category': '家居生活', 'store_id': 3}, ensure_ascii=False),
        ])

        records = evaluate_visual_search.read_dataset(path)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['image'], (self.root / 'queries' / 'mug.jpg').resolve())
        self.assertEqual(records[0]['relevant'], {12, 18})
        self.assertEqual(records[0]['category'], '家居生活')
        self.assertEqual(records[0]['store_id'], 3)

    def test_rejects_malformed_or_out_of_scope_rows_with_line_number(self):
        invalid_rows = [
            '{',
            json.dumps({'relevant_product_ids': [1]}),
            json.dumps({'image': 'queries/mug.jpg', 'relevant_product_ids': []}),
            json.dumps({'image': 'queries/mug.jpg', 'relevant_product_ids': [True]}),
            json.dumps({'image': 'queries/mug.jpg', 'relevant_product_ids': [1], 'category': 5}),
            json.dumps({'image': 'queries/mug.jpg', 'relevant_product_ids': [1], 'store_id': 0}),
            json.dumps({'image': 'missing.jpg', 'relevant_product_ids': [1]}),
        ]
        for row in invalid_rows:
            with self.subTest(row=row), self.assertRaisesRegex(ValueError, '第 1 行'):
                evaluate_visual_search.read_dataset(self.write_dataset([row]))

    def test_rejects_empty_dataset(self):
        with self.assertRaisesRegex(ValueError, '没有有效查询行'):
            evaluate_visual_search.read_dataset(self.write_dataset(['', '  ']))


class EvaluationMetricTests(unittest.TestCase):
    def test_metrics_at_k_uses_ranked_hits_and_first_relevant_position(self):
        metrics = evaluate_visual_search.metrics_at_k([8, 1, 2], {1, 2, 4}, 2)

        self.assertEqual(metrics, {
            'hit_rate': 1.0,
            'precision': 0.5,
            'recall': 1 / 3,
            'mrr': 0.5,
        })

    def test_miss_and_empty_relevance_have_zero_metrics(self):
        self.assertEqual(evaluate_visual_search.metrics_at_k([8, 9], {1}, 2), {
            'hit_rate': 0.0, 'precision': 0.0, 'recall': 0.0, 'mrr': 0.0,
        })
        self.assertEqual(evaluate_visual_search.metrics_at_k([1], set(), 2), {
            'hit_rate': 0.0, 'precision': 0.0, 'recall': 0.0, 'mrr': 0.0,
        })

    def test_remote_image_storage_reads_without_probing_or_creating_bucket(self):
        events = []

        class Response:
            def read(self): return b'image bytes'
            def close(self): events.append('close')
            def release_conn(self): events.append('release')

        class FakeMinio:
            def __init__(self, *args, **kwargs):
                events.append('client')

            def bucket_exists(self, bucket):
                events.append('bucket_exists')
                raise AssertionError('evaluation must not probe or create buckets')

            def get_object(self, bucket, key):
                events.append(('get_object', bucket, key))
                return Response()

        class S3Error(Exception):
            pass

        minio_module = ModuleType('minio')
        minio_module.Minio = FakeMinio
        minio_error_module = ModuleType('minio.error')
        minio_error_module.S3Error = S3Error
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {'OSS_ENDPOINT': 'storage.example:9000',
                                        'OSS_ACCESS_KEY': 'reader', 'OSS_SECRET_KEY': 'secret',
                                        'OSS_BUCKET': 'catalog'}), \
                patch.dict(sys.modules, {'minio': minio_module,
                                         'minio.error': minio_error_module}):
            storage = evaluate_visual_search.read_only_storage(Path(directory) / 'uploads')

            self.assertEqual(storage.get('item.jpg'), b'image bytes')

        self.assertNotIn('bucket_exists', events)
        self.assertIn(('get_object', 'catalog', 'item.jpg'), events)


class EvaluationLiveFilterTests(unittest.IsolatedAsyncioTestCase):
    async def test_ranked_results_obey_current_inventory_category_and_store_state(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        database_path = Path(directory.name) / 'catalog.sqlite'
        with sqlite3.connect(database_path) as connection:
            connection.executescript('''
                CREATE TABLE stores (id INTEGER PRIMARY KEY, is_active INTEGER NOT NULL);
                CREATE TABLE products (id INTEGER PRIMARY KEY, is_active INTEGER NOT NULL,
                    stock INTEGER NOT NULL, store_id INTEGER NOT NULL, category TEXT NOT NULL);
                INSERT INTO stores VALUES (2, 1), (3, 0);
                INSERT INTO products VALUES
                    (1, 1, 4, 2, '家居生活'),
                    (2, 1, 4, 2, '数码科技'),
                    (3, 1, 4, 3, '家居生活'),
                    (4, 1, 0, 2, '家居生活'),
                    (5, 0, 4, 2, '家居生活');
            ''')

        class TestDatabase:
            backend = 'sqlite'
            path = database_path

            @contextmanager
            def connection(self):
                raw = sqlite3.connect(self.path)
                raw.row_factory = sqlite3.Row
                try:
                    yield raw
                finally:
                    raw.close()

        class FakeVisualSearch:
            def __init__(self):
                self.filters = None

            async def search_image(self, image_bytes, connection, storage, category, store_id):
                self.filters = (image_bytes, category, store_id)
                return [5, 4, 3, 2, 1]

        query_image = Path(directory.name) / 'query.jpg'
        query_image.write_bytes(b'image')
        record = {'image': query_image, 'relevant': {1}, 'category': '家居生活',
                  'store_id': 2, 'line_number': 1}
        visual = FakeVisualSearch()

        result = await evaluate_visual_search.evaluate(
            [record], TestDatabase(), visual, storage=object(), k=5)

        self.assertEqual(visual.filters, (b'image', '家居生活', 2))
        self.assertEqual(result['per_query'][0]['top_k'], [1])
        self.assertEqual(result['hit_rate_at_5'], 1.0)
        self.assertEqual(result['precision_at_5'], 0.2)
        self.assertEqual(result['recall_at_5'], 1.0)
        self.assertEqual(result['mrr_at_5'], 1.0)

        invalid_labels = dict(record, relevant={2}, line_number=9)
        with self.assertRaises(ValueError) as error:
            await evaluate_visual_search.evaluate(
                [invalid_labels], TestDatabase(), visual, storage=object(), k=5)
        self.assertIn('数据集第 9 行', str(error.exception))
        self.assertIn('[2]', str(error.exception))

        class MutatingVisualSearch:
            async def search_image(self, image_bytes, connection, storage, category, store_id):
                await connection.execute('UPDATE products SET stock=0 WHERE id=1')
                return []

        with self.assertRaises(sqlite3.OperationalError):
            await evaluate_visual_search.evaluate(
                [record], TestDatabase(), MutatingVisualSearch(), storage=object(), k=5)
        with sqlite3.connect(database_path) as connection:
            self.assertEqual(connection.execute('SELECT stock FROM products WHERE id=1').fetchone()[0], 4)


if __name__ == '__main__':
    unittest.main()
