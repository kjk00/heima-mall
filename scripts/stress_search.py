"""Search stress on a 10k-product catalog: unique hit vs keyword group, in-process WSGI/ASGI."""
import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, suppress
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import platform
import sqlite3
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['REDIS_URL'] = ''

from app.database import transaction
from app.server import MallApplication
from tests.client import Client

CATEGORIES = ['数码科技', '服饰箱包', '运动户外', '家居生活', '美妆个护', '食品饮料']
UNIQUE_NAME = '星陨限定礼盒 X9'
UNIQUE_QUERY = '星陨限定礼盒'
KEYWORD = '耳机'


def distribution(values):
    if not values:
        return None
    values = sorted(values)
    percentile = lambda p: round(values[max(0, math.ceil(len(values) * p) - 1)], 3)
    return {'min_ms': round(values[0], 3), 'mean_ms': round(statistics.mean(values), 3),
            'p50_ms': percentile(.5), 'p95_ms': percentile(.95), 'p99_ms': percentile(.99),
            'max_ms': round(values[-1], 3)}


@contextmanager
def sqlite_connection(path):
    connection = sqlite3.connect(path, timeout=10, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


def catalog_rows(stores, total, group):
    yield (1, UNIQUE_NAME, '独一无二的压测目标商品', '家居生活', 88800, 99800, 66,
           '/static/images/candle.svg', '限定', '#336699', '压力测试唯一目标商品', 88, 1, 1, stores[0])
    for i in range(2, group + 2):
        yield (i, f'降噪{KEYWORD} Pro {i:05d}', f'同类关键词商品 {i}', '数码科技',
               19900 + (i * 37) % 40000, 59900 + (i * 37) % 40000, 50 + i % 500,
               '/static/images/headphones.svg', '', '#224466', '压力测试同类商品', (i * 13) % 5000, 1, 1, stores[i % len(stores)])
    for i in range(group + 2, total + 1):
        category = CATEGORIES[i % len(CATEGORIES)]
        yield (i, f'{category} 日常好物 {i:05d}', f'第 {i} 号压测商品', category,
               1000 + (i * 37) % 90000, 2000 + (i * 37) % 90000, 50 + i % 500,
               '/static/images/sneakers.svg', '', '#336699', '压力测试普通商品', (i * 13) % 5000, 1, 1, stores[i % len(stores)])


def seed(connect, total, group):
    with connect() as connection, transaction(connection):
        connection.execute('DELETE FROM products')
        stores = [row['id'] for row in connection.execute('SELECT id FROM stores ORDER BY id')]
        # Bulk load would fire the catalog revision trigger once per row; bump it once instead.
        for event in ('insert', 'update', 'delete'):
            connection.execute(f'DROP TRIGGER IF EXISTS catalog_cache_products_{event}')
            connection.execute(f'DROP TRIGGER IF EXISTS product_sync_{event}')
        connection.executemany('''INSERT INTO products(id,name,subtitle,category,price,original_price,stock,image,
            badge,color,description,sales,is_active,version,store_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                             catalog_rows(stores, total, group))
        for event in ('INSERT', 'UPDATE', 'DELETE'):
            connection.execute(f'''CREATE TRIGGER IF NOT EXISTS catalog_cache_products_{event.lower()}
                AFTER {event} ON products BEGIN
                UPDATE catalog_cache_state SET revision=revision+1 WHERE id=1; END''')
        from app.search import sync_enabled
        if sync_enabled():
            for event, reference in (('INSERT', 'NEW'), ('UPDATE', 'NEW'), ('DELETE', 'OLD')):
                connection.execute(f'''CREATE TRIGGER IF NOT EXISTS product_sync_{event.lower()}
                    AFTER {event} ON products BEGIN
                    INSERT INTO product_sync_outbox(product_id) VALUES ({reference}.id); END''')
        connection.execute('UPDATE catalog_cache_state SET revision=revision+1 WHERE id=1')
        counts = {row['category']: row['n'] for row in connection.execute('SELECT category,COUNT(*) AS n FROM products GROUP BY category')}
        # Fold the WAL into the main file so other connections (ES sync worker) see the full catalog.
        with suppress(sqlite3.OperationalError):
            connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    return counts


async def build_index(database):
    from elasticsearch import AsyncElasticsearch
    from app.search import index_alias
    from app.search_sync import full_reindex
    es = AsyncElasticsearch(os.environ['ELASTICSEARCH_URL'], request_timeout=120)
    try:
        started = time.perf_counter()
        target = await full_reindex(database, es, index_alias(database.path))
        return {'index': target, 'reindex_seconds': round(time.perf_counter() - started, 3)}
    finally:
        await es.close()


async def wait_for_index(path, expected, timeout=180):
    """Let the running sync worker own reindexing; poll until the alias holds one index of the right size."""
    from elasticsearch import AsyncElasticsearch
    from app.search import index_alias
    es = AsyncElasticsearch(os.environ['ELASTICSEARCH_URL'], request_timeout=30)
    alias = index_alias(path)
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if await es.indices.exists_alias(name=alias):
                indices = list((await es.indices.get_alias(name=alias)).keys())
                if len(indices) == 1 and (await es.count(index=alias))['count'] == expected:
                    return {'index': indices[0], 'docs': expected}
            await asyncio.sleep(0.5)
        raise RuntimeError(f'ES 索引未在 {timeout}s 内就绪（期望 {expected} 件）')
    finally:
        await es.close()


async def drop_sync_resources(path):
    from elasticsearch import AsyncElasticsearch
    from app.search import index_alias, sync_queue
    es = AsyncElasticsearch(os.environ['ELASTICSEARCH_URL'], request_timeout=30)
    try:
        alias = index_alias(path)
        if await es.indices.exists_alias(name=alias):
            indices = list((await es.indices.get_alias(name=alias)).keys())
            if indices:
                await es.indices.delete(index=','.join(indices))
    finally:
        await es.close()
    import aio_pika
    connection = await aio_pika.connect_robust(os.environ['RABBITMQ_URL'])
    try:
        channel = await connection.channel()
        try:
            queue = await channel.declare_queue(sync_queue(path), durable=True, passive=True)
        except aio_pika.exceptions.ChannelNotFoundEntity:
            return
        await queue.delete(if_empty=False, if_unused=False)
    finally:
        await connection.close()


def summarize(rows, concurrency, requests, elapsed):
    failures = [row for row in rows if not row[1]]
    return {'concurrency': concurrency, 'requests': requests, 'failures': len(failures),
            'failure_samples': [{'status': row[2], 'total': row[3]} for row in failures[:5]],
            'seconds': round(elapsed, 3), 'rps': round(requests / elapsed, 1),
            'latency': distribution([row[0] for row in rows])}


def run_level(app, path, expected_total, expected_items, concurrency, requests, warmup=5):
    client = Client(app)
    for _ in range(warmup):
        client.get(path)

    def one(_):
        started = time.perf_counter()
        response = client.get(path)
        elapsed = (time.perf_counter() - started) * 1000
        payload = response['json'] or {}
        ok = response['status'] == 200 and payload.get('total') == expected_total and len(payload.get('items', [])) == expected_items
        return elapsed, ok, response['status'], payload.get('total')

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix='search') as pool:
        rows = list(pool.map(one, range(requests)))
    return summarize(rows, concurrency, requests, time.perf_counter() - started)


async def run_level_async(client, path, expected_total, expected_items, concurrency, requests, warmup=5):
    for _ in range(warmup):
        await client.get(path)
    gate = asyncio.Semaphore(concurrency)

    async def one(_):
        async with gate:
            started = time.perf_counter()
            response = await client.get(path)
            elapsed = (time.perf_counter() - started) * 1000
            payload = response.json() if response.status_code == 200 else {}
            ok = response.status_code == 200 and payload.get('total') == expected_total and len(payload.get('items', [])) == expected_items
            return elapsed, ok, response.status_code, payload.get('total')

    started = time.perf_counter()
    rows = await asyncio.gather(*(one(i) for i in range(requests)))
    return summarize(rows, concurrency, requests, time.perf_counter() - started)


def scenarios_for(args):
    scenarios = [
        {'key': 'unique', 'name': '唯一商品搜索', 'path': f'/api/products?q={UNIQUE_QUERY}', 'total': 1, 'items': 1,
         'description': f'{args.products} 件商品中按名称定位唯一商品（q={UNIQUE_QUERY}，命中 1 件）'},
        {'key': 'keyword', 'name': '同类关键词搜索', 'path': f'/api/products?q={KEYWORD}', 'total': args.group, 'items': 12,
         'description': f'关键词搜索同类商品（q={KEYWORD}，命中 {args.group} 件，取第 1 页）'},
        {'key': 'deep', 'name': '同类关键词深翻页', 'path': f'/api/products?q={KEYWORD}&page={args.deep_page}', 'total': args.group, 'items': 12,
         'description': f'同类关键词搜索第 {args.deep_page} 页（OFFSET {(args.deep_page - 1) * 12}）'},
        {'key': 'browse', 'name': '无关键词浏览（对照）', 'path': '/api/products', 'total': args.products, 'items': 12,
         'description': '不带关键词的目录浏览，作为搜索开销对照'},
    ]
    return [s for s in scenarios if not args.only or s['key'] in args.only]


def run_stdlib(args, report):
    from app.search import sync_enabled
    with tempfile.TemporaryDirectory(prefix='heima-search-stress-') as directory:
        path = Path(directory) / 'stress.db'
        app = MallApplication(path)
        report['catalog']['categories'] = seed(app.state.database.connection, args.products, args.group)
        print(f'目录就绪：{args.products} 件商品，同类关键词 {args.group} 件', flush=True)
        if sync_enabled():
            from app.async_contract import SyncDatabaseAdapter
            report['search_index'] = asyncio.run(build_index(SyncDatabaseAdapter(app.state.database)))
            print(f"ES 索引就绪：{report['search_index']['index']}（重建耗时 {report['search_index']['reindex_seconds']}s）", flush=True)
        warmup = 5 if args.products <= 100000 else 2
        try:
            for scenario in scenarios_for(args):
                levels = []
                for concurrency in args.concurrency:
                    level = run_level(app, scenario['path'], scenario['total'], scenario['items'], concurrency, args.requests, warmup)
                    levels.append(level)
                    print(f"{scenario['name']} 并发 {concurrency}：{level['rps']} req/s，P95 {level['latency']['p95_ms']} ms，失败 {level['failures']}", flush=True)
                report['scenarios'].append({key: scenario[key] for key in ('name', 'path', 'description')} | {'levels': levels})
        finally:
            if sync_enabled():
                asyncio.run(drop_sync_resources(path))


async def run_fastapi(args, report):
    import httpx
    from app.main import create_app
    from app.search import sync_enabled
    if sync_enabled():
        # The lifespan worker is the sole reindexer; speed up its reconcile so the
        # post-seed catalog is indexed quickly, and never reindex concurrently here.
        os.environ['SEARCH_RECONCILE_SECONDS'] = '2'
        os.environ['SEARCH_SYNC_SECONDS'] = '0.5'
    with tempfile.TemporaryDirectory(prefix='heima-search-stress-') as directory:
        path = Path(directory) / 'stress.db'
        application = create_app(path)
        async with application.router.lifespan_context(application):
            report['catalog']['categories'] = seed(lambda: sqlite_connection(path), args.products, args.group)
            print(f'目录就绪：{args.products} 件商品，同类关键词 {args.group} 件', flush=True)
            if sync_enabled():
                started = time.perf_counter()
                info = await wait_for_index(path, args.products)
                info['reindex_seconds'] = round(time.perf_counter() - started, 3)
                report['search_index'] = info
                print(f"ES 索引就绪：{info['index']}（worker 重建耗时 {info['reindex_seconds']}s）", flush=True)
            warmup = 5 if args.products <= 100000 else 2
            transport = httpx.ASGITransport(app=application)
            async with httpx.AsyncClient(transport=transport, base_url='http://testserver',
                                         headers={'X-Requested-With': 'HeimaMall'}) as client:
                for scenario in scenarios_for(args):
                    levels = []
                    for concurrency in args.concurrency:
                        level = await run_level_async(client, scenario['path'], scenario['total'], scenario['items'], concurrency, args.requests, warmup)
                        levels.append(level)
                        print(f"{scenario['name']} 并发 {concurrency}：{level['rps']} req/s，P95 {level['latency']['p95_ms']} ms，失败 {level['failures']}", flush=True)
                    report['scenarios'].append({key: scenario[key] for key in ('name', 'path', 'description')} | {'levels': levels})
        if sync_enabled():
            await drop_sync_resources(path)


def write_report(report, args):
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    markdown = args.output.with_suffix('.md')
    es = report['search_engine'] == 'elasticsearch'
    engine_text = ('搜索走 Elasticsearch（wildcard 查询，事务 outbox→RabbitMQ 同步链路已建索引），命中 id 回表取最新行；'
                   if es else '搜索走数据库 LIKE 扫描；')
    lines = [f'# {args.products} 件商品搜索压力测试', '',
             f"测试时间：{report['timestamp_utc']}。Python {report['environment']['python']}，{report['environment']['os']}，{report['environment']['cpu_count']} 核。后端：{report['mode']}。搜索引擎：{report['search_engine']}。", '',
             f"独立临时 SQLite 数据库，商品 {args.products} 件（其中含关键词「{KEYWORD}」的同类商品 {args.group} 件，唯一目标商品 1 件）。"
             '通过真实请求入口（stdlib 为 WSGI 多线程，fastapi 为 ASGI 异步并发）调用 GET /api/products，保留参数校验、'
             + engine_text + 'COUNT 统计、分页与店铺信息装配；'
             '未启用 Redis 目录缓存，测得的是搜索引擎本身的速度；不包含 TCP 与前端渲染。每档并发预热后计数。', '']
    if es and 'search_index' in report:
        lines += [f"压测前全量重建索引耗时 {report['search_index']['reindex_seconds']}s（{args.products} 件商品）。", '']
    lines += ['| 场景 | 并发 | 请求数 | 失败 | 吞吐(req/s) | P50(ms) | P95(ms) | P99(ms) | 最大(ms) |',
              '| --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    for scenario in report['scenarios']:
        for level in scenario['levels']:
            latency = level['latency']
            lines.append(f"| {scenario['name']} | {level['concurrency']} | {level['requests']} | {level['failures']} | {level['rps']} | "
                         f"{latency['p50_ms']} | {latency['p95_ms']} | {latency['p99_ms']} | {latency['max_ms']} |")
    lines += ['', '## 场景说明', '']
    for scenario in report['scenarios']:
        lines.append(f"- {scenario['name']}：{scenario['description']}。")
    env_prefix = ('ELASTICSEARCH_URL=http://127.0.0.1:9200 RABBITMQ_URL=amqp://guest:guest@127.0.0.1:5672/ '
                  'DATABASE_BACKEND=sqlite ') if es else ''
    output_label = args.output
    if args.output.is_absolute():
        try:
            output_label = args.output.relative_to(ROOT)
        except ValueError:
            pass
    lines += ['', '## 复现命令', '',
              f"```bash\n{env_prefix}.venv/bin/python scripts/stress_search.py --backend {args.backend} --products {args.products} --group {args.group} "
              f"--concurrency {' '.join(str(c) for c in args.concurrency)} --requests {args.requests} "
              + (f"--only {' '.join(args.only)} " if args.only else '')
              + f"--output {output_label}\n```", '',
              f'逐档明细见 `{args.output.name}`。']
    markdown.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return markdown


def main():
    parser = argparse.ArgumentParser(description='独立数据库：万件商品目录的搜索压力测试')
    parser.add_argument('--backend', choices=['stdlib', 'fastapi'], default='stdlib')
    parser.add_argument('--products', type=int, default=10000)
    parser.add_argument('--group', type=int, default=1200, help='含关键词"耳机"的同类商品数量')
    parser.add_argument('--deep-page', type=int, default=50)
    parser.add_argument('--only', nargs='+', choices=['unique', 'keyword', 'deep', 'browse'], help='只跑指定场景')
    parser.add_argument('--concurrency', type=int, nargs='+', default=[1, 16, 64])
    parser.add_argument('--requests', type=int, default=200, help='每个场景每并发档的请求数')
    parser.add_argument('--output', type=Path, default=ROOT / 'reports/stress-search.json')
    args = parser.parse_args()
    if args.group < args.deep_page * 12 or args.products < args.group + 1 or min(args.concurrency) < 1:
        parser.error('同类商品需不少于深翻页偏移量，商品总数需大于同类商品数，并发需为正数')
    from app.search import sync_enabled
    report = {'timestamp_utc': datetime.now(timezone.utc).isoformat(),
              'mode': 'in-process WSGI + SQLite' if args.backend == 'stdlib' else 'in-process ASGI (FastAPI) + SQLite',
              'search_engine': 'elasticsearch' if sync_enabled() else 'database (LIKE)',
              'catalog_cache': 'disabled (REDIS_URL empty)',
              'environment': {'python': platform.python_version(), 'os': platform.platform(), 'cpu_count': os.cpu_count()},
              'catalog': {'products': args.products, 'keyword_group': args.group, 'keyword': KEYWORD},
              'scenarios': []}
    if args.backend == 'fastapi':
        asyncio.run(run_fastapi(args, report))
    else:
        run_stdlib(args, report)
    markdown = write_report(report, args)
    print(f'报告：{markdown}', flush=True)
    return 0 if all(level['failures'] == 0 for scenario in report['scenarios'] for level in scenario['levels']) else 1


if __name__ == '__main__':
    raise SystemExit(main())