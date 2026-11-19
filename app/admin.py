"""Administrator operations shared by both HTTP adapters."""
import base64
import binascii
import hashlib
import ipaddress
import json
import sqlite3
import os
import tempfile
import asyncio
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import logic
from app.async_contract import transaction
from app.errors import MallError
from app.image_validation import image_extension
from app.schemas import number, text
from app.security import create_session, hash_password_async, verify_password_async

ASSETS = Path(__file__).resolve().parent / 'static' / 'images'
PRODUCT_COLUMNS = ('name', 'subtitle', 'category', 'price', 'original_price', 'stock',
                   'image', 'badge', 'color', 'description', 'detail_html', 'params_json', 'is_active','store_id')
# Public admin product payload fields (params is the list form of params_json).
PRODUCT_INPUT_FIELDS = ('name', 'subtitle', 'category', 'price', 'original_price', 'stock',
                        'image', 'badge', 'color', 'description', 'detail_html', 'params', 'is_active', 'store_id')
LEGACY_PRODUCT_FIELDS = ('name', 'subtitle', 'category', 'price', 'original_price', 'stock',
                         'image', 'badge', 'color', 'description', 'is_active', 'store_id')


def require_admin(user):
    if user.get('role') != 'admin' or not user.get('is_active'):
        raise MallError(403, '需要管理员权限')
    if not user.get('is_super_admin') and not user.get('store_id'):
        raise MallError(403, '管理员账号尚未绑定店铺，请联系终端管理员')
    return user


def require_super_admin(user):
    require_admin(user)
    if not user.get('is_super_admin'):
        raise MallError(403, '需要终端管理员权限')
    return user


def store_scope(user, store_id=None):
    require_admin(user)
    if user.get('is_super_admin'):
        return store_id
    if store_id is not None and store_id != user['store_id']:
        raise MallError(404, '店铺不存在或无管理权限')
    return user['store_id']


def scope_clause(user, column='store_id'):
    store_id = store_scope(user)
    return (f'{column}=?', (store_id,)) if store_id is not None else ('1=1', ())


async def current_admin(request, connection):
    return require_admin((await logic.current_user(request, connection)))


def local_request(request):
    try:
        address = ipaddress.ip_address(request.client.host)
        return address.is_loopback or bool(getattr(address, 'ipv4_mapped', None) and address.ipv4_mapped.is_loopback)
    except (ValueError, AttributeError):
        return False


async def admin_setup_status(request, connection):
    exists = (await connection.execute("SELECT 1 FROM users WHERE role='admin' LIMIT 1")).fetchone()
    return {'setup_required': not bool(exists), 'can_setup': local_request(request)}


async def admin_setup(payload, response, request, connection):
    if not local_request(request):
        raise MallError(403, '请在服务器本机初始化管理员')
    if (await connection.execute("SELECT 1 FROM users WHERE role='admin' LIMIT 1")).fetchone():
        raise MallError(409, '管理员已初始化，请直接登录')
    existing = (await connection.execute('SELECT * FROM users WHERE phone=?', (payload.phone,))).fetchone()
    if existing and not (await verify_password_async(payload.password, existing['password_hash'])):
        raise MallError(401, '该手机号已注册，请输入该账号的现有密码')
    password = None if existing else (await hash_password_async(payload.password))
    try:
        async with transaction(connection):
            if (await connection.execute("SELECT 1 FROM users WHERE role='admin' LIMIT 1")).fetchone():
                raise MallError(409, '管理员已初始化，请直接登录')
            if existing:
                user_id = existing['id']
                (await connection.execute("UPDATE users SET role='admin',is_super_admin=1,store_id=NULL,is_active=1,name=? WHERE id=?", (payload.name, user_id)))
            else:
                result = (await connection.execute("INSERT INTO users(phone,name,password_hash,role,is_super_admin) VALUES (?,?,?,'admin',1)",
                                            (payload.phone, payload.name, password)))
                user_id = result.lastrowid
            (await connection.execute('DELETE FROM sessions WHERE user_id=?', (user_id,)))
            token = (await create_session(connection, user_id))
    except sqlite3.IntegrityError:
        raise MallError(409, '该账号刚刚发生变更，请重新提交')
    logic.session_cookie(response, token)
    return {'id': user_id, 'name': payload.name, 'phone': payload.phone, 'role': 'admin', 'is_active': 1, 'is_super_admin': 1, 'store_id': None}


