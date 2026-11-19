"""Asynchronous MySQL/SQLite -> Elasticsearch product sync.

Reliability chain: product writes fire triggers into product_sync_outbox inside
the SAME transaction; a relay publishes drained ids to a durable RabbitMQ queue
with publisher confirms and only then deletes outbox rows; a consumer re-reads
current rows and bulk-writes ES, acking after success. Every step is idempotent,
so crashes and duplicate deliveries converge to the database state. A periodic
reconcile (count + version checksum) and startup check rebuild the index behind
an atomic alias swap when drift is detected.
"""
import asyncio
import json
import logging
import os
import secrets
import time
from contextlib import contextmanager, suppress

from app.search import document, index_alias, index_body, sync_queue

LOGGER = logging.getLogger(__name__)
RELAY_BATCH = 1000
BULK_BATCH = 1000


def settings():
    interval = float(os.getenv('SEARCH_SYNC_SECONDS', '2'))
    reconcile = float(os.getenv('SEARCH_RECONCILE_SECONDS', '300'))
    if not 0 < interval <= 60 or not 0 < reconcile <= 86400:
        raise ValueError('SEARCH_SYNC_SECONDS/SEARCH_RECONCILE_SECONDS配置无效')
    return interval, reconcile


async def params_for_rows(rows, path):
    """Index parameters consistent with each SQL row, repairing MongoDB drift."""
    from app import mongodb
    return await mongodb.read_many_params(rows, path)


async def fetch_documents(connection, product_ids):
    """Current DB state wins: existing rows upsert, vanished rows delete."""
    if not product_ids:
        return [], []
    placeholders = ','.join('?' for _ in product_ids)
    rows = (await connection.execute(
        f'SELECT * FROM products WHERE id IN ({placeholders})', tuple(product_ids))).fetchall()
    found = {row['id'] for row in rows}
    params = await params_for_rows(rows, getattr(connection, 'path', None))
    return [document(row, params[row['id']]) for row in rows], [i for i in product_ids if i not in found]


async def bulk_apply(es, alias, documents, deletes):
    operations = []
    for doc in documents:
        operations.append({'index': {'_index': alias, '_id': doc['id']}})
        operations.append(doc)
    for product_id in deletes:
        operations.append({'delete': {'_index': alias, '_id': product_id}})
    if not operations:
        return
    response = await es.bulk(operations=operations)
    if response.get('errors'):
        failed = [item for item in response['items'] if next(iter(item.values())).get('error')]
        raise RuntimeError(f'ES批量写入失败：{failed[:3]}')


async def relay_once(database, channel, queue_name):
    async with database.connection() as connection:
        rows = (await connection.execute(
            'SELECT seq,product_id FROM product_sync_outbox ORDER BY seq LIMIT ?', (RELAY_BATCH,))).fetchall()
        if not rows:
            return 0
        # One message per drained batch; the consumer re-reads current state, so
        # coalescing repeated edits of one product is safe and order-free.
        import aio_pika
        product_ids = list(dict.fromkeys(row['product_id'] for row in rows))
        await channel.default_exchange.publish(
            aio_pika.Message(json.dumps({'product_ids': product_ids}).encode(),
                             delivery_mode=aio_pika.DeliveryMode.PERSISTENT),
            routing_key=queue_name)
        await connection.execute('DELETE FROM product_sync_outbox WHERE seq<=?', (rows[-1]['seq'],))
        return len(rows)


async def consume_once(message, database, es, alias):
    payload = json.loads(message.body)
    async with database.connection() as connection:
        documents, deletes = await fetch_documents(connection, payload['product_ids'])
    await bulk_apply(es, alias, documents, deletes)


async def consumer_loop(queue, database, es, alias):
    async with queue.iterator() as messages:
        async for message in messages:
            try:
                await consume_once(message, database, es, alias)
            except Exception:
                LOGGER.warning('商品同步消费失败，消息将重新入队')
                await asyncio.sleep(1)
                await message.nack(requeue=True)
            else:
                await message.ack()


async def iter_product_documents(database, batch=BULK_BATCH):
    last_id = 0
    while True:
        async with database.connection() as connection:
            rows = (await connection.execute(
                'SELECT * FROM products WHERE id>? ORDER BY id LIMIT ?', (last_id, batch))).fetchall()
            path = getattr(connection, 'path', None)
        if not rows:
            return
        params = await params_for_rows(rows, path)
        for row in rows:
            yield document(row, params[row['id']])
        last_id = rows[-1]['id']


