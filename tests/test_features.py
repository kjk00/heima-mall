import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from app.server import MallApplication
from tests.client import Client


class FeaturesTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.app=MallApplication(Path(self.tmp.name)/'shop.db')
        self.user=Client(self.app);self.other=Client(self.app);self.admin=Client(self.app);self.guest=Client(self.app)
        self.uid=self.user.post('/api/auth/register',{'phone':'13800138000','name':'用户甲','password':'test123456'})['json']['id']
        self.other.post('/api/auth/register',{'phone':'13900139000','name':'用户乙','password':'test123456'})
        self.admin.post('/api/admin/setup',{'phone':'13700137000','name':'店铺客服','password':'test123456'})

    def ok(self,result,status=200):
        self.assertEqual(result['status'],status,result);return result['json']

    def address(self,client=None,**kw):
        data={'recipient':'小马','phone':'13800138000','address':'上海市浦东新区测试路100号','label':'家','is_default':False}|kw
        return self.ok((client or self.user).post('/api/addresses',data))

    def order(self,**kw):
        self.ok(self.user.post('/api/cart',{'product_id':1,'quantity':1}))
        data={'recipient':'小马','phone':'13800138000','address':'上海市浦东新区测试路100号','idempotency_key':'order-key-00000000001'}|kw
        return self.ok(self.user.post('/api/orders',data),201)

    def paid_order(self,**kw):
        o=self.order(**kw);return self.ok(self.user.post('/api/orders/'+o['id']+'/pay'))

    def refund(self,o,key='refund-key-0000000001'):
        return self.ok(self.user.post('/api/orders/'+o['id']+'/refund',{'reason':'商品不需要了','idempotency_key':key}))

    def approve(self,r,**kw):
        return self.ok(self.admin.post('/api/admin/refunds/'+str(r['id'])+'/decision',{'approve':True}|kw))

    def test_address_ownership_defaults_and_checkout_snapshot(self):
        a=self.address();b=self.address(recipient='公司',is_default=True)
        rows=self.ok(self.user.get('/api/addresses'));self.assertEqual([r['id'] for r in rows if r['is_default']],[b['id']])
        for method in ('PUT','DELETE'):
            r=self.other.request(method,f"/api/addresses/{a['id']}",{'recipient':'乙','phone':'13900139000','address':'另一个测试地址123'} if method=='PUT' else None)
            self.assertEqual(r['status'],404)
        o=self.order(address_id=b['id']);self.assertEqual(o['recipient'],'公司')
        self.ok(self.user.request('DELETE',f"/api/addresses/{b['id']}"))
        self.assertEqual(self.ok(self.user.get('/api/addresses'))[0]['is_default'],1)
        self.assertEqual(self.ok(self.user.get('/api/orders/'+o['id']))['recipient'],'公司')

    def test_checkout_cannot_use_another_users_address_and_saves_manual_once(self):
        a=self.address(client=self.other)
        self.ok(self.user.post('/api/cart',{'product_id':1}))
        payload={'recipient':'甲','phone':'13800138000','address':'手动输入的测试地址','idempotency_key':'address-key-000000001','address_id':a['id']}
        self.assertEqual(self.user.post('/api/orders',payload)['status'],404)
        self.assertEqual(self.ok(self.user.get('/api/cart'))['count'],1)
        payload.update(address_id=None,save_address=True)
        o=self.ok(self.user.post('/api/orders',payload),201)
        self.assertEqual(self.ok(self.user.post('/api/orders',payload),201)['id'],o['id'])
        self.assertEqual(len(self.ok(self.user.get('/api/addresses'))),1)

    def test_settings_password_revokes_other_sessions_preserves_current(self):
        second=Client(self.app);self.ok(second.login('13800138000','test123456'))
        self.ok(self.user.request('PUT','/api/settings',{'name':'新昵称','notify_support':False}))
        self.assertEqual(self.ok(self.user.get('/api/auth/me'))['name'],'新昵称')
        self.assertEqual(self.user.post('/api/settings/password',{'old_password':'wrong1234','new_password':'newpass123'})['status'],400)
        self.ok(self.user.post('/api/settings/password',{'old_password':'test123456','new_password':'newpass123'}))
        self.assertEqual(second.get('/api/auth/me')['status'],401)
        self.assertEqual(self.user.get('/api/auth/me')['status'],200)
        self.assertEqual(self.user.get('/api/settings')['json']['notify_support'],0)

    def test_coupon_applies_cents_and_cancel_releases_it(self):
        c=self.ok(self.user.get('/api/coupons'))[-1]
        self.ok(self.user.post(f"/api/coupons/{c['id']}/claim"))
        self.ok(self.user.post(f"/api/coupons/{c['id']}/claim"))
        o=self.order(coupon_id=c['id']);self.assertEqual(o['discount'],1000);self.assertEqual(o['total'],o['subtotal']+o['shipping_fee']-1000)
        self.assertEqual(self.ok(self.user.get('/api/coupons'))[-1]['used_order_id'],o['id'])
        self.ok(self.user.post('/api/orders/'+o['id']+'/cancel'))
        self.assertIsNone(self.ok(self.user.get('/api/coupons'))[-1]['used_order_id'])

    def test_coupon_threshold_rate_rounding_shipping_and_expiry(self):
        c=self.ok(self.user.get('/api/coupons'))[0];self.ok(self.user.post(f"/api/coupons/{c['id']}/claim"))
        with self.app.state.database.connection() as db:db.execute('UPDATE products SET price=19901 WHERE id=1')
        o=self.order(coupon_id=c['id']);self.assertEqual(o['discount'],1990);self.assertEqual(o['shipping_fee'],0)
        self.ok(self.user.post('/api/orders/'+o['id']+'/cancel'))
        with self.app.state.database.connection() as db:db.execute("UPDATE coupons SET expires_at='2021-01-01T00:00:00Z' WHERE id=?",(c['id'],))
        self.ok(self.user.post('/api/cart',{'product_id':1}))
        bad={'recipient':'甲','phone':'13800138000','address':'上海测试地址123','coupon_id':c['id'],'idempotency_key':'new-order-key-000001'}
        self.assertEqual(self.user.post('/api/orders',bad)['status'],409)
        self.assertEqual(self.ok(self.user.get('/api/cart'))['count'],1)
        with self.app.state.database.connection() as db:self.assertIsNone(db.execute('SELECT used_order_id FROM user_coupons WHERE coupon_id=?',(c['id'],)).fetchone()[0])

    def test_coupon_requires_claim_and_threshold_caps_large_discounts(self):
        c=self.ok(self.user.get('/api/coupons'))[0]
        self.ok(self.user.post('/api/cart',{'product_id':6}))
        payload={'recipient':'甲','phone':'13800138000','address':'上海市测试路123','coupon_id':c['id'],'idempotency_key':'threshold-order-key-001'}
        self.assertEqual(self.user.post('/api/orders',payload)['status'],409)
        self.ok(self.user.post(f"/api/coupons/{c['id']}/claim"))
        self.assertEqual(self.user.post('/api/orders',payload)['status'],409)
        with self.app.state.database.connection() as db:db.execute('UPDATE coupons SET threshold=0,kind=\'amount\',value=10000,max_discount=100000 WHERE id=?',(c['id'],))
        o=self.ok(self.user.post('/api/orders',payload),201)
        self.assertEqual(o['discount'],6900);self.assertEqual(o['total'],1000)

    def test_concurrent_coupon_last_claim_and_redemption(self):
        c=self.ok(self.user.get('/api/coupons'))[-1]
        with self.app.state.database.connection() as db:db.execute('UPDATE coupons SET claim_limit=1 WHERE id=?',(c['id'],))
        barrier=threading.Barrier(2)
        def claim(client):barrier.wait();return client.post(f"/api/coupons/{c['id']}/claim")
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(claim,[self.user,self.other]))
        self.assertEqual(sorted(r['status'] for r in results),[200,409])
        winner=self.user if results[0]['status']==200 else self.other
        self.ok(winner.post('/api/cart',{'product_id':1}))
        barrier=threading.Barrier(2)
        def buy(key):
            client=Client(self.app);client.cookies=winner.cookies.copy();barrier.wait()
            return client.post('/api/orders',{'recipient':'甲','phone':'13800138000','address':'上海市测试路123','coupon_id':c['id'],'idempotency_key':key})
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(buy,['concurrent-key-001','concurrent-key-002']))
        self.assertEqual(sorted(r['status'] for r in results),[201,400])
        with self.app.state.database.connection() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM orders WHERE coupon_id=?',(c['id'],)).fetchone()[0],1)

    def test_refund_only_paid_owned_orders_and_request_idempotency(self):
        o=self.order();self.assertEqual(self.user.post('/api/orders/'+o['id']+'/refund',{'reason':'退货','idempotency_key':'refund-test-key-001'})['status'],409)
        self.ok(self.user.post('/api/orders/'+o['id']+'/pay'));r=self.refund(o);self.assertEqual(self.refund(o)['id'],r['id'])
        self.assertEqual(self.other.post('/api/orders/'+o['id']+'/refund',{'reason':'退货','idempotency_key':'refund-test-key-001'})['status'],404)
        self.assertEqual(self.user.post('/api/orders/'+o['id']+'/pay')['status'],409)
        self.assertEqual(self.admin.post('/api/admin/orders/'+o['id']+'/ship',{'carrier':'顺丰','tracking_no':'SF123456'})['status'],409)

    def test_refund_approval_restores_inventory_sales_coupon_and_revenue_once(self):
        before=self.ok(self.user.get('/api/products/1'))
        c=self.ok(self.user.get('/api/coupons'))[-1];self.ok(self.user.post(f"/api/coupons/{c['id']}/claim"))
        o=self.paid_order(coupon_id=c['id']);r=self.refund(o);self.assertEqual(r['amount'],o['total'])
        self.approve(r);self.approve(r)
        p=self.ok(self.user.get('/api/products/1'));self.assertEqual(p['stock'],before['stock']);self.assertEqual(p['sales'],before['sales'])
        self.assertEqual(self.ok(self.admin.get('/api/admin/dashboard'))['summary']['revenue'],0)
        self.assertIsNone(self.ok(self.user.get('/api/coupons'))[-1]['used_order_id'])
        users=self.ok(self.admin.get('/api/admin/users'))['items'];self.assertEqual(next(u for u in users if u['id']==self.uid)['spent'],0)

    def test_shipped_refund_only_restocks_when_return_received(self):
        before=self.ok(self.user.get('/api/products/1'))
        o=self.paid_order();self.ok(self.admin.post('/api/admin/orders/'+o['id']+'/ship',{'carrier':'顺丰','tracking_no':'SF123456'}));r=self.refund(o);self.approve(r)
        self.assertEqual(self.ok(self.user.get('/api/products/1'))['stock'],before['stock']-1)
        self.assertEqual(self.user.post('/api/orders/'+o['id']+'/receive')['status'],409)
        o2=self.paid_order(idempotency_key='second-order-key-0001');self.ok(self.admin.post('/api/admin/orders/'+o2['id']+'/ship',{'carrier':'顺丰','tracking_no':'SF123457'}));r2=self.refund(o2,'second-refund-key-001');self.approve(r2,restock=True)
        self.assertEqual(self.ok(self.user.get('/api/products/1'))['stock'],before['stock']-1)

    def test_concurrent_refund_approval_changes_inventory_once(self):
        before=self.ok(self.user.get('/api/products/1'))
        r=self.refund(self.paid_order());barrier=threading.Barrier(2)
        def approve(_):
            client=Client(self.app);client.cookies=self.admin.cookies.copy();barrier.wait()
            return client.post(f"/api/admin/refunds/{r['id']}/decision",{'approve':True})
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(approve,range(2)))
        self.assertEqual([r['status'] for r in results],[200,200])
        p=self.ok(self.user.get('/api/products/1'));self.assertEqual(p['stock'],before['stock']);self.assertEqual(p['sales'],before['sales'])

    def test_rejected_refund_can_be_reapplied_and_cannot_change_decision(self):
        o=self.paid_order();r=self.refund(o)
        self.assertEqual(self.admin.post(f"/api/admin/refunds/{r['id']}/decision",{'approve':False})['status'],422)
        self.ok(self.admin.post(f"/api/admin/refunds/{r['id']}/decision",{'approve':False,'note':'请补充原因'}))
        self.assertEqual(self.admin.post(f"/api/admin/refunds/{r['id']}/decision",{'approve':True})['status'],409)
        r2=self.refund(o,'second-refund-key-001');self.assertNotEqual(r['id'],r2['id'])

    def test_tracking_progress_no_backwards_requires_shipment_and_idempotent(self):
        o=self.paid_order();url='/api/admin/orders/'+o['id']+'/tracking'
        self.assertEqual(self.admin.post(url,{'stage':'transit'})['status'],409)
        self.ok(self.admin.post(url,{'stage':'packing','note':'正在打包'}));self.ok(self.admin.post(url,{'stage':'packing'}))
        self.ok(self.admin.post('/api/admin/orders/'+o['id']+'/ship',{'carrier':'顺丰','tracking_no':'SF123456'}))
        self.ok(self.admin.post(url,{'stage':'transit'}));self.assertEqual(self.admin.post(url,{'stage':'packing'})['status'],409)
        self.ok(self.admin.post(url,{'stage':'delivered'}));result=self.ok(self.user.get('/api/orders/'+o['id']))
        self.assertEqual(result['delivery_stage'],'delivered');self.assertEqual([r['stage'] for r in result['tracking_events']].count('packing'),1)
        result=self.ok(self.user.post('/api/orders/'+o['id']+'/receive'));self.assertEqual(result['fulfillment_status'],'completed')

    def test_history_private_latest_unique_and_clear(self):
        for pid in (1,2,1):self.ok(self.user.post('/api/history',{'product_id':pid}))
        rows=self.ok(self.user.get('/api/history'));self.assertEqual(rows['total'],2);self.assertEqual(rows['items'][0]['id'],1)
        self.assertEqual(self.ok(self.other.get('/api/history'))['total'],0)
        self.ok(self.user.request('DELETE','/api/history'));self.assertEqual(self.ok(self.user.get('/api/history'))['total'],0)

    def test_public_comments_visible_to_guests_private_fields_not_exposed(self):
        self.assertEqual(self.guest.post('/api/products/1/comments',{'content':'测试'})['status'],401)
        c=self.ok(self.user.post('/api/products/1/comments',{'content':'这款商品怎么样？<script>alert(1)</script>'}))
        rows=self.ok(self.guest.get('/api/products/1/comments'))
        self.assertEqual(rows['total'],1);self.assertEqual(rows['items'][0]['name'],'用户甲')
        self.assertNotIn('phone',rows['items'][0]);self.assertNotIn('user_id',rows['items'][0])
        self.assertEqual(self.other.request('PATCH',f"/api/admin/discussions/{c['id']}",{'is_visible':False})['status'],403)
        self.ok(self.admin.request('PATCH',f"/api/admin/discussions/{c['id']}",{'is_visible':False}));self.assertEqual(self.ok(self.guest.get('/api/products/1/comments'))['total'],0)
        self.ok(self.admin.request('PATCH',f"/api/admin/discussions/{c['id']}",{'is_visible':True}));self.assertEqual(self.ok(self.guest.get('/api/products/1/comments'))['total'],1)

    def test_verified_reviews_purchase_required_edit_and_moderation_stays_hidden(self):
        data={'rating':5,'content':'很好用'}
        self.assertEqual(self.user.request('PUT','/api/products/1/reviews',data)['status'],403)
        o=self.paid_order();self.ok(self.user.request('PUT','/api/products/1/reviews',data))
        self.ok(self.user.request('PUT','/api/products/1/reviews',{'rating':4,'content':'更新评价'}))
        rows=self.ok(self.guest.get('/api/products/1/reviews'));self.assertEqual(rows['total'],1);self.assertEqual(rows['average_rating'],4)
        rid=rows['items'][0]['id'];self.ok(self.admin.request('PATCH',f'/api/admin/discussions/{rid}?kind=reviews',{'is_visible':False}))
        self.ok(self.user.request('PUT','/api/products/1/reviews',data));self.assertEqual(self.ok(self.guest.get('/api/products/1/reviews'))['total'],0)
        self.approve(self.refund(o));self.assertEqual(self.user.request('PUT','/api/products/1/reviews',data)['status'],403)

    def test_support_real_admin_reply_ownership_idempotent_and_read_race(self):
        self.assertEqual(self.ok(self.user.get('/api/support/messages'))['items'],[])
        payload={'content':'你好，查询订单','client_message_id':'message-key-00000001'}
        a=self.ok(self.user.post('/api/support/messages',payload));self.assertEqual(self.ok(self.user.post('/api/support/messages',payload))['id'],a['id'])
        self.assertEqual(self.user.post('/api/support/messages',payload|{'content':'改过的消息'})['status'],409)
        c=self.ok(self.admin.get('/api/admin/support'))['items'][0];self.assertEqual(c['unread'],1)
        self.assertEqual(self.other.get(f"/api/admin/support/{c['id']}/messages")['status'],403)
        self.assertEqual(self.ok(self.other.get('/api/support/messages'))['items'],[])
        self.ok(self.admin.post(f"/api/admin/support/{c['id']}/read",{'last_message_id':a['id']}))
        reply=self.ok(self.admin.post(f"/api/admin/support/{c['id']}/messages",{'content':'正在查看您的订单','client_message_id':'staff-message-000001'}))
        first=self.ok(self.user.get('/api/support/messages'));self.assertEqual(first['unread'],1)
        second=self.ok(self.admin.post(f"/api/admin/support/{c['id']}/messages",{'content':'已发货','client_message_id':'staff-message-000002'}))
        self.ok(self.user.post('/api/support/read',{'last_message_id':reply['id']}))
        self.assertEqual(self.ok(self.user.get('/api/support/messages'))['unread'],1)
        self.ok(self.user.post('/api/support/read',{'last_message_id':second['id']}));self.ok(self.user.post('/api/support/read',{'last_message_id':reply['id']}))
        self.assertEqual(self.ok(self.user.get('/api/support/messages'))['unread'],0)
        self.assertEqual(self.user.post('/api/support/read',{'last_message_id':999999})['status'],422)

    def test_support_pagination_and_cannot_mark_other_conversation_read(self):
        for i in range(5):self.ok(self.user.post('/api/support/messages',{'content':str(i),'client_message_id':f'message-pagination-{i:04}'}))
        rows=self.ok(self.user.get('/api/support/messages?page_size=2'));self.assertEqual([m['content'] for m in rows['items']],['3','4'])
        earlier=self.ok(self.user.get(f"/api/support/messages?page_size=2&before_id={rows['items'][0]['id']}"));self.assertEqual([m['content'] for m in earlier['items']],['1','2'])
        msg=self.ok(self.other.post('/api/support/messages',{'content':'乙的消息','client_message_id':'other-message-000001'}))
        self.assertEqual(self.user.post('/api/support/read',{'last_message_id':msg['id']})['status'],422)

    def test_coupon_admin_terms_permissions_validation_and_discount_filter(self):
        c={'code':'EXTRA10','name':'测试券','kind':'amount','value':1000,'threshold':0,'max_discount':0,'claim_limit':1,'starts_at':'2020-01-01T00:00:00Z','expires_at':'2100-01-01T00:00:00Z','is_active':True}
        self.assertEqual(self.user.post('/api/admin/coupons',c)['status'],403)
        saved=self.ok(self.admin.post('/api/admin/coupons',c));self.ok(self.user.post(f"/api/coupons/{saved['id']}/claim"))
        self.assertEqual(self.admin.request('PUT',f"/api/admin/coupons/{saved['id']}",c|{'value':2000})['status'],409)
        self.ok(self.admin.request('PUT',f"/api/admin/coupons/{saved['id']}",c|{'is_active':False}))
        self.assertEqual(self.other.post(f"/api/coupons/{saved['id']}/claim")['status'],409)
        with self.app.state.database.connection() as db:db.execute('UPDATE products SET original_price=price WHERE id=1')
        rows=self.ok(self.guest.get('/api/products?on_sale=true'));self.assertNotIn(1,[p['id'] for p in rows['items']])
        self.assertEqual(self.guest.get('/api/products?on_sale=unknown')['status'],422)
        self.assertEqual(self.user.get('/api/products/1/comments?page=0')['status'],422)
        self.assertEqual(self.admin.get('/api/admin/discussions?kind=unknown')['status'],422)


if __name__=='__main__': unittest.main()
