import sqlite3
import secrets
import os
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS catalog_cache_state (
    id INTEGER PRIMARY KEY CHECK(id=1), generation TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS product_sync_outbox (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS stores (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, description TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '', is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
    shipping_fee INTEGER NOT NULL DEFAULT 1000 CHECK(shipping_fee>=0),
    free_shipping_threshold INTEGER NOT NULL DEFAULT 19900 CHECK(free_shipping_threshold>=0),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, phone TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL, password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
    expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS login_captchas (
    id TEXT PRIMARY KEY, code TEXT NOT NULL, expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, subtitle TEXT NOT NULL,
    category TEXT NOT NULL, price INTEGER NOT NULL CHECK(price >= 0),
    original_price INTEGER NOT NULL, stock INTEGER NOT NULL CHECK(stock >= 0),
    image TEXT NOT NULL, badge TEXT NOT NULL DEFAULT '',
    color TEXT NOT NULL, description TEXT NOT NULL, sales INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS cart_items (
    user_id INTEGER NOT NULL REFERENCES users(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL CHECK(quantity BETWEEN 1 AND 99),
    PRIMARY KEY(user_id, product_id)
);
CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','paid','cancelled')),
    subtotal INTEGER NOT NULL, shipping_fee INTEGER NOT NULL, total INTEGER NOT NULL,
    recipient TEXT NOT NULL, phone TEXT NOT NULL, address TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(user_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS orders_user ON orders(user_id, created_at);
CREATE INDEX IF NOT EXISTS orders_expiry ON orders(status, created_at);
CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY, order_id TEXT NOT NULL REFERENCES orders(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    name TEXT NOT NULL, image TEXT NOT NULL,
    price INTEGER NOT NULL, quantity INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS addresses (
    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
    recipient TEXT NOT NULL, phone TEXT NOT NULL, address TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '', is_default INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS address_default ON addresses(user_id) WHERE is_default=1;
CREATE TABLE IF NOT EXISTS coupons (
    id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('amount','rate')), value INTEGER NOT NULL,
    threshold INTEGER NOT NULL DEFAULT 0, max_discount INTEGER NOT NULL DEFAULT 0,
    claim_limit INTEGER NOT NULL DEFAULT 200, starts_at TEXT NOT NULL,
    expires_at TEXT NOT NULL, is_active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS user_coupons (
    user_id INTEGER NOT NULL REFERENCES users(id), coupon_id INTEGER NOT NULL REFERENCES coupons(id),
    used_order_id TEXT REFERENCES orders(id),
    claimed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    PRIMARY KEY(user_id,coupon_id)
);
CREATE TABLE IF NOT EXISTS refunds (
    id INTEGER PRIMARY KEY, order_id TEXT NOT NULL REFERENCES orders(id),
    user_id INTEGER NOT NULL REFERENCES users(id), reason TEXT NOT NULL, amount INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')),
    idempotency_key TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', restocked INTEGER NOT NULL DEFAULT 0,
    processed_by INTEGER REFERENCES users(id), processed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE(user_id,idempotency_key)
);
CREATE UNIQUE INDEX IF NOT EXISTS refund_open_order ON refunds(order_id) WHERE status IN ('pending','approved');
CREATE TABLE IF NOT EXISTS tracking_events (
    id INTEGER PRIMARY KEY, order_id TEXT NOT NULL REFERENCES orders(id),
    stage TEXT NOT NULL, note TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE TABLE IF NOT EXISTS browsing_history (
    user_id INTEGER NOT NULL REFERENCES users(id), product_id INTEGER NOT NULL REFERENCES products(id),
    viewed_at TEXT NOT NULL, sort_key INTEGER NOT NULL, PRIMARY KEY(user_id,product_id)
);
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
    product_id INTEGER NOT NULL REFERENCES products(id), order_id TEXT NOT NULL REFERENCES orders(id),
    rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5), content TEXT NOT NULL,
    is_visible INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE(user_id,product_id)
);
CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
    product_id INTEGER NOT NULL REFERENCES products(id), content TEXT NOT NULL,
    is_visible INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
    store_id INTEGER NOT NULL REFERENCES stores(id),
    customer_read_id INTEGER NOT NULL DEFAULT 0, staff_read_id INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE(user_id,store_id)
);
CREATE TABLE IF NOT EXISTS support_messages (
    id INTEGER PRIMARY KEY, conversation_id INTEGER NOT NULL REFERENCES conversations(id),
    sender_id INTEGER NOT NULL REFERENCES users(id), sender_side TEXT NOT NULL CHECK(sender_side IN ('customer','staff')),
    content TEXT NOT NULL, client_message_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE(conversation_id,sender_id,sender_side,client_message_id)
);
CREATE INDEX IF NOT EXISTS support_thread ON support_messages(conversation_id,id);
CREATE INDEX IF NOT EXISTS review_product ON reviews(product_id,is_visible,id);
CREATE INDEX IF NOT EXISTS comment_product ON comments(product_id,is_visible,id);
"""

# Additive migrations preserve existing accounts, carts, orders, and inventory.
MIGRATIONS = {
    'users': {
        'role': "TEXT NOT NULL DEFAULT 'customer' CHECK(role IN ('customer','admin'))",
        'is_active': 'INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1))',
        'notify_support': 'INTEGER NOT NULL DEFAULT 1 CHECK(notify_support IN (0,1))',
        'store_id': 'INTEGER REFERENCES stores(id)',
        'is_super_admin': 'INTEGER NOT NULL DEFAULT 0 CHECK(is_super_admin IN (0,1))',
    },
    'products': {
        'is_active': 'INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1))',
        'version': 'INTEGER NOT NULL DEFAULT 1',
        'store_id': 'INTEGER REFERENCES stores(id)',
        'detail_html': "TEXT NOT NULL DEFAULT ''",
        'params_json': "TEXT NOT NULL DEFAULT '[]'",
    },
    'orders': {
        'request_hash': "TEXT NOT NULL DEFAULT ''",
        'fulfillment_status': "TEXT NOT NULL DEFAULT 'unfulfilled' CHECK(fulfillment_status IN ('unfulfilled','shipped','completed'))",
        'carrier': "TEXT NOT NULL DEFAULT ''",
        'tracking_no': "TEXT NOT NULL DEFAULT ''",
        'shipped_at': 'TEXT',
        'completed_at': 'TEXT',
        'delivery_stage': "TEXT NOT NULL DEFAULT 'awaiting'",
        'discount': 'INTEGER NOT NULL DEFAULT 0',
        'coupon_id': 'INTEGER REFERENCES coupons(id)',
        'store_id': 'INTEGER REFERENCES stores(id)',
        'store_name': "TEXT NOT NULL DEFAULT '黑马自营店'",
    },
    'conversations': {'store_id': 'INTEGER REFERENCES stores(id)'},
    'coupons': {'store_id': 'INTEGER REFERENCES stores(id)'},
}


class Database:
    def __new__(cls, path=None):
        if path is None and os.getenv('DATABASE_BACKEND', 'mysql') == 'mysql':
            from app.mysql import MySQLDatabase
            return MySQLDatabase()
        return super().__new__(cls)

    def __init__(self, path=None):
        backend = os.getenv('DATABASE_BACKEND', 'mysql')
        if path is None and backend not in {'mysql', 'sqlite'}:
            raise ValueError('DATABASE_BACKEND必须为mysql或sqlite')
        self.path = Path(path or os.getenv('DATABASE_PATH', str(Path(__file__).resolve().parent.parent / 'data/mall.db')))
        self.backend = 'sqlite'

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self):
        from app.seed import PRODUCTS

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(SCHEMA)
            old_conversations = 'store_id' not in {r['name'] for r in connection.execute('PRAGMA table_info(conversations)')}
            # Rebuild the legacy UNIQUE(user_id) table without changing message IDs or references.
            if old_conversations:
                connection.execute('PRAGMA foreign_keys=OFF')
            with transaction(connection):
                connection.execute('INSERT OR IGNORE INTO catalog_cache_state(id,generation) VALUES (1,?)', (secrets.token_hex(16),))
                for table in ('products', 'stores'):
                    for event in ('INSERT', 'UPDATE', 'DELETE'):
                        connection.execute(f'''CREATE TRIGGER IF NOT EXISTS catalog_cache_{table}_{event.lower()}
                            AFTER {event} ON {table} BEGIN
                            UPDATE catalog_cache_state SET revision=revision+1 WHERE id=1; END''')
                # Outbox triggers only exist while the ES+MQ sync pipeline is
                # configured; enabling later starts with a full reindex anyway.
                from app.search import sync_enabled
                if sync_enabled():
                    for event, reference in (('INSERT', 'NEW'), ('UPDATE', 'NEW'), ('DELETE', 'OLD')):
                        connection.execute(f'''CREATE TRIGGER IF NOT EXISTS product_sync_{event.lower()}
                            AFTER {event} ON products BEGIN
                            INSERT INTO product_sync_outbox(product_id) VALUES ({reference}.id); END''')
                else:
                    for event in ('insert', 'update', 'delete'):
                        connection.execute(f'DROP TRIGGER IF EXISTS product_sync_{event}')
                    connection.execute('DELETE FROM product_sync_outbox')
                connection.executemany('INSERT OR IGNORE INTO stores(id,name,description) VALUES (?,?,?)',[
                    (1,'黑马自营店','商城自营与历史订单服务'),(2,'黑马数码旗舰店','耳机、音箱与数码好物'),
                    (3,'日常家居生活馆','家居与生活用品'),(4,'城市穿搭精品店','服饰、箱包与配饰'),
                    (5,'轻运动户外店','运动与户外用品'),(6,'清新美妆个护店','美妆与个人护理'),
                    (7,'原味食品咖啡店','食品、饮料与咖啡')])
                for table, additions in MIGRATIONS.items():
                    columns = {row['name'] for row in connection.execute(f'PRAGMA table_info({table})')}
                    for name, definition in additions.items():
                        if name not in columns:
                            connection.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
                    # Existing administrators managed the entire platform before store permissions.
                    if table == 'users' and 'is_super_admin' not in columns:
                        connection.execute("UPDATE users SET is_super_admin=1 WHERE role='admin'")
                connection.executemany(
                    """INSERT OR IGNORE INTO products
                    (id,name,subtitle,category,price,original_price,stock,image,badge,color,description,sales)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", PRODUCTS
                )
                # Assign only unassigned products; later administrator edits survive restarts.
                connection.execute("""UPDATE products SET store_id=CASE category
                    WHEN '数码科技' THEN 2 WHEN '家居生活' THEN 3 WHEN '服饰箱包' THEN 4
                    WHEN '运动户外' THEN 5 WHEN '美妆个护' THEN 6 WHEN '食品饮料' THEN 7 ELSE 1 END
                    WHERE store_id IS NULL""")
                connection.execute('UPDATE orders SET store_id=1 WHERE store_id IS NULL')
                connection.execute('UPDATE conversations SET store_id=1 WHERE store_id IS NULL')
                if old_conversations:
                    connection.execute("""CREATE TABLE conversations_new (
                        id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),
                        store_id INTEGER NOT NULL REFERENCES stores(id),
                        customer_read_id INTEGER NOT NULL DEFAULT 0,staff_read_id INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                        UNIQUE(user_id,store_id))""")
                    connection.execute('INSERT INTO conversations_new SELECT id,user_id,store_id,customer_read_id,staff_read_id,created_at FROM conversations')
                    connection.execute('DROP TABLE conversations')
                    connection.execute('ALTER TABLE conversations_new RENAME TO conversations')
                connection.execute('CREATE INDEX IF NOT EXISTS products_store ON products(store_id,is_active,id)')
                connection.execute('CREATE INDEX IF NOT EXISTS orders_store ON orders(store_id,created_at)')
                connection.executemany("""INSERT OR IGNORE INTO coupons
                    (code,name,kind,value,threshold,max_discount,claim_limit,starts_at,expires_at)
                    VALUES (?,?,?,?,?,?,?,'2020-01-01T00:00:00Z','2100-01-01T00:00:00Z')""",
                    [('WELCOME10','新人满99减10','amount',1000,9900,0,200),
                     ('LIFE90','生活好物9折券','rate',90,19900,5000,200)])
                connection.execute("UPDATE orders SET delivery_stage='delivered' WHERE fulfillment_status='completed' AND delivery_stage='awaiting'")
                connection.execute("UPDATE orders SET delivery_stage='dispatched' WHERE fulfillment_status='shipped' AND delivery_stage='awaiting'")
                if connection.execute('PRAGMA foreign_key_check').fetchone():
                    raise sqlite3.IntegrityError('店铺迁移存在无效关联')
            if old_conversations:
                connection.execute('PRAGMA foreign_keys=ON')

    @contextmanager
    def connection(self):
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()


@contextmanager
def transaction(connection):
    if hasattr(connection, 'begin_transaction'):
        connection.begin_transaction()
    else:
        connection.execute("BEGIN IMMEDIATE")
    try:
        yield
        connection.execute("COMMIT")
    except BaseException:
        connection.execute("ROLLBACK")
        raise

