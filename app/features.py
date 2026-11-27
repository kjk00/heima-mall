"""Account, promotions, after-sales, product discussions and shop support."""
import sqlite3
import time
from dataclasses import asdict
from datetime import datetime, timezone
from app import logic
from app.admin import admin_order, require_admin, scope_clause, store_scope
from app.async_contract import transaction
from app.errors import MallError
from app.security import hash_password_async, verify_password_async, create_session


def now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


async def page_data(connection, sql, args=(), page=1, page_size=10):
    total = (await connection.execute('SELECT COUNT(*) FROM (' + sql + ') AS page_rows', args)).fetchone()[0]
    rows = (await connection.execute(sql + ' LIMIT ? OFFSET ?', (*args, page_size, (page-1)*page_size)))
    return {'items': [dict(r) for r in rows], 'total': total, 'page': page, 'page_size': page_size}


async def addresses(user, connection):
    return [dict(r) for r in (await connection.execute('SELECT * FROM addresses WHERE user_id=? ORDER BY is_default DESC,id DESC', (user['id'],)))]


async def address_row(address_id, user, connection):
    row = (await connection.execute('SELECT * FROM addresses WHERE id=? AND user_id=?', (address_id,user['id']))).fetchone()
    if not row: raise MallError(404,'地址不存在')
    return dict(row)


async def save_address(payload, user, connection, address_id=None):
    if address_id is not None: (await address_row(address_id,user,connection))
    count = (await connection.execute('SELECT COUNT(*) FROM addresses WHERE user_id=?',(user['id'],))).fetchone()[0]
    if address_id is None and count >= 50: raise MallError(409,'最多保存 50 个地址')
    make_default = payload.is_default or not (await connection.execute('SELECT 1 FROM addresses WHERE user_id=? AND is_default=1',(user['id'],))).fetchone()
    if make_default: (await connection.execute('UPDATE addresses SET is_default=0 WHERE user_id=?',(user['id'],)))
    values = (payload.recipient,payload.phone,payload.address,payload.label,make_default)
    if address_id is None:
        address_id = (await connection.execute('INSERT INTO addresses(recipient,phone,address,label,is_default,user_id) VALUES (?,?,?,?,?,?)',(*values,user['id']))).lastrowid
    else:
        (await connection.execute('UPDATE addresses SET recipient=?,phone=?,address=?,label=?,is_default=? WHERE id=? AND user_id=?',(*values,address_id,user['id'])))
    # Turning off the only default picks another address (or retains this one).
    if not (await connection.execute('SELECT 1 FROM addresses WHERE user_id=? AND is_default=1',(user['id'],))).fetchone():
        (await connection.execute('UPDATE addresses SET is_default=1 WHERE id=(SELECT id FROM addresses WHERE user_id=? ORDER BY (id=?) ASC,id LIMIT 1)',(user['id'],address_id)))
    return (await address_row(address_id,user,connection))


async def create_address(payload,user,connection):
    async with transaction(connection):
        await connection.lock_user(user['id'])
        return (await save_address(payload,user,connection))


async def update_address(address_id,payload,user,connection):
    async with transaction(connection):
        await connection.lock_user(user['id'])
        return (await save_address(payload,user,connection,address_id))


async def delete_address(address_id,user,connection):
    async with transaction(connection):
        await connection.lock_user(user['id'])
        (await address_row(address_id,user,connection))
        (await connection.execute('DELETE FROM addresses WHERE id=? AND user_id=?',(address_id,user['id'])))
        if not (await connection.execute('SELECT 1 FROM addresses WHERE user_id=? AND is_default=1',(user['id'],))).fetchone():
            (await connection.execute('UPDATE addresses SET is_default=1 WHERE id=(SELECT id FROM addresses WHERE user_id=? ORDER BY id DESC LIMIT 1)',(user['id'],)))
    return (await addresses(user,connection))


async def settings(user,connection):
    return dict((await connection.execute('SELECT name,phone,notify_support FROM users WHERE id=?',(user['id'],))).fetchone())


async def update_settings(payload,user,connection):
    (await connection.execute('UPDATE users SET name=?,notify_support=? WHERE id=?',(payload.name,payload.notify_support,user['id'])))
    return (await settings(user,connection))


