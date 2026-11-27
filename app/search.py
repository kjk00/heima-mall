"""Optional Elasticsearch read path for catalog search; MySQL/SQLite stays authoritative.

Enabled by ELASTICSEARCH_URL. Documents are fed asynchronously through the
outbox -> RabbitMQ -> bulk pipeline in app.search_sync. Any ES failure trips a
short breaker and the caller falls back to the SQL LIKE query.
"""
import hashlib
import logging
import os
import time

LOGGER = logging.getLogger(__name__)
SORTS = {
    'recommended': [{'id': 'asc'}],
    'newest': [{'id': 'desc'}],
    'popular': [{'sales': 'desc'}, {'id': 'asc'}],
    'price_asc': [{'price': 'asc'}, {'id': 'asc'}],
    'price_desc': [{'price': 'desc'}, {'id': 'asc'}],
}
# Elasticsearch refuses from+size beyond this window; deeper pages use SQL.
MAX_RESULT_WINDOW = 10000


def sync_enabled():
    return bool(os.getenv('ELASTICSEARCH_URL', '').strip() and os.getenv('RABBITMQ_URL', '').strip())


def namespace(database_path):
    return hashlib.sha256(str(database_path.resolve()).encode()).hexdigest()[:12]


def index_alias(database_path):
    return os.getenv('ELASTICSEARCH_ALIAS', 'heima_products') + '-' + namespace(database_path)


def sync_queue(database_path):
    return 'heima.product.sync.' + namespace(database_path)


def escape_wildcard(value):
    return value.replace('\\', '\\\\').replace('*', '\\*').replace('?', '\\?')


def document(row, params=None):
    from app.html_sanitize import text_content
    from app.mongodb import parse_params
    data = dict(row)
    if params is None:
        params = parse_params(data.get('params_json'))
    params_text = ' '.join(f"{item['name']} {item['value']}" for item in params)
    return {
        'id': data['id'], 'name': data['name'], 'subtitle': data['subtitle'],
        'category': data['category'], 'store_id': data['store_id'],
        'price': data['price'], 'original_price': data['original_price'],
        'sales': data['sales'], 'version': data['version'],
        'is_active': bool(data['is_active']),
        'on_sale': data['original_price'] > data['price'],
        'description': data.get('description', ''),
        'detail_text': text_content(data.get('detail_html', '')),
        'params_text': params_text,
    }


def index_body():
    return {
        'settings': {'number_of_shards': 1, 'number_of_replicas': 0, 'refresh_interval': '1s'},
        'mappings': {'properties': {
            'id': {'type': 'long'},
            'name': {'type': 'wildcard'},
            'subtitle': {'type': 'wildcard'},
            'category': {'type': 'keyword'},
            'store_id': {'type': 'long'},
            'price': {'type': 'long'},
            'original_price': {'type': 'long'},
            'sales': {'type': 'long'},
            'version': {'type': 'long'},
            'is_active': {'type': 'boolean'},
            'on_sale': {'type': 'boolean'},
            'description': {'type': 'wildcard'},
            'detail_text': {'type': 'wildcard'},
            'params_text': {'type': 'wildcard'},
        }},
    }


def search_arguments(arguments, active_store_ids, matched_store_ids):
    """Mirror logic.products' WHERE/ORDER semantics; store names resolve live in SQL."""
    filters = [{'term': {'is_active': True}}, {'terms': {'store_id': active_store_ids}}]
    if arguments['store_id'] is not None:
        filters.append({'term': {'store_id': arguments['store_id']}})
    if arguments['on_sale']:
        filters.append({'term': {'on_sale': True}})
    if arguments['category']:
        filters.append({'term': {'category': arguments['category']}})
    query = {'bool': {'filter': filters}}
    keyword = arguments['q'].strip()
    if keyword:
        pattern = '*' + escape_wildcard(keyword) + '*'
        should = [{'wildcard': {'name': {'value': pattern, 'case_insensitive': True}}},
                  {'wildcard': {'subtitle': {'value': pattern, 'case_insensitive': True}}},
                  {'wildcard': {'description': {'value': pattern, 'case_insensitive': True}}},
                  {'wildcard': {'detail_text': {'value': pattern, 'case_insensitive': True}}},
                  {'wildcard': {'params_text': {'value': pattern, 'case_insensitive': True}}}]
        if matched_store_ids:
            should.append({'terms': {'store_id': matched_store_ids}})
        query['bool']['must'] = [{'bool': {'should': should, 'minimum_should_match': 1}}]
    size = arguments['page_size']
    return {
        'query': query,
        'sort': SORTS[arguments['sort']],
        'from_': (arguments['page'] - 1) * size,
        'size': size,
        'track_total_hits': True,
        'source': ['id'],
    }


