"""Measure labeled image-search quality against the current live catalog.

Dataset JSONL format (paths are relative to the dataset file when not absolute):
    {"image": "queries/item-01.jpg", "relevant_product_ids": [12, 18]}
Optional per-query filters: "category" and "store_id".
"""
import argparse
import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.async_contract import SyncConnectionAdapter
from app.config import load_environment
from app.database import Database
from app.visual_search import VisualSearch, read_only_catalog_storage


def read_dataset(path):
    path = Path(path).expanduser().resolve()
    records = []
    for line_number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            image = Path(record['image']).expanduser()
            if not image.is_absolute():
                image = path.parent / image
            ids = record['relevant_product_ids']
            if (not isinstance(ids, list) or not ids
                    or any(type(value) is not int or value < 1 for value in ids)):
                raise ValueError('relevant_product_ids必须为正整数数组')
            category = record.get('category', '')
            if not isinstance(category, str):
                raise ValueError('category必须为字符串')
            store_id = record.get('store_id')
            if store_id is not None and (type(store_id) is not int or store_id < 1):
                raise ValueError('store_id必须为正整数')
            if not image.is_file():
                raise ValueError(f'查询图片不存在：{image}')
            records.append({'image': image, 'relevant': set(ids),
                            'category': category, 'store_id': store_id,
                            'line_number': line_number})
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise ValueError(f'数据集第 {line_number} 行无效：{error}') from error
    if not records:
        raise ValueError('数据集没有有效查询行')
    return records


def metrics_at_k(ranked_ids, relevant_ids, k):
    ranked = list(ranked_ids)[:k]
    relevant = set(relevant_ids)
    hits = sum(product_id in relevant for product_id in ranked)
    first = next((rank for rank, product_id in enumerate(ranked, 1)
                  if product_id in relevant), None)
    return {
        'hit_rate': float(hits > 0),
        'precision': hits / k,
        'recall': hits / len(relevant) if relevant else 0.0,
        'mrr': 1 / first if first is not None else 0.0,
    }


@contextmanager
def read_only_connection(database):
    """Open an explicit read-only transaction for one evaluation query."""
    with database.connection() as raw_connection:
        if getattr(database, 'backend', 'sqlite') == 'mysql':
            raw_connection.execute('START TRANSACTION READ ONLY')
        else:
            raw_connection.execute('PRAGMA query_only=ON')
            raw_connection.execute('BEGIN')
        try:
            yield SyncConnectionAdapter(raw_connection, database.path)
        except BaseException:
            raw_connection.execute('ROLLBACK')
            raise
        else:
            raw_connection.execute('COMMIT')


async def available_product_ids(connection, product_ids, category='', store_id=None):
    """Apply the live storefront eligibility and optional query filters."""
    if not product_ids:
        return set()
    placeholders = ','.join('?' for _ in product_ids)
    filters = [
        'is_active=1', 'stock>0',
        'store_id IN (SELECT id FROM stores WHERE is_active=1)',
        f'id IN ({placeholders})',
    ]
    arguments = list(product_ids)
    if category:
        filters.append('category=?')
        arguments.append(category)
    if store_id is not None:
        filters.append('store_id=?')
        arguments.append(store_id)
    rows = await connection.execute(
        'SELECT id FROM products WHERE ' + ' AND '.join(filters), arguments)
    return {row['id'] for row in rows.fetchall()}


async def evaluate(records, database, visual_search, storage, k):
    totals = {'hit_rate': 0.0, 'precision': 0.0, 'recall': 0.0, 'mrr': 0.0}
    details = []
    for record in records:
        image_bytes = record['image'].read_bytes()
        with read_only_connection(database) as connection:
            relevant_available = await available_product_ids(
                connection, record['relevant'], record['category'], record['store_id'])
            missing_relevance = record['relevant'] - relevant_available
            if missing_relevance:
                line_number = record.get('line_number', '?')
                raise ValueError(
                    f'数据集第 {line_number} 行的相关商品不在当前上架、有库存、营业店铺及筛选范围内：'
                    f'{sorted(missing_relevance)}')
            candidates = await visual_search.search_image(
                image_bytes, connection, storage, record['category'], record['store_id'])
            available = await available_product_ids(
                connection, candidates, record['category'], record['store_id'])
            ranked = [product_id for product_id in candidates if product_id in available]
        values = metrics_at_k(ranked, record['relevant'], k)
        for metric, value in values.items():
            totals[metric] += value
        details.append({'image': str(record['image']), 'relevant_product_ids': sorted(record['relevant']),
                        'top_k': ranked[:k], **values})
    count = len(records)
    return {'queries': count, 'k': k,
            **{f'{metric}_at_{k}': value / count for metric, value in totals.items()},
            'per_query': details}


def read_only_storage(root):
    """Build image storage without probing or creating a remote bucket."""
    return read_only_catalog_storage(root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dataset', type=Path, help='标注 JSONL 文件')
    parser.add_argument('--k', type=int, default=10, help='评估前 K 个结果（默认 10）')
    arguments = parser.parse_args()
    if not 1 <= arguments.k <= 1000:
        parser.error('--k必须在1–1000之间')
    records = read_dataset(arguments.dataset)
    load_environment()
    database = Database()
    if (getattr(database, 'backend', 'mysql') == 'sqlite'
            and not Path(database.path).expanduser().is_file()):
        raise RuntimeError('商城 SQLite 数据库不存在，请先启动应用完成初始化')
    storage = read_only_storage(database.path.parent / 'uploads')
    visual_search = VisualSearch.from_environment()
    try:
        if not visual_search.enabled:
            raise RuntimeError('本地图片特征索引不可用，请先运行 scripts/rebuild_visual_index.py')
        result = asyncio.run(evaluate(records, database, visual_search, storage, arguments.k))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        visual_search.close()


if __name__ == '__main__':
    main()
