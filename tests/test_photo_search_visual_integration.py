"""Exercise real local image features through both HTTP application adapters.

Every test owns a temporary SQLite catalog, generated raster images, and an
on-disk feature index. No provider, model, external service, or TCP port is used.
"""
import base64
import hashlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from app.database import Database
from app.server import MallApplication
from tests.client import Client


PHOTO_PATH = '/api/search/photo'
CUP_CATEGORY = '视觉测试杯具'
BAG_CATEGORY = '视觉测试箱包'
RED = (230, 25, 25)
BLUE = (25, 45, 225)
GREEN = (25, 200, 45)
YELLOW = (235, 210, 20)
FASTAPI_AVAILABLE = all(importlib.util.find_spec(name) for name in (
    'fastapi', 'httpx', 'sqlalchemy', 'aiomysql', 'aiosqlite', 'greenlet',
))


def cup_photo(color, image_format='PNG', offset=0, private_metadata=False):
    """Generate a known color/outline fixture, without claiming semantic accuracy."""
    image = Image.new('RGB', (96, 96), (245, 245, 245))
    drawing = ImageDraw.Draw(image)
    drawing.ellipse((55 + offset, 28, 81 + offset, 59), outline=color, width=7)
    drawing.rounded_rectangle((20 + offset, 23, 63 + offset, 76), radius=7, fill=color)
    output = io.BytesIO()
    options = {'format': image_format}
    if image_format == 'JPEG':
        options['quality'] = 95
    if private_metadata:
        metadata = Image.Exif()
        metadata[270] = 'private-query-metadata-canary'
        options['exif'] = metadata
    image.save(output, **options)
    return output.getvalue()


