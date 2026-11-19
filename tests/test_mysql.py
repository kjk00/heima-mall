"""Verify dialect, result shape, transaction contract and migration rollback."""
from decimal import Decimal
from datetime import date
import asyncio
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from app.async_contract import SyncConnectionAdapter
from app.database import Database, transaction
from app.features import page_data
from app.mysql import MySQLDatabase, Result, translate_sql
from scripts.migrate_sqlite_to_mysql import TABLES, copy_snapshot
from app.config import load_environment
from app.server import MallApplication
from tests.client import Client


class MySQLContractTests(unittest.TestCase):
    def test_paginated_count_uses_a_mysql_compatible_derived_table(self):
        with sqlite3.connect(':memory:') as raw:
            raw.row_factory = sqlite3.Row
            raw.execute('CREATE TABLE discussions(id INTEGER, product_id INTEGER, is_visible INTEGER)')
            raw.executemany('INSERT INTO discussions VALUES (?,?,?)',
                            [(1,1,1),(2,1,1),(3,1,1),(4,1,0),(5,2,1)])
            connection = SyncConnectionAdapter(raw)
            with patch.object(connection, 'execute', AsyncMock(wraps=connection.execute)) as execute:
                result = asyncio.run(page_data(connection,
                    'SELECT id FROM discussions WHERE product_id=? AND is_visible=1 ORDER BY id DESC',
                    (1,), page=2, page_size=2))
            count_sql, parameters = execute.call_args_list[0].args
            # MySQL error 1248: every derived table must have its own alias.
            self.assertRegex(translate_sql(count_sql), r'\)\s+(?:AS\s+)?[A-Za-z_]\w*\s*$')
            self.assertEqual(parameters, (1,))
            self.assertEqual(result, {'items':[{'id':1}], 'total':3, 'page':2, 'page_size':2})

    def test_local_environment_preserves_exported_values_and_literal_passwords(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'.env'
            path.write_text("MYSQL_HOST=127.0.0.1\nMYSQL_PASSWORD='literal#value$not-expanded'\n")
            with patch.dict(os.environ, {'MYSQL_HOST':'exported-host'}, clear=True):
                load_environment(path)
                self.assertEqual(os.environ['MYSQL_HOST'], 'exported-host')
                self.assertEqual(os.environ['MYSQL_PASSWORD'], 'literal#value$not-expanded')

    def test_parameter_translation_preserves_literals_and_percent(self):
        sql = "SELECT '?' AS literal, name FROM products WHERE name LIKE ? AND subtitle='100%'"
        self.assertEqual(translate_sql(sql) % ("'耳机%'",), "SELECT '?' AS literal, name FROM products WHERE name LIKE '耳机%' AND subtitle='100%'")

    def test_upsert_timestamp_date_and_scalar_max_translation(self):
        sql = "INSERT INTO reviews VALUES (?) ON CONFLICT(user_id,product_id) DO UPDATE SET rating=excluded.rating,updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')"
        translated = translate_sql(sql) % (5,)
        self.assertIn('ON DUPLICATE KEY UPDATE rating=VALUES(rating)', translated)
        self.assertIn("DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%dT%H:%i:%sZ')", translated)
        self.assertIn('GREATEST(0,sales-2)', translate_sql('UPDATE products SET sales=MAX(0,sales-?)') % (2,))
        self.assertIn('MAX(id)', translate_sql('SELECT MAX(id) FROM products') % ())
        self.assertIn('INTERVAL 8 HOUR', translate_sql("SELECT date(created_at,'+8 hours') FROM orders") % ())
        self.assertIn('created_seq DESC', translate_sql('SELECT id FROM orders ORDER BY rowid DESC') % ())

    def test_result_rows_support_mapping_index_and_json_numeric_types(self):
        class Cursor:
            rowcount, lastrowid, description = 1, 4, ['a']
            def fetchall(self):
                return [{'total': Decimal('100'), 'rating': Decimal('4.5'), 'day': date(2026,11,17)}]
        result = Result(Cursor())
        row = result.fetchone()
        self.assertEqual((row[0], row['rating'], dict(row)), (100, 4.5, {'total': 100, 'rating': 4.5, 'day': '2026-11-17'}))
        self.assertIsNone(result.fetchone())

    def test_mysql_default_and_explicit_sqlite_fixture_selection(self):
        with patch.dict(os.environ, {'DATABASE_BACKEND': 'mysql'}):
            self.assertIsInstance(Database(), MySQLDatabase)
            self.assertEqual(Database(Path('/tmp/explicit-test.db')).backend, 'sqlite')
        with patch.dict(os.environ, {'DATABASE_BACKEND': 'invalid'}):
            with self.assertRaises(ValueError):
                Database()

    def test_mysql_database_name_validation_rejects_sql_fragments(self):
        with patch.dict(os.environ, {'MYSQL_DATABASE': 'mall`; DROP DATABASE mysql'}):
            with self.assertRaises(ValueError):
                MySQLDatabase()

    def test_mysql_transaction_rollback_preserves_original_failure(self):
        class Connection:
            def __init__(self): self.calls = []
            def begin_transaction(self): self.calls.append('MYSQL BEGIN + INNODB LOCK')
            def execute(self, sql): self.calls.append(sql)
        c = Connection()
        with self.assertRaisesRegex(ValueError, 'injected'), transaction(c):
            raise ValueError('injected')
        self.assertEqual(c.calls, ['MYSQL BEGIN + INNODB LOCK', 'ROLLBACK'])

    def test_migration_refuses_nonempty_target_and_copies_an_empty_target(self):
        with tempfile.TemporaryDirectory() as directory:
            source_path, target_path = Path(directory)/'source.db', Path(directory)/'target.db'
            client = Client(MallApplication(source_path))
            self.assertEqual(client.post('/api/auth/register', {'name':'迁移测试','phone':'13900000001','password':'migration-test-only'})['status'], 201)
            self.assertEqual(client.post('/api/cart', {'product_id':1})['status'], 200)
            response = client.post('/api/orders', {'recipient':'迁移测试','phone':'13900000001','address':'迁移测试独立地址','idempotency_key':'migration-test-order-0001'})
            self.assertEqual(response['status'], 201)
            order_id = response['json']['id']
            self.assertEqual(client.post(f'/api/orders/{order_id}/pay')['status'], 200)
            expected_order = client.get(f'/api/orders/{order_id}')['json']
            Database(target_path).initialize()
            with sqlite3.connect(source_path) as source, Database(target_path).connection() as target:
                with self.assertRaisesRegex(ValueError, '已有业务数据'):
                    copy_snapshot(source, target)
                with transaction(target):
                    for table in reversed(TABLES):
                        target.execute(f'DELETE FROM `{table}`')
                counts = copy_snapshot(source, target)
                self.assertEqual(counts['products'], 20)
                self.assertEqual(counts['stores'], 7)
                self.assertEqual(counts['order_items'], 1)
                self.assertFalse(target.execute('PRAGMA foreign_key_check').fetchall())
            restored = Client(MallApplication(target_path))
            restored.cookies = dict(client.cookies)
            self.assertEqual(restored.get('/api/auth/me')['status'], 200)
            self.assertEqual(restored.get(f'/api/orders/{order_id}')['json'], expected_order)


if __name__ == '__main__':
    unittest.main()
