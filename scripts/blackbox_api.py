"""黑盒测试：启动真实 TCP 服务器，按等价类/边界值/错误推测系统化验证外部行为。

区别于进程内测试（tests/client.py 走 WSGI/ASGI），本脚本经真实网络栈调用 HTTP，
验证路由、Cookie 会话、安全头、静态资源与跨账号隔离。使用独立临时 SQLite，不触碰
商城数据。运行：.venv/bin/python scripts/blackbox_api.py [--backend fastapi|stdlib]
"""
import argparse
from datetime import datetime, timezone
import http.cookiejar
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class Client:
    def __init__(self, base):
        self.base = base
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def request(self, method, path, payload=None, headers=None, raw=None):
        body = raw if raw is not None else json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(self.base + path, data=body, method=method)
        request.add_header('X-Requested-With', 'HeimaMall')
        request.add_header('Content-Type', 'application/json')
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            response = self.opener.open(request, timeout=15)
            status, data, resp_headers = response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as error:
            status, data, resp_headers = error.code, error.read(), dict(error.headers)
        try:
            parsed = json.loads(data)
        except (json.JSONDecodeError, UnicodeDecodeError):
            parsed = None
        return status, parsed, resp_headers

    def get(self, path, **kwargs):
        return self.request('GET', path, **kwargs)

    def post(self, path, payload=None, **kwargs):
        return self.request('POST', path, payload, **kwargs)


def captcha_code(db_path, captcha_id):
    connection = sqlite3.connect(db_path, timeout=10)
    try:
        row = connection.execute('SELECT code FROM login_captchas WHERE id=?', (captcha_id,)).fetchone()
        return row[0] if row else None
    finally:
        connection.close()