async def change_password(payload,user,response,connection):
    async with transaction(connection):
        await connection.lock_user(user['id'])
        row = (await connection.execute('SELECT password_hash FROM users WHERE id=?',(user['id'],))).fetchone()
        if not (await verify_password_async(payload.old_password,row['password_hash'])): raise MallError(400,'原密码不正确')
        (await connection.execute('UPDATE users SET password_hash=? WHERE id=?',((await hash_password_async(payload.new_password)),user['id'])))
        (await connection.execute('DELETE FROM sessions WHERE user_id=?',(user['id'],)))
        token = (await create_session(connection,user['id']))
    logic.session_cookie(response,token)
    return {'message':'密码已修改，其他设备已退出登录'}


async def coupons(user,connection):
    return [dict(r) for r in (await connection.execute('''SELECT c.*,uc.claimed_at,uc.used_order_id,
        (SELECT COUNT(*) FROM user_coupons WHERE coupon_id=c.id) AS claimed_count
        FROM coupons c LEFT JOIN user_coupons uc ON uc.coupon_id=c.id AND uc.user_id=?
        WHERE c.is_active=1 OR uc.user_id IS NOT NULL ORDER BY c.id DESC''',(user['id'],)))]


async def claim_coupon(coupon_id,user,connection):
    async with transaction(connection):
        await connection.lock_user(user['id'])
        await connection.lock_coupon(coupon_id)
        row = (await connection.execute('SELECT * FROM coupons WHERE id=?',(coupon_id,))).fetchone()
        if not row: raise MallError(404,'优惠券不存在')
        if (await connection.execute('SELECT 1 FROM user_coupons WHERE user_id=? AND coupon_id=?',(user['id'],coupon_id))).fetchone(): return {'message':'已领取'}
        if not row['is_active'] or not row['starts_at'] <= now() < row['expires_at']: raise MallError(409,'优惠券尚未开始或已失效')
        if (await connection.execute('SELECT COUNT(*) FROM user_coupons WHERE coupon_id=?',(coupon_id,))).fetchone()[0] >= row['claim_limit']: raise MallError(409,'优惠券已领完')
        (await connection.execute('INSERT INTO user_coupons(user_id,coupon_id) VALUES (?,?)',(user['id'],coupon_id)))
    return {'message':'领取成功'}


async def coupon_discount(coupon_id,subtotal,user,connection,store_id=None):
    if coupon_id is None: return 0
    c = (await connection.execute('''SELECT c.*,uc.used_order_id FROM coupons c JOIN user_coupons uc ON uc.coupon_id=c.id
        WHERE c.id=? AND uc.user_id=?''',(coupon_id,user['id']))).fetchone()
    if not c or c['used_order_id'] or not c['is_active'] or not c['starts_at'] <= now() < c['expires_at']: raise MallError(409,'优惠券未领取、已使用或已失效')
    if c['store_id'] is not None and c['store_id'] != store_id: raise MallError(409,'优惠券仅限所属店铺使用')
    if subtotal < c['threshold']: raise MallError(409,'商品金额未达到优惠券使用门槛')
    discount = c['value'] if c['kind']=='amount' else (subtotal*(100-c['value'])+50)//100
    return min(subtotal,discount,c['max_discount'] or subtotal)


async def admin_coupons(user,connection,page=1,page_size=10):
    require_admin(user)
    where, args = scope_clause(user, 'c.store_id')
    return (await page_data(connection,'SELECT c.*,(SELECT COUNT(*) FROM user_coupons WHERE coupon_id=c.id) AS claimed_count FROM coupons c WHERE ' + where + ' ORDER BY id DESC',args,page,page_size))


