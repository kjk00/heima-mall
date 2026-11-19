"""MongoDB store for merchant product parameters (商品参数).

MySQL stays the transactional source of truth (products.params_json); MongoDB is
the read-optimized parameter document store keyed by integer product id. When
MONGODB_URL is unset or the server is unreachable, callers transparently fall
back to the MySQL params_json column so publishing and detail reads never fail.
The database name is namespaced per catalog database to isolate test runs.
"""
import asyncio
import json
import logging
import os
import time

LOGGER = logging.getLogger(__name__)
_disabled_until = 0.0
_warned_until = 0.0
_store = None
_store_key = None
_store_loop = None


def namespace(database_path):
    import hashlib
    return hashlib.sha256(str(database_path.resolve()).encode()).hexdigest()[:12]


def parse_params(raw):
    try:
        value = json.loads(raw or '[]')
    except (ValueError, TypeError):
        return []
    if not isinstance(value, list):
        return []
    return [{'name': str(item.get('name', ''))[:40], 'value': str(item.get('value', ''))[:200]}
            for item in value if isinstance(item, dict)]


class ProductParams:
    def __init__(self, url, database):
        import motor.motor_asyncio as motor
        self.client = motor.AsyncIOMotorClient(url, serverSelectionTimeoutMS=1500, connectTimeoutMS=1500)
        self.collection = self.client[database]['product_params']

    async def get_many(self, product_ids):
        if not product_ids:
            return {}
        cursor = self.collection.find({'_id': {'$in': list(product_ids)}})
        return {doc['_id']: doc.get('params', []) async for doc in cursor}

    async def set(self, product_id, params):
        await self.collection.replace_one({'_id': product_id}, {'_id': product_id, 'params': params}, upsert=True)

    async def delete(self, product_id):
        await self.collection.delete_one({'_id': product_id})

    async def close(self):
        self.client.close()


def store(database_path):
    global _store, _store_key, _store_loop
    url = os.getenv('MONGODB_URL', '').strip()
    if not url or time.monotonic() < _disabled_until:
        return None
    database = os.getenv('MONGODB_DATABASE', '').strip() or 'heima_params_' + namespace(database_path)
    key = (url, database)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    # Motor binds its client to the creating event loop; the legacy WSGI adapter
    # runs a fresh loop per request, so rebuild whenever the loop changes.
    if _store is not None and (_store_key != key or _store_loop is not loop):
        _store.close()
        _store, _store_key, _store_loop = None, None, None
    if _store is None:
        try:
            import motor  # noqa: F401
        except ImportError:
            LOGGER.warning('MongoDB参数存储未启用：请安装requirements.txt中的motor依赖')
            return None
        try:
            _store, _store_key, _store_loop = ProductParams(url, database), key, loop
        except Exception:
            _trip()
            return None
    return _store


def _trip():
    global _disabled_until, _warned_until
    now = time.monotonic()
    if now >= _warned_until:
        LOGGER.warning('MongoDB暂时不可用，商品参数已降级为MySQL params_json')
        _warned_until = now + 30
    _disabled_until = now + 5


async def read_params(row, database_path):
    """MongoDB first, MySQL params_json fallback."""
    instance = store(database_path)
    if instance is not None:
        try:
            found = await instance.get_many([row['id']])
            if row['id'] in found:
                return found[row['id']]
        except Exception:
            _trip()
    return parse_params(row.get('params_json'))


async def write_params(product_id, params, database_path):
    instance = store(database_path)
    if instance is None:
        return False
    try:
        await instance.set(product_id, params)
        return True
    except Exception:
        _trip()
        return False


async def delete_params(product_id, database_path):
    instance = store(database_path)
    if instance is None:
        return False
    try:
        await instance.delete(product_id)
        return True
    except Exception:
        _trip()
        return False


async def close():
    global _store, _store_key, _store_loop
    if _store is not None:
        _store.close()
        _store, _store_key, _store_loop = None, None, None