"""MySQL implementation of the application's small DB-API connection contract."""
from contextlib import contextmanager
from decimal import Decimal
from datetime import date, datetime
import hashlib
import os
from pathlib import Path
import re
import secrets
import sqlite3

ROOT = Path(__file__).resolve().parent.parent


def translate_sql(sql):
    sql = re.sub(r'INSERT\s+OR\s+IGNORE', 'INSERT IGNORE', sql, flags=re.I)
    sql = re.sub(r'ON CONFLICT\s*\([^)]*\)\s*DO UPDATE SET', 'ON DUPLICATE KEY UPDATE', sql, flags=re.I)
    sql = re.sub(r'excluded\.(\w+)', r'VALUES(\1)', sql, flags=re.I)
    sql = re.sub(r"strftime\('%Y-%m-%dT%H:%M:%SZ',\s*'now'\)",
                 "DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%dT%H:%i:%sZ')", sql, flags=re.I)
    sql = re.sub(r"date\(created_at,\s*'\+8 hours'\)",
                 "DATE(DATE_ADD(STR_TO_DATE(created_at,'%Y-%m-%dT%H:%i:%sZ'),INTERVAL 8 HOUR))", sql, flags=re.I)
    sql = re.sub(r'MAX\(([^()]*)\)', lambda m: ('GREATEST' if ',' in m[1] else 'MAX') + '(' + m[1] + ')', sql, flags=re.I)
    sql = re.sub(r'\browid\b', 'created_seq', sql, flags=re.I)
    # PyMySQL uses % formatting for bound parameters. Escape literal percent
    # characters, and substitute ? only outside SQL quoted strings/identifiers.
    sql = sql.replace('%', '%%')
    output, quote, index = [], None, 0
    while index < len(sql):
        character = sql[index]
        if quote:
            output.append(character)
            if character == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    output.append(sql[index + 1])
                    index += 1
                else:
                    quote = None
        elif character in "'\"`":
            quote = character
            output.append(character)
        else:
            output.append('%s' if character == '?' else character)
        index += 1
    return ''.join(output)


class Row(dict):
    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


class Result:
    def __init__(self, cursor):
        self.rowcount, self.lastrowid = cursor.rowcount, cursor.lastrowid
        self.rows = []
        if cursor.description:
            for record in cursor.fetchall():
                self.rows.append(Row({key: (int(value) if value == value.to_integral_value() else float(value))
                                      if isinstance(value, Decimal) else value.isoformat() if isinstance(value, (date, datetime)) else value
                                      for key, value in record.items()}))
        self.position = 0

    def fetchone(self):
        if self.position == len(self.rows):
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


class MySQLConnection:
    def __init__(self, raw, driver):
        self.raw, self.driver = raw, driver

    def execute(self, sql, parameters=()):
        try:
            with self.raw.cursor() as cursor:
                cursor.execute(translate_sql(sql), parameters)
                return Result(cursor)
        except self.driver.IntegrityError as error:
            raise sqlite3.IntegrityError('MySQL数据约束冲突') from error
        except self.driver.MySQLError as error:
            raise sqlite3.OperationalError('MySQL数据库操作失败') from error

    def executemany(self, sql, parameters):
        # Preserve lastrowid/row semantics across the two backends; seed and
        # migration batches are bounded and run in one transaction.
        result = None
        for values in parameters:
            result = self.execute(sql, values)
        return result

    def begin_transaction(self):
        self.execute('START TRANSACTION')
        # Existing business operations depend on SQLite BEGIN IMMEDIATE's
        # serial write contract. An InnoDB row lock preserves it across workers,
        # rather than relying on a process-local Python lock.
        self.execute('SELECT id FROM mall_transaction_guard WHERE id=1 FOR UPDATE')

    def close(self):
        self.raw.close()


