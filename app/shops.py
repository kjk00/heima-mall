"""Store management and shared store metadata."""
import sqlite3
from dataclasses import asdict
from app.admin import require_admin, require_super_admin, search_pattern, scope_clause, store_scope
from app.async_contract import transaction
from app.errors import MallError
from app.features import page_data


async def store_row(store_id, connection, active=False):
    row=(await connection.execute('SELECT * FROM stores WHERE id=?',(store_id,))).fetchone()
    if not row:
        raise MallError(404,'店铺不存在')
    if active and not row['is_active']:
        raise MallError(409,'店铺已暂停营业')
    return dict(row)


async def product_data(row,connection):
    product=dict(row)
    product.pop('params_json',None)
    product.pop('detail_html',None)
    store=(await store_row(product['store_id'],connection))
    return product | {'store_name':store['name'],'store_active':store['is_active'],
        'store_version':store['version'],'store_description':store['description'],
        'store_phone':store['phone'],'store_shipping_fee':store['shipping_fee'],
        'free_shipping_threshold':store['free_shipping_threshold']}


async def product_detail(row,connection):
    """Detail view: MySQL base + detail_html, MongoDB params (MySQL fallback), Redis-cached upstream."""
    from app import mongodb
    data=await product_data(row,connection)
    data['detail_html']=row['detail_html'] if 'detail_html' in row.keys() else ''
    path=getattr(connection,'path',None)
    data['params']=await mongodb.read_params(dict(row),path) if path is not None else mongodb.parse_params(row['params_json'] if 'params_json' in row.keys() else '[]')
    return data


async def stores(connection,q=''):
    args=(search_pattern(q.strip()),)*2 if q.strip() else ()
    where=" AND (s.name LIKE ? ESCAPE '\\' OR s.description LIKE ? ESCAPE '\\')" if q.strip() else ''
    return [dict(r) for r in (await connection.execute('''SELECT s.id,s.name,s.description,s.phone,s.shipping_fee,
        s.free_shipping_threshold,(SELECT COUNT(*) FROM products p WHERE p.store_id=s.id AND p.is_active=1) AS products
        FROM stores s WHERE s.is_active=1'''+where+' ORDER BY s.id',args))]


async def search_suggestions(request,connection,q=''):
    from app import logic
    user=None
    try:
        user=(await logic.current_user(request,connection))
    except MallError as error:
        if error.status_code!=401:raise
    recent=[]
    if user:
        recent=(await connection.execute('''SELECT p.* FROM browsing_history h JOIN products p ON p.id=h.product_id
            JOIN stores s ON s.id=p.store_id WHERE h.user_id=? AND p.is_active=1 AND s.is_active=1
            ORDER BY h.sort_key DESC LIMIT 4''',(user['id'],))).fetchall()
    categories=list(dict.fromkeys(r['category'] for r in recent))
    interested_stores={r['store_id'] for r in recent}
    clauses=['p.is_active=1','p.store_id IN (SELECT id FROM stores WHERE is_active=1)']
    args=[]
    if q.strip():
        clauses.append("(p.name LIKE ? ESCAPE '\\' OR p.subtitle LIKE ? ESCAPE '\\' OR p.store_id IN (SELECT id FROM stores WHERE name LIKE ? ESCAPE '\\'))")
        args.extend([search_pattern(q.strip())]*3)
    priority=''
    if categories:
        priority='CASE WHEN p.category IN ('+','.join('?' for _ in categories)+') THEN 0 ELSE 1 END,'
    rows=(await connection.execute('SELECT p.* FROM products p WHERE '+' AND '.join(clauses)+' ORDER BY '+priority+'p.sales DESC,p.id LIMIT 6',(*args,*categories))).fetchall()
    # Only show recent items when no search term is present, keeping typed suggestions relevant.
    selected={r['id']:(await product_data(r,connection))|{'suggestion_label':'最近浏览'} for r in recent} if not q.strip() else {}
    for row in rows:
        if row['id'] not in selected:selected[row['id']]=(await product_data(row,connection))|{'suggestion_label':'感兴趣的热门好物' if row['category'] in categories else '热门好物'}
    store_rows=[s for s in (await stores(connection,q)) if s['products']]
    sales={r['store_id']:r['sales'] for r in (await connection.execute('SELECT store_id,SUM(sales) AS sales FROM products WHERE is_active=1 GROUP BY store_id'))}
    store_rows.sort(key=lambda s:(s['id'] not in interested_stores,-sales.get(s['id'],0),s['id']))
    for row in store_rows:row['suggestion_label']='来自最近浏览' if row['id'] in interested_stores else '热门店铺'
    return {'products':list(selected.values())[:8],'stores':store_rows[:4],'personalized':bool(recent)}


