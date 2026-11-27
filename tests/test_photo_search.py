"""Photo recognition safety, provider contract, and both HTTP adapter regressions."""
import asyncio
import base64
from contextlib import asynccontextmanager
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from PIL import Image

from app import photo_search
from app.errors import MallError
from app.schemas import ImageUploadInput
from app.server import MallApplication
from tests.client import Client


def image_data(size=(80, 60), exif=False, image_format='JPEG'):
    image = Image.new('RGB', size, (120, 100, 200))
    output = io.BytesIO()
    metadata = Image.Exif()
    if exif:
        metadata[270] = 'private-camera-metadata'
    save_options = {'format': image_format}
    if image_format == 'JPEG':
        save_options['exif'] = metadata
    image.save(output, **save_options)
    return base64.b64encode(output.getvalue()).decode('ascii')


def completion(words):
    return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'keywords': words})}}]}


class PhotoImageTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js 未安装')
    def test_photo_search_browser_controller(self):
        result = subprocess.run(['node', 'tests/test_photo_search.js'], cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_reencoded_image_is_bounded_and_removes_metadata(self):
        result = photo_search.prepare_image(image_data((1800, 900), exif=True))
        self.assertTrue(result.startswith('data:image/jpeg;base64,'))
        content = base64.b64decode(result.split(',')[1])
        with Image.open(io.BytesIO(content)) as image:
            self.assertEqual(image.size, (1280, 640))
            self.assertEqual(dict(image.getexif()), {})
        self.assertNotIn(b'private-camera-metadata', content)

    def test_invalid_encoding_fake_image_and_oversize_are_rejected(self):
        for raw, status in (('bad-base64!', 422), (base64.b64encode(b'not an image').decode(), 422),
                            (base64.b64encode(b'x' * (photo_search.MAX_BYTES + 1)).decode(), 413)):
            with self.subTest(status=status), self.assertRaises(MallError) as error:
                photo_search.prepare_image(raw)
            self.assertEqual(error.exception.status_code, status)

    def test_png_and_webp_are_reencoded_and_oversized_dimensions_are_rejected(self):
        for image_format in ('PNG', 'WEBP'):
            with self.subTest(image_format=image_format):
                result = photo_search.prepare_image(image_data(image_format=image_format))
                self.assertTrue(result.startswith('data:image/jpeg;base64,'))
                with Image.open(io.BytesIO(base64.b64decode(result.split(',')[1]))) as converted:
                    self.assertEqual(converted.format, 'JPEG')
                    self.assertEqual(converted.size, (80, 60))
        with self.assertRaises(MallError) as error:
            photo_search.prepare_image(image_data((5000, 4000), image_format='PNG'))
        self.assertEqual(error.exception.status_code, 422)

    def test_keywords_allow_unicode_but_reject_html_and_unbounded_output(self):
        self.assertEqual(photo_search.parse_keywords(completion([' 耳机 ', '耳机', '马克杯'])), ['耳机', '马克杯'])
        self.assertEqual(photo_search.parse_keywords(completion([])), [])
        self.assertEqual(photo_search.parse_keywords(completion(['a' * 20, 'b', 'c', 'd'])),
                         ['a' * 20, 'b', 'c', 'd'])
        for words in (['<script>'], ['a' * 21], ['耳机'] * 5, [42], '耳机', ['']):
            with self.subTest(words=words), self.assertRaises(MallError):
                photo_search.parse_keywords(completion(words))
        for finish_reason in ('length', 'content_filter'):
            response = completion(['耳机'])
            response['choices'][0]['finish_reason'] = finish_reason
            with self.subTest(finish_reason=finish_reason), self.assertRaises(MallError):
                photo_search.parse_keywords(response)
        for response in ({}, {'choices': []}, {'choices': [{'message': {'content': 'invalid json'}}]}):
            with self.assertRaises(MallError):
                photo_search.parse_keywords(response)


class PhotoRecognitionTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_service_does_not_prepare_or_send_image(self):
        instance = photo_search.PhotoSearch()
        with patch.object(photo_search, 'prepare_image') as prepare, self.assertRaises(MallError) as error:
            await instance.recognize('invalid', 'host')
        self.assertEqual(error.exception.status_code, 503)
        prepare.assert_not_called()

    async def test_invalid_image_never_calls_provider_and_releases_capacity(self):
        instance = photo_search.PhotoSearch('test-only-key')
        instance.request_keywords = AsyncMock()
        with self.assertRaises(MallError):
            await instance.recognize('invalid', 'host')
        instance.request_keywords.assert_not_called()
        self.assertEqual(instance.in_flight, 0)

    async def test_host_and_global_limits_expire_without_unbounded_state(self):
        instance = photo_search.PhotoSearch('test-only-key')
        with patch('app.photo_search.time.monotonic', return_value=100.0) as clock:
            for _ in range(5):
                with instance.reserve('one'): pass
            with self.assertRaises(MallError) as error:
                with instance.reserve('one'): pass
            self.assertEqual(error.exception.status_code, 429)
            for host in range(25):
                with instance.reserve(str(host)): pass
            with self.assertRaises(MallError):
                with instance.reserve('another'): pass
            clock.return_value = 160.0
            with instance.reserve('one'): pass
            self.assertEqual(len(instance.attempts), 1)

    async def test_busy_or_cancelled_request_releases_capacity(self):
        instance = photo_search.PhotoSearch('test-only-key')
        with instance.reserve('one'), instance.reserve('two'):
            with self.assertRaises(MallError):
                with instance.reserve('three'): pass
        entered = asyncio.Event()
        async def pending(image):
            entered.set()
            await asyncio.Event().wait()
        instance.request_keywords = pending
        task = asyncio.create_task(instance.recognize(image_data(), 'four'))
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(instance.in_flight, 0)

    def stub_provider(self, content, status=200):
        class Body:
            async def iter_chunked(self, size):
                for offset in range(0, len(content), size):
                    yield content[offset:offset + size]
        response = type('Response', (), {'status': status, 'content': Body()})()
        captured = {}
        class Session:
            def __init__(self, **kwargs): captured['timeout'] = kwargs['timeout']
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            @asynccontextmanager
            async def post(self, url, **kwargs):
                captured.update(url=url, **kwargs)
                yield response
        self.enterContext(patch('aiohttp.ClientSession', Session))
        return captured

    async def test_provider_request_uses_image_contract_and_does_not_follow_redirects(self):
        captured = self.stub_provider(json.dumps(completion(['耳机'])).encode())
        instance = photo_search.PhotoSearch('test-only-key')
        self.assertEqual(await instance.recognize(image_data(), 'host'), ['耳机'])
        self.assertEqual(captured['url'], 'https://api.openai.com/v1/chat/completions')
        self.assertFalse(captured['allow_redirects'])
        self.assertFalse(captured['json']['store'])
        content = captured['json']['messages'][1]['content'][1]
        self.assertEqual(content['type'], 'image_url')
        self.assertTrue(content['image_url']['url'].startswith('data:image/jpeg;base64,'))

    async def test_provider_errors_hide_upstream_body_and_secrets(self):
        self.stub_provider(b'private upstream diagnostic test-only-key', status=401)
        with self.assertRaises(MallError) as error:
            await photo_search.PhotoSearch('test-only-key').request_keywords('data:image/jpeg;base64,a')
        self.assertEqual(error.exception.status_code, 503)
        self.assertNotIn('test-only-key', error.exception.detail)

    async def test_provider_response_size_is_bounded(self):
        self.stub_provider(b'x' * (photo_search.MAX_RESPONSE_BYTES + 1))
        with self.assertRaises(MallError) as error:
            await photo_search.PhotoSearch('test-only-key').request_keywords('image')
        self.assertEqual(error.exception.status_code, 502)

    async def test_provider_timeout_connection_failure_and_invalid_json_are_sanitized(self):
        import aiohttp
        instance = photo_search.PhotoSearch('test-only-key')
        for failure in (aiohttp.ClientError('private host details'), TimeoutError('private timeout details')):
            with self.subTest(failure=type(failure).__name__), \
                    patch('aiohttp.ClientSession', side_effect=failure), self.assertRaises(MallError) as error:
                await instance.request_keywords('image')
            self.assertEqual(error.exception.status_code, 503)
            self.assertNotIn('private', error.exception.detail)
        self.stub_provider(b'{not-json')
        with self.assertRaises(MallError) as error:
            await instance.request_keywords('image')
        self.assertEqual(error.exception.status_code, 502)

    def test_environment_configuration_is_trimmed_and_defaults_are_applied(self):
        with patch.dict(os.environ, {'PHOTO_SEARCH_API_KEY': ' key ',
                                     'PHOTO_SEARCH_BASE_URL': ' https://vision.example/v1/ ',
                                     'PHOTO_SEARCH_MODEL': ' model-x '}, clear=False):
            configured = photo_search.PhotoSearch.from_environment()
        self.assertTrue(configured.enabled)
        self.assertEqual(configured.api_key, 'key')
        self.assertEqual(configured.base_url, 'https://vision.example/v1')
        self.assertEqual(configured.model, 'model-x')
        with patch.dict(os.environ, {}, clear=True):
            defaults = photo_search.PhotoSearch.from_environment()
        self.assertFalse(defaults.enabled)
        self.assertEqual(defaults.model, 'gpt-4.1-mini')

    def test_provider_configuration_requires_https_without_embedded_credentials(self):
        for address in ('http://unsafe.invalid', 'https://key:secret@host.invalid',
                        'https://host.invalid?key=secret', 'https://host.invalid/#secret'):
            with self.subTest(address=address), self.assertRaises(ValueError):
                photo_search.PhotoSearch('test-only-key', base_url=address)


class PhotoSearchHTTPTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'mall.db'
        self.app = MallApplication(self.path)
        self.client = Client(self.app)
        self.payload = {'filename': 'photo.jpg', 'data_base64': image_data()}

    def enable(self, words):
        instance = photo_search.PhotoSearch('test-only-key')
        instance.request_keywords = AsyncMock(return_value=words)
        self.app.state.photo_search = instance
        return instance

    def test_unconfigured_feature_is_explicit_and_never_calls_provider(self):
        self.assertEqual(self.client.get('/api/search/photo/status')['json'],
                         {'enabled': False, 'mode': 'disabled'})
        response = self.client.post('/api/search/photo', self.payload)
        self.assertEqual(response['status'], 503)
        self.assertNotIn('API_KEY', response['json']['detail'])

    def test_matching_returns_only_live_catalog_products(self):
        self.enable(['耳机'])
        result = self.client.post('/api/search/photo', self.payload)
        self.assertEqual(result['status'], 200, result)
        self.assertTrue(result['json']['items'])
        self.assertTrue(all('耳机' in item['name'] or '耳机' in item['subtitle'] for item in result['json']['items']))
        with self.app.state.database.connection() as connection:
            connection.execute("UPDATE products SET is_active=0 WHERE name LIKE '%耳机%'")
        result = self.client.post('/api/search/photo', self.payload)
        self.assertEqual(result['json']['items'], [])
        self.assertEqual(result['json']['total'], 0)
        self.assertFalse((self.path.parent / 'uploads').exists())

    def test_photo_search_fills_partial_or_empty_index_results_from_live_catalog(self):
        service = self.enable(['数码科技'])
        self.app.state.search_engine = None
        baseline = self.client.post('/api/search/photo', self.payload)['json']
        expected_ids = [item['id'] for item in baseline['items']]
        self.assertGreater(len(expected_ids), 1)

        class FakeSearch:
            def __init__(self, ids):
                self.ids = ids
            async def read_photo(self, connection, keywords, category='', store_id=None):
                return {'ids': self.ids}

        for ids in ([], expected_ids[:1], [999999]):
            with self.subTest(indexed_ids=ids):
                self.app.state.search_engine = FakeSearch(ids)
                service.attempts.clear()
                result = self.client.post('/api/search/photo', self.payload)
                self.assertEqual(result['status'], 200, result)
                self.assertEqual([item['id'] for item in result['json']['items']], expected_ids)

    def test_catalog_matching_checks_category_and_escapes_like_wildcards(self):
        self.enable(['数码科技'])
        by_category = self.client.post('/api/search/photo', self.payload)
        self.assertEqual(by_category['status'], 200)
        self.assertTrue(by_category['json']['items'])
        self.assertTrue(all(item['category'] == '数码科技' for item in by_category['json']['items']))
        self.enable(['%'])
        wildcard = self.client.post('/api/search/photo', self.payload)
        self.assertEqual(wildcard['json']['items'], [])

    def test_catalog_limit_order_total_and_per_host_rate_limit(self):
        with self.app.state.database.connection() as connection:
            connection.execute("UPDATE products SET category='照片识别测试'")
        self.enable(['照片识别测试'])
        result = self.client.post('/api/search/photo', self.payload)
        products = result['json']['items']
        self.assertEqual(result['json']['total'], 12)
        self.assertTrue(result['json']['has_more'])
        self.assertEqual(len(products), 12)
        self.assertEqual([product['sales'] for product in products],
                         sorted((product['sales'] for product in products), reverse=True))
        self.enable(['耳机'])
        for _ in range(5):
            self.assertEqual(self.client.post('/api/search/photo', self.payload, remote_addr='192.0.2.5')['status'], 200)
        limited = self.client.post('/api/search/photo', self.payload, remote_addr='192.0.2.5')
        self.assertEqual(limited['status'], 429)

    def test_request_schema_body_size_and_content_type_boundaries(self):
        self.enable(['耳机'])
        for payload in ({'filename': 'photo.jpg'},
                        {'filename': 'photo.jpg', 'data_base64': self.payload['data_base64'], 'extra': 1},
                        {'filename': '', 'data_base64': self.payload['data_base64']}):
            with self.subTest(payload=payload.keys()):
                self.assertEqual(self.client.post('/api/search/photo', payload)['status'], 422)
        self.assertEqual(self.client.post('/api/search/photo', self.payload,
                                          headers={'Content-Type': 'text/plain'})['status'], 415)
        too_large = self.client.post('/api/search/photo', raw=b'x' * (3 * 1024 * 1024 + 1))
        self.assertEqual(too_large['status'], 413)

    def test_paused_stores_are_excluded_and_empty_recognition_does_not_return_all_products(self):
        self.enable(['耳机'])
        with self.app.state.database.connection() as connection:
            connection.execute('UPDATE stores SET is_active=0')
        self.assertEqual(self.client.post('/api/search/photo', self.payload)['json']['items'], [])
        self.enable([])
        self.assertEqual(self.client.post('/api/search/photo', self.payload)['json'],
                         {'mode': 'keywords', 'keywords': [], 'items': [], 'total': 0, 'has_more': False})

    def test_public_endpoint_still_requires_same_origin_request_protection(self):
        self.enable(['耳机'])
        self.assertEqual(self.client.post('/api/search/photo', self.payload,
                                         headers={'X-Requested-With': ''})['status'], 403)
        self.assertEqual(self.client.post('/api/search/photo', self.payload,
                                         headers={'Origin': 'https://other.invalid'})['status'], 403)
        response = self.client.post('/api/search/photo', self.payload, headers={'Content-Type': 'text/plain'})
        self.assertEqual(response['status'], 415)

    def test_fastapi_adapter_accepts_photo_bodies_larger_than_default_json_limit(self):
        from fastapi.testclient import TestClient
        from app.main import create_app
        app = create_app(self.path)
        instance = photo_search.PhotoSearch('test-only-key')
        instance.request_keywords = AsyncMock(return_value=['耳机'])
        app.state.photo_search = instance
        # A noisy image exceeds 16 KB, unlike the solid-color test fixture.
        image = Image.frombytes('RGB', (200, 200), os.urandom(200 * 200 * 3))
        output = io.BytesIO()
        image.save(output, format='PNG')
        payload = {'filename': 'photo.png', 'data_base64': base64.b64encode(output.getvalue()).decode(),
                   'category': '数码科技', 'store_id': 2}
        self.assertGreater(len(payload['data_base64']), 16384)
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/search/photo/status').json(),
                             {'enabled': True, 'mode': 'keywords'})
            result = client.post('/api/search/photo', json=payload, headers={'X-Requested-With': 'HeimaMall'})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertTrue(result.json()['items'])
            self.assertEqual(client.post('/api/search/photo', content=b'x' * (3 * 1024 * 1024 + 1),
                                         headers={'Content-Type': 'application/json', 'X-Requested-With': 'HeimaMall'}).status_code, 413)