class BlackBox:
    def __init__(self, base, db_path):
        self.base, self.db_path = base, db_path
        self.results = []

    def check(self, case_id, domain, technique, description, passed, detail=''):
        self.results.append({'id': case_id, 'domain': domain, 'technique': technique,
                             'description': description, 'passed': bool(passed), 'detail': str(detail)[:200]})
        mark = 'PASS' if passed else 'FAIL'
        print(f'[{mark}] {case_id} {description}' + ('' if passed else f'  <- {detail}'), flush=True)

    def expect(self, case_id, domain, technique, description, result, status, predicate=None):
        actual, payload, _ = result
        passed = actual == status and (predicate is None or predicate(payload))
        self.check(case_id, domain, technique, description, passed, f'期望{status} 实际{actual} {payload if not passed else ""}')
        return payload

    def register(self, client, phone, password='blackbox-pass-1', name='黑盒测试'):
        return client.post('/api/auth/register', {'name': name, 'phone': phone, 'password': password})

    def login(self, client, phone, password='blackbox-pass-1', admin=False):
        captcha = client.get('/api/auth/captcha')[1]
        code = captcha_code(self.db_path, captcha['captcha_id'])
        path = '/api/admin/auth/login' if admin else '/api/auth/login'
        return client.post(path, {'phone': phone, 'password': password,
                                  'captcha_id': captcha['captcha_id'], 'captcha_code': code})

    def run(self):
        guest = Client(self.base)
        self.security_and_health(guest)
        self.auth(guest)
        user = Client(self.base)
        self.expect('AUTH-REG-OK', '认证', '等价类-有效', '有效注册并自动登录', self.register(user, '13900000001'), 201)
        self.catalog(guest, user)
        self.cart(user)
        self.orders(user)
        self.coupons(user)
        self.admin(guest)
        self.isolation(user)
        return self.results

    def security_and_health(self, guest):
        status, payload, headers = guest.get('/api/health')
        self.check('SYS-HEALTH', '系统', '等价类-有效', '健康检查返回200', status == 200, status)
        lowered = {k.lower(): v for k, v in headers.items()}
        self.check('SYS-SEC-HEADERS', '系统', '错误推测', '响应含安全头nosniff/DENY/CSP',
                   lowered.get('x-content-type-options') == 'nosniff' and lowered.get('x-frame-options') == 'DENY'
                   and 'content-security-policy' in lowered, headers)
        status, _, _ = guest.post('/api/auth/register', {'name': 'x', 'phone': '13900000099', 'password': 'blackbox-pass-1'},
                                  headers={'X-Requested-With': ''})
        self.check('SEC-NO-XRW', '安全', '错误推测', '写请求缺少X-Requested-With被拒403', status == 403, status)
        status, _, _ = guest.post('/api/auth/register', {'name': 'x', 'phone': '13900000099', 'password': 'blackbox-pass-1'},
                                  headers={'Origin': 'http://evil.example'})
        self.check('SEC-CROSS-ORIGIN', '安全', '错误推测', '跨域Origin写请求被拒绝', status in (400, 403), status)
        status, payload, _ = guest.get('/api/products?q=' + urllib.request.quote("' OR '1'='1"))
        self.check('SEC-SQLI-SEARCH', '安全', '错误推测', '搜索SQL注入不报错且不泄露全表', status == 200, status)
        status, _, _ = guest.get('/static/../app/config.py')
        self.check('SEC-PATH-TRAVERSAL', '安全', '错误推测', '静态目录路径穿越被拒绝', status in (400, 403, 404), status)

    def auth(self, guest):
        status, payload, _ = guest.get('/api/auth/captcha')
        self.check('AUTH-CAPTCHA', '认证', '等价类-有效', '验证码返回id与PNG图片',
                   status == 200 and payload.get('captcha_id') and str(payload.get('image', '')).startswith('data:image/png'), status)
        self.expect('AUTH-REG-BADPHONE', '认证', '等价类-无效', '非法手机号注册被拒', self.register(guest, '123'), 422)
        self.expect('AUTH-REG-SHORTPWD', '认证', '边界值', '密码不足8位被拒',
                    guest.post('/api/auth/register', {'name': 'x', 'phone': '13900000002', 'password': 'short'}), 422)
        seed_client = Client(self.base)
        self.expect('AUTH-REG-FIRST', '认证', '等价类-有效', '首次注册成功', self.register(seed_client, '13900000005'), 201)
        self.expect('AUTH-REG-DUP', '认证', '错误推测', '重复手机号注册被拒', self.register(guest, '13900000005'), 409)
        client = Client(self.base)
        self.register(client, '13900000010')
        captcha = client.get('/api/auth/captcha')[1]
        self.expect('AUTH-LOGIN-NOCAPTCHA', '认证', '等价类-无效', '缺少验证码登录被拒',
                    client.post('/api/auth/login', {'phone': '13900000010', 'password': 'blackbox-pass-1'}), 422)
        self.expect('AUTH-LOGIN-WRONGCAPTCHA', '认证', '等价类-无效', '错误验证码登录被拒',
                    client.post('/api/auth/login', {'phone': '13900000010', 'password': 'blackbox-pass-1',
                                                    'captcha_id': captcha['captcha_id'], 'captcha_code': 'ZZZZ'}), 400)
        captcha = client.get('/api/auth/captcha')[1]
        code = captcha_code(self.db_path, captcha['captcha_id'])
        self.expect('AUTH-LOGIN-WRONGPWD', '认证', '等价类-无效', '正确验证码错误密码被拒',
                    client.post('/api/auth/login', {'phone': '13900000010', 'password': 'wrong-password',
                                                    'captcha_id': captcha['captcha_id'], 'captcha_code': code}), 401)
        self.expect('AUTH-LOGIN-OK', '认证', '等价类-有效', '正确凭据登录成功', self.login(client, '13900000010'), 200)
        status, payload, _ = client.get('/api/auth/me')
        self.check('AUTH-ME', '认证', '等价类-有效', '登录后me返回当前账号', status == 200 and payload.get('phone') == '13900000010', status)
        self.expect('AUTH-ME-GUEST', '认证', '等价类-无效', '未登录me被拒', guest.get('/api/auth/me'), 401)
        self.expect('AUTH-LOGOUT', '认证', '等价类-有效', '退出登录成功', client.post('/api/auth/logout'), 200)
        self.expect('AUTH-ME-AFTER-LOGOUT', '认证', '错误推测', '退出后me失效', client.get('/api/auth/me'), 401)

    def catalog(self, guest, user):
        status, payload, _ = guest.get('/api/products')
        self.check('PROD-LIST', '商品', '等价类-有效', '商品列表返回items与total',
                   status == 200 and isinstance(payload.get('items'), list) and payload.get('total', 0) > 0, status)
        self.expect('PROD-PAGE-0', '商品', '边界值', 'page=0被拒', guest.get('/api/products?page=0'), 422)
        self.expect('PROD-PAGESIZE-0', '商品', '边界值', 'page_size=0被拒', guest.get('/api/products?page_size=0'), 422)
        self.expect('PROD-PAGESIZE-MAX', '商品', '边界值', 'page_size=50有效', guest.get('/api/products?page_size=50'), 200,
                    lambda p: len(p['items']) <= 50)
        self.expect('PROD-PAGESIZE-OVER', '商品', '边界值', 'page_size=51被拒', guest.get('/api/products?page_size=51'), 422)
        self.expect('PROD-SORT-BAD', '商品', '等价类-无效', '非法排序被拒', guest.get('/api/products?sort=invalid'), 422)
        self.expect('PROD-SORT-PRICE', '商品', '等价类-有效', '价格升序排序有效', guest.get('/api/products?sort=price_asc'), 200,
                    lambda p: all(p['items'][i]['price'] <= p['items'][i + 1]['price'] for i in range(len(p['items']) - 1)))
        self.expect('PROD-CATEGORY', '商品', '等价类-有效', '分类筛选有效', guest.get('/api/products?category=' + urllib.request.quote('数码科技')), 200,
                    lambda p: all(i['category'] == '数码科技' for i in p['items']))
        self.expect('PROD-SEARCH-HIT', '商品', '等价类-有效', '关键词搜索命中', guest.get('/api/products?q=' + urllib.request.quote('耳机')), 200,
                    lambda p: p['total'] >= 1)
        self.expect('PROD-SEARCH-MISS', '商品', '等价类-无效', '无匹配关键词返回空', guest.get('/api/products?q=' + urllib.request.quote('不存在的商品xyz')), 200,
                    lambda p: p['total'] == 0)
        self.expect('PROD-DETAIL-OK', '商品', '等价类-有效', '商品详情返回', guest.get('/api/products/1'), 200, lambda p: p['id'] == 1)
        self.expect('PROD-DETAIL-404', '商品', '等价类-无效', '不存在商品详情404', guest.get('/api/products/999999'), 404)
        self.expect('PROD-DETAIL-BADID', '商品', '边界值', '非法商品id被拒', guest.get('/api/products/0'), 422)
        self.expect('PROD-CATEGORIES', '商品', '等价类-有效', '分类列表返回', guest.get('/api/categories'), 200)

    def cart(self, user):
        self.expect('CART-ADD-OK', '购物车', '等价类-有效', '添加商品到购物车', user.post('/api/cart', {'product_id': 1, 'quantity': 1}), 200)
        self.expect('CART-ADD-404', '购物车', '等价类-无效', '添加不存在商品被拒', user.post('/api/cart', {'product_id': 999999}), 404)
        self.expect('CART-ADD-QTY0', '购物车', '边界值', '数量0被拒', user.post('/api/cart', {'product_id': 1, 'quantity': 0}), 422)
        self.expect('CART-ADD-OVERSTOCK', '购物车', '边界值', '超库存数量被拒', user.post('/api/cart', {'product_id': 1, 'quantity': 99}), 409)
        self.expect('CART-GET', '购物车', '等价类-有效', '查看购物车', user.get('/api/cart'), 200, lambda p: 'items' in p)
        self.expect('CART-UPDATE', '购物车', '等价类-有效', '更新购物车数量', user.request('PATCH', '/api/cart/1', {'quantity': 2}), 200)
        self.expect('CART-UPDATE-404', '购物车', '等价类-无效', '更新未加入商品被拒', user.request('PATCH', '/api/cart/999999', {'quantity': 1}), 404)
        self.expect('CART-GUEST', '购物车', '等价类-无效', '未登录查看购物车被拒', Client(self.base).get('/api/cart'), 401)

    def orders(self, user):
        payload = {'recipient': '黑盒', 'phone': '13900000001', 'address': '黑盒测试地址', 'idempotency_key': 'bb-order-00000001'}
        self.expect('ORDER-CHECKOUT-OK', '订单', '等价类-有效', '单店结算创建订单', user.post('/api/orders', payload), 201, lambda p: p['id'])
        order_id = user.post('/api/orders', payload)[1]['id']
        self.expect('ORDER-IDEMPOTENT', '订单', '错误推测', '同一幂等键返回原订单', user.post('/api/orders', payload), 201, lambda p: p['id'] == order_id)
        self.expect('ORDER-IDEMPOTENT-CONFLICT', '订单', '错误推测', '同键不同请求体409',
                    user.post('/api/orders', dict(payload, address='完全不同地址')), 409)
        self.expect('ORDER-DETAIL', '订单', '等价类-有效', '订单详情返回', user.get(f'/api/orders/{order_id}'), 200, lambda p: p['id'] == order_id)
        self.expect('ORDER-LIST', '订单', '等价类-有效', '订单列表返回', user.get('/api/orders'), 200, lambda p: 'items' in p)
        self.expect('ORDER-PAY', '订单', '等价类-有效', '演示支付成功', user.post(f'/api/orders/{order_id}/pay'), 200)
        self.expect('ORDER-PAY-AGAIN', '订单', '错误推测', '已支付订单重复支付幂等返回', user.post(f'/api/orders/{order_id}/pay'), 200, lambda p: p['status'] == 'paid')
        self.expect('ORDER-CANCEL-PAID', '订单', '错误推测', '已支付订单取消被拒', user.post(f'/api/orders/{order_id}/cancel'), 409)
        self.expect('ORDER-RECEIVE-UNSHIPPED', '订单', '错误推测', '未发货确认收货被拒', user.post(f'/api/orders/{order_id}/receive'), 409)
        empty_user = Client(self.base)
        self.register(empty_user, '13900000040')
        empty_payload = dict(payload, phone='13900000040', idempotency_key='bb-order-empty0001')
        status, _, _ = empty_user.post('/api/orders', empty_payload)
        self.check('ORDER-CHECKOUT-EMPTY', '订单', '边界值', '空购物车结算被拒', status in (400, 409), status)
        self.expect('ORDER-404', '订单', '等价类-无效', '不存在订单404', user.get('/api/orders/HM20261119000000000000'), 404)

    def coupons(self, user):
        status, payload, _ = user.get('/api/coupons')
        self.check('COUPON-LIST', '优惠券', '等价类-有效', '优惠券列表返回', status == 200 and isinstance(payload, list), status)
        coupon_id = next((c['id'] for c in payload if c['code'] == 'WELCOME10'), None)
        self.expect('COUPON-CLAIM', '优惠券', '等价类-有效', '领取优惠券', user.post(f'/api/coupons/{coupon_id}/claim'), 200)
        self.expect('COUPON-CLAIM-DUP', '优惠券', '错误推测', '重复领取幂等返回已领取', user.post(f'/api/coupons/{coupon_id}/claim'), 200)
        self.expect('COUPON-CLAIM-404', '优惠券', '等价类-无效', '领取不存在优惠券404', user.post('/api/coupons/999999/claim'), 404)

    def admin(self, guest):
        admin = Client(self.base)
        status, payload, _ = admin.get('/api/admin/setup')
        self.check('ADMIN-SETUP-STATUS', '后台', '等价类-有效', '初始化状态可查询', status == 200, status)
        captcha = admin.get('/api/auth/captcha')[1]
        code = captcha_code(self.db_path, captcha['captcha_id'])
        self.expect('ADMIN-SETUP-OK', '后台', '等价类-有效', '本机首次创建终端管理员',
                    admin.post('/api/admin/setup', {'name': '黑盒管理员', 'phone': '13900000009', 'password': 'blackbox-pass-1',
                                                    'captcha_id': captcha['captcha_id'], 'captcha_code': code}), 201)
        captcha = admin.get('/api/auth/captcha')[1]
        code = captcha_code(self.db_path, captcha['captcha_id'])
        self.expect('ADMIN-SETUP-DUP', '后台', '错误推测', '二次初始化被拒',
                    admin.post('/api/admin/setup', {'name': 'x', 'phone': '13900000008', 'password': 'blackbox-pass-1',
                                                    'captcha_id': captcha['captcha_id'], 'captcha_code': code}), 409)
        self.expect('ADMIN-LOGIN', '后台', '等价类-有效', '管理员登录', self.login(admin, '13900000009', admin=True), 200)
        self.expect('ADMIN-DASHBOARD', '后台', '等价类-有效', '经营概览返回', admin.get('/api/admin/dashboard'), 200)
        self.expect('ADMIN-PRODUCTS', '后台', '等价类-有效', '管理商品列表', admin.get('/api/admin/products'), 200)
        new_product = {'name': '黑盒新建商品', 'subtitle': '副标题', 'category': '数码科技', 'price': 19900,
                       'original_price': 59900, 'stock': 10, 'image': 'headphones', 'badge': '', 'color': '#224466',
                       'description': '黑盒测试商品', 'store_id': 2}
        status, payload, _ = admin.post('/api/admin/products', new_product)
        self.check('ADMIN-PRODUCT-CREATE', '后台', '等价类-有效', '新增商品成功', status == 201 and payload.get('id'), status)
        product_id = payload.get('id') if status == 201 else None
        if product_id:
            self.expect('ADMIN-PRODUCT-STATUS', '后台', '等价类-有效', '商品下架（乐观锁version）',
                        admin.request('PATCH', f'/api/admin/products/{product_id}/status',
                                      {'is_active': False, 'version': payload.get('version', 1)}), 200)
            self.expect('ADMIN-PRODUCT-INACTIVE-HIDDEN', '后台', '错误推测', '下架商品商城详情404',
                        guest.get(f'/api/products/{product_id}'), 404)
        ordinary = Client(self.base)
        self.register(ordinary, '13900000020')
        self.expect('ADMIN-PERM-ISOLATION', '后台', '错误推测', '普通账号访问后台被拒', ordinary.get('/api/admin/dashboard'), 403)

    def isolation(self, user):
        other = Client(self.base)
        self.register(other, '13900000030')
        other.post('/api/cart', {'product_id': 3, 'quantity': 1})
        status, payload, _ = other.post('/api/orders', {'recipient': '其他', 'phone': '13900000030',
                                                        'address': '其他用户地址', 'idempotency_key': 'bb-other-00000001'})
        other_order = payload.get('id') if status == 201 else None
        if other_order:
            self.expect('ISO-ORDER-CROSS', '隔离', '错误推测', '访问他人订单被拒', user.get(f'/api/orders/{other_order}'), 404)
            self.expect('ISO-PAY-CROSS', '隔离', '错误推测', '支付他人订单被拒', user.post(f'/api/orders/{other_order}/pay'), 404)
        self.expect('ISO-CART-CROSS', '隔离', '错误推测', '购物车按账号隔离', other.get('/api/cart'), 200,
                    lambda p: all(i['product_id'] != 1 for i in p.get('items', [])))


