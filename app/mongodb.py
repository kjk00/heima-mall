"""MongoDB store for merchant product parameters (商品参数).

MySQL stays the transactional source of truth (products.params_json); MongoDB is
the read-optimized parameter document store keyed by integer product id. When
MONGODB_URL is unset or the server is unreachable, callers transparently fall
back to the MySQL params_json column so publishing and detail reads never fail.
Reads validate the replica against the SQL snapshot and repair drift; versioned
atomic writes prevent a delayed mirror or repair from replacing a newer document.
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

    async def get_documents(self, product_ids):
        if not product_ids:
            return {}
        cursor = self.collection.find({'_id': {'$in': list(product_ids)}}, {'params': 1, 'version': 1})
        return {doc['_id']: doc async for doc in cursor}

    async def get_many(self, product_ids):
        return {key: doc.get('params', []) for key, doc in (await self.get_documents(product_ids)).items()}

    async def set(self, product_id, params, version):
        # Match only _id: a version predicate with upsert would try to insert a
        # duplicate _id when a newer document already exists. Compare and update
        # inside one atomic operation instead; legacy/malformed versions migrate.
        stored_version = {'$cond': [{'$in': [{'$type': '$version'}, ['int', 'long']]}, '$version', -1]}
        can_update = {'$lte': [stored_version, version]}
        await self.collection.update_one({'_id': product_id}, [{'$set': {
            'params': {'$cond': [can_update, {'$literal': params}, '$params']},
            'version': {'$cond': [can_update, version, '$version']},
        }}], upsert=True)

    async def delete(self, product_id):
        await self.collection.delete_one({'_id': product_id})

    def close(self):
        """Motor's client close is synchronous; do not create an unawaited coroutine."""
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


async def read_many_params(rows, database_path):
    """Use the SQL snapshot as authority; best-effort repair of the Mongo replica."""
    rows = [dict(row) for row in rows]
    params = {row['id']: parse_params(row.get('params_json')) for row in rows}
    if not rows:
        return params
    instance = store(database_path) if database_path is not None else None
    if instance is None:
        return params
    try:
        found = await instance.get_documents(list(params))
    except Exception:
        _trip()
        return params
    for row in rows:
        product_id, version = row['id'], row.get('version', 0)
        doc = found.get(product_id)
        if doc is not None:
            replica_version = doc.get('version')
            # The SQL read may precede a concurrent commit. Return its consistent
            # snapshot, but do not use it to downgrade a newer Mongo document.
            if isinstance(replica_version, int) and not isinstance(replica_version, bool) and replica_version > version:
                continue
            if replica_version == version and doc.get('params') == params[product_id]:
                continue
        try:
            await instance.set(product_id, params[product_id], version)
        except Exception:
            _trip()
            break
    return params


async def read_params(row, database_path):
    return (await read_many_params([row], database_path))[row['id']]


async def write_params(product_id, params, database_path, version):
    instance = store(database_path)
    if instance is None:
        return False
    try:
        await instance.set(product_id, params, version)
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
