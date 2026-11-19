"""Copy a consistent SQLite snapshot into an empty configured MySQL database."""
import argparse
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.database import transaction
from app.mysql import MySQLDatabase
from app.config import load_environment

TABLES = ('stores', 'users', 'products', 'coupons', 'orders', 'order_items', 'sessions',
          'cart_items', 'addresses', 'user_coupons', 'refunds', 'tracking_events',
          'browsing_history', 'reviews', 'comments', 'conversations', 'support_messages')


def copy_snapshot(source, target):
    counts = {}
    source.execute('BEGIN')
    try:
        with transaction(target):
            for table in TABLES:
                if target.execute(f'SELECT COUNT(*) FROM `{table}`').fetchone()[0]:
                    raise ValueError('目标MySQL库已有业务数据，迁移已停止；请指定新的空数据库')
            for table in TABLES:
                columns = [r[1] for r in source.execute(f'PRAGMA table_info({table})')]
                if not columns:
                    raise ValueError(f'源SQLite缺少{table}表，请先使用当前版本完成升级并备份')
                if any(not column.replace('_', '').isalnum() for column in columns):
                    raise ValueError('源数据库列名不符合预期')
                # Preserve rowid ordering for orders created in the same second.
                query = f'SELECT * FROM `{table}`' + (' ORDER BY rowid' if table == 'orders' else '')
                rows = source.execute(query).fetchall()
                names = ','.join(f'`{column}`' for column in columns)
                markers = ','.join('?' for _ in columns)
                target.executemany(f'INSERT INTO `{table}` ({names}) VALUES ({markers})', rows)
                copied = target.execute(f'SELECT COUNT(*) FROM `{table}`').fetchone()[0]
                if copied != len(rows):
                    raise ValueError(f'{table}迁移行数不一致')
                counts[table] = copied
    finally:
        source.execute('ROLLBACK')
    return counts


def main():
    load_environment()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT / 'data/mall.db')
    args = parser.parse_args()
    if not args.source.is_file():
        parser.error('源SQLite文件不存在')
    database = MySQLDatabase()
    try:
        database.initialize(seed=False, create_schema=True)
        with sqlite3.connect(args.source.resolve().as_uri() + '?mode=ro', uri=True) as source, database.connection() as target:
            counts = copy_snapshot(source, target)
        print('迁移完成，源SQLite保留：' + '，'.join(f'{table}={count}' for table, count in counts.items()))
    except (ValueError, RuntimeError, sqlite3.DatabaseError) as error:
        parser.exit(1, f'迁移未完成：{error}\n')


if __name__ == '__main__':
    main()
