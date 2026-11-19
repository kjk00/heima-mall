"""Framework-independent shop operations shared by both HTTP servers."""
import os
import secrets
import sqlite3
import time
from app.async_contract import transaction
from app.errors import MallError as HTTPException
from app.order_expiration import EXPIRED_MESSAGE, expire_due_orders, expire_locked_order, expire_order, payment_metadata
from app.security import SESSION_SECONDS, create_session, hash_password_async, token_hash, verify_password_async
from app.schemas import checkout_fingerprint

COOKIE = 'heima_session'
CAPTCHA_SECONDS = 300

async def login_captcha(request, connection):
    attempts = request.app.state.captcha_attempts
    now = time.monotonic()
    key = request.client.host if request.client else 'unknown'
    with request.app.state.login_lock:
        attempts[:] = [(host, stamp) for host, stamp in attempts if now - stamp < 60]
        if sum((host == key for host, _ in attempts)) >= 30 or len(attempts) >= 10000:
            raise HTTPException(429, '验证码获取过于频繁，请一分钟后重试')
        attempts.append((key, now))
    from app.captcha import generate
    code, image = generate()
    captcha_id = secrets.token_urlsafe(16)
    expires_at = int(time.time()) + CAPTCHA_SECONDS
    await connection.execute('DELETE FROM login_captchas WHERE expires_at <= ?', (int(time.time()),))
    await connection.execute('INSERT INTO login_captchas(id,code,expires_at) VALUES (?,?,?)', (captcha_id, code, expires_at))
    return {'captcha_id': captcha_id, 'image': image, 'expires_in': CAPTCHA_SECONDS}

async def consume_captcha(connection, payload):
    if not payload.captcha_id or not payload.captcha_code:
        raise HTTPException(422, '请输入图形验证码')
    row = (await connection.execute('SELECT code,expires_at FROM login_captchas WHERE id=?', (payload.captcha_id,))).fetchone()
    await connection.execute('DELETE FROM login_captchas WHERE id=?', (payload.captcha_id,))
    if not row or row['expires_at'] <= int(time.time()) or row['code'] != payload.captcha_code.upper():
        raise HTTPException(400, '验证码不正确或已过期，请点击图片刷新后重试')

async def current_user(request, connection):
    token = request.cookies.get(COOKIE)
    if token:
        user = (await connection.execute('SELECT u.id,u.name,u.phone,u.role,u.is_active,u.notify_support,u.store_id,u.is_super_admin FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?', (token_hash(token), int(time.time())))).fetchone()
        if user:
            if not user['is_active']:
                raise HTTPException(403, '账号已停用，请联系管理员')
            return dict(user)
    raise HTTPException(401, '请先登录，再继续操作')

def session_cookie(response, token):
    response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True, secure=os.getenv('COOKIE_SECURE') == '1', samesite='lax', path='/')

async def cart_data(connection, user_id, store_id=None):
    from app.shops import product_data
    rows = (await connection.execute('SELECT p.*,c.quantity FROM cart_items c JOIN products p ON p.id=c.product_id WHERE c.user_id=? ORDER BY p.id', (user_id,))).fetchall()
    items = [(await product_data(row,connection)) | {'line_total': row['price'] * row['quantity']} for row in rows
             if store_id is None or row['store_id']==store_id]
    groups={}
    for item in items:
        group=groups.setdefault(item['store_id'],{'store_id':item['store_id'],'store_name':item['store_name'],
            'store_active':item['store_active'],'store_version':item['store_version'],
            'free_shipping_threshold':item['free_shipping_threshold'],'items':[]})
        group['items'].append(item)
    for group in groups.values():
        group['subtotal']=sum(i['line_total'] for i in group['items'])
        group['count']=sum(i['quantity'] for i in group['items'])
        group['shipping_fee']=0 if group['subtotal']>=group['free_shipping_threshold'] else group['items'][0]['store_shipping_fee']
        group['total']=group['subtotal']+group['shipping_fee']
    subtotal = sum((item['line_total'] for item in items))
    shipping = sum(g['shipping_fee'] for g in groups.values())
    return {'items': items, 'groups':list(groups.values()),'count': sum((item['quantity'] for item in items)), 'subtotal': subtotal, 'shipping_fee': shipping, 'total': subtotal + shipping}