async def admin_save_coupon(payload,user,connection,coupon_id=None):
    require_admin(user)
    values = asdict(payload)
    try:
        async with transaction(connection):
            if coupon_id is not None:
                await connection.lock_coupon(coupon_id)
                where, args = scope_clause(user)
                old=(await connection.execute('SELECT * FROM coupons WHERE id=? AND ' + where,(coupon_id,*args))).fetchone()
                if not old: raise MallError(404,'优惠券不存在')
            values['store_id'] = store_scope(user, payload.store_id)
            if values['store_id'] is not None:
                from app.shops import store_row
                (await store_row(values['store_id'],connection))
            if coupon_id is None:
                coupon_id=(await connection.execute('INSERT INTO coupons('+','.join(values)+') VALUES ('+','.join('?' for _ in values)+')',tuple(values.values()))).lastrowid
            else:
                # Issued terms are immutable, but availability and quota may be managed.
                claimed=(await connection.execute('SELECT COUNT(*) FROM user_coupons WHERE coupon_id=?',(coupon_id,))).fetchone()[0]
                if claimed and any(old[k]!=values[k] for k in ('code','kind','value','threshold','max_discount','starts_at','expires_at','store_id')): raise MallError(409,'已领取优惠券不可修改优惠规则，请新建优惠券')
                if payload.claim_limit < claimed: raise MallError(409,'领取上限不能低于已领取数量')
                (await connection.execute('UPDATE coupons SET '+','.join(k+'=?' for k in values)+' WHERE id=?',(*values.values(),coupon_id)))
    except sqlite3.IntegrityError: raise MallError(409,'券码已存在')
    return dict((await connection.execute('SELECT * FROM coupons WHERE id=?',(coupon_id,))).fetchone())


async def history(user,connection,page=1,page_size=10):
    return (await page_data(connection,'''SELECT p.*,h.viewed_at FROM browsing_history h JOIN products p ON p.id=h.product_id
        WHERE h.user_id=? ORDER BY h.sort_key DESC,p.id DESC''',(user['id'],),page,page_size))


async def record_history(payload,user,connection):
    (await logic.product(payload.product_id,connection))
    (await connection.execute('''INSERT INTO browsing_history VALUES (?,?,?,?) ON CONFLICT(user_id,product_id)
        DO UPDATE SET viewed_at=excluded.viewed_at,sort_key=excluded.sort_key''',(user['id'],payload.product_id,now(),time.time_ns())))
    return {'message':'已记录'}


async def clear_history(user,connection):
    (await connection.execute('DELETE FROM browsing_history WHERE user_id=?',(user['id'],)))
    return {'message':'浏览历史已清空'}


async def refund_open(order_id,connection):
    return (await connection.execute("SELECT 1 FROM refunds WHERE order_id=? AND status IN ('pending','approved')",(order_id,))).fetchone()


async def request_refund(order_id,payload,user,connection):
    async with transaction(connection):
        await connection.lock_user(user['id'])
        await connection.lock_order(order_id)
        order=(await logic.order_data(connection,order_id,user['id']))
        existing=(await connection.execute('SELECT * FROM refunds WHERE user_id=? AND idempotency_key=?',(user['id'],payload.idempotency_key))).fetchone()
        if existing:
            if existing['order_id']!=order_id or existing['reason']!=payload.reason: raise MallError(409,'幂等键已用于其他退款请求')
            return dict(existing)
        if order['status']!='paid': raise MallError(409,'只有已支付订单可以申请退款')
        if (await refund_open(order_id,connection)): raise MallError(409,'该订单已有退款申请')
        rid=(await connection.execute('INSERT INTO refunds(order_id,user_id,reason,amount,idempotency_key) VALUES (?,?,?,?,?)',(order_id,user['id'],payload.reason,order['total'],payload.idempotency_key))).lastrowid
    return dict((await connection.execute('SELECT * FROM refunds WHERE id=?',(rid,))).fetchone())


async def admin_refunds(user,connection,page=1,page_size=10):
    require_admin(user)
    where, args = scope_clause(user, 'o.store_id')
    return (await page_data(connection,'''SELECT r.*,u.name,o.fulfillment_status FROM refunds r JOIN users u ON u.id=r.user_id
        JOIN orders o ON o.id=r.order_id WHERE ''' + where + " ORDER BY (r.status='pending') DESC,r.id DESC",args,page,page_size))