async def store(store_id,connection):
    result=(await store_row(store_id,connection))
    if not result['is_active']:raise MallError(404,'店铺已暂停营业')
    result.pop('version')
    result['products']=(await connection.execute('SELECT COUNT(*) FROM products WHERE store_id=? AND is_active=1',(store_id,))).fetchone()[0]
    return result


async def admin_stores(user,connection,q='',status='all',page=1,page_size=10):
    require_admin(user)
    clause, parameters = scope_clause(user, 's.id')
    clauses,args=[clause],list(parameters)
    if q:
        clauses.append("(s.name LIKE ? ESCAPE '\\' OR s.description LIKE ? ESCAPE '\\')")
        args.extend([search_pattern(q)]*2)
    if status not in {'all','active','inactive'}:raise MallError(422,'店铺状态不正确')
    if status!='all':clauses.append('s.is_active=?');args.append(status=='active')
    where=' WHERE '+' AND '.join(clauses) if clauses else ''
    sql='''SELECT s.*,(SELECT COUNT(*) FROM products p WHERE p.store_id=s.id) AS products,
        (SELECT COUNT(*) FROM products p WHERE p.store_id=s.id AND p.is_active=1) AS active_products,
        (SELECT COUNT(*) FROM orders o WHERE o.store_id=s.id) AS orders,
        (SELECT COALESCE(SUM(o.total),0) FROM orders o WHERE o.store_id=s.id AND o.status='paid'
        AND NOT EXISTS(SELECT 1 FROM refunds r WHERE r.order_id=o.id AND r.status='approved')) AS revenue
        FROM stores s'''+where+' ORDER BY s.id DESC'
    return (await page_data(connection,sql,tuple(args),page,page_size))


async def admin_store(store_id,user,connection):
    store_scope(user, store_id)
    return (await store_row(store_id,connection))


async def admin_create_store(payload,user,connection):
    require_super_admin(user)
    values=asdict(payload)
    try:
        async with transaction(connection):
            store_id=(await connection.execute('INSERT INTO stores('+','.join(values)+') VALUES ('+','.join('?' for _ in values)+')',tuple(values.values()))).lastrowid
    except sqlite3.IntegrityError:raise MallError(409,'店铺名称已存在')
    return (await store_row(store_id,connection))


async def admin_update_store(store_id,payload,user,connection):
    store_scope(user, store_id)
    values=asdict(payload);version=values.pop('version')
    try:
        async with transaction(connection):
            (await store_row(store_id,connection))
            result=(await connection.execute('UPDATE stores SET '+','.join(k+'=?' for k in values)+',version=version+1 WHERE id=? AND version=?',(*values.values(),store_id,version)))
            if not result.rowcount:raise MallError(409,'店铺已发生变化，请重新打开编辑窗口')
    except sqlite3.IntegrityError:raise MallError(409,'店铺名称已存在')
    return (await store_row(store_id,connection))


async def admin_store_status(store_id,payload,user,connection):
    store_scope(user, store_id)
    async with transaction(connection):
        (await store_row(store_id,connection))
        result=(await connection.execute('UPDATE stores SET is_active=?,version=version+1 WHERE id=? AND version=?',(payload.is_active,store_id,payload.version)))
        if not result.rowcount:raise MallError(409,'店铺已发生变化，请刷新后重试')
    return (await store_row(store_id,connection))

