"""Unit tests for the Phase-3 product content pipeline (sanitizer, params, storage, ES text)."""
import tempfile
import unittest
from pathlib import Path

from app import html_sanitize, mongodb, storage
from app.schemas import AdminProductInput, product_params, product_detail_html
from app.search import document


def product_payload(**changes):
    base = dict(name='测试商品', subtitle='短介绍', category='家居生活', price=6900,
                original_price=9900, stock=10, image='mug', badge='', color='#eae5ed',
                description='商品详情介绍')
    base.update(changes)
    return base


class SanitizerTests(unittest.TestCase):
    def test_drops_scripts_iframes_and_event_handlers(self):
        raw = '<p onclick="alert(1)">你好</p><script>alert(1)</script><iframe src="x"></iframe>'
        clean = html_sanitize.sanitize_html(raw)
        self.assertEqual(clean, '<p>你好</p>')
        self.assertNotIn('script', clean)
        self.assertNotIn('onclick', clean)

    def test_keeps_presentational_tags_and_escapes_data(self):
        clean = html_sanitize.sanitize_html('<ul><li>材质 &lt;陶瓷&gt;</li></ul><h3>规格</h3>')
        self.assertEqual(clean, '<ul><li>材质 &lt;陶瓷&gt;</li></ul><h3>规格</h3>')

    def test_img_src_restricted_to_media_keys(self):
        key = 'a' * 64 + '.png'
        good = html_sanitize.sanitize_html(f'<img src="/media/{key}" alt="图">')
        self.assertIn(f'src="/media/{key}"', good)
        bad = html_sanitize.sanitize_html('<img src="https://evil.example/x.png" onerror="alert(1)">')
        self.assertNotIn('src=', bad)
        self.assertNotIn('onerror', bad)

    def test_text_content_strips_tags_and_collapses_whitespace(self):
        text = html_sanitize.text_content('<p>第一段</p><p>第二段<br>第三段</p><script>隐藏</script>')
        self.assertEqual(text, '第一段 第二段 第三段')


class SchemaContentTests(unittest.TestCase):
    def test_params_normalized_and_validated(self):
        payload = AdminProductInput(**product_payload(params=[{'name': '品牌', 'value': '黑马'}]))
        self.assertEqual(payload.params, [{'name': '品牌', 'value': '黑马'}])
        self.assertEqual(payload.detail_html, '')

    def test_detail_html_sanitized_on_input(self):
        payload = AdminProductInput(**product_payload(detail_html='<p>ok</p><script>bad()</script>'))
        self.assertEqual(payload.detail_html, '<p>ok</p>')

    def test_invalid_params_rejected(self):
        for bad in ([{'name': '', 'value': 'x'}], [{'name': 'x', 'value': ''}], ['not-a-dict'], 'text'):
            with self.assertRaises(ValueError):
                product_params(bad)
        with self.assertRaises(ValueError):
            product_params([{'name': 'n', 'value': 'v'}] * 31)
        with self.assertRaises(ValueError):
            product_detail_html('x' * 20001)


class ParseParamsTests(unittest.TestCase):
    def test_parse_params_handles_bad_json_and_shapes(self):
        self.assertEqual(mongodb.parse_params(None), [])
        self.assertEqual(mongodb.parse_params('not json'), [])
        self.assertEqual(mongodb.parse_params('{"a":1}'), [])
        self.assertEqual(mongodb.parse_params('[{"name":"品牌","value":"黑马"},"x"]'),
                         [{'name': '品牌', 'value': '黑马'}])


class LocalStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'uploads'

    def test_put_get_exists_delete_roundtrip(self):
        local = storage.LocalStorage(self.root)
        key = 'b' * 64 + '.png'
        self.assertFalse(local.exists(key))
        local.put(key, b'image-bytes', 'image/png')
        self.assertTrue(local.exists(key))
        self.assertEqual(local.get(key), b'image-bytes')
        local.delete(key)
        self.assertFalse(local.exists(key))
        self.assertIsNone(local.get(key))

    def test_object_storage_falls_back_when_primary_fails(self):
        class BrokenPrimary:
            def put(self, *a): raise RuntimeError('down')
            def get(self, *a): raise RuntimeError('down')
            def exists(self, *a): raise RuntimeError('down')
            def delete(self, *a): raise RuntimeError('down')
        obj = storage.ObjectStorage(self.root, BrokenPrimary())
        key = 'c' * 64 + '.jpg'
        obj.put(key, b'data', 'image/jpeg')
        self.assertEqual(obj.get(key), b'data')
        self.assertTrue(obj.exists(key))
        obj.delete(key)
        self.assertFalse(obj.exists(key))

    def test_object_storage_without_primary_uses_local(self):
        obj = storage.ObjectStorage(self.root, None)
        key = 'd' * 64 + '.webp'
        obj.put(key, b'webp', 'image/webp')
        self.assertEqual(obj.get(key), b'webp')
        self.assertEqual(obj.url(key), '/media/' + key)


class DocumentTextTests(unittest.TestCase):
    def row(self, **changes):
        base = dict(id=1, name='耳机', subtitle='无线', category='数码', store_id=2, price=100,
                    original_price=200, sales=3, version=4, is_active=1, description='降噪耳机',
                    detail_html='<p>舒适佩戴</p><script>x</script>', params_json='[{"name":"品牌","value":"黑马"}]')
        base.update(changes)
        return base

    def test_document_includes_searchable_text_fields(self):
        doc = document(self.row())
        self.assertEqual(doc['description'], '降噪耳机')
        self.assertEqual(doc['detail_text'], '舒适佩戴')
        self.assertEqual(doc['params_text'], '品牌 黑马')

    def test_document_params_override_wins(self):
        doc = document(self.row(), [{'name': '材质', 'value': '陶瓷'}])
        self.assertEqual(doc['params_text'], '材质 陶瓷')

    def test_document_tolerates_missing_content_columns(self):
        row = self.row()
        del row['detail_html'], row['params_json'], row['description']
        doc = document(row)
        self.assertEqual(doc['detail_text'], '')
        self.assertEqual(doc['params_text'], '')
        self.assertEqual(doc['description'], '')


if __name__ == '__main__':
    unittest.main()