async def admin_decide_refund(refund_id,payload,user,connection):
    require_admin(user)
    async with transaction(connection):
        row=(await connection.execute('SELECT * FROM refunds WHERE id=?',(refund_id,))).fetchone()
        if not row: raise MallError(404,'退款申请不存在')
        await connection.lock_user(row['user_id'])
        await connection.lock_order(row['order_id'])
        order=(await admin_order(row['order_id'],user,connection))
        decision='approved' if payload.approve else 'rejected'
        if row['status']!='pending':
            if row['status']==decision: return dict(row)
            raise MallError(409,'该退款申请已经审核')
        restock=payload.approve and (payload.restock or order['fulfillment_status']=='unfulfilled')
        if payload.approve:
            await connection.lock_products(item['product_id'] for item in order['items'])
        (await connection.execute('UPDATE refunds SET status=?,note=?,restocked=?,processed_by=?,processed_at=? WHERE id=?',(decision,payload.note,restock,user['id'],now(),refund_id)))
        if payload.approve:
            for item in sorted(order['items'], key=lambda item: item['product_id']):
                (await connection.execute('UPDATE products SET stock=stock+?,sales=MAX(0,sales-?),version=version+1 WHERE id=?',(item['quantity'] if restock else 0,item['quantity'],item['product_id'])))
            (await connection.execute('UPDATE user_coupons SET used_order_id=NULL WHERE used_order_id=?',(order['id'],)))
    return dict((await connection.execute('SELECT * FROM refunds WHERE id=?',(refund_id,))).fetchone())


STAGES=('awaiting','packing','dispatched','transit','delivered')
STAGE_LABELS={'awaiting':'等待处理','packing':'打包中','dispatched':'已发货','transit':'运输中','delivered':'已到货'}


async def tracking_event(connection,order_id,stage,note=''):
    (await connection.execute('INSERT INTO tracking_events(order_id,stage,note) VALUES (?,?,?)',(order_id,stage,note or STAGE_LABELS[stage])))


async def admin_tracking(order_id,payload,user,connection):
    require_admin(user)
    async with transaction(connection):
        await connection.lock_order(order_id)
        order=(await admin_order(order_id,user,connection))
        if order['status']!='paid' or (await refund_open(order_id,connection)): raise MallError(409,'订单未支付或正在退款，无法更新物流')
        old=order['delivery_stage']
        if payload.stage==old: return order
        if STAGES.index(payload.stage)<STAGES.index(old): raise MallError(409,'物流状态不能回退')
        if payload.stage=='packing' and order['fulfillment_status']!='unfulfilled': raise MallError(409,'订单已经发货')
        if payload.stage in {'transit','delivered'} and order['fulfillment_status']=='unfulfilled': raise MallError(409,'请先填写物流单号并发货')
        (await connection.execute('UPDATE orders SET delivery_stage=? WHERE id=?',(payload.stage,order_id)))
        (await tracking_event(connection,order_id,payload.stage,payload.note))
    return (await admin_order(order_id,user,connection))


async def discussions(product_id,connection,kind='comments',page=1,page_size=10):
    if not (await connection.execute('SELECT 1 FROM products WHERE id=?',(product_id,))).fetchone(): raise MallError(404,'商品不存在')
    table='reviews' if kind=='reviews' else 'comments'
    columns='r.id,u.name,r.content,r.created_at'+(',r.rating,r.updated_at' if table=='reviews' else '')
    result=(await page_data(connection,f'SELECT {columns} FROM {table} r JOIN users u ON u.id=r.user_id WHERE r.product_id=? AND r.is_visible=1 ORDER BY r.id DESC',(product_id,),page,page_size))
    if table=='reviews':
        result['average_rating']=(await connection.execute('SELECT COALESCE(AVG(rating),0) FROM reviews WHERE product_id=? AND is_visible=1',(product_id,))).fetchone()[0]
    return result


async def product_reviews(product_id,connection,page=1,page_size=10): return (await discussions(product_id,connection,'reviews',page,page_size))

async def product_comments(product_id,connection,page=1,page_size=10): return (await discussions(product_id,connection,'comments',page,page_size))


