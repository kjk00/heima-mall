"""Connection diagnostics preserve wrapped causes without exposing secrets."""
import errno
import unittest

from scripts.check_mysql_async import connection_failure_hint


class ConnectionCheckTests(unittest.TestCase):
    def test_permission_denial_wrapped_as_connection_failure(self):
        denied = PermissionError(errno.EPERM, 'Operation not permitted')
        driver = RuntimeError(2003, 'Cannot connect; password=private-value')
        driver.__cause__ = denied
        outer = RuntimeError('database temporarily unavailable')
        outer.__cause__ = driver
        hint = connection_failure_hint(outer)
        self.assertIn('当前执行环境禁止', hint)
        self.assertIn('尚无法验证账号密码', hint)
        self.assertNotIn('private-value', hint)

    def test_mysql_error_codes_explain_the_correct_remedy(self):
        for code, expected in ((1045, '认证失败'), (1049, '数据库不存在'),
                               (1044, '访问权限'), (1142, '访问权限')):
            with self.subTest(code=code):
                driver = RuntimeError(code, 'secret SQL and credentials')
                outer = RuntimeError('wrapped')
                outer.orig = driver
                hint = connection_failure_hint(outer)
                self.assertIn(expected, hint)
                self.assertNotIn('secret', hint)

    def test_refused_service_is_distinct_from_environment_permissions(self):
        hint = connection_failure_hint(ConnectionRefusedError(errno.ECONNREFUSED, 'Connection refused'))
        self.assertIn('请启动MySQL服务', hint)
        self.assertNotIn('执行环境禁止', hint)

    def test_unknown_error_and_cyclic_causes_do_not_expose_details(self):
        error = RuntimeError('secret connection URL')
        error.__cause__ = error
        hint = connection_failure_hint(error)
        self.assertIn('检查MySQL服务', hint)
        self.assertNotIn('secret', hint)


if __name__ == '__main__':
    unittest.main()
