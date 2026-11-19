"""Optional Redis cache for public catalog reads; SQLite remains authoritative."""
import hashlib
import json
import logging
import os
import random
import secrets
import threading
import time

LOGGER = logging.getLogger(__name__)


class CacheUnavailable(Exception):
    pass


class RedisBackend:
    def __init__(self, url):
        import redis
        from redis.backoff import NoBackoff
        from redis.retry import Retry
        self.errors = redis.exceptions.RedisError
        self.client = redis.Redis.from_url(url, socket_connect_timeout=0.1,
            socket_timeout=0.1, max_connections=64, decode_responses=True,
            retry_on_timeout=False, retry=Retry(NoBackoff(), 0))

    def command(self, method, *args, **kwargs):
        try:
            return getattr(self.client, method)(*args, **kwargs)
        except (self.errors, UnicodeError) as error:
            raise CacheUnavailable() from error

    def get(self, key):
        return self.command('get', key)

    def put(self, key, value, ttl):
        return self.command('set', key, value, ex=ttl)

    def acquire(self, key, token):
        return self.command('set', key, token, nx=True, ex=5)

    def release(self, key, token):
        return self.command('eval',
            "if redis.call('GET',KEYS[1]) == ARGV[1] then return redis.call('DEL',KEYS[1]) else return 0 end",
            1, key, token)


class CatalogCache:
    OPERATIONS = frozenset({'product', 'products', 'categories', 'store', 'stores'})

    def __init__(self, database_path, backend=None, ttl=30):
        self.backend = backend
        self.ttl = ttl
        self.namespace = 'heima:catalog:' + hashlib.sha256(str(database_path.resolve()).encode()).hexdigest()[:32]
        self._locks = [threading.Lock() for _ in range(128)]
        self._disabled_until = 0
        self._warning_lock = threading.Lock()

    @classmethod
    def from_environment(cls, database_path):
        url = os.getenv('REDIS_URL', '').strip()
        ttl = int(os.getenv('REDIS_CACHE_TTL_SECONDS', '30'))
        if not 1 <= ttl <= 3600:
            raise ValueError('REDIS_CACHE_TTL_SECONDS 必须为1–3600秒')
        backend = None
        if url:
            try:
                backend = RedisBackend(url)
            except (ImportError, ValueError):
                LOGGER.warning('Redis缓存未启用：请安装redis依赖并检查REDIS_URL配置')
        return cls(database_path, backend=backend, ttl=ttl)

    def _trip(self):
        with self._warning_lock:
            if time.monotonic() >= self._disabled_until:
                LOGGER.warning('Redis缓存暂时不可用，已降级为数据库读取')
            self._disabled_until = time.monotonic() + 2

    def _cached(self, key):
        raw = self.backend.get(key)
        if raw is None:
            return None
        try:
            envelope = json.loads(raw)
            if isinstance(envelope, dict) and envelope.get('format') == 1 and isinstance(envelope.get('value'), (dict, list)):
                return envelope['value']
        except (ValueError, TypeError):
            pass
        return None

    def read(self, connection, operation, arguments, loader):
        if operation not in self.OPERATIONS or self.backend is None or time.monotonic() < self._disabled_until:
            return loader()
        # The revision advances in the SAME SQLite transaction as product/store
        # writes. A failed Redis invalidation or an old builder cannot resurrect
        # stale stock, price, visibility or store metadata after a committed write.
        state = connection.execute('SELECT generation,revision FROM catalog_cache_state WHERE id=1').fetchone()
        encoded = json.dumps([operation, arguments], sort_keys=True, ensure_ascii=False, separators=(',', ':'))
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        key = f'{self.namespace}:{state["generation"]}:r{state["revision"]}:{digest}'
        try:
            cached = self._cached(key)
            if cached is not None:
                return cached
        except CacheUnavailable:
            self._trip()
            return loader()
        lock = self._locks[int(digest[:8], 16) % len(self._locks)]
        if not lock.acquire(timeout=0.2):
            return loader()
        try:
            try:
                cached = self._cached(key)
                if cached is not None:
                    return cached
                token = secrets.token_hex(16)
                acquired = self.backend.acquire(key + ':lock', token)
            except CacheUnavailable:
                self._trip()
                return loader()
            if not acquired:
                deadline = time.monotonic() + 0.2
                while time.monotonic() < deadline:
                    time.sleep(0.01)
                    try:
                        cached = self._cached(key)
                    except CacheUnavailable:
                        self._trip()
                        break
                    if cached is not None:
                        return cached
                return loader()
            try:
                value = loader()
                ttl = self.ttl + random.randint(0, max(1, self.ttl // 5))
                try:
                    self.backend.put(key, json.dumps({'format': 1, 'value': value}, ensure_ascii=False), ttl)
                except CacheUnavailable:
                    self._trip()
                return value
            finally:
                try:
                    self.backend.release(key + ':lock', token)
                except CacheUnavailable:
                    self._trip()
        finally:
            lock.release()

    def close(self):
        if isinstance(self.backend, RedisBackend):
            self.backend.client.close()
