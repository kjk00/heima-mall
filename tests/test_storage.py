"""MinIO startup-failure recovery without network access or application restarts."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from app import storage


class MemoryPrimary:
    def __init__(self):
        self.objects = {'remote.png': b'remote image'}
        self.available = True

    def _check(self):
        if not self.available:
            raise ConnectionError('service unavailable')

    def put(self, key, content, content_type):
        self._check()
        self.objects[key] = content

    def get(self, key):
        self._check()
        return self.objects.get(key)

    def exists(self, key):
        self._check()
        return key in self.objects

    def delete(self, key):
        self._check()
        self.objects.pop(key, None)


class MinioRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / 'uploads'
        self.primary = MemoryPrimary()
        self.clock = self.enterContext(patch('app.storage.time.monotonic', return_value=100.0))
        self.factory = self.enterContext(patch('app.storage.MinioStorage'))
        self.enterContext(patch.dict(os.environ, {
            'OSS_ENDPOINT': 'storage.invalid:9000', 'OSS_ACCESS_KEY': 'test-access',
            'OSS_SECRET_KEY': 'test-secret', 'OSS_BUCKET': 'test-bucket', 'OSS_SECURE': '1',
        }))

    def failed_startup(self):
        self.factory.side_effect = [ConnectionError('startup failed'), self.primary]
        instance = storage.ObjectStorage.from_environment(self.root)
        self.assertIsNone(instance.primary)
        self.assertEqual(self.factory.call_count, 1)
        return instance

    def test_startup_failure_recovers_and_keeps_local_uploads_readable(self):
        instance = self.failed_startup()
        instance.put('local.png', b'local image', 'image/png')
        self.assertEqual(instance.get('local.png'), b'local image')
        self.clock.return_value = 105.0
        instance.put('new.png', b'new image', 'image/png')
        self.assertIs(instance.primary, self.primary)
        self.assertEqual(self.primary.objects['new.png'], b'new image')
        self.assertEqual(instance.get('remote.png'), b'remote image')
        self.assertEqual(instance.get('local.png'), b'local image')
        self.assertTrue(instance.exists('local.png'))
        self.assertEqual(instance.url('local.png'), '/media/local.png')
        self.assertEqual(self.factory.call_count, 2)

    def test_each_operation_can_trigger_startup_recovery(self):
        for operation in ('put', 'get', 'exists', 'delete'):
            with self.subTest(operation=operation):
                self.clock.return_value = 100.0
                self.factory.reset_mock()
                self.primary = MemoryPrimary()
                instance = self.failed_startup()
                self.clock.return_value = 105.0
                if operation == 'put':
                    instance.put('new.png', b'new image')
                    self.assertEqual(self.primary.objects['new.png'], b'new image')
                elif operation == 'get':
                    self.assertEqual(instance.get('remote.png'), b'remote image')
                elif operation == 'exists':
                    self.assertTrue(instance.exists('remote.png'))
                else:
                    instance.delete('remote.png')
                    self.assertNotIn('remote.png', self.primary.objects)
                self.assertIs(instance.primary, self.primary)
                self.assertEqual(self.factory.call_count, 2)

    def test_repeated_failures_are_limited_to_one_attempt_per_cooldown(self):
        self.factory.side_effect = [ConnectionError('startup'), ConnectionError('retry'), self.primary]
        instance = storage.ObjectStorage.from_environment(self.root)
        for timestamp, attempts in ((100.0, 1), (104.99, 1), (105.0, 2), (109.99, 2), (110.0, 3)):
            self.clock.return_value = timestamp
            for _ in range(5):
                instance.get('missing.png')
            self.assertEqual(self.factory.call_count, attempts)
        self.assertIs(instance.primary, self.primary)

    def test_unconfigured_or_incomplete_settings_never_attempt_a_connection(self):
        for key in ('OSS_ENDPOINT', 'OSS_ACCESS_KEY', 'OSS_SECRET_KEY'):
            with self.subTest(missing=key), patch.dict(os.environ, {key: ''}):
                instance = storage.ObjectStorage.from_environment(self.root)
                instance.put('local.png', b'local image')
                self.clock.return_value += 100
                self.assertEqual(instance.get('local.png'), b'local image')
        self.factory.assert_not_called()

    def test_recovery_uses_the_original_configuration(self):
        instance = self.failed_startup()
        with patch.dict(os.environ, {'OSS_ENDPOINT': 'changed.invalid', 'OSS_SECRET_KEY': 'changed-secret'}):
            self.clock.return_value = 105.0
            self.assertEqual(instance.get('remote.png'), b'remote image')
        self.factory.assert_called_with('storage.invalid:9000', 'test-access', 'test-secret',
                                        'test-bucket', secure=True)

    def test_concurrent_requests_create_only_one_replacement_client(self):
        started, release = threading.Event(), threading.Event()
        attempts = []
        def connect(*args, **kwargs):
            attempts.append(True)
            if len(attempts) == 1:
                raise ConnectionError('startup failed')
            started.set()
            if not release.wait(timeout=5):
                raise RuntimeError('test reconnection was not released')
            return self.primary
        self.factory.side_effect = connect
        instance = storage.ObjectStorage.from_environment(self.root)
        instance.local.put('local.png', b'local image', 'image/png')
        self.clock.return_value = 105.0
        with ThreadPoolExecutor(max_workers=8) as executor:
            leader = executor.submit(instance.get, 'local.png')
            try:
                self.assertTrue(started.wait(timeout=2))
                followers = [executor.submit(instance.get, 'local.png') for _ in range(6)]
                self.assertEqual([future.result(timeout=2) for future in followers], [b'local image'] * 6)
                self.assertEqual(self.factory.call_count, 2)
            finally:
                release.set()
            self.assertEqual(leader.result(timeout=2), b'local image')
        self.assertIs(instance.primary, self.primary)

    def test_startup_error_does_not_log_exception_credentials(self):
        self.factory.side_effect = ConnectionError('request failed with test-secret')
        with self.assertLogs('app.storage', level='WARNING') as records:
            storage.ObjectStorage.from_environment(self.root)
        self.assertNotIn('test-secret', '\n'.join(records.output))

    def test_transient_failure_of_an_existing_client_still_recovers(self):
        self.factory.return_value = self.primary
        instance = storage.ObjectStorage.from_environment(self.root)
        self.primary.available = False
        instance.put('local.png', b'local image')
        self.primary.available = True
        self.clock.return_value = 105.0
        instance.put('remote-new.png', b'new image')
        self.assertEqual(self.primary.objects['remote-new.png'], b'new image')
        self.assertEqual(instance.get('local.png'), b'local image')
        self.factory.assert_called_once()
