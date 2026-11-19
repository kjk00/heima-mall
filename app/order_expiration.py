"""Release unpaid reservations after fifteen minutes, under the order locks."""
import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import logging
import threading

from app.async_contract import SyncConnectionAdapter, transaction

PAYMENT_TIMEOUT = timedelta(minutes=15)
SWEEP_INTERVAL_SECONDS = 5
EXPIRED_MESSAGE = '订单已超过15分钟支付期限，已自动取消，请重新下单'
logger = logging.getLogger(__name__)


def utc_now():
    return datetime.now(timezone.utc)


def payment_deadline(created_at):
    return datetime.fromisoformat(created_at.replace('Z', '+00:00')) + PAYMENT_TIMEOUT


def payment_metadata(order, tracking_events):
    result = {
        'payment_expires_at': payment_deadline(order['created_at']).isoformat(),
        'is_expired': order['status'] == 'cancelled' and any(
            event['stage'] == 'expired' for event in tracking_events),
    }
    if order['status'] == 'pending':
        result['server_time'] = utc_now().isoformat()
    return result


async def expire_locked_order(connection, order, now=None):
    """Caller owns the buyer/order locks and commits before rejecting payment."""
    now = now or utc_now()
    if order['status'] != 'pending' or payment_deadline(order['created_at']) > now:
        return False
    items = (await connection.execute(
        'SELECT product_id,quantity FROM order_items WHERE order_id=? ORDER BY product_id',
        (order['id'],))).fetchall()
    await connection.lock_products(item['product_id'] for item in items)
    changed = await connection.execute(
        "UPDATE orders SET status='cancelled' WHERE id=? AND status='pending'", (order['id'],))
    if not changed.rowcount:
        return False
    for item in items:
        await connection.execute('UPDATE products SET stock=stock+?,version=version+1 WHERE id=?',
                                 (item['quantity'], item['product_id']))
    await connection.execute('UPDATE user_coupons SET used_order_id=NULL WHERE used_order_id=?',
                             (order['id'],))
    await connection.execute('INSERT INTO tracking_events(order_id,stage,note) VALUES (?,?,?)',
                             (order['id'], 'expired', '超过15分钟未支付，订单已自动取消'))
    return True


async def expire_order(connection, order_id, user_id, now=None):
    async with transaction(connection):
        await connection.lock_user(user_id)
        await connection.lock_order(order_id)
        order = (await connection.execute(
            'SELECT id,status,created_at FROM orders WHERE id=? AND user_id=?',
            (order_id, user_id))).fetchone()
        return bool(order and await expire_locked_order(connection, order, now))


async def expire_due_orders(connection, user_id=None, store_id=None, limit=100):
    now = utc_now()
    clauses, args = ["status='pending'", 'created_at<=?'], [
        (now - PAYMENT_TIMEOUT).strftime('%Y-%m-%dT%H:%M:%SZ')]
    if user_id is not None:
        clauses.append('user_id=?')
        args.append(user_id)
    if store_id is not None:
        clauses.append('store_id=?')
        args.append(store_id)
    rows = (await connection.execute('SELECT id,user_id FROM orders WHERE ' + ' AND '.join(clauses)
        + ' ORDER BY created_at,id LIMIT ?', (*args, limit))).fetchall()
    expired = 0
    for row in rows:
        expired += await expire_order(connection, row['id'], row['user_id'], now)
    return expired


async def expire_orders_once(database):
    async with database.connection() as connection:
        return await expire_due_orders(connection)


async def run_order_expiration(database, interval=None):
    interval = SWEEP_INTERVAL_SECONDS if interval is None else interval
    wait = interval
    while True:
        await asyncio.sleep(wait)
        wait = interval
        try:
            if await expire_orders_once(database) >= 100:
                wait = 0
        except Exception:
            logger.warning('订单过期清理暂时失败，将在下一轮重试')


def expire_orders_sync(database):
    with database.connection() as connection:
        return asyncio.run(expire_due_orders(SyncConnectionAdapter(connection)))


@contextmanager
def legacy_expiration_worker(database, interval=SWEEP_INTERVAL_SECONDS):
    expire_orders_sync(database)
    stopped = threading.Event()

    def work():
        while not stopped.wait(interval):
            try:
                expire_orders_sync(database)
            except Exception:
                logger.warning('订单过期清理暂时失败，将在下一轮重试')

    worker = threading.Thread(target=work, name='order-expiration', daemon=True)
    worker.start()
    try:
        yield
    finally:
        stopped.set()
        worker.join(timeout=15)
