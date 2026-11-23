"""Small WSGI adapter for local development, using only the Python standard library."""
import inspect
import json
import logging
import mimetypes
import os
import re
import secrets
import sqlite3
import threading
import asyncio
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from app import admin, logic, features, shops, photo_search
from app.database import Database
from app.cache import CatalogCache
from app.config import load_environment
from app.async_contract import SyncConnectionAdapter
from app.errors import MallError
from app.media import media_file, media_bytes
from app.storage import ObjectStorage
from app.routes import ROUTES, openapi_document
from app.schemas import number, text
from app.search import SearchEngine
from app.security import hash_password

ROOT = Path(__file__).resolve().parent.parent
SECURITY_HEADERS = [('X-Content-Type-Options', 'nosniff'), ('X-Frame-Options', 'DENY'),
    ('Referrer-Policy', 'strict-origin-when-cross-origin'),
    ('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'")]


class CookieResponse:
    def __init__(self):
        self.headers = []

    def set_cookie(self, key, value, max_age=None, httponly=False, secure=False, samesite='lax', path='/'):
        cookie = SimpleCookie()
        cookie[key] = value
        cookie[key]['path'] = path
        cookie[key]['samesite'] = samesite
        if max_age is not None:
            cookie[key]['max-age'] = max_age
        if httponly:
            cookie[key]['httponly'] = True
        if secure:
            cookie[key]['secure'] = True
        self.headers.append(('Set-Cookie', cookie[key].OutputString()))

    def delete_cookie(self, key, path='/'):
        self.set_cookie(key, '', max_age=0, httponly=True, path=path)


class MallApplication:
    def __init__(self, database_path=None):
        if database_path is None:
            load_environment()
        self.state = SimpleNamespace(
            database=Database(database_path),
            login_attempts=[], captcha_attempts=[], login_lock=threading.Lock(), dummy_password=hash_password(secrets.token_urlsafe(24))
        )
        self.state.database.initialize()
        self.state.catalog_cache = CatalogCache.from_environment(self.state.database.path)
        self.state.search_engine = SearchEngine.from_environment(self.state.database.path)
        self.state.media_root = self.state.database.path.parent / 'uploads'
        self.state.storage = ObjectStorage.from_environment(self.state.media_root)
        self.state.photo_search = photo_search.PhotoSearch.from_environment()
        self.routes = [(route, re.compile('^' + re.sub(r'\{(\w+)\}', r'(?P<\1>[^/]+)', route.path) + '$')) for route in ROUTES]

    def __call__(self, environ, start_response):
        response = CookieResponse()
        method = environ.get('REQUEST_METHOD', 'GET')
        path = environ.get('PATH_INFO', '/')
        status = 200
        mime = 'application/json; charset=utf-8'
        try:
            if path.startswith('/api/') and method in {'POST', 'PUT', 'PATCH', 'DELETE'}:
                self.protect_request(environ)
            public_page = re.fullmatch(r'/(?:products|stores)/[1-9]\d{0,18}/?',path)
            if (path in {'/', '/docs', '/admin', '/admin/'} or public_page) and method in {'GET', 'HEAD'}:
                filename = 'index.html' if path == '/' or public_page else 'docs.html' if path == '/docs' else 'admin.html'
                body = (ROOT / 'app' / 'templates' / filename).read_bytes()
                mime = 'text/html; charset=utf-8'
            elif path.startswith('/static/') and method in {'GET', 'HEAD'}:
                static_root = (ROOT / 'app' / 'static').resolve()
                file = (static_root / path.removeprefix('/static/')).resolve()
                if not file.is_relative_to(static_root) or not file.is_file():
                    raise MallError(404, '文件不存在')
                body = file.read_bytes()
                mime = mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
            elif path.startswith('/media/') and method in {'GET', 'HEAD'}:
                body, mime = media_bytes(self.state.storage, path.removeprefix('/media/'))
            elif path == '/openapi.json' and method in {'GET', 'HEAD'}:
                body = self.encode(openapi_document())
            else:
                result, status = self.dispatch(environ, response)
                body = self.encode(result)
        except MallError as error:
            status, body = error.status_code, self.encode({'detail': error.detail})
        except (ValueError, TypeError, OverflowError) as error:
            status, body = 422, self.encode({'detail': str(error)})
        except sqlite3.OperationalError:
            logging.exception('Database operation failed')
            status, body = 503, self.encode({'detail': '数据库暂时繁忙，请稍后重试'})
        except Exception:
            logging.exception('Unhandled request error')
            status, body = 500, self.encode({'detail': '服务暂时不可用，请稍后重试'})
        headers = [('Content-Type', mime), ('Content-Length', str(len(body))), ('Cache-Control', 'no-store' if path.startswith('/api/') else 'no-cache')]
        start_response(f'{status} {HTTPStatus(status).phrase}', headers + SECURITY_HEADERS + response.headers)
        return [b'' if method == 'HEAD' else body]

    @staticmethod
    def encode(data):
        return json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode('utf-8')

    @staticmethod
    def protect_request(environ):
        if environ.get('HTTP_X_REQUESTED_WITH') != 'HeimaMall':
            raise MallError(403, '请求缺少 X-Requested-With: HeimaMall 标头')
        origin = environ.get('HTTP_ORIGIN')
        if origin:
            parsed = urlsplit(origin)
            if (parsed.scheme, parsed.netloc) != (environ.get('wsgi.url_scheme', 'http'), environ.get('HTTP_HOST')):
                raise MallError(403, '不允许跨站提交请求')

    def dispatch(self, environ, response):
        path = environ.get('PATH_INFO', '/')
        method = environ.get('REQUEST_METHOD', 'GET')
        matched_path = False
        for route, pattern in self.routes:
            match = pattern.fullmatch(path)
            if not match:
                continue
            matched_path = True
            if route.method != ('GET' if method == 'HEAD' else method):
                continue
            cookies = SimpleCookie()
            try:
                cookies.load(environ.get('HTTP_COOKIE', ''))
            except CookieError:
                pass
            request = SimpleNamespace(cookies={key: item.value for key, item in cookies.items()},
                                      app=self, client=SimpleNamespace(host=environ.get('REMOTE_ADDR', 'local')),
                                      headers={key[5:].lower().replace('_', '-'): value
                                               for key, value in environ.items() if key.startswith('HTTP_')})
            if route.admin:
                with self.state.database.connection() as auth_connection:
                    asyncio.run(admin.current_admin(request, SyncConnectionAdapter(auth_connection)))
            arguments = match.groupdict()
            for identifier in ('product_id', 'user_id', 'address_id', 'coupon_id', 'refund_id', 'comment_id', 'conversation_id','store_id'):
                if identifier in arguments:
                    arguments[identifier] = number(int(arguments[identifier]), '编号', 1, 2**63 - 1)
            if route.model:
                content_type = environ.get('CONTENT_TYPE', '').split(';')[0].lower()
                if content_type != 'application/json':
                    raise MallError(415, '请求内容必须为 application/json')
                length = int(environ.get('CONTENT_LENGTH') or 0)
                maximum = 3 * 1024 * 1024 if route.operation in {'admin_upload_image', 'search_photo'} else 16384
                if not 0 < length <= maximum:
                    raise MallError(413 if length > maximum else 400, '请求正文为空或过大')
                try:
                    payload = json.loads(environ['wsgi.input'].read(length))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    raise MallError(400, 'JSON 格式不正确')
                if not isinstance(payload, dict):
                    raise MallError(422, '请求正文必须为 JSON 对象')
                arguments['payload'] = route.model(**payload)
            try:
                query = parse_qs(environ.get('QUERY_STRING', ''), max_num_fields=20)
            except ValueError:
                raise MallError(422, '查询参数过多')
            if route.operation in {'products', 'orders'}:
                arguments['page'] = number(int(query.get('page', ['1'])[0]), '页码', 1, 2**31 - 1)
                arguments['page_size'] = number(int(query.get('page_size', ['12' if route.operation == 'products' else '10'])[0]), '每页数量', 1, 50)
            if route.operation == 'products':
                raw=query.get('store_id',[''])[0]
                arguments['store_id']=number(int(raw),'店铺编号',1,2**63-1) if raw else None
                sale=query.get('on_sale',['false'])[0]
                if sale not in {'true','false','1','0'}: raise MallError(422,'折扣筛选参数不正确')
                arguments['on_sale']=sale in {'true','1'}
                arguments['q'] = text(query.get('q', [''])[0], '搜索词', 0, 100)
                arguments['category'] = text(query.get('category', [''])[0], '分类', 0, 30)
                arguments['sort'] = query.get('sort', ['recommended'])[0]
                if arguments['sort'] not in {'recommended', 'newest', 'popular', 'price_asc', 'price_desc'}:
                    raise MallError(422, '排序方式不正确')
            if route.operation in {'admin_products', 'admin_orders', 'admin_users'}:
                arguments.update(admin.query_arguments(query, route.operation))
            module = {'shops': shops, 'features': features, 'photo_search': photo_search}.get(route.module)
            handler = getattr(module or (admin if route.operation.startswith('admin_') else logic), route.operation)
            parameters = inspect.signature(handler).parameters
            if route.module:
                if 'store_id' in parameters and 'store_id' not in arguments:
                    raw=query.get('store_id',[str(parameters['store_id'].default) if parameters['store_id'].default is not None else ''])[0]
                    arguments['store_id']=number(int(raw),'店铺编号',1,2**63-1) if raw else None
                if 'q' in parameters:arguments['q']=text(query.get('q',[''])[0],'搜索词',0,100)
                if route.operation=='admin_stores':
                    arguments['status']=query.get('status',['all'])[0]
                for key,default,minimum,maximum in [('page',1,1,2**31-1),('page_size',10,1,50),('after_id',0,0,2**63-1),('before_id',0,0,2**63-1)]:
                    if key in parameters: arguments[key]=number(int(query.get(key,[str(parameters[key].default)])[0]),key,minimum,maximum)
                if 'kind' in parameters:
                    arguments['kind']=query.get('kind',['comments'])[0]
                    if arguments['kind'] not in {'comments','reviews'}: raise MallError(422,'评论类型不正确')
            with self.state.database.connection() as connection:
                if route.admin:
                    arguments['user'] = asyncio.run(admin.current_admin(request, SyncConnectionAdapter(connection)))
                elif route.auth:
                    arguments['user'] = asyncio.run(logic.current_user(request, SyncConnectionAdapter(connection)))
                if 'connection' in parameters:
                    arguments['connection'] = SyncConnectionAdapter(connection, self.state.database.path)
                if 'search' in parameters:
                    arguments['search'] = self.state.search_engine
                if 'request' in parameters:
                    arguments['request'] = request
                if 'response' in parameters:
                    arguments['response'] = response
                if route.operation in CatalogCache.OPERATIONS and method in {'GET', 'HEAD'}:
                    cache_arguments = {key: value for key, value in arguments.items() if key != 'connection'}
                    return self.state.catalog_cache.read(connection, route.operation, cache_arguments,
                        lambda: self.invoke(handler, arguments)), route.status
                return self.invoke(handler, arguments), route.status
        raise MallError(405 if matched_path else 404, '请求方法不支持' if matched_path else '接口不存在')

    @staticmethod
    def invoke(handler, arguments):
        result = handler(**arguments)
        return asyncio.run(result) if inspect.isawaitable(result) else result