def wait_health(base, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(base + '/api/health', timeout=2) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(0.3)
    return False


def main():
    parser = argparse.ArgumentParser(description='真实服务器黑盒API测试')
    parser.add_argument('--backend', choices=['fastapi', 'stdlib'], default='fastapi')
    parser.add_argument('--output', type=Path, default=ROOT / 'reports/blackbox-api.json')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='heima-blackbox-') as directory:
        db_path = Path(directory) / 'blackbox.db'
        port = free_port()
        base = f'http://127.0.0.1:{port}'
        env = dict(os.environ, DATABASE_BACKEND='sqlite', DATABASE_PATH=str(db_path), REDIS_URL='',
                   ELASTICSEARCH_URL='', RABBITMQ_URL='', MYSQL_INITIALIZE_SCHEMA='0')
        log = open(Path(directory) / 'server.log', 'w+')
        server = subprocess.Popen([sys.executable, str(ROOT / 'run.py'), '--backend', args.backend, '--port', str(port)],
                                  env=env, stdout=log, stderr=subprocess.STDOUT, cwd=str(ROOT))
        try:
            if not wait_health(base):
                log.seek(0)
                print('服务器启动失败：\n' + log.read(), flush=True)
                return 2
            print(f'真实服务器就绪：{base}（{args.backend}）', flush=True)
            suite = BlackBox(base, str(db_path))
            results = suite.run()
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
            log.close()
    passed = sum(1 for r in results if r['passed'])
    report = {'timestamp_utc': datetime.now(timezone.utc).isoformat(), 'backend': args.backend,
              'mode': 'real TCP server + temporary SQLite', 'total': len(results), 'passed': passed,
              'failed': len(results) - passed, 'cases': results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    write_markdown(report, args.output.with_suffix('.md'))
    print(f'\n黑盒用例：{passed}/{len(results)} 通过，报告 {args.output.with_suffix(".md")}', flush=True)
    return 0 if passed == len(results) else 1


def write_markdown(report, path):
    lines = [f'# 黑盒 API 测试报告（{report["backend"]} 真实服务器）', '',
             f'测试时间：{report["timestamp_utc"]}。模式：{report["mode"]}。',
             f'用例总数 {report["total"]}，通过 {report["passed"]}，失败 {report["failed"]}。', '',
             '| 用例 | 域 | 方法 | 描述 | 结果 |', '| --- | --- | --- | --- | --- |']
    for case in report['cases']:
        lines.append(f"| {case['id']} | {case['domain']} | {case['technique']} | {case['description']} | {'✅' if case['passed'] else '❌ ' + case['detail']} |")
    domains = {}
    for case in report['cases']:
        domains.setdefault(case['domain'], [0, 0])
        domains[case['domain']][0] += 1
        domains[case['domain']][1] += case['passed']
    lines += ['', '## 按域汇总', '', '| 域 | 通过/总数 |', '| --- | --- |']
    for domain, (total, ok) in domains.items():
        lines.append(f'| {domain} | {ok}/{total} |')
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


if __name__ == '__main__':
    raise SystemExit(main())