async def write_review(product_id,payload,user,connection):
    async with transaction(connection):
        purchase=(await connection.execute('''SELECT o.id FROM orders o JOIN order_items i ON i.order_id=o.id
            WHERE o.user_id=? AND i.product_id=? AND o.status='paid'
            AND NOT EXISTS (SELECT 1 FROM refunds r WHERE r.order_id=o.id AND r.status IN ('pending','approved'))
            ORDER BY o.created_at DESC LIMIT 1''',(user['id'],product_id))).fetchone()
        if not purchase: raise MallError(403,'购买并支付该商品后才能评价，退款中的订单不可评价')
        # Hidden reviews stay hidden when edited, so moderation cannot be bypassed.
        (await connection.execute('''INSERT INTO reviews(user_id,product_id,order_id,rating,content) VALUES (?,?,?,?,?)
            ON CONFLICT(user_id,product_id) DO UPDATE SET rating=excluded.rating,content=excluded.content,
            order_id=excluded.order_id,updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')''',(user['id'],product_id,purchase['id'],payload.rating,payload.content)))
    return {'message':'评价已保存'}


async def write_comment(product_id,payload,user,connection):
    (await logic.product(product_id,connection))
    cid=(await connection.execute('INSERT INTO comments(user_id,product_id,content) VALUES (?,?,?)',(user['id'],product_id,payload.content))).lastrowid
    return {'id':cid,'message':'评论已发布'}


async def admin_discussions(user,connection,page=1,page_size=10,kind='comments'):
    require_admin(user)
    table='reviews' if kind=='reviews' else 'comments'
    where, args = scope_clause(user, 'p.store_id')
    return (await page_data(connection,f'SELECT r.*,u.name,p.name AS product_name FROM {table} r JOIN users u ON u.id=r.user_id JOIN products p ON p.id=r.product_id WHERE ' + where + ' ORDER BY r.id DESC',args,page,page_size))


async def admin_visibility(comment_id,payload,user,connection,kind='comments'):
    require_admin(user)
    table='reviews' if kind=='reviews' else 'comments'
    where, args = scope_clause(user)
    result=(await connection.execute(f'UPDATE {table} SET is_visible=? WHERE id=? AND product_id IN (SELECT id FROM products WHERE ' + where + ')',(payload.is_visible,comment_id,*args)))
    if not result.rowcount: raise MallError(404,'评论或评价不存在')
    return {'message':'显示状态已更新'}


async def conversation(user,connection,store_id=1):
    row=(await connection.execute('SELECT * FROM conversations WHERE user_id=? AND store_id=?',(user['id'],store_id))).fetchone()
    return dict(row) if row else None


async def thread(conversation_id,connection,after_id=0,before_id=0,page_size=50):
    args=[conversation_id]
    where='conversation_id=?'
    if after_id: where+=' AND id>?';args.append(after_id)
    if before_id: where+=' AND id<?';args.append(before_id)
    direction='ASC' if after_id else 'DESC'
    rows=[dict(r) for r in (await connection.execute(f'SELECT id,content,sender_side,created_at FROM support_messages WHERE {where} ORDER BY id {direction} LIMIT ?',(*args,page_size)))]
    if not after_id: rows.reverse()
    return rows


async def support_conversations(user,connection):
    rows=[dict(r) for r in (await connection.execute("""SELECT c.id,c.store_id,s.name AS store_name,s.is_active AS store_active,
        (SELECT COUNT(*) FROM support_messages m WHERE m.conversation_id=c.id AND m.sender_side='staff' AND m.id>c.customer_read_id) AS unread,
        (SELECT content FROM support_messages m WHERE m.conversation_id=c.id ORDER BY m.id DESC LIMIT 1) AS last_message,
        (SELECT MAX(id) FROM support_messages m WHERE m.conversation_id=c.id) AS last_id
        FROM conversations c JOIN stores s ON s.id=c.store_id WHERE c.user_id=? ORDER BY last_id DESC""",(user['id'],)))]
    return {'items':rows,'unread':sum(r['unread'] for r in rows),'notify_support':(await settings(user,connection))['notify_support']}


async def support_messages(user,connection,after_id=0,before_id=0,page_size=50,store_id=1):
    from app.shops import store_row
    store=(await store_row(store_id,connection))
    c=(await conversation(user,connection,store_id))
    unread=(await connection.execute("SELECT COUNT(*) FROM support_messages WHERE conversation_id=? AND sender_side='staff' AND id>?",(c['id'],c['customer_read_id']))).fetchone()[0] if c else 0
    return {'conversation_id':c['id'] if c else None,'store_id':store_id,'store_name':store['name'],
        'store_active':store['is_active'],'items':(await thread(c['id'],connection,after_id,before_id,page_size)) if c else [],
        'unread':unread,'notify_support':(await settings(user,connection))['notify_support']}