async def order_data(connection, order_id, user_id):
    row = (await connection.execute('SELECT * FROM orders WHERE id=? AND user_id=?', (order_id, user_id))).fetchone()
    if not row:
        raise HTTPException(404, '订单不存在')
    result = dict(row)
    result.pop('idempotency_key')
    result.pop('request_hash', None)
    result.pop('created_seq', None)
    result['items'] = [dict(item) for item in (await connection.execute('SELECT product_id,name,image,price,quantity FROM order_items WHERE order_id=? ORDER BY id', (order_id,))).fetchall()]
    refund=(await connection.execute('SELECT id,reason,amount,status,note,created_at,processed_at FROM refunds WHERE order_id=? ORDER BY id DESC LIMIT 1',(order_id,))).fetchone()
    result['refund']=dict(refund) if refund else None
    result['tracking_events']=[dict(r) for r in (await connection.execute('SELECT stage,note,created_at FROM tracking_events WHERE order_id=? ORDER BY id',(order_id,)))]
    result.update(payment_metadata(result, result['tracking_events']))
    return result

async def health(connection):
    (await connection.execute('SELECT 1'))
    return {'status': 'ok'}

async def categories(connection):
    return [dict(row) for row in (await connection.execute('SELECT category AS name,COUNT(*) AS count FROM products WHERE is_active=1 AND store_id IN (SELECT id FROM stores WHERE is_active=1) GROUP BY category ORDER BY MIN(id)'))]

async def products(connection, q='', category='', sort='recommended', page=1, page_size=12, on_sale=False,store_id=None, search=None):
    from app.shops import product_data
    if search is not None:
        result = await search.read(connection, dict(q=q, category=category, sort=sort, page=page,
                                                    page_size=page_size, on_sale=on_sale, store_id=store_id))
        if result is not None:
            rows = []
            if result['ids']:
                placeholders = ','.join('?' for _ in result['ids'])
                fresh = {row['id']: row for row in (await connection.execute(
                    f'SELECT * FROM products WHERE id IN ({placeholders})', tuple(result['ids']))).fetchall()}
                # ES is eventually consistent; displayed fields and visibility
                # always come from the live database rows.
                rows = [fresh[i] for i in result['ids'] if i in fresh and fresh[i]['is_active']]
            return {'items': [(await product_data(row, connection)) for row in rows],
                    'total': result['total'], 'page': page, 'page_size': page_size}
    conditions, parameters = (['is_active=1','store_id IN (SELECT id FROM stores WHERE is_active=1)'], [])
    if store_id is not None:
        conditions.append('store_id=?');parameters.append(store_id)
    if on_sale: conditions.append('original_price>price')
    if q.strip():
        conditions.append("(name LIKE ? ESCAPE '\\' OR subtitle LIKE ? ESCAPE '\\' OR store_id IN (SELECT id FROM stores WHERE name LIKE ? ESCAPE '\\'))")
        search = q.strip().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        parameters.extend([f'%{search}%', f'%{search}%', f'%{search}%'])
    if category:
        conditions.append('category=?')
        parameters.append(category)
    where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
    sorting = {'recommended': 'id ASC', 'newest': 'id DESC', 'popular': 'sales DESC,id ASC', 'price_asc': 'price ASC,id ASC', 'price_desc': 'price DESC,id ASC'}[sort]
    total = (await connection.execute('SELECT COUNT(*) FROM products' + where, parameters)).fetchone()[0]
    rows = (await connection.execute('SELECT * FROM products' + where + ' ORDER BY ' + sorting + ' LIMIT ? OFFSET ?', parameters + [page_size, (page - 1) * page_size])).fetchall()
    return {'items': [(await product_data(row,connection)) for row in rows], 'total': total, 'page': page, 'page_size': page_size}

async def product(product_id, connection):
    from app.shops import product_detail
    row = (await connection.execute('SELECT * FROM products WHERE id=? AND is_active=1 AND store_id IN (SELECT id FROM stores WHERE is_active=1)', (product_id,))).fetchone()
    if not row:
        raise HTTPException(404, '商品不存在')
    return (await product_detail(row,connection))

