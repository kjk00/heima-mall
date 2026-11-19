"""SQLAlchemy AsyncSession / aiomysql runtime; no synchronous request DB driver."""
import asyncio
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
import os
from pathlib import Path
import secrets
import sqlite3

from sqlalchemy import event
from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.database import Database
from app.mysql import MySQLDatabase, Row, translate_sql

ROOT = Path(__file__).resolve().parent.parent


class BufferedResult:
    def __init__(self, result):
        self.rowcount = result.rowcount
        self.lastrowid = getattr(result, 'lastrowid', None) if not result.returns_rows else None
        self.rows = []
        if result.returns_rows:
            for row in result.mappings().all():
                self.rows.append(Row({key: int(value) if isinstance(value, Decimal) and value == value.to_integral_value()
                    else float(value) if isinstance(value, Decimal) else value.isoformat() if isinstance(value, date) else value
                    for key, value in row.items()}))
        self.position = 0

    def fetchone(self):
        if self.position >= len(self.rows):
            return None
        row = self.rows[self.position]
        self.position += 1
        return row

    def fetchall(self):
        rows = self.rows[self.position:]
        self.position = len(self.rows)
        return rows

    def __iter__(self):
        return iter(self.fetchall())


class AsyncConnection:
    def __init__(self, session, backend, path=None):
        self.session, self.backend, self.explicit = session, backend, False
        self.path = path
        self.timeout = float(os.getenv('DB_OPERATION_TIMEOUT_SECONDS', '10'))
        if not 0 < self.timeout <= 120:
            raise ValueError('DB_OPERATION_TIMEOUT_SECONDS必须大于0且不超过120秒')

    async def execute(self, sql, parameters=()):
        return await self._execute(sql, tuple(parameters))

    async def _execute(self, sql, parameters):
        try:
            async with asyncio.timeout(self.timeout):
                connection = await self.session.connection()
                query = translate_sql(sql) if self.backend == 'mysql' else sql
                result = BufferedResult(await connection.exec_driver_sql(query, parameters))
                if not self.explicit:
                    await self.session.commit()
                return result
        except IntegrityError as error:
            if not self.explicit:
                await self.session.rollback()
            raise sqlite3.IntegrityError('数据库约束冲突') from error
        except (SQLAlchemyError, TimeoutError) as error:
            if not self.explicit:
                await self.session.rollback()
            raise sqlite3.OperationalError('异步数据库暂时不可用') from error
        except asyncio.CancelledError:
            if not self.explicit:
                await self.session.rollback()
            raise

    async def executemany(self, sql, values):
        parameters = [tuple(value) for value in values]
        return await self._execute(sql, parameters) if parameters else None

    async def begin_transaction(self, serialized=True):
        if self.explicit:
            raise RuntimeError('不允许嵌套业务事务')
        if self.session.in_transaction():
            await self.session.rollback()
        self.explicit = True
        try:
            await self.session.begin()
            if self.backend == 'sqlite':
                await self.execute('BEGIN IMMEDIATE')
            elif serialized:
                await self.execute('SELECT id FROM mall_transaction_guard WHERE id=1 FOR UPDATE')
        except BaseException:
            await self.rollback()
            raise

    async def commit(self):
        try:
            await self.session.commit()
        finally:
            self.explicit = False

    async def rollback(self):
        try:
            await self.session.rollback()
        finally:
            self.explicit = False

    @asynccontextmanager
    async def atomic(self, serialized=False):
        try:
            async with asyncio.timeout(self.timeout):
                await self.begin_transaction(serialized=serialized)
                try:
                    yield self.session
                    await self.commit()
                except BaseException:
                    await self.rollback()
                    raise
        except TimeoutError as error:
            raise sqlite3.OperationalError('数据库事务超时，请稍后重试') from error

    async def lock_user(self, user_id):
        from sqlalchemy import select
        from app.models import User
        return await self.session.scalar(select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True))

    async def lock_order(self, order_id):
        from sqlalchemy import select
        from app.models import Order
        return await self.session.scalar(select(Order).where(Order.id == order_id).with_for_update().execution_options(populate_existing=True))

    async def lock_coupon(self, coupon_id):
        from sqlalchemy import select
        from app.models import Coupon
        return await self.session.scalar(select(Coupon).where(Coupon.id == coupon_id).with_for_update().execution_options(populate_existing=True))

    async def lock_products(self, product_ids):
        from sqlalchemy import select
        from app.models import Product
        products = {}
        for product_id in sorted(set(product_ids)):
            products[product_id] = await self.session.scalar(select(Product).where(Product.id == product_id)
                .with_for_update().execution_options(populate_existing=True))
        return products