async def send_message(c,payload,user,connection,side):
    existing=(await connection.execute('SELECT * FROM support_messages WHERE conversation_id=? AND sender_id=? AND sender_side=? AND client_message_id=?',(c['id'],user['id'],side,payload.client_message_id))).fetchone()
    if existing:
        if existing['content']!=payload.content: raise MallError(409,'消息编号已用于其他内容')
        return {'id':existing['id'],'message':'消息已发送'}
    mid=(await connection.execute('INSERT INTO support_messages(conversation_id,sender_id,sender_side,content,client_message_id) VALUES (?,?,?,?,?)',(c['id'],user['id'],side,payload.content,payload.client_message_id))).lastrowid
    return {'id':mid,'message':'消息已发送'}


async def support_send(payload,user,connection):
    from app.shops import store_row
    async with transaction(connection):
        (await store_row(payload.store_id,connection,active=True))
        (await connection.execute('INSERT OR IGNORE INTO conversations(user_id,store_id) VALUES (?,?)',(user['id'],payload.store_id)))
        return (await send_message((await conversation(user,connection,payload.store_id)),payload,user,connection,'customer'))


async def mark_read(c,payload,connection,side):
    if payload.last_message_id and not (await connection.execute('SELECT 1 FROM support_messages WHERE id=? AND conversation_id=?',(payload.last_message_id,c['id']))).fetchone(): raise MallError(422,'已读消息不属于当前会话')
    column='customer_read_id' if side=='customer' else 'staff_read_id'
    (await connection.execute(f'UPDATE conversations SET {column}=MAX({column},?) WHERE id=?',(payload.last_message_id,c['id'])))
    return {'message':'已读状态已更新'}


async def support_read(payload,user,connection):
    async with transaction(connection):
        c=(await conversation(user,connection,payload.store_id))
        if not c:
            if payload.last_message_id: raise MallError(422,'会话不存在')
            return {'message':'暂无消息'}
        return (await mark_read(c,payload,connection,'customer'))


async def admin_conversation(conversation_id,user,connection):
    require_admin(user)
    where, args = scope_clause(user)
    row=(await connection.execute('SELECT * FROM conversations WHERE id=? AND ' + where,(conversation_id,*args))).fetchone()
    if not row: raise MallError(404,'会话不存在')
    return dict(row)


async def admin_support(user,connection,page=1,page_size=10,store_id=None):
    store_id = store_scope(user, store_id)
    where=' WHERE c.store_id=?' if store_id is not None else ''
    args=(store_id,) if store_id is not None else ()
    result=(await page_data(connection,'''SELECT c.id,u.name,u.phone,c.store_id,s.name AS store_name,
        (SELECT content FROM support_messages WHERE conversation_id=c.id ORDER BY id DESC LIMIT 1) AS last_message,
        (SELECT COUNT(*) FROM support_messages WHERE conversation_id=c.id AND sender_side='customer' AND id>c.staff_read_id) AS unread,
        (SELECT MAX(id) FROM support_messages WHERE conversation_id=c.id) AS last_id
        FROM conversations c JOIN users u ON u.id=c.user_id JOIN stores s ON s.id=c.store_id'''+where+' ORDER BY last_id DESC',args,page,page_size))
    result['unread_total']=(await connection.execute("SELECT COUNT(*) FROM support_messages m JOIN conversations c ON c.id=m.conversation_id WHERE m.sender_side='customer' AND m.id>c.staff_read_id"+(' AND c.store_id=?' if store_id is not None else ''),args)).fetchone()[0]
    return result


async def admin_support_messages(conversation_id,user,connection,after_id=0,before_id=0,page_size=50):
    (await admin_conversation(conversation_id,user,connection))
    return {'items':(await thread(conversation_id,connection,after_id,before_id,page_size))}


async def admin_support_send(conversation_id,payload,user,connection):
    async with transaction(connection): return (await send_message((await admin_conversation(conversation_id,user,connection)),payload,user,connection,'staff'))


async def admin_support_read(conversation_id,payload,user,connection):
    async with transaction(connection): return (await mark_read((await admin_conversation(conversation_id,user,connection)),payload,connection,'staff'))