class MySQLDatabase:
    backend = 'mysql'

    def __init__(self):
        self.host = os.getenv('MYSQL_HOST', '127.0.0.1')
        self.port = int(os.getenv('MYSQL_PORT', '3306'))
        self.name = os.getenv('MYSQL_DATABASE', 'heima_mall')
        self.user = os.getenv('MYSQL_USER', 'root')
        self.password = os.getenv('MYSQL_PASSWORD', '')
        self.socket = os.getenv('MYSQL_UNIX_SOCKET') or None
        if not re.fullmatch(r'[a-zA-Z0-9_]{1,64}', self.name):
            raise ValueError('MYSQL_DATABASE只允许1–64位字母、数字和下划线')
        identity = hashlib.sha256(f'{self.host}:{self.port}:{self.socket}:{self.name}'.encode()).hexdigest()[:16]
        self.path = ROOT / 'data' / f'mysql-{identity}'

    def connect(self, include_database=True):
        try:
            import pymysql
        except ImportError as error:
            raise RuntimeError('MySQL驱动未安装，请安装requirements.txt中的PyMySQL依赖') from error
        try:
            raw = pymysql.connect(host=self.host, port=self.port, user=self.user,
                password=self.password, database=self.name if include_database else None,
                unix_socket=self.socket, charset='utf8mb4', autocommit=True,
                connect_timeout=3, read_timeout=15, write_timeout=15,
                client_flag=pymysql.constants.CLIENT.FOUND_ROWS,
                cursorclass=pymysql.cursors.DictCursor)
            connection = MySQLConnection(raw, pymysql)
            connection.execute("SET SESSION sql_mode='STRICT_TRANS_TABLES,NO_BACKSLASH_ESCAPES,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION'")
            connection.execute("SET SESSION time_zone='+00:00'")
            connection.execute('SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED')
            return connection
        except pymysql.MySQLError as error:
            raise RuntimeError('本地MySQL连接失败，请检查服务状态及MYSQL_*配置') from error

    def initialize(self, seed=True, create_schema=None):
        if create_schema is None:
            create_schema = os.getenv('MYSQL_INITIALIZE_SCHEMA', '1') == '1'
        if not create_schema:
            with self.connection() as connection:
                connection.execute('SELECT generation,revision FROM catalog_cache_state WHERE id=1')
                connection.execute('SELECT id FROM mall_transaction_guard WHERE id=1')
            return
        with self.connection(include_database=False) as connection:
            connection.execute(f'CREATE DATABASE IF NOT EXISTS `{self.name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci')
        with self.connection() as connection:
            statements = (ROOT / 'app' / 'mysql_schema.sql').read_text(encoding='utf-8').split(';')
            for statement in statements:
                if statement.strip():
                    connection.execute(statement)
            exists = connection.execute('SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=? AND TABLE_NAME=? AND COLUMN_NAME=?',
                                        (self.name, 'orders', 'request_hash')).fetchone()
            if not exists:
                connection.execute("ALTER TABLE orders ADD COLUMN request_hash VARCHAR(64) NOT NULL DEFAULT ''")
            for column, ddl in (('detail_html', "TEXT NOT NULL DEFAULT ('')"), ('params_json', "TEXT NOT NULL DEFAULT ('[]')")):
                exists = connection.execute('SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=? AND TABLE_NAME=? AND COLUMN_NAME=?',
                                            (self.name, 'products', column)).fetchone()
                if not exists:
                    connection.execute(f'ALTER TABLE products ADD COLUMN {column} {ddl}')
            if not connection.execute('SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=? AND TABLE_NAME=? AND INDEX_NAME=?',
                    (self.name, 'orders', 'orders_expiry')).fetchone():
                connection.execute('CREATE INDEX orders_expiry ON orders(status,created_at)')
            connection.execute('INSERT IGNORE INTO catalog_cache_state(id,generation) VALUES (1,?)', (secrets.token_hex(16),))
            connection.execute('INSERT IGNORE INTO mall_transaction_guard(id) VALUES (1)')
            for table in ('products', 'stores'):
                for event in ('INSERT', 'UPDATE', 'DELETE'):
                    name = f'catalog_cache_{table}_{event.lower()}'
                    exists = connection.execute('SELECT 1 FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=? AND TRIGGER_NAME=?', (self.name, name)).fetchone()
                    if not exists:
                        connection.execute(f'CREATE TRIGGER `{name}` AFTER {event} ON `{table}` FOR EACH ROW UPDATE catalog_cache_state SET revision=revision+1 WHERE id=1')
            from app.search import sync_enabled
            if sync_enabled():
                for event, reference in (('INSERT', 'NEW'), ('UPDATE', 'NEW'), ('DELETE', 'OLD')):
                    name = f'product_sync_{event.lower()}'
                    exists = connection.execute('SELECT 1 FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=? AND TRIGGER_NAME=?', (self.name, name)).fetchone()
                    if not exists:
                        connection.execute(f'CREATE TRIGGER `{name}` AFTER {event} ON `products` FOR EACH ROW INSERT INTO product_sync_outbox(product_id) VALUES ({reference}.id)')
            else:
                for event in ('insert', 'update', 'delete'):
                    connection.execute(f'DROP TRIGGER IF EXISTS product_sync_{event}')
                connection.execute('DELETE FROM product_sync_outbox')
            if seed:
                self._seed(connection)

    def _seed(self, connection):
        from app.seed import PRODUCTS
        from app.database import transaction
        with transaction(connection):
            connection.executemany('INSERT IGNORE INTO stores(id,name,description) VALUES (?,?,?)', [
                (1,'黑马自营店','商城自营与历史订单服务'),(2,'黑马数码旗舰店','耳机、音箱与数码好物'),
                (3,'日常家居生活馆','家居与生活用品'),(4,'城市穿搭精品店','服饰、箱包与配饰'),
                (5,'轻运动户外店','运动与户外用品'),(6,'清新美妆个护店','美妆与个人护理'),(7,'原味食品咖啡店','食品、饮料与咖啡')])
            connection.executemany('INSERT IGNORE INTO products(id,name,subtitle,category,price,original_price,stock,image,badge,color,description,sales) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', PRODUCTS)
            connection.execute("UPDATE products SET store_id=CASE category WHEN '数码科技' THEN 2 WHEN '家居生活' THEN 3 WHEN '服饰箱包' THEN 4 WHEN '运动户外' THEN 5 WHEN '美妆个护' THEN 6 WHEN '食品饮料' THEN 7 ELSE 1 END WHERE store_id IS NULL")
            connection.executemany("INSERT IGNORE INTO coupons(code,name,kind,value,threshold,max_discount,claim_limit,starts_at,expires_at) VALUES (?,?,?,?,?,?,?,'2020-01-01T00:00:00Z','2100-01-01T00:00:00Z')", [
                ('WELCOME10','新人满99减10','amount',1000,9900,0,200),('LIFE90','生活好物9折券','rate',90,19900,5000,200)])

    @contextmanager
    def connection(self, include_database=True):
        connection = self.connect(include_database)
        try:
            yield connection
        finally:
            connection.close()