async def admin_login(payload, response, request, connection):
    return (await logic.login(payload, response, request, connection, admin_only=True))


def admin_me(user):
    return require_admin(user)


async def admin_dashboard(user, connection):
    require_admin(user)
    from app.order_expiration import expire_due_orders
    await expire_due_orders(connection, store_id=store_scope(user, None))
    orders_where, order_args = scope_clause(user)
    products_where, product_args = scope_clause(user)
    customers_where, customer_args = scope_clause(user, 'o.store_id')
    china_time = timezone(timedelta(hours=8))
    today = datetime.now(china_time).date()
    summary = dict((await connection.execute("""SELECT COUNT(*) AS orders,
        COALESCE(SUM(CASE WHEN status='paid' AND NOT EXISTS (SELECT 1 FROM refunds r WHERE r.order_id=orders.id AND r.status='approved') THEN total ELSE 0 END),0) AS revenue,
        SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending_orders,
        SUM(CASE WHEN status='paid' AND fulfillment_status='unfulfilled' AND NOT EXISTS (SELECT 1 FROM refunds r WHERE r.order_id=orders.id AND r.status IN ('pending','approved')) THEN 1 ELSE 0 END) AS awaiting_shipment,
        SUM(CASE WHEN date(created_at,'+8 hours')=? THEN 1 ELSE 0 END) AS today_orders FROM orders WHERE """ + orders_where, (str(today), *order_args))).fetchone())
    summary = {key: value or 0 for key, value in summary.items()}
    summary.update(dict((await connection.execute("""SELECT COUNT(*) AS products,
        COALESCE(SUM(is_active),0) AS active_products,
        COALESCE(SUM(CASE WHEN is_active=1 AND stock<=10 THEN 1 ELSE 0 END),0) AS low_stock FROM products WHERE """ + products_where, product_args)).fetchone()))
    summary['customers'] = (await connection.execute("SELECT COUNT(*) FROM users u WHERE u.role='customer'" +
        (" AND EXISTS (SELECT 1 FROM orders o WHERE o.user_id=u.id AND " + customers_where + ")" if customer_args else ''), customer_args)).fetchone()[0]
    first_day = today - timedelta(days=6)
    start = datetime.combine(first_day, datetime.min.time(), tzinfo=china_time).astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    rows = (await connection.execute("""SELECT date(created_at,'+8 hours') AS day,COUNT(*) AS orders,
        COALESCE(SUM(CASE WHEN status='paid' AND NOT EXISTS (SELECT 1 FROM refunds r WHERE r.order_id=orders.id AND r.status='approved') THEN total ELSE 0 END),0) AS revenue
        FROM orders WHERE created_at>=? AND """ + orders_where + ' GROUP BY day', (start, *order_args))).fetchall()
    by_day = {row['day']: dict(row) for row in rows}
    trend = []
    for offset in range(7):
        day = str(first_day + timedelta(days=offset))
        trend.append(by_day.get(day, {'day': day, 'orders': 0, 'revenue': 0}))
    recent = (await admin_orders(user, connection, page_size=5))['items']
    low_stock = [dict(row) for row in (await connection.execute('SELECT * FROM products WHERE is_active=1 AND stock<=10 AND ' + products_where + ' ORDER BY stock,id LIMIT 5', product_args))]
    return {'summary': summary, 'trend': trend, 'recent_orders': recent, 'low_stock_products': low_stock}


async def admin_options(user, connection):
    require_admin(user)
    where, args = scope_clause(user)
    categories = [row[0] for row in (await connection.execute('SELECT DISTINCT category FROM products WHERE ' + where + ' ORDER BY category', args))]
    images = [{'image': file.stem, 'label': file.stem, 'url': f'/static/images/{file.name}'}
              for file in sorted(ASSETS.glob('*.svg')) if file.stem != 'favicon']
    where, args = scope_clause(user, 'id')
    stores=[dict(r) for r in (await connection.execute('SELECT id,name,is_active FROM stores WHERE ' + where + ' ORDER BY id', args))]
    return {'categories': categories, 'images': images,'stores':stores}


