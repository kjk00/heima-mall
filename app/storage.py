"""Object storage for product images: MinIO (S3/OSS-compatible) with local-disk fallback.

Uploads are immutable sha256-named objects. The storefront always references
/media/<key>; the HTTP adapter streams bytes from whichever backend holds them,
so switching MinIO on/off never changes product image URLs. When MinIO is
unconfigured or unreachable, objects fall back to the local uploads directory.
"""
import logging
import os
import tempfile
import time
from pathlib import Path

LOGGER = logging.getLogger(__name__)


class LocalStorage:
    def __init__(self, root):
        self.root = Path(root)

    def put(self, key, content, content_type):
        self.root.mkdir(parents=True, exist_ok=True)
        # Publish the immutable object only after the entire file is durable.
        with tempfile.NamedTemporaryFile(dir=self.root, delete=False) as file:
            temporary = Path(file.name)
            try:
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        try:
            os.replace(temporary, self.root / key)
        finally:
            temporary.unlink(missing_ok=True)

    def get(self, key):
        path = self.root / key
        return path.read_bytes() if path.is_file() else None

    def exists(self, key):
        return (self.root / key).is_file()

    def delete(self, key):
        (self.root / key).unlink(missing_ok=True)


class MinioStorage:
    def __init__(self, endpoint, access_key, secret_key, bucket, secure=False):
        from minio import Minio
        self.client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
        self.bucket = bucket
        if not self.client.bucket_exists(bucket):
            self.client.make_bucket(bucket)

    def put(self, key, content, content_type):
        import io
        self.client.put_object(self.bucket, key, io.BytesIO(content), length=len(content), content_type=content_type)

    def get(self, key):
        from minio.error import S3Error
        try:
            response = self.client.get_object(self.bucket, key)
        except S3Error as error:
            if error.code in ('NoSuchKey', 'NoSuchBucket'):
                return None
            raise
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def exists(self, key):
        from minio.error import S3Error
        try:
            self.client.stat_object(self.bucket, key)
            return True
        except S3Error as error:
            if error.code in ('NoSuchKey', 'NoSuchBucket'):
                return False
            raise

    def delete(self, key):
        self.client.remove_object(self.bucket, key)


class ObjectStorage:
    def __init__(self, root, primary=None):
        self.local = LocalStorage(root)
        self.primary = primary
        self.disabled_until = 0.0
        self.warned_until = 0.0

    @classmethod
    def from_environment(cls, root):
        endpoint = os.getenv('OSS_ENDPOINT', '').strip()
        access = os.getenv('OSS_ACCESS_KEY', '').strip()
        secret = os.getenv('OSS_SECRET_KEY', '').strip()
        primary = None
        if endpoint and access and secret:
            try:
                primary = MinioStorage(endpoint, access, secret, os.getenv('OSS_BUCKET', 'heima-mall'),
                                       secure=os.getenv('OSS_SECURE', '0') == '1')
            except Exception:
                LOGGER.warning('MinIO对象存储不可用，图片降级为本地磁盘存储', exc_info=True)
        return cls(root, primary)

    def _trip(self):
        now = time.monotonic()
        if now >= self.warned_until:
            LOGGER.warning('MinIO对象存储暂时不可用，图片已降级为本地磁盘存储')
            self.warned_until = now + 30
        self.disabled_until = now + 5

    def _healthy(self):
        return self.primary is not None and time.monotonic() >= self.disabled_until

    def put(self, key, content, content_type='application/octet-stream'):
        if self._healthy():
            try:
                self.primary.put(key, content, content_type)
                return key
            except Exception:
                self._trip()
        self.local.put(key, content, content_type)
        return key

    def get(self, key):
        if self._healthy():
            try:
                data = self.primary.get(key)
                if data is not None:
                    return data
            except Exception:
                self._trip()
        return self.local.get(key)

    def exists(self, key):
        if self._healthy():
            try:
                if self.primary.exists(key):
                    return True
            except Exception:
                self._trip()
        return self.local.exists(key)

    def delete(self, key):
        if self.primary is not None:
            try:
                self.primary.delete(key)
            except Exception:
                self._trip()
        self.local.delete(key)

    @staticmethod
    def url(key):
        return '/media/' + key