async def full_reindex(database, es, alias):
    """Build a fresh index, then swap the alias atomically and drop old indices."""
    target = f'{alias}-{secrets.token_hex(6)}'
    await es.indices.create(index=target, **index_body())
    try:
        operations = []
        async for doc in iter_product_documents(database):
            operations.append({'index': {'_index': target, '_id': doc['id']}})
            operations.append(doc)
            if len(operations) >= 2 * BULK_BATCH:
                response = await es.bulk(operations=operations)
                if response.get('errors'):
                    raise RuntimeError('ES重建索引批量写入失败')
                operations = []
        if operations:
            response = await es.bulk(operations=operations)
            if response.get('errors'):
                raise RuntimeError('ES重建索引批量写入失败')
        await es.indices.refresh(index=target)
        previous = []
        if await es.indices.exists_alias(name=alias):
            previous = list((await es.indices.get_alias(name=alias)).keys())
        actions = [{'add': {'index': target, 'alias': alias}}]
        actions += [{'remove': {'index': old, 'alias': alias}} for old in previous]
        await es.indices.update_aliases(actions=actions)
        for old in previous:
            await es.indices.delete(index=old)
    except BaseException:
        with suppress(Exception):
            await es.indices.delete(index=target)
        raise
    LOGGER.info('商品搜索索引重建完成：%s', target)
    return target


async def reconcile_once(database, es, alias):
    if not await es.indices.exists_alias(name=alias):
        await full_reindex(database, es, alias)
        return True
    async with database.connection() as connection:
        row = (await connection.execute('SELECT COUNT(*) AS total,COALESCE(SUM(version),0) AS checksum FROM products')).fetchone()
    count = await es.count(index=alias, query={'match_all': {}})
    aggregation = await es.search(index=alias, size=0, aggs={'checksum': {'sum': {'field': 'version'}}},
                                  track_total_hits=True)
    es_checksum = int(aggregation['aggregations']['checksum']['value'] or 0)
    if count['count'] != row['total'] or es_checksum != row['checksum']:
        LOGGER.warning('商品搜索索引与数据库不一致（%s/%s vs %s/%s），触发重建',
                       count['count'], es_checksum, row['total'], row['checksum'])
        await full_reindex(database, es, alias)
        return True
    return False


async def run_search_sync(database, should_stop=lambda: False):
    """Long-running worker; safe to cancel. Retries setup while ES/MQ are down."""
    import aio_pika
    from elasticsearch import AsyncElasticsearch
    url = os.environ['ELASTICSEARCH_URL'].strip()
    broker = os.environ['RABBITMQ_URL'].strip()
    alias, queue_name = index_alias(database.path), sync_queue(database.path)
    interval, reconcile_interval = settings()
    while not should_stop():
        es = AsyncElasticsearch(url, request_timeout=30, max_retries=1, retry_on_timeout=True)
        connection = None
        try:
            connection = await aio_pika.connect_robust(broker)
            channel = await connection.channel(publisher_confirms=True)
            queue = await channel.declare_queue(queue_name, durable=True)
            await reconcile_once(database, es, alias)
            consumer = asyncio.create_task(consumer_loop(queue, database, es, alias))
            last_reconcile = time.monotonic()
            try:
                while not should_stop():
                    if consumer.done():
                        consumer.result()
                    try:
                        await relay_once(database, channel, queue_name)
                        if time.monotonic() - last_reconcile >= reconcile_interval:
                            await reconcile_once(database, es, alias)
                            last_reconcile = time.monotonic()
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        LOGGER.warning('商品同步中继暂时失败，将在下一轮重试', exc_info=True)
                    await asyncio.sleep(interval)
            finally:
                consumer.cancel()
                # Cleanup errors must not mask the original exception (a masked
                # CancelledError would turn shutdown into an endless retry loop).
                with suppress(Exception):
                    await asyncio.wait_for(asyncio.shield(consumer), timeout=5)
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.warning('Elasticsearch/RabbitMQ连接失败，%s秒后重试', interval, exc_info=True)
            await asyncio.sleep(interval)
        finally:
            if connection is not None:
                with suppress(Exception, TimeoutError):
                    await asyncio.wait_for(connection.close(), timeout=5)
            with suppress(Exception, TimeoutError):
                await asyncio.wait_for(es.close(), timeout=5)


@contextmanager
def legacy_search_sync_worker(database):
    """Dedicated thread with one persistent loop for the legacy WSGI server."""
    import threading
    from app.async_contract import SyncDatabaseAdapter
    database = SyncDatabaseAdapter(database)
    stopped = threading.Event()

    def work():
        async def runner():
            task = asyncio.create_task(run_search_sync(database, should_stop=stopped.is_set))
            while not stopped.is_set():
                await asyncio.sleep(0.5)
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        try:
            asyncio.run(runner())
        except Exception:
            LOGGER.exception('商品搜索同步线程异常退出')

    worker = threading.Thread(target=work, name='search-sync', daemon=True)
    worker.start()
    try:
        yield
    finally:
        stopped.set()
        worker.join(timeout=30)