class VisualPhotoContract:
    """Share observable acceptance criteria between WSGI and FastAPI."""

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.database_path = self.root / 'catalog.db'
        self.enterContext(patch.dict('os.environ', {
            'DATABASE_BACKEND': 'sqlite',
            'DATABASE_PATH': str(self.database_path),
            'VISUAL_SEARCH_INDEX_PATH': str(self.root / 'visual-index'),
            'VISUAL_SEARCH_CANDIDATES': '200',
            'PHOTO_SEARCH_API_KEY': '',
            'PHOTO_SEARCH_TRUSTED_PROXY': '',
            'REDIS_URL': '',
            'ELASTICSEARCH_URL': '',
            'RABBITMQ_URL': '',
            'MONGODB_URL': '',
            'OSS_ENDPOINT': '',
        }))
        self.open_application()
        self.database = Database(self.database_path)
        self.service = self.application.state.photo_search
        self.visual = self.service.visual_search
        self.images = {number: self.store_image(cup_photo(color))
                       for number, color in ((1, RED), (2, BLUE), (3, GREEN))}
        with self.database.connection() as connection:
            connection.execute('UPDATE products SET is_active=0')
            for number, category, store_id in (
                    (1, CUP_CATEGORY, 2), (2, CUP_CATEGORY, 2), (3, BAG_CATEGORY, 3)):
                connection.execute(
                    'UPDATE products SET name=?,image=?,category=?,store_id=?,'
                    'stock=10,is_active=1,version=version+1 WHERE id=?',
                    (f'视觉测试商品{number}', self.images[number], category, store_id, number))
            rows = connection.execute(
                'SELECT id,image FROM products WHERE is_active=1 ORDER BY id').fetchall()
            revision = connection.execute(
                'SELECT revision FROM catalog_cache_state WHERE id=1').fetchone()['revision']
        self.assertEqual(self.visual.rebuild_index(
            rows, revision, self.application.state.storage), 3)

    def store_image(self, content):
        key = hashlib.sha256(content).hexdigest() + '.png'
        self.application.state.storage.put(key, content, 'image/png')
        return '/media/' + key

    def query(self, content=None, **filters):
        content = cup_photo(RED) if content is None else content
        payload = {'filename': 'query-photo.png',
                   'data_base64': base64.b64encode(content).decode('ascii'), **filters}
        return self.request('POST', PHOTO_PATH, payload)

    def search(self, content=None, **filters):
        status, body = self.query(content, **filters)
        self.assertEqual(status, 200, body)
        self.assertEqual(body['mode'], 'visual')
        self.assertEqual(body['keywords'], [])
        self.assertEqual(body['total'], len(body['items']))
        self.assertLessEqual(body['total'], 12)
        return body

    def change_catalog(self, sql, parameters=()):
        with self.database.connection() as connection:
            connection.execute(sql, parameters)

    def add_product(self, image):
        with self.database.connection() as connection:
            return connection.execute(
                'INSERT INTO products(name,subtitle,category,price,original_price,stock,'
                'image,badge,color,description,sales,store_id) '
                'SELECT ?,subtitle,category,price,original_price,10,?,badge,color,'
                'description,sales,store_id FROM products WHERE id=1',
                ('新增视觉测试商品', image)).lastrowid

    @staticmethod
    def ids(body):
        return [item['id'] for item in body['items']]

    def test_v01_built_index_reports_visual_mode_without_api_key(self):
        self.assertEqual(self.service.api_key, '')
        self.assertEqual(self.request('GET', PHOTO_PATH + '/status'),
                         (200, {'enabled': True, 'mode': 'visual'}))

    def test_v02_identical_photo_ranks_matching_product_first(self):
        body = self.search()
        self.assertEqual(self.ids(body)[0], 1)
        self.assertEqual(set(self.ids(body)), {1, 2, 3})
        self.assertFalse(body['has_more'])
        self.assertEqual(body['items'][0]['image'], self.images[1])

    def test_v03_png_jpeg_and_webp_queries_match_the_same_product(self):
        for image_format in ('PNG', 'JPEG', 'WEBP'):
            with self.subTest(image_format=image_format):
                self.assertEqual(self.ids(self.search(cup_photo(RED, image_format)))[0], 1)

    def test_v04_category_and_store_filters_are_combined(self):
        self.assertEqual(self.ids(self.search(category=CUP_CATEGORY, store_id=2)), [1, 2])
        self.assertEqual(self.ids(self.search(category=CUP_CATEGORY, store_id=3)), [])
        self.assertEqual(self.ids(self.search(store_id=3)), [3])

    def test_v05_sold_out_product_is_excluded_after_index_build(self):
        self.change_catalog('UPDATE products SET stock=0,version=version+1 WHERE id=1')
        self.assertEqual(set(self.ids(self.search())), {2, 3})

    def test_v06_hidden_product_is_excluded_after_index_build(self):
        self.change_catalog('UPDATE products SET is_active=0,version=version+1 WHERE id=1')
        self.assertEqual(set(self.ids(self.search())), {2, 3})

    def test_v07_paused_store_excludes_all_its_products(self):
        self.change_catalog('UPDATE stores SET is_active=0,version=version+1 WHERE id=2')
        self.assertEqual(self.ids(self.search()), [3])

    def test_v08_restocked_product_returns_without_manual_index_rebuild(self):
        self.change_catalog('UPDATE products SET stock=0,version=version+1 WHERE id=1')
        self.assertNotIn(1, self.ids(self.search()))
        self.change_catalog('UPDATE products SET stock=5,version=version+1 WHERE id=1')
        self.assertEqual(self.ids(self.search())[0], 1)

    def test_v09_replacing_catalog_images_updates_similarity_ranking(self):
        self.change_catalog('UPDATE products SET image=?,version=version+1 WHERE id=1',
                            (self.images[2],))
        self.change_catalog('UPDATE products SET image=?,version=version+1 WHERE id=3',
                            (self.images[1],))
        body = self.search()
        self.assertEqual(self.ids(body)[0], 3)
        self.assertEqual(body['items'][0]['image'], self.images[1])

    def test_v10_new_product_is_searchable_without_manual_index_rebuild(self):
        yellow = cup_photo(YELLOW)
        new_id = self.add_product(self.store_image(yellow))
        self.assertEqual(self.ids(self.search(yellow))[0], new_id)

    def test_v11_twelve_and_thirteen_matches_have_correct_limit_and_has_more(self):
        for _ in range(9):
            self.add_product(self.images[1])
        exactly_twelve = self.search()
        self.assertEqual(exactly_twelve['total'], 12)
        self.assertEqual(len(set(self.ids(exactly_twelve))), 12)
        self.assertFalse(exactly_twelve['has_more'])
        self.add_product(self.images[1])
        thirteen = self.search()
        self.assertEqual(thirteen['total'], 12)
        self.assertEqual(len(set(self.ids(thirteen))), 12)
        self.assertTrue(thirteen['has_more'])

    def test_v12_invalid_or_unsupported_image_content_is_rejected(self):
        for label, encoded in (
                ('bad base64', 'invalid-base64!'),
                ('not an image', base64.b64encode(b'not an image').decode('ascii')),
                ('truncated png', base64.b64encode(cup_photo(RED)[:40]).decode('ascii')),
                ('gif image', base64.b64encode(cup_photo(RED, 'GIF')).decode('ascii'))):
            with self.subTest(label=label):
                status, _ = self.request('POST', PHOTO_PATH,
                                         {'filename': 'query.png', 'data_base64': encoded})
                self.assertEqual(status, 422)
        self.assertEqual(self.service.in_flight, 0)

    def test_v13_invalid_filters_and_unknown_fields_are_rejected(self):
        for invalid in (
                {'store_id': 0}, {'store_id': -1}, {'store_id': True}, {'store_id': '2'},
                {'store_id': 2**63}, {'category': []}, {'category': 'x' * 31},
                {'unexpected': True}):
            with self.subTest(invalid=invalid):
                status, _ = self.query(**invalid)
                self.assertEqual(status, 422)

    def test_v14_visual_search_enforces_same_origin_and_request_header(self):
        payload = {'filename': 'query.png',
                   'data_base64': base64.b64encode(cup_photo(RED)).decode('ascii')}
        for headers in ({'X-Requested-With': ''}, {'Origin': 'https://attacker.invalid'}):
            with self.subTest(headers=headers):
                status, _ = self.request('POST', PHOTO_PATH, payload, headers)
                self.assertEqual(status, 403)
        self.assertEqual(self.ids(self.search())[0], 1)

    def test_v15_sixth_visual_request_within_a_minute_is_rate_limited(self):
        for _ in range(5):
            self.search()
        status, body = self.query()
        self.assertEqual(status, 429)
        self.assertIn('detail', body)
        self.assertEqual(self.service.in_flight, 0)

    def test_v16_missing_or_corrupt_index_disables_search_safely(self):
        manifest = self.visual.index_path / 'manifest.json'
        manifest.unlink()
        self.assertEqual(self.request('GET', PHOTO_PATH + '/status'),
                         (200, {'enabled': False, 'mode': 'disabled'}))
        self.assertEqual(self.query()[0], 503)
        manifest.write_text('{broken json', encoding='utf-8')
        self.assertEqual(self.request('GET', PHOTO_PATH + '/status'),
                         (200, {'enabled': False, 'mode': 'disabled'}))
        self.assertEqual(self.query()[0], 503)

    def test_v17_query_does_not_change_catalog_or_save_original_image(self):
        def asset_snapshot():
            return {str(path.relative_to(self.root)): hashlib.sha256(path.read_bytes()).hexdigest()
                    for directory in (self.root / 'uploads', self.visual.index_path)
                    for path in directory.rglob('*') if path.is_file()}

        def catalog_snapshot():
            with self.database.connection() as connection:
                return [tuple(row) for row in connection.execute(
                    'SELECT id,image,stock,version FROM products ORDER BY id').fetchall()]

        assets_before = asset_snapshot()
        catalog_before = catalog_snapshot()
        query = cup_photo(RED, 'JPEG', offset=2, private_metadata=True)
        self.assertIn(b'private-query-metadata-canary', query)
        self.assertEqual(self.ids(self.search(query))[0], 1)
        self.assertEqual(asset_snapshot(), assets_before)
        self.assertEqual(catalog_snapshot(), catalog_before)

    def test_v18_no_visible_products_returns_an_empty_visual_result(self):
        self.change_catalog('UPDATE products SET is_active=0,version=version+1')
        self.assertEqual(self.search(), {'mode': 'visual', 'keywords': [], 'items': [],
                                         'total': 0, 'has_more': False})

    def test_v19_index_io_error_returns_sanitized_service_error(self):
        with self.assertLogs('app.visual_search', level='ERROR'):
            with patch.object(self.visual, '_search', side_effect=OSError('/private/index-secret')):
                status, body = self.query()
        self.assertEqual(status, 503)
        self.assertIn('detail', body)
        self.assertNotIn('index-secret', str(body))
        self.assertNotIn('Traceback', str(body))
        self.assertEqual(self.service.in_flight, 0)
        self.assertEqual(self.ids(self.search())[0], 1)


class VisualPhotoWSGITests(VisualPhotoContract, unittest.TestCase):
    def open_application(self):
        self.application = MallApplication(self.database_path)
        self.client = Client(self.application)
        self.addCleanup(self.application.state.photo_search.close)

    def request(self, method, path, payload=None, headers=None):
        response = self.client.request(method, path, payload, headers=headers)
        return response['status'], response['json']


@unittest.skipUnless(FASTAPI_AVAILABLE, 'FastAPI/异步 SQLite 测试依赖未安装')
class VisualPhotoFastAPITests(VisualPhotoContract, unittest.TestCase):
    def open_application(self):
        from fastapi.testclient import TestClient
        from app.main import create_app
        self.application = create_app(self.database_path)
        self.client = self.enterContext(TestClient(self.application))

    def request(self, method, path, payload=None, headers=None):
        response = self.client.request(method, path, json=payload,
                                       headers={'X-Requested-With': 'HeimaMall', **(headers or {})})
        return response.status_code, response.json()


if __name__ == '__main__':
    unittest.main()
