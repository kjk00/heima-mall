"""手动全量重建 Elasticsearch 商品索引（新索引 + 别名原子切换）。

用法：先配置 ELASTICSEARCH_URL（及数据库相关变量），然后
    python scripts/reindex_search.py
"""
import asyncio
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import load_environment


async def main():
    load_environment()
    url = os.getenv('ELASTICSEARCH_URL', '').strip()
    if not url:
        raise SystemExit('请先设置 ELASTICSEARCH_URL')
    from elasticsearch import AsyncElasticsearch
    from app.database import Database
    from app.search import index_alias
    from app.search_sync import full_reindex
    database = Database()
    if hasattr(database, 'initialize'):
        database.initialize()
    es = AsyncElasticsearch(url, request_timeout=60, max_retries=1, retry_on_timeout=True)
    alias = index_alias(database.path)
    started = time.monotonic()
    try:
        target = await full_reindex(database, es, alias)
        count = await es.count(index=alias, query={'match_all': {}})
        print(f'重建完成：别名 {alias} -> {target}，共 {count["count"]} 件商品，耗时 {time.monotonic() - started:.1f}s')
    finally:
        await es.close()
        if hasattr(database, 'close'):
            database.close()


if __name__ == '__main__':
    asyncio.run(main())