async def register(payload, response, connection):
    password = (await hash_password_async(payload.password))
    try:
        async with transaction(connection):
            result = (await connection.execute('INSERT INTO users(phone,name,password_hash) VALUES (?,?,?)', (payload.phone, payload.name, password)))
            token = (await create_session(connection, result.lastrowid))
    except sqlite3.IntegrityError:
        raise HTTPException(409, '该手机号已注册，请直接登录')
    session_cookie(response, token)
    return {'id': result.lastrowid, 'name': payload.name, 'phone': payload.phone, 'role': 'customer', 'is_active': 1}

async def login(payload, response, request, connection, admin_only=False):
    attempts = request.app.state.login_attempts
    now = time.monotonic()
    key = request.client.host if request.client else 'unknown'
    with request.app.state.login_lock:
        attempts[:] = [(host, stamp) for host, stamp in attempts if now - stamp < 60]
        if sum((host == key for host, _ in attempts)) >= 20 or len(attempts) >= 10000:
            raise HTTPException(429, '登录尝试过于频繁，请一分钟后重试')
        attempts.append((key, now))
    await consume_captcha(connection, payload)
    row = (await connection.execute('SELECT * FROM users WHERE phone=?', (payload.phone,))).fetchone()
    encoded = row['password_hash'] if row else request.app.state.dummy_password
    valid = (await verify_password_async(payload.password, encoded))
    if not row or not valid:
        raise HTTPException(401, '手机号或密码不正确')
    if not row['is_active']:
        raise HTTPException(403, '账号已停用，请联系管理员')
    if admin_only and row['role'] != 'admin':
        raise HTTPException(403, '该账号没有后台管理权限')
    if admin_only:
        from app.admin import require_admin
        require_admin(dict(row))
    token = (await create_session(connection, row['id']))
    session_cookie(response, token)
    return {key: row[key] for key in ('id','name','phone','role','is_active','store_id','is_super_admin')}

def me(user):
    return user

async def logout(request, response, connection):
    token = request.cookies.get(COOKIE)
    if token:
        (await connection.execute('DELETE FROM sessions WHERE token_hash=?', (token_hash(token),)))
    response.delete_cookie(COOKIE, path='/')
    return {'message': '已退出登录'}

async def get_cart(user, connection):
    return (await cart_data(connection, user['id']))

async def add_cart(payload, user, connection):
    from app.shops import store_row
    async with transaction(connection):
        product = (await connection.execute('SELECT stock,is_active,store_id FROM products WHERE id=?', (payload.product_id,))).fetchone()
        if not product:
            raise HTTPException(404, '商品不存在')
        if not product['is_active']:
            raise HTTPException(409, '商品已下架，请选购其他商品')
        (await store_row(product['store_id'],connection,active=True))
        existing = (await connection.execute('SELECT quantity FROM cart_items WHERE user_id=? AND product_id=?', (user['id'], payload.product_id))).fetchone()
        quantity = (existing['quantity'] if existing else 0) + payload.quantity
        if quantity > min(product['stock'], 99):
            raise HTTPException(409, '库存不足，或购物车数量已达到上限')
        (await connection.execute('INSERT INTO cart_items VALUES (?,?,?) ON CONFLICT(user_id,product_id) DO UPDATE SET quantity=excluded.quantity', (user['id'], payload.product_id, quantity)))
    return (await cart_data(connection, user['id']))

async def update_cart(product_id, payload, user, connection):
    from app.shops import store_row
    async with transaction(connection):
        row = (await connection.execute('SELECT p.stock,p.is_active,p.store_id FROM cart_items c JOIN products p ON p.id=c.product_id WHERE c.user_id=? AND c.product_id=?', (user['id'], product_id))).fetchone()
        if not row:
            raise HTTPException(404, '购物车中没有该商品')
        if not row['is_active']:
            raise HTTPException(409, '商品已下架，请从购物车移除')
        (await store_row(row['store_id'],connection,active=True))
        if payload.quantity > row['stock']:
            raise HTTPException(409, '商品库存不足')
        (await connection.execute('UPDATE cart_items SET quantity=? WHERE user_id=? AND product_id=?', (payload.quantity, user['id'], product_id)))
    return (await cart_data(connection, user['id']))

