"""Small async connection contract shared by ASGI and the legacy WSGI adapter."""
from contextlib import asynccontextmanager


class SyncDatabaseAdapter:
    """Expose a synchronous Database/MySQLDatabase through the async worker contract."""
    def __init__(self, database):
        self.database = database
        self.path = database.path

    @asynccontextmanager
    async def connection(self):
        with self.database.connection() as connection:
            yield SyncConnectionAdapter(connection, self.path)


class SyncConnectionAdapter:
    """Run only inside a dedicated legacy WSGI request thread, never ASGI."""
    def __init__(self, connection, path=None):
        self.connection = connection
        self.path = path

    async def execute(self, sql, parameters=()):
        return self.connection.execute(sql, parameters)

    async def executemany(self, sql, parameters):
        return self.connection.executemany(sql, parameters)

    async def begin_transaction(self):
        if hasattr(self.connection, 'begin_transaction'):
            self.connection.begin_transaction()
        else:
            self.connection.execute('BEGIN IMMEDIATE')

    async def commit(self):
        self.connection.execute('COMMIT')

    async def rollback(self):
        self.connection.execute('ROLLBACK')

    async def lock_user(self, user_id):
        # MySQL row locks also coordinate with the async ORM server/workers.
        if hasattr(self.connection, 'begin_transaction'):
            return self.connection.execute('SELECT id FROM users WHERE id=? FOR UPDATE', (user_id,)).fetchone()
        return None

    async def lock_order(self, order_id):
        if hasattr(self.connection, 'begin_transaction'):
            return self.connection.execute('SELECT id FROM orders WHERE id=? FOR UPDATE', (order_id,)).fetchone()
        return None

    async def lock_coupon(self, coupon_id):
        return None

    async def lock_products(self, product_ids):
        if hasattr(self.connection, 'begin_transaction'):
            return {product_id: self.connection.execute('SELECT id FROM products WHERE id=? FOR UPDATE',
                    (product_id,)).fetchone() for product_id in sorted(set(product_ids))}
        return {}


@asynccontextmanager
async def transaction(connection):
    if hasattr(connection, 'atomic'):
        async with connection.atomic(serialized=True):
            yield
        return
    await connection.begin_transaction()
    try:
        yield
        await connection.commit()
    except BaseException:
        await connection.rollback()
        raise