def _query_arguments(query, operation):
    values = {'page': number(int(query.get('page', ['1'])[0]), '页码', 1, 2**31 - 1),
              'page_size': number(int(query.get('page_size', ['10'])[0]), '每页数量', 1, 50),
              'q': text(query.get('q', [''])[0], '关键词', 0, 100)}
    status = query.get('status', ['all'])[0]
    choices = {'admin_products': {'all', 'active', 'inactive', 'low_stock'},
               'admin_orders': {'all', 'pending', 'paid', 'cancelled', 'unfulfilled', 'shipped', 'completed'},
               'admin_users': {'all', 'active', 'inactive'}}
    if status not in choices[operation]:
        raise MallError(422, '筛选状态不正确')
    values['status'] = status
    if operation == 'admin_products':
        values['category'] = text(query.get('category', [''])[0], '分类', 0, 30)
    if operation in {'admin_products','admin_orders'}:
        raw=query.get('store_id',[''])[0]
        values['store_id']=number(int(raw),'店铺编号',1,2**63-1) if raw else None
    return values


def query_arguments(query, operation):
    try:
        return _query_arguments(query, operation)
    except (ValueError, TypeError, OverflowError) as error:
        raise MallError(422, str(error))


def search_pattern(value):
    return '%' + value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'


async def admin_products(user, connection, q='', status='all', category='', page=1, page_size=10,store_id=None):
    from app.shops import product_data
    store_id = store_scope(user, store_id)
    clauses, parameters = [], []
    if store_id is not None:clauses.append('store_id=?');parameters.append(store_id)
    if q:
        clauses.append("(name LIKE ? ESCAPE '\\' OR subtitle LIKE ? ESCAPE '\\')")
        parameters.extend([search_pattern(q)] * 2)
    if category:
        clauses.append('category=?')
        parameters.append(category)
    if status != 'all':
        clauses.append({'active': 'is_active=1', 'inactive': 'is_active=0', 'low_stock': 'is_active=1 AND stock<=10'}[status])
    where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
    total = (await connection.execute('SELECT COUNT(*) FROM products' + where, parameters)).fetchone()[0]
    rows = (await connection.execute('SELECT * FROM products' + where + ' ORDER BY id DESC LIMIT ? OFFSET ?',
                              parameters + [page_size, (page - 1) * page_size])).fetchall()
    return {'items': [(await product_data(row,connection)) for row in rows], 'total': total, 'page': page, 'page_size': page_size}


async def admin_product(product_id, user, connection):
    from app.shops import product_detail
    require_admin(user)
    where, args = scope_clause(user)
    row = (await connection.execute('SELECT * FROM products WHERE id=? AND ' + where, (product_id, *args))).fetchone()
    if not row:
        raise MallError(404, '商品不存在')
    return (await product_detail(row,connection))


def validate_image(image, request):
    if image.startswith('/media/'):
        if not request.app.state.storage.exists(image.removeprefix('/media/')):
            raise MallError(422, '商品图片不存在，请选择素材或重新上传')
    else:
        path = ASSETS / f'{image}.svg'
        if not path.is_file() or path.name == 'favicon.svg':
            raise MallError(422, '商品图片不存在，请选择素材或重新上传')


async def validate_image_async(image, request):
    return await asyncio.to_thread(validate_image, image, request)


async def admin_create_product(payload, user, request, connection):
    from app.shops import store_row
    require_admin(user)
    (await validate_image_async(payload.image, request))
    values = asdict(payload)
    values['store_id']=store_scope(user, payload.store_id) or 1
    values['params_json']=json.dumps(values['params'], ensure_ascii=False)
    async with transaction(connection):
        (await store_row(values['store_id'],connection,active=payload.is_active))
        result = (await connection.execute(f"INSERT INTO products ({','.join(PRODUCT_COLUMNS)}) VALUES ({','.join('?' for _ in PRODUCT_COLUMNS)})",
                                    [values[column] for column in PRODUCT_COLUMNS]))
        product_id = result.lastrowid
    await mirror_params(product_id, connection)
    return (await admin_product(product_id, user, connection))


