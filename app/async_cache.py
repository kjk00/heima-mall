"""Awaitable Redis catalog caching with bounded stampede waits and failover."""
import asyncio
import hashlib
import json
import logging
import os
import random
import secrets
import time
from app.cache import CacheUnavailable

LOGGER = logging.getLogger(__name__)


class AsyncRedisBackend:
    def __init__(self, url):
        from redis.asyncio import Redis
        from redis.asyncio.retry import Retry
        from redis.backoff import NoBackoff
        from redis.exceptions import RedisError
        self.errors = RedisError
        self.client = Redis.from_url(url, decode_responses=True, max_connections=64,
            socket_connect_timeout=0.1, socket_timeout=0.1,
            retry=Retry(NoBackoff(), 0), retry_on_timeout=False)

    async def command(self, method, *args, **kwargs):
        try:
            return await getattr(self.client, method)(*args, **kwargs)
        except (self.errors, UnicodeError) as error:
            raise CacheUnavailable() from error

    async def get(self, key):
        return await self.command('get', key)

    async def put(self, key, value, ttl):
        return await self.command('set', key, value, ex=ttl)

    async def acquire(self, key, token):
        return await self.command('set', key, token, nx=True, ex=5)

    async def release(self, key, token):
        return await self.command('eval',
            "if redis.call('GET',KEYS[1]) == ARGV[1] then return redis.call('DEL',KEYS[1]) else return 0 end", 1, key, token)

    async def close(self):
        await self.client.aclose()


class AsyncCatalogCache:
    OPERATIONS = frozenset({'product','products','categories','store','stores'})

    def __init__(self, path, backend=None, ttl=30):
        if not 1 <= ttl <= 3600:
            raise ValueError('REDIS_CACHE_TTL_SECONDS必须为1–3600秒')
        self.backend, self.ttl = backend, ttl
        self.namespace = 'heima:catalog:' + hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:32]
        self.locks = [asyncio.Lock() for _ in range(128)]
        self.disabled_until = 0

    @classmethod
    def from_environment(cls, path):
        url = os.getenv('REDIS_URL','').strip()
        backend = AsyncRedisBackend(url) if url else None
        return cls(path, backend, int(os.getenv('REDIS_CACHE_TTL_SECONDS','30')))

    def trip(self):
        if time.monotonic() >= self.disabled_until:
            LOGGER.warning('异步Redis缓存不可用，已回源数据库')
        self.disabled_until = time.monotonic() + 2

    async def cached(self, key):
        raw = await self.backend.get(key)
        if raw is None:
            return None
        try:
            envelope = json.loads(raw)
            if isinstance(envelope,dict) and envelope.get('format') == 1 and isinstance(envelope.get('value'),(dict,list)):
                return envelope['value']
        except (ValueError,TypeError):
            pass
        return None

    async def read(self, connection, operation, arguments, loader):
        if operation not in self.OPERATIONS or self.backend is None or time.monotonic() < self.disabled_until:
            return await loader()
        state = (await connection.execute('SELECT generation,revision FROM catalog_cache_state WHERE id=1')).fetchone()
        digest = hashlib.sha256(json.dumps([operation,arguments],sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
        key = f'{self.namespace}:{state["generation"]}:r{state["revision"]}:{digest}'
        try:
            cached = await self.cached(key)
            if cached is not None:
                return cached
        except CacheUnavailable:
            self.trip()
            return await loader()
        lock = self.locks[int(digest[:8],16) % len(self.locks)]
        try:
            async with asyncio.timeout(0.2):
                await lock.acquire()
        except TimeoutError:
            return await loader()
        try:
            try:
                cached = await self.cached(key)
                if cached is not None:
                    return cached
                token = secrets.token_hex(16)
                acquired = await self.backend.acquire(key+':lock',token)
            except CacheUnavailable:
                self.trip()
                return await loader()
            if not acquired:
                deadline = time.monotonic()+0.2
                while time.monotonic() < deadline:
                    await asyncio.sleep(0.01)
                    try:
                        cached = await self.cached(key)
                    except CacheUnavailable:
                        self.trip()
                        break
                    if cached is not None:
                        return cached
                return await loader()
            try:
                value = await loader()
                try:
                    await self.backend.put(key,json.dumps({'format':1,'value':value},ensure_ascii=False),
                        self.ttl+random.randint(0,max(1,self.ttl//5)))
                except CacheUnavailable:
                    self.trip()
                return value
            finally:
                try:
                    await self.backend.release(key+':lock',token)
                except CacheUnavailable:
                    self.trip()
        finally:
            lock.release()

    async def close(self):
        if self.backend:
            await self.backend.close()