def photo_search_arguments(keywords, active_store_ids, category='', store_id=None):
    """Build the Elasticsearch query used by photo recognition."""
    should = []
    for keyword in keywords:
        pattern = '*' + escape_wildcard(keyword) + '*'
        should.extend([
            {'wildcard': {'name': {'value': pattern, 'case_insensitive': True}}},
            {'wildcard': {'subtitle': {'value': pattern, 'case_insensitive': True}}},
            {'wildcard': {'category': {'value': pattern, 'case_insensitive': True}}},
        ])
    filters = [
            {'term': {'is_active': True}},
            {'terms': {'store_id': active_store_ids}},
        ]
    if category:
        filters.append({'term': {'category': category}})
    if store_id is not None:
        filters.append({'term': {'store_id': store_id}})
    return {
        'query': {'bool': {'filter': filters,
                           'must': [{'bool': {'should': should, 'minimum_should_match': 1}}]}},
        'sort': [{'sales': 'desc'}, {'id': 'asc'}],
        'from_': 0,
        'size': 13,
        'track_total_hits': False,
        'source': ['id'],
    }


class SearchEngine:
    def __init__(self, url, alias, timeout=2.0):
        self.url, self.alias, self.timeout = url, alias, timeout
        self._sync_client = None
        self._async_client = None
        self._disabled_until = 0.0
        self._warned_until = 0.0

    @classmethod
    def from_environment(cls, database_path):
        url = os.getenv('ELASTICSEARCH_URL', '').strip()
        if not url:
            return None
        try:
            import elasticsearch  # noqa: F401
        except ImportError:
            LOGGER.warning('ES搜索未启用：请安装requirements.txt中的elasticsearch依赖')
            return None
        timeout = float(os.getenv('SEARCH_ES_TIMEOUT_SECONDS', '2'))
        if not 0 < timeout <= 30:
            raise ValueError('SEARCH_ES_TIMEOUT_SECONDS必须大于0且不超过30秒')
        return cls(url, index_alias(database_path), timeout=timeout)

    def _trip(self):
        now = time.monotonic()
        if now >= self._warned_until:
            LOGGER.warning('Elasticsearch暂时不可用，搜索已降级为数据库查询')
            self._warned_until = now + 30
        self._disabled_until = now + 5

    def _client(self, connection):
        from elasticsearch import AsyncElasticsearch, Elasticsearch
        from app.async_contract import SyncConnectionAdapter
        # Legacy WSGI requests run one asyncio.run loop per thread, so an async
        # transport cannot be shared there; the sync client's pool is thread-safe.
        if isinstance(connection, SyncConnectionAdapter):
            if self._sync_client is None:
                self._sync_client = Elasticsearch(self.url, request_timeout=self.timeout,
                                                  max_retries=0, retry_on_timeout=False)
            return self._sync_client, True
        if self._async_client is None:
            self._async_client = AsyncElasticsearch(self.url, request_timeout=self.timeout,
                                                    max_retries=0, retry_on_timeout=False)
        return self._async_client, False

    async def read(self, connection, arguments):
        """Return {'total', 'ids'} in result order, or None to use the SQL path."""
        if time.monotonic() < self._disabled_until:
            return None
        if arguments['page'] * arguments['page_size'] > MAX_RESULT_WINDOW:
            return None
        try:
            active = [row['id'] for row in (await connection.execute('SELECT id FROM stores WHERE is_active=1')).fetchall()]
            matched = []
            keyword = arguments['q'].strip()
            if keyword:
                pattern = '%' + keyword.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
                matched = [row['id'] for row in (await connection.execute(
                    "SELECT id FROM stores WHERE name LIKE ? ESCAPE '\\'", (pattern,))).fetchall()]
            body = search_arguments(arguments, active, matched)
            client, synchronous = self._client(connection)
            if synchronous:
                response = client.search(index=self.alias, **body)
            else:
                response = await client.search(index=self.alias, **body)
            hits = response['hits']
            total = hits['total']
            if isinstance(total, dict):
                total = total['value']
            return {'total': total, 'ids': [hit['_source']['id'] for hit in hits['hits']]}
        except Exception:
            self._trip()
            return None

    async def read_photo(self, connection, keywords, category='', store_id=None):
        """Return photo-match ids from ES, or None to use the SQL fallback."""
        if not keywords or time.monotonic() < self._disabled_until:
            return None
        try:
            active = [row['id'] for row in (await connection.execute(
                'SELECT id FROM stores WHERE is_active=1')).fetchall()]
            if not active:
                return {'ids': []}
            body = photo_search_arguments(keywords, active, category, store_id)
            client, synchronous = self._client(connection)
            if synchronous:
                response = client.search(index=self.alias, **body)
            else:
                response = await client.search(index=self.alias, **body)
            hits = response['hits']
            return {'ids': [hit['_source']['id'] for hit in hits['hits']]}
        except Exception:
            self._trip()
            return None

    async def close(self):
        if self._sync_client is not None:
            self._sync_client.close()
            self._sync_client = None
        if self._async_client is not None:
            await self._async_client.close()
            self._async_client = None
