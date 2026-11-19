import os
from pathlib import Path
import tempfile
import unittest

from app.admin import PRODUCT_INPUT_FIELDS
from app.database import Database
from app.server import MallApplication
from manage_admin import assign_admin, generate_store_admins
from tests.client import Client


class AdminPermissionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'mall.db'
        self.app = MallApplication(self.path)
        self.root = Client(self.app)
        self.root.post('/api/admin/setup', {'name':'终端管理员','phone':'13800138000','password':'test123456'})
        self.shop = self.manager('13900139000', 2)
        self.other_shop = self.manager('13700137000', 3)
        self.customer = Client(self.app)
        self.customer_id = self.ok(self.customer.post('/api/auth/register',
            {'name':'顾客','phone':'13600136000','password':'test123456'}), 201)['id']

    def ok(self, response, status=200):
        self.assertEqual(response['status'], status, response)
        return response['json']

    def manager(self, phone, store_id):
        client = Client(self.app)
        self.ok(client.post('/api/auth/register', {'name':'店铺管理员','phone':phone,'password':'test123456'}), 201)
        with self.app.state.database.connection() as connection:
            assign_admin(connection, phone, store_id)
        self.assertEqual(client.get('/api/auth/me')['status'], 401)
        self.ok(client.login(phone,'test123456',admin=True))
        return client

    def order(self, product_id, paid=True):
        self.ok(self.customer.post('/api/cart', {'product_id':product_id}))
        order = self.ok(self.customer.post('/api/orders', {'recipient':'顾客','phone':'13600136000',
            'address':'上海市测试路100号','idempotency_key':f'permissions-order-{product_id:04d}'}), 201)
        if paid:
            order = self.ok(self.customer.post(f"/api/orders/{order['id']}/pay"))
        return order

    def coupon(self, manager, code, **extra):
        return self.ok(manager.post('/api/admin/coupons', {'code':code,'name':'测试优惠券','kind':'amount',
            'value':100,'threshold':0,'max_discount':0,'claim_limit':10,'starts_at':'2020-01-01T00:00:00Z',
            'expires_at':'2100-01-01T00:00:00Z', **extra}))

    def test_store_lists_options_and_filter_tampering(self):
        self.assertTrue(self.ok(self.root.get('/api/admin/auth/me'))['is_super_admin'])
        self.assertEqual(self.ok(self.shop.get('/api/admin/auth/me'))['store_id'], 2)
        self.assertEqual([s['id'] for s in self.ok(self.shop.get('/api/admin/stores'))['items']], [2])
        self.assertGreater(self.ok(self.root.get('/api/admin/stores'))['total'], 1)
        self.assertEqual([s['id'] for s in self.ok(self.shop.get('/api/admin/options'))['stores']], [2])
        for resource in ('products','orders','support'):
            self.assertEqual(self.shop.get(f'/api/admin/{resource}?store_id=3')['status'], 404)
            self.ok(self.root.get(f'/api/admin/{resource}?store_id=3'))
        products = self.ok(self.shop.get('/api/admin/products'))['items']
        self.assertTrue(products)
        self.assertTrue(all(p['store_id'] == 2 for p in products))
        self.assertEqual(self.shop.get('/api/admin/stores/3')['status'], 404)
        self.assertEqual(self.shop.post('/api/admin/stores', {'name':'跨店创建'})['status'], 403)

    def test_cross_store_product_edits_and_store_status_are_rejected(self):
        own = self.ok(self.shop.get('/api/admin/products/1'))
        other = self.ok(self.root.get('/api/admin/products/6'))
        payload = {key:own[key] for key in PRODUCT_INPUT_FIELDS}
        payload.update(is_active=True, version=own['version'], store_id=3)
        self.assertEqual(self.shop.request('PUT','/api/admin/products/1',payload)['status'], 404)
        payload = {key:other[key] for key in PRODUCT_INPUT_FIELDS}
        payload.update(is_active=True, version=other['version'])
        self.assertEqual(self.shop.request('PUT','/api/admin/products/6',payload)['status'], 404)
        self.assertEqual(self.shop.get('/api/admin/products/6')['status'], 404)
        self.assertEqual(self.shop.request('PATCH','/api/admin/products/6/status',
            {'is_active':False,'version':other['version']})['status'], 404)
        store = self.ok(self.root.get('/api/admin/stores/3'))
        self.assertEqual(self.shop.request('PATCH','/api/admin/stores/3/status',
            {'is_active':False,'version':store['version']})['status'], 404)
        payload = {key:own[key] for key in PRODUCT_INPUT_FIELDS if key != 'store_id'}
        payload['is_active'] = True
        created = self.ok(self.shop.post('/api/admin/products',payload), 201)
        self.assertEqual(created['store_id'], 2)
        self.assertEqual(self.ok(self.root.get('/api/admin/products/6'))['version'], other['version'])

    def test_orders_statistics_and_customer_spending_are_scoped(self):
        own, other = self.order(1), self.order(6)
        self.assertEqual([o['id'] for o in self.ok(self.shop.get('/api/admin/orders'))['items']], [own['id']])
        dashboard = self.ok(self.shop.get('/api/admin/dashboard'))
        self.assertEqual(dashboard['summary']['orders'], 1)
        self.assertEqual(dashboard['summary']['revenue'], own['total'])
        self.assertEqual(sum(day['orders'] for day in dashboard['trend']), 1)
        self.assertEqual([o['id'] for o in dashboard['recent_orders']], [own['id']])
        users = self.ok(self.shop.get('/api/admin/users'))['items']
        self.assertEqual(len(users), 1)
        self.assertEqual(users[0]['order_count'], 1)
        self.assertEqual(users[0]['spent'], own['total'])
        self.assertEqual(self.ok(self.root.get('/api/admin/dashboard'))['summary']['revenue'], own['total']+other['total'])
        for method, suffix, body in [('GET','',None), ('POST','/cancel',None),
            ('POST','/ship',{'carrier':'顺丰','tracking_no':'SF123456'}), ('POST','/tracking',{'stage':'packing','note':''})]:
            self.assertEqual(self.shop.request(method,f"/api/admin/orders/{other['id']}{suffix}",body)['status'], 404)
        self.assertEqual(self.shop.request('PATCH',f'/api/admin/users/{self.customer_id}/status',
            {'is_active':False})['status'], 403)
        self.ok(self.customer.get('/api/auth/me'))

    def test_refunds_comments_reviews_and_support_are_isolated(self):
        own, other = self.order(1), self.order(6)
        for order in (own, other):
            self.ok(self.customer.post(f"/api/orders/{order['id']}/refund", {'reason':'测试退款',
                'idempotency_key':f"permissions-refund-{order['store_id']:04d}"}))
        self.assertEqual(len(self.ok(self.shop.get('/api/admin/refunds'))['items']), 1)
        other_refund = self.ok(self.other_shop.get('/api/admin/refunds'))['items'][0]
        self.assertEqual(self.shop.post(f"/api/admin/refunds/{other_refund['id']}/decision", {'approve':True})['status'], 404)
        for pid in (1,6):
            self.ok(self.customer.post(f'/api/products/{pid}/comments', {'content':f'店铺商品{pid}'}))
        discussion = self.ok(self.other_shop.get('/api/admin/discussions'))['items'][0]
        self.assertEqual(len(self.ok(self.shop.get('/api/admin/discussions'))['items']), 1)
        self.assertEqual(self.shop.request('PATCH',f"/api/admin/discussions/{discussion['id']}", {'is_visible':False})['status'], 404)
        # Reviews share the same scope rule; provision one against the existing purchase snapshot.
        with self.app.state.database.connection() as connection:
            review_id = connection.execute('INSERT INTO reviews(user_id,product_id,order_id,rating,content) VALUES (?,?,?,?,?)',
                (self.customer_id,6,other['id'],5,'测试购买评价')).lastrowid
        self.assertEqual(self.ok(self.shop.get('/api/admin/discussions?kind=reviews'))['total'], 0)
        self.assertEqual(self.shop.request('PATCH',f'/api/admin/discussions/{review_id}?kind=reviews', {'is_visible':False})['status'], 404)
        for store_id in (2,3):
            self.ok(self.customer.post('/api/support/messages', {'store_id':store_id,'content':'客服问题',
                'client_message_id':f'permissions-message-{store_id:04d}'}))
        support = self.ok(self.shop.get('/api/admin/support'))
        self.assertEqual(support['unread_total'], 1)
        self.assertEqual([c['store_id'] for c in support['items']], [2])
        other_thread = self.ok(self.other_shop.get('/api/admin/support'))['items'][0]
        path = f"/api/admin/support/{other_thread['id']}"
        self.assertEqual(self.shop.get(path+'/messages')['status'], 404)
        self.assertEqual(self.shop.post(path+'/messages', {'content':'跨店回复','client_message_id':'permissions-staff-0001'})['status'], 404)
        self.assertEqual(self.shop.post(path+'/read', {'last_message_id':0})['status'], 404)
        self.ok(self.root.get(path+'/messages'))

    def test_store_coupons_cannot_be_managed_or_used_by_other_stores(self):
        coupon = self.coupon(self.shop,'STORE2')
        self.assertEqual(coupon['store_id'], 2)
        self.assertEqual([c['id'] for c in self.ok(self.shop.get('/api/admin/coupons'))['items']], [coupon['id']])
        self.assertEqual(self.ok(self.other_shop.get('/api/admin/coupons'))['total'], 0)
        payload = {key:coupon[key] for key in ('code','name','kind','value','threshold','max_discount','claim_limit','starts_at','expires_at','store_id')}
        payload['is_active'] = False
        self.assertEqual(self.other_shop.request('PUT',f"/api/admin/coupons/{coupon['id']}",payload)['status'], 404)
        self.assertEqual(self.shop.request('PUT','/api/admin/coupons/1',payload)['status'], 404)
        self.ok(self.customer.post(f"/api/coupons/{coupon['id']}/claim"))
        self.ok(self.customer.post('/api/cart', {'product_id':6}))
        checkout = {'recipient':'顾客','phone':'13600136000','address':'上海市测试路100号',
            'idempotency_key':'permissions-coupon-0001','coupon_id':coupon['id'],'store_id':3}
        self.assertEqual(self.customer.post('/api/orders',checkout)['status'], 409)
        self.ok(self.customer.post('/api/cart', {'product_id':1}))
        checkout['store_id'] = 2
        self.assertEqual(self.ok(self.customer.post('/api/orders',checkout),201)['discount'],100)

    def test_unbound_admin_and_client_supplied_privileges_cannot_bypass_scope(self):
        credentials = {'name':'无店铺管理员','phone':'13500135000','password':'test123456'}
        client = Client(self.app)
        for extra in ({'role':'admin'}, {'is_super_admin':1}, {'store_id':2}):
            self.assertEqual(client.post('/api/auth/register',credentials | extra)['status'],422)
        user = self.ok(client.post('/api/auth/register',credentials),201)
        with self.app.state.database.connection() as connection:
            connection.execute("UPDATE users SET role='admin' WHERE id=?",(user['id'],))
        self.assertEqual(client.get('/api/admin/stores')['status'],403)
        self.assertEqual(client.login(credentials['phone'],credentials['password'],admin=True)['status'],403)
        self.ok(client.login(credentials['phone'],credentials['password']))
        self.assertFalse(self.ok(client.get('/api/auth/me'))['is_super_admin'])

    def test_terminal_assignments_revoke_sessions_and_survive_restarts(self):
        with self.app.state.database.connection() as connection:
            assign_admin(connection,'13900139000',super_admin=True)
        self.assertEqual(self.shop.get('/api/admin/stores')['status'],401)
        self.ok(self.shop.login('13900139000','test123456',admin=True))
        self.assertGreater(self.ok(self.shop.get('/api/admin/stores'))['total'],1)
        with self.app.state.database.connection() as connection:
            assign_admin(connection,'13900139000',3)
        restarted = MallApplication(self.path)
        client = Client(restarted)
        self.ok(client.login('13900139000','test123456',admin=True))
        self.assertEqual([s['id'] for s in self.ok(client.get('/api/admin/stores'))['items']],[3])
        self.assertEqual(client.get('/api/admin/stores/2')['status'],404)

    def test_random_generation_preserves_existing_accounts_and_permissions(self):
        output = Path(self.directory.name) / 'accounts.md'
        with self.app.state.database.connection() as connection:
            before = [tuple(r) for r in connection.execute('SELECT id,password_hash,store_id,is_super_admin FROM users ORDER BY id')]
            expected = connection.execute('SELECT COUNT(*) FROM stores').fetchone()[0]-2
            self.assertEqual(generate_store_admins(connection,output),expected)
            after = [tuple(r) for r in connection.execute('SELECT id,password_hash,store_id,is_super_admin FROM users WHERE id<=? ORDER BY id',(len(before),))]
            self.assertEqual(before,after)
            self.assertEqual(generate_store_admins(connection,output),0)
            self.assertEqual(connection.execute("SELECT COUNT(DISTINCT store_id) FROM users WHERE role='admin' AND is_super_admin=0").fetchone()[0],expected+2)
        self.assertEqual(os.stat(output).st_mode & 0o777,0o600)
        for line in output.read_text().splitlines()[4:]:
            fields = [value.strip() for value in line.split('|')]
            if len(fields)!=7 or not fields[1].isdigit():
                continue
            client = Client(self.app)
            self.ok(client.login(fields[4],fields[5],admin=True))
            self.assertEqual(self.ok(client.get('/api/admin/auth/me'))['store_id'],int(fields[1]))

    def test_legacy_platform_admin_upgrade_preserves_passwords_and_data(self):
        order = self.order(1)
        with self.app.state.database.connection() as connection:
            before = [tuple(r) for r in connection.execute('SELECT id,password_hash,role FROM users ORDER BY id')]
            connection.execute('ALTER TABLE users DROP COLUMN is_super_admin')
            connection.execute('ALTER TABLE users DROP COLUMN store_id')
        Database(self.path).initialize()
        with self.app.state.database.connection() as connection:
            self.assertEqual([tuple(r) for r in connection.execute('SELECT id,password_hash,role FROM users ORDER BY id')],before)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND is_super_admin=1").fetchone()[0],3)
            self.assertEqual(connection.execute("SELECT is_super_admin FROM users WHERE id=?",(self.customer_id,)).fetchone()[0],0)
            self.assertEqual(connection.execute('SELECT total FROM orders WHERE id=?',(order['id'],)).fetchone()[0],order['total'])
