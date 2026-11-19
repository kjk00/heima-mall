import tempfile
import unittest
from pathlib import Path
from app.server import MallApplication
from app.admin import PRODUCT_INPUT_FIELDS, LEGACY_PRODUCT_FIELDS
from tests.client import Client


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'stores.db';self.app=MallApplication(self.path)
        self.user=Client(self.app);self.other=Client(self.app);self.manager=Client(self.app);self.guest=Client(self.app)
        self.user.post('/api/auth/register',{'phone':'13800138000','name':'用户甲','password':'test123456'})
        self.other.post('/api/auth/register',{'phone':'13900139000','name':'用户乙','password':'test123456'})
        self.manager.post('/api/admin/setup',{'phone':'13700137000','name':'平台管理员','password':'test123456'})

    def ok(self,r,status=200):
        self.assertEqual(r['status'],status,r);return r['json']

    def create_store(self,**changes):
        return self.ok(self.manager.post('/api/admin/stores',{'name':'测试新店','description':'耳机与日常精品','phone':'13800138000','shipping_fee':800,'free_shipping_threshold':20000,'is_active':True}|changes),201)

    def edit_store(self,s,**changes):
        fields=('name','description','phone','shipping_fee','free_shipping_threshold','is_active','version')
        payload={k:s[k] for k in fields};payload['is_active']=bool(payload['is_active']);payload.update(changes)
        return self.manager.request('PUT',f"/api/admin/stores/{s['id']}",payload)

    def move_product(self,pid,store_id):
        p=self.ok(self.manager.get('/api/admin/products/'+str(pid)))
        payload={k:p[k] for k in PRODUCT_INPUT_FIELDS};payload['is_active']=bool(payload['is_active']);payload.update(store_id=store_id,version=p['version'])
        return self.manager.request('PUT','/api/admin/products/'+str(pid),payload)

    def add(self,pid):return self.ok(self.user.post('/api/cart',{'product_id':pid}))

    def checkout(self,store_id=None,key='store-order-key-0001'):
        payload={'recipient':'用户甲','phone':'13800138000','address':'上海市测试路123号','idempotency_key':key}
        if store_id is not None:payload['store_id']=store_id
        return self.user.post('/api/orders',payload)

    def send(self,store_id,content='查询商品',client=None,key='store-message-key-001'):
        return (client or self.user).post('/api/support/messages',{'store_id':store_id,'content':content,'client_message_id':key})

    def test_product_store_assignment_public_filter_and_admin_options(self):
        p1=self.ok(self.guest.get('/api/products/1'));p6=self.ok(self.guest.get('/api/products/6'))
        self.assertNotEqual(p1['store_id'],p6['store_id']);self.assertTrue(p1['store_name'])
        rows=self.ok(self.guest.get('/api/products?store_id=2'))['items'];self.assertTrue(rows);self.assertTrue(all(p['store_id']==2 for p in rows))
        self.assertEqual(self.ok(self.guest.get('/api/stores/2'))['products'],len(rows))
        self.assertEqual(self.guest.get('/api/products?store_id=0')['status'],422)
        self.assertIn('stores',self.ok(self.manager.get('/api/admin/options')))

    def test_store_crud_permissions_unique_names_version_and_restart(self):
        payload={'name':'不能创建的店铺'}
        self.assertEqual(self.user.post('/api/admin/stores',payload)['status'],403)
        self.assertEqual(self.guest.get('/api/admin/stores')['status'],401)
        s=self.create_store();self.assertEqual(self.manager.post('/api/admin/stores',{'name':s['name']})['status'],409)
        edited=self.ok(self.edit_store(s,name='新名称',shipping_fee=0,free_shipping_threshold=0))
        self.assertEqual(self.edit_store(s,name='旧窗口保存')['status'],409)
        self.assertEqual(self.ok(self.guest.get('/api/stores/'+str(s['id'])))['shipping_fee'],0)
        self.ok(self.move_product(1,s['id']))
        p=self.ok(self.manager.get('/api/admin/products/1'))
        legacy={k:p[k] for k in LEGACY_PRODUCT_FIELDS}
        legacy.update(is_active=bool(p['is_active']),version=p['version'],subtitle='旧客户端修改介绍')
        self.ok(self.manager.request('PUT','/api/admin/products/1',legacy))
        self.assertEqual(self.ok(self.guest.get('/api/products/1'))['store_id'],s['id'])
        restarted=Client(MallApplication(self.path));self.assertEqual(self.ok(restarted.get('/api/products/1'))['store_id'],s['id'])
        self.assertEqual(self.ok(restarted.get('/api/stores/'+str(s['id'])))['name'],'新名称')
        self.assertEqual(edited['version'],s['version']+1)

    def test_store_search_status_pagination_and_bad_input(self):
        self.create_store(name='测试%_店铺')
        rows=self.ok(self.manager.get('/api/admin/stores?q=%25_'));self.assertEqual(rows['total'],1)
        self.assertEqual(self.ok(self.manager.get('/api/admin/stores?page_size=1&page=2'))['page'],2)
        for payload in ({'name':'','shipping_fee':1000},{'name':'测试','shipping_fee':-1},{'name':'测试','is_active':'true'},{'name':'测试','phone':'非法电话'}):
            self.assertEqual(self.manager.post('/api/admin/stores',payload)['status'],422)
        self.assertEqual(self.manager.get('/api/admin/stores?status=unknown')['status'],422)
        self.assertEqual(self.manager.get('/api/admin/products?store_id=0')['status'],422)

    def test_checkout_separate_store_subtotals_shipping_and_remaining_cart(self):
        self.add(2);cart=self.add(6)
        self.assertEqual(len(cart['groups']),2);self.assertEqual(cart['shipping_fee'],2000)
        self.assertEqual(self.checkout()['status'],409)
        o=self.ok(self.checkout(4),201)
        self.assertEqual([i['product_id'] for i in o['items']],[2]);self.assertEqual(o['shipping_fee'],1000)
        self.assertEqual(o['store_id'],4);self.assertEqual(o['store_name'],'城市穿搭精品店')
        remaining=self.ok(self.user.get('/api/cart'));self.assertEqual([i['id'] for i in remaining['items']],[6])
        self.assertEqual(self.ok(self.checkout(4),201)['id'],o['id'])
        self.assertEqual(self.checkout(3)['status'],409)
        second=self.ok(self.checkout(3,'store-order-key-0002'),201);self.assertEqual([i['product_id'] for i in second['items']],[6])
        self.assertEqual(self.ok(self.user.get('/api/cart'))['count'],0)

    def test_coupon_threshold_uses_only_selected_store_and_rollback(self):
        c=self.ok(self.user.get('/api/coupons'))[-1];self.ok(self.user.post(f"/api/coupons/{c['id']}/claim"))
        self.add(2);self.add(6)
        payload={'recipient':'甲','phone':'13800138000','address':'上海市测试路123','idempotency_key':'coupon-store-key-001','store_id':3,'coupon_id':c['id']}
        self.assertEqual(self.user.post('/api/orders',payload)['status'],409)
        self.assertEqual(self.ok(self.user.get('/api/cart'))['count'],2)
        payload['store_id']=4;o=self.ok(self.user.post('/api/orders',payload),201)
        self.assertEqual(o['subtotal'],18900);self.assertEqual(o['discount'],1000);self.assertEqual(o['total'],18900)
        self.assertEqual(self.ok(self.user.get('/api/cart'))['count'],1)

    def test_store_shipping_settings_order_snapshot_and_product_move(self):
        s=self.create_store(name='免邮店',shipping_fee=0,free_shipping_threshold=0);self.ok(self.move_product(6,s['id']))
        self.add(6);o=self.ok(self.checkout(s['id']),201);self.assertEqual(o['shipping_fee'],0)
        s=self.ok(self.edit_store(s,name='店铺改名',shipping_fee=1800,free_shipping_threshold=90000))
        self.ok(self.move_product(6,3))
        saved=self.ok(self.user.get('/api/orders/'+o['id']));self.assertEqual(saved['store_name'],'免邮店');self.assertEqual(saved['store_id'],s['id']);self.assertEqual(saved['shipping_fee'],0)
        self.assertEqual(self.ok(self.manager.get('/api/admin/orders?store_id='+str(s['id'])))['total'],1)
        self.assertEqual(self.ok(self.manager.get('/api/admin/products?store_id='+str(s['id'])))['total'],0)

    def test_paused_store_blocks_new_purchases_preserves_paid_after_sales(self):
        self.add(6);o=self.ok(self.checkout(3),201);self.ok(self.user.post('/api/orders/'+o['id']+'/pay'))
        self.add(6);s=self.ok(self.manager.get('/api/admin/stores/3'))
        self.ok(self.manager.request('PATCH','/api/admin/stores/3/status',{'is_active':False,'version':s['version']}))
        self.assertEqual(self.guest.get('/api/products/6')['status'],404)
        self.assertEqual(self.ok(self.guest.get('/api/products?store_id=3'))['total'],0)
        self.assertNotIn(3,[s['id'] for s in self.ok(self.guest.get('/api/stores'))])
        self.assertEqual(self.user.post('/api/cart',{'product_id':6})['status'],409)
        self.assertEqual(self.checkout(3,'paused-order-key-001')['status'],409)
        self.ok(self.manager.post('/api/admin/orders/'+o['id']+'/ship',{'carrier':'顺丰','tracking_no':'SF123456'}))
        refund=self.ok(self.user.post('/api/orders/'+o['id']+'/refund',{'reason':'退货','idempotency_key':'paused-refund-key-001'}))
        self.ok(self.manager.post(f"/api/admin/refunds/{refund['id']}/decision",{'approve':True}))
        self.assertEqual(self.ok(self.user.get('/api/orders/'+o['id']))['refund']['status'],'approved')

    def test_store_conversations_independent_ids_messages_cursors_and_admin_filter(self):
        a=self.ok(self.send(2,'耳机的问题'));b=self.ok(self.send(3,'杯子的问题'))
        self.assertNotEqual(a['id'],b['id'])
        self.assertEqual(self.ok(self.send(2,'耳机的问题'))['id'],a['id'])
        two=self.ok(self.user.get('/api/support/messages?store_id=2'));three=self.ok(self.user.get('/api/support/messages?store_id=3'))
        self.assertNotEqual(two['conversation_id'],three['conversation_id'])
        self.assertEqual([m['content'] for m in two['items']],['耳机的问题']);self.assertEqual([m['content'] for m in three['items']],['杯子的问题'])
        self.assertEqual(self.user.post('/api/support/read',{'store_id':3,'last_message_id':a['id']})['status'],422)
        self.assertEqual(self.ok(self.other.get('/api/support/messages?store_id=2'))['items'],[])
        self.assertEqual(len(self.ok(self.user.get('/api/support/conversations'))['items']),2)
        inbox=self.ok(self.manager.get('/api/admin/support?store_id=2'));self.assertEqual(len(inbox['items']),1);self.assertEqual(inbox['items'][0]['store_id'],2)
        cid=inbox['items'][0]['id'];reply=self.ok(self.manager.post(f'/api/admin/support/{cid}/messages',{'content':'本店客服回复','client_message_id':'store-staff-key-0001'}))
        self.assertEqual(self.ok(self.user.get('/api/support/messages?store_id=3'))['unread'],0)
        self.assertEqual(self.ok(self.user.get('/api/support/messages?store_id=2'))['unread'],1)
        self.ok(self.user.post('/api/support/read',{'store_id':2,'last_message_id':reply['id']}))
        self.assertEqual(self.ok(self.user.get('/api/support/conversations'))['unread'],0)

    def test_paused_store_keeps_chat_history_and_staff_replies(self):
        msg=self.ok(self.send(2));s=self.ok(self.manager.get('/api/admin/stores/2'))
        self.ok(self.manager.request('PATCH','/api/admin/stores/2/status',{'is_active':False,'version':s['version']}))
        self.assertEqual(self.send(2,content='新问题',key='paused-message-00001')['status'],409)
        history=self.ok(self.user.get('/api/support/messages?store_id=2'));self.assertEqual(history['store_active'],0);self.assertEqual(history['items'][0]['id'],msg['id'])
        cid=history['conversation_id'];self.ok(self.manager.post(f'/api/admin/support/{cid}/messages',{'content':'售后仍可处理','client_message_id':'paused-staff-key-001'}))
        self.assertEqual(len(self.ok(self.user.get('/api/support/messages?store_id=2'))['items']),2)

    def test_search_store_names_returns_store_and_its_products_literal_safe(self):
        s=self.create_store(name='月光精品店');self.ok(self.move_product(1,s['id']))
        stores=self.ok(self.guest.get('/api/stores?q=月光'));self.assertEqual([r['id'] for r in stores],[s['id']])
        products=self.ok(self.guest.get('/api/products?q=月光'));self.assertEqual([p['id'] for p in products['items']],[1])
        rec=self.ok(self.guest.get('/api/search/suggestions?q=月光'));self.assertEqual(rec['stores'][0]['name'],'月光精品店');self.assertEqual(rec['products'][0]['id'],1)
        self.assertEqual(self.ok(self.guest.get('/api/products?q=%25'))['total'],0)
        self.assertEqual(self.ok(self.guest.get('/api/stores?q=%25')),[])
        self.assertEqual(self.ok(self.guest.get('/api/search/suggestions?q=%25'))['products'],[])

    def test_search_recommendations_use_own_recent_history_and_hide_paused_stores(self):
        self.ok(self.user.post('/api/history',{'product_id':6}))
        mine=self.ok(self.user.get('/api/search/suggestions'));self.assertTrue(mine['personalized']);self.assertEqual(mine['products'][0]['id'],6);self.assertEqual(mine['products'][0]['suggestion_label'],'最近浏览');self.assertEqual(mine['stores'][0]['id'],3)
        for client in (self.guest,self.other):
            result=self.ok(client.get('/api/search/suggestions'));self.assertFalse(result['personalized']);self.assertNotIn('最近浏览',[p['suggestion_label'] for p in result['products']])
        s=self.ok(self.manager.get('/api/admin/stores/3'));self.ok(self.manager.request('PATCH','/api/admin/stores/3/status',{'is_active':False,'version':s['version']}))
        mine=self.ok(self.user.get('/api/search/suggestions'));self.assertNotIn(6,[p['id'] for p in mine['products']]);self.assertNotIn(3,[s['id'] for s in mine['stores']])
        self.assertEqual(self.guest.get('/api/search/suggestions?q='+'x'*101)['status'],422)

    def test_legacy_conversation_upgrade_keeps_messages_and_read_positions(self):
        first=self.ok(self.send(1,'旧聊天'))
        history=self.ok(self.user.get('/api/support/messages'));cid=history['conversation_id']
        reply=self.ok(self.manager.post(f'/api/admin/support/{cid}/messages',{'content':'旧回复','client_message_id':'legacy-staff-key-001'}))
        self.ok(self.user.post('/api/support/read',{'last_message_id':reply['id']}))
        with self.app.state.database.connection() as db:
            db.execute('PRAGMA foreign_keys=OFF');db.execute('BEGIN IMMEDIATE')
            db.execute('''CREATE TABLE old_conversations(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL UNIQUE REFERENCES users(id),customer_read_id INTEGER NOT NULL DEFAULT 0,staff_read_id INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL)''')
            db.execute('INSERT INTO old_conversations SELECT id,user_id,customer_read_id,staff_read_id,created_at FROM conversations')
            db.execute('DROP TABLE conversations');db.execute('ALTER TABLE old_conversations RENAME TO conversations');db.execute('COMMIT')
        restarted=MallApplication(self.path);client=Client(restarted);client.cookies=self.user.cookies.copy()
        history=self.ok(client.get('/api/support/messages?store_id=1'))
        self.assertEqual(history['conversation_id'],cid);self.assertEqual([m['id'] for m in history['items']],[first['id'],reply['id']]);self.assertEqual(history['unread'],0)
        self.ok(self.send(2,client=client,key='migration-new-key-001'))
        self.assertEqual(len(self.ok(client.get('/api/support/conversations'))['items']),2)
        with restarted.state.database.connection() as db:self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])


if __name__=='__main__':unittest.main()