async def admin_update_product(product_id, payload, user, request, connection):
    from app.shops import store_row
    require_admin(user)
    (await validate_image_async(payload.image, request))
    values = asdict(payload)
    values['params_json']=json.dumps(values['params'], ensure_ascii=False)
    async with transaction(connection):
        current=(await admin_product(product_id, user, connection))
        values['store_id']=store_scope(user, payload.store_id or current['store_id'])
        (await store_row(values['store_id'],connection,active=payload.is_active))
        result = (await connection.execute('UPDATE products SET ' + ','.join(column + '=?' for column in PRODUCT_COLUMNS) + ',version=version+1 WHERE id=? AND version=?',
                                    [values[column] for column in PRODUCT_COLUMNS] + [product_id, payload.version]))
        if result.rowcount != 1:
            raise MallError(409, '商品或库存已发生变化，请重新打开编辑窗口后保存')
    await mirror_params(product_id, connection)
    return (await admin_product(product_id, user, connection))


async def mirror_params(product_id, connection):
    """Mirror a committed SQL snapshot, including its version (best effort)."""
    path = getattr(connection, 'path', None)
    if path is None:
        return
    from app import mongodb
    row = (await connection.execute('SELECT id,params_json,version FROM products WHERE id=?', (product_id,))).fetchone()
    if row is not None:
        await mongodb.write_params(product_id, mongodb.parse_params(row['params_json']), path, row['version'])


async def admin_product_status(product_id, payload, user, connection):
    from app.shops import store_row
    require_admin(user)
    async with transaction(connection):
        product=(await admin_product(product_id, user, connection))
        (await store_row(product['store_id'],connection,active=payload.is_active))
        result = (await connection.execute('UPDATE products SET is_active=?,version=version+1 WHERE id=? AND version=?',
                                    (payload.is_active, product_id, payload.version)))
        if result.rowcount != 1:
            raise MallError(409, '商品已发生变化，请刷新后重试')
    return (await admin_product(product_id, user, connection))


def admin_upload_image(payload, user, request):
    require_admin(user)
    try:
        content = base64.b64decode(payload.data_base64, validate=True)
    except (ValueError, binascii.Error):
        raise MallError(422, '图片编码不正确')
    if not 0 < len(content) <= 2 * 1024 * 1024:
        raise MallError(413, '图片大小不能超过 2 MB')
    extension = image_extension(content)
    name = hashlib.sha256(content).hexdigest() + '.' + extension
    content_type = {'png': 'image/png', 'jpg': 'image/jpeg', 'webp': 'image/webp'}[extension]
    request.app.state.storage.put(name, content, content_type)
    return {'image': '/media/' + name, 'url': '/media/' + name, 'size': len(content)}


async def admin_order(order_id, user, connection):
    require_admin(user)
    where, args = scope_clause(user)
    row = (await connection.execute('SELECT user_id FROM orders WHERE id=? AND ' + where, (order_id, *args))).fetchone()
    if not row:
        raise MallError(404, '订单不存在')
    result = (await logic.order_data(connection, order_id, row['user_id']))
    customer = (await connection.execute('SELECT id,name,phone FROM users WHERE id=?', (row['user_id'],))).fetchone()
    result['customer'] = dict(customer)
    return result


async def admin_orders(user, connection, q='', status='all', page=1, page_size=10,store_id=None):
    store_id = store_scope(user, store_id)
    from app.order_expiration import expire_due_orders
    await expire_due_orders(connection, store_id=store_id)
    clauses, parameters = [], []
    if store_id is not None:clauses.append('store_id=?');parameters.append(store_id)
    if q:
        clauses.append("""(id LIKE ? ESCAPE '\\' OR recipient LIKE ? ESCAPE '\\' OR phone LIKE ? ESCAPE '\\'
            OR EXISTS (SELECT 1 FROM users u WHERE u.id=orders.user_id AND
            (u.phone LIKE ? ESCAPE '\\' OR u.name LIKE ? ESCAPE '\\')))""")
        parameters.extend([search_pattern(q)] * 5)
    if status in {'pending', 'paid', 'cancelled'}:
        clauses.append('status=?')
        parameters.append(status)
    elif status != 'all':
        clauses.append("status='paid' AND fulfillment_status=?")
        parameters.append(status)
    where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
    total = (await connection.execute('SELECT COUNT(*) FROM orders' + where, parameters)).fetchone()[0]
    ids = (await connection.execute('SELECT id FROM orders' + where + ' ORDER BY created_at DESC,rowid DESC LIMIT ? OFFSET ?',
                             parameters + [page_size, (page - 1) * page_size])).fetchall()
    return {'items': [(await admin_order(row['id'], user, connection)) for row in ids], 'total': total, 'page': page, 'page_size': page_size}


