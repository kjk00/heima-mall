"""Keep generated administrator credentials private and out of source control."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from app.database import Database
from manage_admin import generate_store_admins


class CredentialManagementTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.repository = self.root / 'repository'
        self.repository.mkdir()
        self.enterContext(patch('manage_admin.ROOT', self.repository))
        self.database = Database(self.root / 'mall.db')
        self.database.initialize()
        self.output = self.repository / 'data/private/accounts.md'

    def admin_count(self, connection):
        return connection.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0]

    def test_generation_restricts_directory_and_file_permissions(self):
        private = self.output.parent
        private.mkdir(parents=True, mode=0o755)
        private.chmod(0o755)
        with self.database.connection() as connection:
            self.assertGreater(generate_store_admins(connection, self.output), 0)
        self.assertEqual(private.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)

    def test_repository_exports_outside_private_directory_are_rejected(self):
        candidates = [self.repository / 'accounts.md',
                      self.repository / 'data/store-admin-accounts.md',
                      self.output.parent / '../accounts.md']
        with self.database.connection() as connection:
            before = self.admin_count(connection)
            for output in candidates:
                with self.subTest(output=str(output)):
                    with self.assertRaises(ValueError):
                        generate_store_admins(connection, output)
                    self.assertFalse(output.resolve().exists())
                    self.assertEqual(self.admin_count(connection), before)

    def test_existing_export_is_never_overwritten(self):
        self.output.parent.mkdir(parents=True)
        self.output.write_text('existing private export', encoding='utf-8')
        with self.database.connection() as connection:
            before = self.admin_count(connection)
            with self.assertRaises(FileExistsError):
                generate_store_admins(connection, self.output)
            self.assertEqual(self.admin_count(connection), before)
        self.assertEqual(self.output.read_text(), 'existing private export')

    def test_failed_generation_rolls_back_accounts_and_removes_partial_export(self):
        with self.database.connection() as connection:
            before = self.admin_count(connection)
            with patch('manage_admin.hash_password', side_effect=['fixture-hash', RuntimeError('failed')]):
                with self.assertRaises(RuntimeError):
                    generate_store_admins(connection, self.output)
            self.assertEqual(self.admin_count(connection), before)
        self.assertFalse(self.output.exists())

    def test_export_cannot_follow_symlink_into_public_repository_directory(self):
        public = self.repository / 'public'
        public.mkdir()
        self.output.parent.parent.mkdir(parents=True)
        self.output.parent.symlink_to(public, target_is_directory=True)
        with self.database.connection() as connection:
            before = self.admin_count(connection)
            with self.assertRaises(ValueError):
                generate_store_admins(connection, self.output)
            self.assertEqual(self.admin_count(connection), before)
        self.assertFalse((public / self.output.name).exists())

    @unittest.skipUnless(shutil.which('git'), 'Git is required to check credential ignore rules')
    def test_git_ignores_credentials_but_preserves_environment_example(self):
        repository = Path(__file__).resolve().parent.parent
        paths = ['data/private/accounts.md', 'data/store-admin-accounts.md',
                 'data/store-admin-accounts-backup.md', 'mima.txt', '.mima.txt.swp',
                 '.env', '.env.local', '.env.production', '.coverage', '.coverage.local',
                 'reports/mysql-connection-history.jsonl', 'reports/mysql-connection-recheck.json',
                 'reports/async-enterprise-quality-results.json',
                 'reports/blackbox-whitebox-test-report.md', 'reports/enterprise-acceptance.log',
                 'reports/enterprise-coverage-summary.txt', 'reports/enterprise-quality-results.json',
                 'reports/frontend-stress.html', '.env.example']
        result = subprocess.run(
            ['git', 'check-ignore', '--no-index', '--stdin'],
            input='\n'.join(paths) + '\n', text=True, capture_output=True,
            cwd=repository, check=True)
        self.assertEqual(set(result.stdout.splitlines()), set(paths[:-1]))
