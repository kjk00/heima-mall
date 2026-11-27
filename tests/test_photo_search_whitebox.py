"""White-box branch and provider-contract tests for photo search."""
import asyncio
import base64
import io
import json
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image

from app import photo_search
from app.errors import MallError
from tests.test_photo_search import completion, image_data


class PhotoSearchWhiteBoxTests(unittest.IsolatedAsyncioTestCase):
    def test_w01_prepare_image_reencodes_pixels_and_strips_metadata(self):
        encoded = photo_search.prepare_image(image_data((1800, 900), exif=True))
        raw = base64.b64decode(encoded.split(',', 1)[1])
        with Image.open(io.BytesIO(raw)) as image:
            self.assertEqual(image.size, (1280, 640))
            self.assertEqual(dict(image.getexif()), {})
        self.assertNotIn(b'private-camera-metadata', raw)

    def test_w02_keyword_parser_normalizes_and_rejects_untrusted_output(self):
        self.assertEqual(photo_search.parse_keywords(completion([' 耳机 ', '耳机', '马克杯'])),
                         ['耳机', '马克杯'])
        for words in (['<img>'], ['a' * 21], ['耳机'] * 5, [None]):
            with self.subTest(words=words), self.assertRaises(MallError):
                photo_search.parse_keywords(completion(words))
        response = completion(['耳机'])
        response['choices'][0]['finish_reason'] = 'length'
        with self.assertRaises(MallError):
            photo_search.parse_keywords(response)

    def test_w03_configuration_rejects_insecure_provider_endpoints(self):
        for endpoint in ('http://vision.invalid', 'https://user:pass@vision.invalid',
                         'https://vision.invalid?token=secret'):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                photo_search.PhotoSearch('key', endpoint)
        with self.assertRaises(ValueError):
            photo_search.PhotoSearch('key', model='')

    def test_w03b_forwarded_address_is_trusted_only_from_configured_proxy(self):
        service = photo_search.PhotoSearch('key')
        trusted_request = SimpleNamespace(client=SimpleNamespace(host='10.0.0.2'),
                                          headers={'x-forwarded-for': '198.51.100.20, 10.0.0.1'})
        untrusted_request = SimpleNamespace(client=SimpleNamespace(host='203.0.113.8'),
                                            headers={'x-forwarded-for': '198.51.100.20'})
        with patch.dict('os.environ', {'PHOTO_SEARCH_TRUSTED_PROXY': '10.0.0.0/8'}):
            self.assertEqual(service.client_host(trusted_request), '198.51.100.20')
            self.assertEqual(service.client_host(untrusted_request), '203.0.113.8')

    async def test_w04_reservation_releases_capacity_on_exception_and_cancellation(self):
        service = photo_search.PhotoSearch('key')
        with self.assertRaises(RuntimeError):
            with service.reserve('host'):
                raise RuntimeError('test')
        self.assertEqual(service.in_flight, 0)
        entered = asyncio.Event()

        async def pending(image):
            entered.set()
            await asyncio.Event().wait()

        service.request_keywords = pending
        task = asyncio.create_task(service.recognize(image_data(), 'host'))
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(service.in_flight, 0)

    async def test_w05_provider_contract_sets_json_mode_and_blocks_redirects(self):
        body = json.dumps(completion(['耳机'])).encode()
        captured = {}

        class Response:
            status = 200

            class Content:
                async def iter_chunked(self, size):
                    yield body

            content = Content()

        class Session:
            def __init__(self, **kwargs):
                captured['timeout'] = kwargs['timeout']

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            @asynccontextmanager
            async def post(self, url, **kwargs):
                captured.update(url=url, **kwargs)
                yield Response()

        with patch('aiohttp.ClientSession', Session):
            result = await photo_search.PhotoSearch('key').recognize(image_data(), 'host')
        self.assertEqual(result, ['耳机'])
        self.assertFalse(captured['allow_redirects'])
        self.assertFalse(captured['json']['store'])
        self.assertEqual(captured['json']['response_format'], {'type': 'json_object'})
        self.assertEqual(captured['json']['messages'][1]['content'][1]['type'], 'image_url')

    async def test_w06_provider_transport_and_response_limits_map_to_safe_errors(self):
        import aiohttp
        service = photo_search.PhotoSearch('key')
        for exception in (aiohttp.ClientError('private network'), TimeoutError('private timeout')):
            with self.subTest(exception=type(exception).__name__), \
                    patch('aiohttp.ClientSession', side_effect=exception), self.assertRaises(MallError) as error:
                await service.request_keywords('image')
            self.assertEqual(error.exception.status_code, 503)
        class Body:
            async def iter_chunked(self, size):
                yield b'x' * (photo_search.MAX_RESPONSE_BYTES + 1)
        response = type('Response', (), {'status': 200, 'content': Body()})()

        class Session:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            @asynccontextmanager
            async def post(self, *args, **kwargs):
                yield response

        with patch('aiohttp.ClientSession', Session), self.assertRaises(MallError) as error:
            await service.request_keywords('image')
        self.assertEqual(error.exception.status_code, 502)

    async def test_w07_shared_redis_limit_applies_across_service_instances(self):
        class SharedRedis:
            def __init__(self):
                self.buckets = {}

            def eval(self, script, key_count, global_key, host_key, now, token):
                assert key_count == 2 and 'ZREMRANGEBYSCORE' in script and 'ZCARD' in script
                cutoff = int(now) - 60000
                global_bucket = [stamp for stamp in self.buckets.get(global_key, []) if stamp > cutoff]
                host_bucket = [stamp for stamp in self.buckets.get(host_key, []) if stamp > cutoff]
                if len(global_bucket) >= 30 or len(host_bucket) >= 5:
                    self.buckets[global_key], self.buckets[host_key] = global_bucket, host_bucket
                    return 0
                self.buckets[global_key] = global_bucket + [int(now)]
                self.buckets[host_key] = host_bucket + [int(now)]
                return 1

        shared = SharedRedis()
        services = [photo_search.PhotoSearch('key') for _ in range(2)]
        for service in services:
            service.shared_limiter = SimpleNamespace(client=shared)
            service.request_keywords = AsyncMock(return_value=['耳机'])
        with patch('app.photo_search.prepare_image', return_value='prepared-image'):
            for index in range(5):
                self.assertEqual(await services[index % 2].recognize('encoded', '198.51.100.1'), ['耳机'])
            with self.assertRaises(MallError) as error:
                await services[1].recognize('encoded', '198.51.100.1')
        self.assertEqual(error.exception.status_code, 429)

    async def test_w08_shared_redis_failure_fails_closed(self):
        class FailedRedis:
            def eval(self, *args):
                raise ConnectionError('private redis endpoint')

        service = photo_search.PhotoSearch('key')
        service.shared_limiter = SimpleNamespace(client=FailedRedis())
        service.request_keywords = AsyncMock(return_value=['耳机'])
        with patch('app.photo_search.prepare_image', return_value='prepared-image'):
            with self.assertRaises(MallError) as error:
                await service.recognize('encoded', 'host')
        self.assertEqual(error.exception.status_code, 503)
        self.assertNotIn('private redis endpoint', error.exception.detail)

