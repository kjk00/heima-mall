"""Public page routes and their browser navigation controller."""
import shutil
import subprocess
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

from app.server import MallApplication
from tests.client import Client

ROOT = Path(__file__).resolve().parents[1]


class PublicPageTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.client = Client(MallApplication(Path(directory.name) / 'mall.db'))

    def test_product_and_store_links_serve_the_app_on_refresh(self):
        for path in ('/products/1', '/products/1/', '/stores/2', '/stores/2/'):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response['status'], 200)
                self.assertIn('text/html', dict(response['headers'])['Content-Type'])
                self.assertIn(b'id="product-page"', response['body'])
                self.assertIn(b'id="storefront-store-banner"', response['body'])
                self.assertIn(b'/static/pages.js', response['body'])
                head = self.client.request('HEAD', path)
                self.assertEqual(head['status'], 200)
                self.assertEqual(head['body'], b'')
                self.assertEqual(dict(head['headers'])['Content-Length'], str(len(response['body'])))
        self.assertEqual(self.client.get('/static/pages.js')['status'], 200)

    def test_admin_links_are_hidden_before_permission_check(self):
        class Links(HTMLParser):
            def __init__(self):
                super().__init__()
                self.links = []

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == 'a' and attrs.get('href') == '/admin':
                    self.links.append(attrs)

        parser = Links()
        parser.feed(self.client.get('/')['body'].decode())
        self.assertEqual({link['id'] for link in parser.links}, {'footer-admin-link','profile-admin-link'})
        self.assertTrue(all('hidden' in link for link in parser.links))

    def test_public_detail_data_links_to_its_own_store_catalog(self):
        product = self.client.get('/api/products/1')['json']
        for field in ('name', 'image', 'description', 'price', 'stock', 'sales', 'store_id',
                      'store_name', 'store_description', 'store_phone', 'store_shipping_fee',
                      'free_shipping_threshold'):
            self.assertIn(field, product)
        store = self.client.get(f"/api/stores/{product['store_id']}")['json']
        self.assertEqual(store['name'], product['store_name'])
        items = self.client.get(f"/api/products?store_id={store['id']}")['json']['items']
        self.assertTrue(items)
        self.assertIn(product['id'], [item['id'] for item in items])
        self.assertTrue(all(item['store_id'] == store['id'] for item in items))

    def test_missing_details_use_api_errors_without_breaking_page_routes(self):
        for kind in ('products', 'stores'):
            self.assertEqual(self.client.get(f'/{kind}/999999')['status'], 200)
            self.assertEqual(self.client.get(f'/api/{kind}/999999')['status'], 404)
            for suffix in ('0', '-1', 'abc', '1/missing'):
                self.assertEqual(self.client.get(f'/{kind}/{suffix}')['status'], 404)


class PageNavigationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js 未安装；页面控制器测试需要 Node.js')
    def test_browser_controller(self):
        result = subprocess.run(['node', 'tests/test_pages.js'], cwd=ROOT,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node.js 未安装；管理员页面测试需要 Node.js')
    def test_admin_permission_controls(self):
        result = subprocess.run(['node','tests/test_admin_permissions.js'],cwd=ROOT,
                                capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