async def remove_cart(product_id, user, connection):
    (await connection.execute('DELETE FROM cart_items WHERE user_id=? AND product_id=?', (user['id'], product_id)))
    return (await cart_data(connection, user['id']))

async def checkout(payload, user, connection):
    from app import features
    from app.shops import store_row
    from app.schemas import AddressInput
    async with transaction(connection):
        account = (await connection.execute('SELECT is_active FROM users WHERE id=?', (user['id'],))).fetchone()
        if not account or not account['is_active']:
            raise HTTPException(403, '账号已停用，无法继续下单')
        existing = (await connection.execute('SELECT id FROM orders WHERE user_id=? AND idempotency_key=?', (user['id'], payload.idempotency_key))).fetchone()
        if existing:
            saved=(await order_data(connection,existing['id'],user['id']))
            if payload.store_id is not None and saved['store_id']!=payload.store_id:
                raise HTTPException(409,'该请求编号已用于其他店铺订单，请重新结算')
            fingerprint = (await connection.execute('SELECT request_hash FROM orders WHERE id=?', (saved['id'],))).fetchone()[0]
            if fingerprint and fingerprint != checkout_fingerprint(payload, payload.store_id or saved['store_id']):
                raise HTTPException(409, '幂等键已用于不同的下单请求')
            return saved
        cart = (await cart_data(connection, user['id']))
        if not cart['items']:
            raise HTTPException(400, '购物车为空，请先选购商品')
        if payload.store_id is None and len(cart['groups'])>1:
            raise HTTPException(409,'购物车包含多家店铺，请选择店铺分别结算')
        store_id=payload.store_id or cart['groups'][0]['store_id']
        store=(await store_row(store_id,connection,active=True))
        cart=(await cart_data(connection,user['id'],store_id))
        if not cart['items']:raise HTTPException(400,'所选店铺的购物车为空')
        for item in cart['items']:
            if not item['is_active']:
                raise HTTPException(409, f"{item['name']} 已下架，请从购物车移除")
            if item['quantity'] > item['stock']:
                raise HTTPException(409, f"{item['name']} 库存不足，请调整数量")
        order_id = 'HM' + time.strftime('%Y%m%d') + secrets.token_hex(6).upper()
        discount=(await features.coupon_discount(payload.coupon_id,cart['subtotal'],user,connection,store_id))
        address=(await features.address_row(payload.address_id,user,connection)) if payload.address_id else {'recipient':payload.recipient,'phone':payload.phone,'address':payload.address}
        (await connection.execute('INSERT INTO orders(id,user_id,subtotal,shipping_fee,total,recipient,phone,address,idempotency_key,discount,coupon_id,store_id,store_name) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', (order_id, user['id'], cart['subtotal'], cart['shipping_fee'], cart['total']-discount, address['recipient'], address['phone'], address['address'], payload.idempotency_key,discount,payload.coupon_id,store_id,store['name'])))
        if payload.coupon_id: (await connection.execute('UPDATE user_coupons SET used_order_id=? WHERE user_id=? AND coupon_id=?',(order_id,user['id'],payload.coupon_id)))
        (await connection.execute('UPDATE orders SET request_hash=? WHERE id=?', (checkout_fingerprint(payload, store_id), order_id)))
        if payload.save_address and not payload.address_id and not (await connection.execute('SELECT 1 FROM addresses WHERE user_id=? AND recipient=? AND phone=? AND address=?',(user['id'],payload.recipient,payload.phone,payload.address))).fetchone():
            (await features.save_address(AddressInput(payload.recipient,payload.phone,payload.address),user,connection))
        (await features.tracking_event(connection,order_id,'awaiting','订单已创建，等待支付'))
        for item in cart['items']:
            (await connection.execute('INSERT INTO order_items(order_id,product_id,name,image,price,quantity) VALUES (?,?,?,?,?,?)', (order_id, item['id'], item['name'], item['image'], item['price'], item['quantity'])))
            (await connection.execute('UPDATE products SET stock=stock-?,version=version+1 WHERE id=?', (item['quantity'], item['id'])))
        (await connection.execute('DELETE FROM cart_items WHERE user_id=? AND product_id IN (SELECT id FROM products WHERE store_id=?)', (user['id'],store_id)))
        return (await order_data(connection, order_id, user['id']))

