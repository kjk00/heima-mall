"""Black-box contract tests for photo-to-product search.

These tests use only HTTP requests and observable response data. The vision
provider is replaced at the application boundary so the suite is deterministic.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from app import photo_search
from app.errors import MallError
from app.server import MallApplication
from tests.client import Client
from tests.test_photo_search import image_data


class PhotoSearchBlackBoxTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.app = MallApplication(Path(self.directory.name) / 'blackbox.db')
        self.client = Client(self.app)
        self.payload = {'filename': 'product.jpg', 'data_base64': image_data()}

    def test_b00_storefront_offers_photo_selection_without_camera_capture(self):
        response = self.client.get('/')
        self.assertEqual(response['status'], 200)
        self.assertIn('aria-label="照片识别商品"'.encode(), response['body'])
        self.assertIn(b'id="photo-search-upload-file"', response['body'])
        self.assertNotIn(b'photo-search-camera', response['body'])
        self.assertNotIn(b'capture="environment"', response['body'])

    def enable(self, result=None, error=None):
        service = photo_search.PhotoSearch('black-box-key')
        service.request_keywords = AsyncMock(side_effect=error, return_value=result)
        self.app.state.photo_search = service
        return service

    def test_b01_status_reports_feature_state_without_exposing_configuration(self):
        response = self.client.get('/api/search/photo/status')
        self.assertEqual(response['status'], 200)
        self.assertEqual(response['json'], {'enabled': False})
        self.enable(['耳机'])
        self.assertEqual(self.client.get('/api/search/photo/status')['json'], {'enabled': True})

    def test_b02_valid_photo_returns_live_catalog_matches(self):
        service = self.enable(['耳机'])
        response = self.client.post('/api/search/photo', self.payload)
        self.assertEqual(response['status'], 200, response)
        body = response['json']
        self.assertEqual(body['keywords'], ['耳机'])
        self.assertGreater(body['total'], 0)
        self.assertLessEqual(len(body['items']), 12)
        self.assertTrue(all(item['is_active'] for item in body['items']))
        service.request_keywords.assert_awaited_once()

    def test_b03_no_detected_product_returns_empty_result(self):
        self.enable([])
        response = self.client.post('/api/search/photo', self.payload)
        self.assertEqual(response['status'], 200)
        self.assertEqual(response['json'], {'keywords': [], 'items': [], 'total': 0, 'has_more': False})

    def test_b04_invalid_request_shapes_return_client_errors(self):
        self.enable(['耳机'])
        cases = [
            ('missing field', {'filename': 'product.jpg'}, 422),
            ('extra field', self.payload | {'unexpected': True}, 422),
            ('wrong media type', self.payload, 415),
            ('malformed json', None, 400),
        ]
        for label, payload, expected in cases:
            with self.subTest(label=label):
                if label == 'wrong media type':
                    response = self.client.post('/api/search/photo', payload,
                                                headers={'Content-Type': 'text/plain'})
                elif label == 'malformed json':
                    response = self.client.post('/api/search/photo', raw=b'{bad-json')
                else:
                    response = self.client.post('/api/search/photo', payload)
                self.assertEqual(response['status'], expected)

    def test_b05_request_protection_blocks_cross_site_and_missing_header(self):
        self.enable(['耳机'])
        missing = self.client.post('/api/search/photo', self.payload,
                                   headers={'X-Requested-With': ''})
        cross_site = self.client.post('/api/search/photo', self.payload,
                                      headers={'Origin': 'https://attacker.invalid'})
        self.assertEqual(missing['status'], 403)
        self.assertEqual(cross_site['status'], 403)

    def test_b06_provider_failure_is_a_sanitized_service_error(self):
        self.enable(error=MallError(503, '图片识别服务暂时不可用，请稍后重试或输入关键词搜索'))
        response = self.client.post('/api/search/photo', self.payload)
        self.assertEqual(response['status'], 503)
        self.assertEqual(response['json']['detail'], '图片识别服务暂时不可用，请稍后重试或输入关键词搜索')

    def test_b07_inactive_product_and_store_are_not_returned(self):
        self.enable(['耳机'])
        with self.app.state.database.connection() as connection:
            connection.execute("UPDATE products SET is_active=0 WHERE name LIKE '%耳机%'")
        self.assertEqual(self.client.post('/api/search/photo', self.payload)['json']['total'], 0)
        with self.app.state.database.connection() as connection:
            connection.execute('UPDATE products SET is_active=1 WHERE name LIKE "%耳机%"')
            connection.execute('UPDATE stores SET is_active=0 WHERE id=2')
        body = self.client.post('/api/search/photo', self.payload)['json']
        self.assertEqual(body['items'], [])
        self.assertEqual(body['total'], 0)

    def test_b08_request_limit_is_visible_as_retryable_error(self):
        self.enable(['耳机'])
        for _ in range(5):
            self.assertEqual(self.client.post('/api/search/photo', self.payload,
                                              remote_addr='198.51.100.8')['status'], 200)
        response = self.client.post('/api/search/photo', self.payload, remote_addr='198.51.100.8')
        self.assertEqual(response['status'], 429)
        self.assertIn('一分钟', response['json']['detail'])