class AsyncDatabase:
    def __init__(self, path=None):
        backend = os.getenv('DATABASE_BACKEND', 'mysql') if path is None else 'sqlite'
        if backend not in {'mysql', 'sqlite'}:
            raise ValueError('DATABASE_BACKEND必须为mysql或sqlite')
        self.backend = backend
        size = int(os.getenv('DB_POOL_SIZE', '32'))
        if not 1 <= size <= 128:
            raise ValueError('DB_POOL_SIZE必须为1–128')
        if backend == 'mysql':
            settings = MySQLDatabase()
            self.settings, self.path = settings, settings.path
            query = {'charset': 'utf8mb4'}
            if settings.socket:
                query['unix_socket'] = settings.socket
            url = URL.create('mysql+aiomysql', username=settings.user, password=settings.password,
                host=settings.host, port=settings.port, database=settings.name, query=query)
            options = {'isolation_level':'READ COMMITTED', 'connect_args':{'connect_timeout':3}}
        else:
            self.path = Path(path or os.getenv('DATABASE_PATH', str(ROOT / 'data/mall.db'))).resolve()
            url = URL.create('sqlite+aiosqlite', database=str(self.path))
            options = {'connect_args':{'timeout':10}}
        self.engine = create_async_engine(url, pool_size=size, max_overflow=0,
            pool_timeout=5, pool_recycle=1800, pool_pre_ping=True, **options)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        @event.listens_for(self.engine.sync_engine, 'connect')
        def configure_connection(connection, record):
            # SQLAlchemy runs this DB-API hook inside its async driver greenlet.
            cursor = connection.cursor()
            try:
                if self.backend == 'mysql':
                    cursor.execute("SET SESSION time_zone='+00:00'")
                    cursor.execute("SET SESSION sql_mode='STRICT_TRANS_TABLES,NO_BACKSLASH_ESCAPES,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION'")
                else:
                    cursor.execute('PRAGMA foreign_keys=ON')
            finally:
                cursor.close()

    async def initialize(self):
        if self.backend == 'sqlite':
            await asyncio.to_thread(Database(self.path).initialize)
            return
        if os.getenv('MYSQL_INITIALIZE_SCHEMA', '1') != '1':
            async with self.connection() as connection:
                if not (await connection.execute('SELECT id FROM catalog_cache_state WHERE id=1')).fetchone():
                    raise RuntimeError('请先初始化MySQL表结构')
            return
        # Database creation uses the SAME async driver, with an administrative URL.
        # URL.set(database=None) is a no-op in SQLAlchemy 2 (None means "unchanged"),
        # so clear it with an empty string to connect without selecting a database.
        admin_engine = create_async_engine(self.engine.url.set(database=''), connect_args={'connect_timeout':3})
        try:
            async with admin_engine.begin() as connection:
                await connection.exec_driver_sql(f'CREATE DATABASE IF NOT EXISTS `{self.settings.name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci')
        finally:
            await admin_engine.dispose()
        async with self.connection() as connection:
            for sql in (ROOT / 'app/mysql_schema.sql').read_text().split(';'):
                if sql.strip():
                    await connection.execute(sql)
            exists = (await connection.execute('SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=? AND TABLE_NAME=? AND COLUMN_NAME=?',
                (self.settings.name, 'orders', 'request_hash'))).fetchone()
            if not exists:
                await connection.execute("ALTER TABLE orders ADD COLUMN request_hash VARCHAR(64) NOT NULL DEFAULT ''")
            for column, ddl in (('detail_html', "TEXT NOT NULL DEFAULT ('')"), ('params_json', "TEXT NOT NULL DEFAULT ('[]')")):
                exists = (await connection.execute('SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=? AND TABLE_NAME=? AND COLUMN_NAME=?',
                    (self.settings.name, 'products', column))).fetchone()
                if not exists:
                    await connection.execute(f'ALTER TABLE products ADD COLUMN {column} {ddl}')
            if not (await connection.execute('SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=? AND TABLE_NAME=? AND INDEX_NAME=?',
                    (self.settings.name, 'orders', 'orders_expiry'))).fetchone():
                await connection.execute('CREATE INDEX orders_expiry ON orders(status,created_at)')
            await connection.execute('INSERT IGNORE INTO catalog_cache_state(id,generation) VALUES (1,?)', (secrets.token_hex(16),))
            await connection.execute('INSERT IGNORE INTO mall_transaction_guard(id) VALUES (1)')
            for table in ('products','stores'):
                for action in ('INSERT','UPDATE','DELETE'):
                    name = f'catalog_cache_{table}_{action.lower()}'
                    if not (await connection.execute('SELECT 1 FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=? AND TRIGGER_NAME=?', (self.settings.name,name))).fetchone():
                        await connection.execute(f'CREATE TRIGGER `{name}` AFTER {action} ON `{table}` FOR EACH ROW UPDATE catalog_cache_state SET revision=revision+1 WHERE id=1')
            from app.search import sync_enabled
            if sync_enabled():
                for action, reference in (('INSERT','NEW'),('UPDATE','NEW'),('DELETE','OLD')):
                    name = f'product_sync_{action.lower()}'
                    if not (await connection.execute('SELECT 1 FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=? AND TRIGGER_NAME=?', (self.settings.name,name))).fetchone():
                        await connection.execute(f'CREATE TRIGGER `{name}` AFTER {action} ON `products` FOR EACH ROW INSERT INTO product_sync_outbox(product_id) VALUES ({reference}.id)')
            else:
                for action in ('insert','update','delete'):
                    await connection.execute(f'DROP TRIGGER IF EXISTS product_sync_{action}')
                await connection.execute('DELETE FROM product_sync_outbox')
            await self.seed(connection)

    async def seed(self, connection):
        from app.seed import PRODUCTS
        async with connection.atomic(serialized=True):
            await connection.executemany('INSERT IGNORE INTO stores(id,name,description) VALUES (?,?,?)',[
                (1,'黑马自营店','历史订单服务'),(2,'黑马数码旗舰店','数码好物'),(3,'日常家居生活馆','家居用品'),
                (4,'城市穿搭精品店','服饰箱包'),(5,'轻运动户外店','运动户外'),(6,'清新美妆个护店','美妆个护'),(7,'原味食品咖啡店','食品咖啡')])
            await connection.executemany('INSERT IGNORE INTO products(id,name,subtitle,category,price,original_price,stock,image,badge,color,description,sales) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', PRODUCTS)
            await connection.execute("UPDATE products SET store_id=CASE category WHEN '数码科技' THEN 2 WHEN '家居生活' THEN 3 WHEN '服饰箱包' THEN 4 WHEN '运动户外' THEN 5 WHEN '美妆个护' THEN 6 WHEN '食品饮料' THEN 7 ELSE 1 END WHERE store_id IS NULL")
            await connection.executemany("INSERT IGNORE INTO coupons(code,name,kind,value,threshold,max_discount,claim_limit,starts_at,expires_at) VALUES (?,?,?,?,?,?,?,'2020-01-01T00:00:00Z','2100-01-01T00:00:00Z')",[
                ('WELCOME10','新人满99减10','amount',1000,9900,0,200),('LIFE90','生活好物9折券','rate',90,19900,5000,200)])

    @asynccontextmanager
    async def connection(self):
        async with self.sessions() as session:
            yield AsyncConnection(session, self.backend, self.path)

    async def close(self):
        await self.engine.dispose()