async def orders(user, connection, page=1, page_size=10):
    await expire_due_orders(connection, user_id=user['id'])
    total = (await connection.execute('SELECT COUNT(*) FROM orders WHERE user_id=?', (user['id'],))).fetchone()[0]
    ids = (await connection.execute('SELECT id FROM orders WHERE user_id=? ORDER BY created_at DESC,rowid DESC LIMIT ? OFFSET ?', (user['id'], page_size, (page - 1) * page_size))).fetchall()
    return {'items': [(await order_data(connection, row['id'], user['id'])) for row in ids], 'total': total, 'page': page, 'page_size': page_size}

async def order(order_id, user, connection):
    await expire_order(connection, order_id, user['id'])
    return (await order_data(connection, order_id, user['id']))

async def pay(order_id, user, connection):
    from app.features import refund_open,tracking_event
    from app.shops import store_row
    async with transaction(connection):
        await connection.lock_user(user['id'])
        await connection.lock_order(order_id)
        order = (await order_data(connection, order_id, user['id']))
        expired = await expire_locked_order(connection, order)
        if (await refund_open(order_id,connection)): raise HTTPException(409,'订单正在退款或已退款')
        if order['status'] == 'cancelled':
            raise HTTPException(409, EXPIRED_MESSAGE if order['is_expired'] else '订单已取消，无法支付')
        if not expired and order['status'] == 'pending':
            await connection.lock_products(item['product_id'] for item in order['items'])
            (await store_row(order['store_id'],connection,active=True))
            expired = await expire_locked_order(connection, order)
            if not expired:
                (await connection.execute("UPDATE orders SET status='paid' WHERE id=?", (order_id,)))
                (await tracking_event(connection,order_id,'awaiting','支付成功，等待店铺处理'))
                for item in order['items']:
                    (await connection.execute('UPDATE products SET sales=sales+?,version=version+1 WHERE id=?', (item['quantity'], item['product_id'])))
        result = await order_data(connection, order_id, user['id'])
    if expired:
        raise HTTPException(409, EXPIRED_MESSAGE)
    return result | {'message': '演示支付成功，未发生真实扣款'}

async def cancel(order_id, user, connection):
    async with transaction(connection):
        await connection.lock_user(user['id'])
        await connection.lock_order(order_id)
        order = (await order_data(connection, order_id, user['id']))
        if order['status'] == 'paid':
            raise HTTPException(409, '已支付订单暂不支持取消')
        expired = await expire_locked_order(connection, order)
        if not expired and order['status'] == 'pending':
            await connection.lock_products(item['product_id'] for item in order['items'])
            (await connection.execute("UPDATE orders SET status='cancelled' WHERE id=?", (order_id,)))
            (await connection.execute('UPDATE user_coupons SET used_order_id=NULL WHERE used_order_id=?',(order_id,)))
            for item in sorted(order['items'], key=lambda item: item['product_id']):
                (await connection.execute('UPDATE products SET stock=stock+?,version=version+1 WHERE id=?', (item['quantity'], item['product_id'])))
        return (await order_data(connection, order_id, user['id']))


async def receive(order_id, user, connection):
    from app.features import refund_open,tracking_event
    async with transaction(connection):
        order = (await order_data(connection, order_id, user['id']))
        if (await refund_open(order_id,connection)): raise HTTPException(409,'退款中的订单无法确认收货')
        if order['status'] != 'paid' or order['fulfillment_status'] not in {'shipped', 'completed'}:
            raise HTTPException(409, '订单尚未发货，无法确认收货')
        if order['fulfillment_status'] == 'shipped':
            (await connection.execute("UPDATE orders SET fulfillment_status='completed',delivery_stage='delivered',completed_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?", (order_id,)))
            (await tracking_event(connection,order_id,'delivered','用户已确认收货'))
        return (await order_data(connection, order_id, user['id']))