async def admin_ship_order(order_id, payload, user, connection):
    from app.features import refund_open,tracking_event
    require_admin(user)
    async with transaction(connection):
        await connection.lock_order(order_id)
        order = (await admin_order(order_id, user, connection))
        if (await refund_open(order_id,connection)): raise MallError(409,'退款中的订单不能发货')
        if order['status'] != 'paid':
            raise MallError(409, '只有已支付订单可以发货')
        if order['fulfillment_status'] != 'unfulfilled':
            if order['carrier'] == payload.carrier and order['tracking_no'] == payload.tracking_no:
                return order
            raise MallError(409, '订单已发货，不能重复修改物流信息')
        (await connection.execute("""UPDATE orders SET fulfillment_status='shipped',carrier=?,tracking_no=?,
            delivery_stage='dispatched',shipped_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?""", (payload.carrier, payload.tracking_no, order_id)))
        (await tracking_event(connection,order_id,'dispatched',payload.carrier+' · '+payload.tracking_no))
        return (await admin_order(order_id, user, connection))


async def admin_cancel_order(order_id, user, connection):
    require_admin(user)
    order = (await admin_order(order_id, user, connection))
    (await logic.cancel(order_id, {'id': order['user_id']}, connection))
    return (await admin_order(order_id, user, connection))


async def admin_users(user, connection, q='', status='all', page=1, page_size=10):
    require_admin(user)
    clauses, parameters = [], []
    where_orders, order_args = scope_clause(user, 'o.store_id')
    if order_args:
        clauses.append("u.role='customer' AND EXISTS (SELECT 1 FROM orders o WHERE o.user_id=u.id AND " + where_orders + ')')
        parameters.extend(order_args)
    if q:
        clauses.append("(u.name LIKE ? ESCAPE '\\' OR u.phone LIKE ? ESCAPE '\\')")
        parameters.extend([search_pattern(q)] * 2)
    if status != 'all':
        clauses.append('u.is_active=?')
        parameters.append(1 if status == 'active' else 0)
    where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
    total = (await connection.execute('SELECT COUNT(*) FROM users u' + where, parameters)).fetchone()[0]
    rows = (await connection.execute("""SELECT u.id,u.name,u.phone,u.role,u.is_active,u.created_at,u.is_super_admin,u.store_id,
        (SELECT COUNT(*) FROM orders o WHERE o.user_id=u.id AND """ + where_orders + """) AS order_count,
        (SELECT COALESCE(SUM(o.total),0) FROM orders o WHERE o.user_id=u.id AND o.status='paid' AND """ + where_orders + """ AND NOT EXISTS (SELECT 1 FROM refunds r WHERE r.order_id=o.id AND r.status='approved')) AS spent
        FROM users u""" + where + ' ORDER BY u.id DESC LIMIT ? OFFSET ?', [*order_args, *order_args, *parameters, page_size, (page - 1) * page_size])).fetchall()
    return {'items': [dict(row) for row in rows], 'total': total, 'page': page, 'page_size': page_size}


async def admin_user_status(user_id, payload, user, connection):
    require_super_admin(user)
    async with transaction(connection):
        await connection.lock_user(user_id)
        target = (await connection.execute('SELECT id,role FROM users WHERE id=?', (user_id,))).fetchone()
        if not target:
            raise MallError(404, '用户不存在')
        if target['role'] == 'admin':
            raise MallError(409, '管理员账号不能在用户管理中停用或修改')
        (await connection.execute('UPDATE users SET is_active=? WHERE id=?', (payload.is_active, user_id)))
        if not payload.is_active:
            (await connection.execute('DELETE FROM sessions WHERE user_id=?', (user_id,)))
        return dict((await connection.execute('SELECT id,name,phone,role,is_active,created_at FROM users WHERE id=?', (user_id,))).fetchone())
