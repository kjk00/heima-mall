from app import admin, logic, features,shops
import asyncio
import inspect
import re
import os
import secrets
import sqlite3
import threading
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit
from fastapi import Depends, FastAPI, Query, Request, Response, Path as APIPath
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from app.async_database import AsyncDatabase as Database, AsyncConnection
from app.async_cache import AsyncCatalogCache as CatalogCache
from app.commerce import Commerce
from app.order_expiration import expire_orders_once, run_order_expiration
from app.config import load_environment
from app.errors import MallError
from app.media import media_file, media_bytes
from app.storage import ObjectStorage
from app.routes import openapi_document, ROUTES
from app.request_guard import RequestGuard
from app.search import SearchEngine, sync_enabled
from app.schemas import (AdminProductInput, AdminProductUpdateInput, CartInput, CheckoutInput,
                         ImageUploadInput, LoginInput, ProductStatusInput, QuantityInput,
                         RegisterInput, ShipmentInput, UserStatusInput)
from app.security import hash_password
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
ROOT = Path(__file__).resolve().parent.parent
COOKIE = 'heima_session'
ProductId = Annotated[int, APIPath(ge=1, le=2**63 - 1)]
UserId = Annotated[int, APIPath(ge=1, le=2**63 - 1)]

async def get_connection(request: Request):
    async with request.app.state.database.connection() as connection:
        yield connection
Connection = Annotated[AsyncConnection, Depends(get_connection)]

async def current_user(request: Request, connection: Connection):
    return (await logic.current_user(request, connection))
User = Annotated[dict, Depends(current_user)]


async def current_admin(request: Request, connection: Connection):
    return (await admin.current_admin(request, connection))


Admin = Annotated[dict, Depends(current_admin)]


def admin_query(request: Request, operation: str):
    return admin.query_arguments({key: request.query_params.getlist(key) for key in request.query_params}, operation)

def create_app(database_path: str | Path | None=None):
    if database_path is None:
        load_environment()
    database = Database(database_path)
    catalog_cache = CatalogCache.from_environment(database.path)
    search_engine = SearchEngine.from_environment(database.path)
    commerce = Commerce()
    concurrency = int(os.getenv('MAX_IN_FLIGHT_REQUESTS','128'))
    queue_wait = float(os.getenv('REQUEST_QUEUE_TIMEOUT_SECONDS','3'))
    if not 1 <= concurrency <= 4096 or not 0 < queue_wait <= 60:
        raise ValueError('请求并发或排队超时配置无效')
    admission = asyncio.Semaphore(concurrency)

    @asynccontextmanager
    async def lifespan(application):
        expiration_task = None
        sync_task = None
        try:
            await database.initialize()
            await expire_orders_once(database)
            expiration_task = asyncio.create_task(run_order_expiration(database))
            application.state.order_expiration_task = expiration_task
            if sync_enabled():
                from app.search_sync import run_search_sync
                sync_task = asyncio.create_task(run_search_sync(database))
            yield
        finally:
            if sync_task is not None:
                sync_task.cancel()
                with suppress(asyncio.CancelledError):
                    await sync_task
            if expiration_task is not None:
                expiration_task.cancel()
                with suppress(asyncio.CancelledError):
                    await expiration_task
            try:
                (await catalog_cache.close())
            finally:
                try:
                    if search_engine is not None:
                        await search_engine.close()
                finally:
                    await database.close()
    application = FastAPI(title='黑马商城 API', version='2.0.0', lifespan=lifespan, docs_url=None, redoc_url=None, description='商品、用户、购物车和订单接口。金额单位：人民币分。支付仅用于演示。')
    application.openapi = openapi_document
    application.add_middleware(RequestGuard)
    application.state.database = database
    application.state.commerce = commerce
    application.state.catalog_cache = catalog_cache
    application.state.search_engine = search_engine
    application.state.media_root = database.path.parent / 'uploads'
    application.state.storage = ObjectStorage.from_environment(application.state.media_root)
    templates = Jinja2Templates(directory=ROOT / 'app' / 'templates')
    application.mount('/static', StaticFiles(directory=ROOT / 'app' / 'static'), name='static')

    @application.exception_handler(MallError)
    async def business_error(request: Request, error: MallError):
        return JSONResponse({'detail': error.detail}, status_code=error.status_code)

    @application.exception_handler(SQLAlchemyError)
    @application.exception_handler(sqlite3.DatabaseError)
    async def database_error(request: Request, error):
        conflict = isinstance(error, (IntegrityError, sqlite3.IntegrityError))
        return JSONResponse({'detail':'数据发生冲突，请刷新后重试' if conflict else '数据库暂时繁忙，请稍后重试'},status_code=409 if conflict else 503)

    @application.get('/docs', include_in_schema=False)
    async def api_docs(request: Request):
        return templates.TemplateResponse(request=request, name='docs.html')

    @application.middleware('http')
    async def protect_requests(request: Request, call_next):
        def secure(response):
            response.headers['X-Content-Type-Options'] = 'nosniff'
            response.headers['X-Frame-Options'] = 'DENY'
            response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
            response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
            if request.url.path.startswith('/api/'):
                response.headers['Cache-Control'] = 'no-store'
            return response
        if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'} and request.url.path.startswith('/api/'):
            origin = request.headers.get('origin')
            if request.headers.get('x-requested-with') != 'HeimaMall':
                return secure(JSONResponse({'detail': '请求缺少 X-Requested-With: HeimaMall 标头'}, status_code=403))
            try:
                same_origin = not origin or (urlsplit(origin).scheme, urlsplit(origin).netloc) == (request.url.scheme, request.url.netloc)
            except ValueError:
                same_origin = False
            if not same_origin:
                return secure(JSONResponse({'detail': '不允许跨站提交请求'}, status_code=403))
        acquired = False
        if request.url.path.startswith('/api/'):
            try:
                async with asyncio.timeout(queue_wait):
                    await admission.acquire()
                acquired = True
            except TimeoutError:
                return secure(JSONResponse({'detail':'请求过于繁忙，请稍后重试'},status_code=503,headers={'Retry-After':'1'}))
        try:
            response = await call_next(request)
        finally:
            if acquired:
                admission.release()
        return secure(response)

    @application.get('/', include_in_schema=False)
    async def storefront(request: Request):
        return templates.TemplateResponse(request=request, name='index.html')

    @application.get('/products/{product_id}', include_in_schema=False)
    async def product_page(product_id: ProductId,request: Request):
        return templates.TemplateResponse(request=request,name='index.html')

    @application.get('/stores/{store_id}', include_in_schema=False)
    async def store_page(store_id: ProductId,request: Request):
        return templates.TemplateResponse(request=request,name='index.html')

    @application.get('/admin', include_in_schema=False)
    @application.get('/admin/', include_in_schema=False)
    async def admin_page(request: Request):
        return templates.TemplateResponse(request=request, name='admin.html')

    @application.get('/media/{filename}', include_in_schema=False)
    async def uploaded_media(filename: str):
        content, content_type = await asyncio.to_thread(media_bytes, application.state.storage, filename)
        return Response(content=content, media_type=content_type,
                        headers={'Cache-Control': 'public, max-age=31536000, immutable'})

    @application.get('/api/health', tags=['系统'])
    async def health(connection: Connection):
        return (await logic.health(connection=connection))

    @application.get('/api/categories', tags=['商品'])
    async def categories(connection: Connection):
        return (await catalog_cache.read(connection, 'categories', {}, lambda: logic.categories(connection=connection)))

    @application.get('/api/products', tags=['商品'])
    async def products(connection: Connection, q: str=Query('', max_length=100), category: str=Query('', max_length=30), sort: str=Query('recommended', pattern='^(recommended|newest|popular|price_asc|price_desc)$'), page: int=Query(1, ge=1, le=2147483647), page_size: int=Query(12, ge=1, le=50), on_sale: bool=False,store_id: int | None=Query(None,ge=1,le=2**63-1)):
        arguments = dict(q=q, category=category, sort=sort, page=page, page_size=page_size, on_sale=on_sale, store_id=store_id)
        return (await catalog_cache.read(connection, 'products', arguments, lambda: logic.products(connection=connection, search=search_engine, **arguments)))

    @application.get('/api/products/{product_id}', tags=['商品'])
    async def product(product_id: ProductId, connection: Connection):
        return (await catalog_cache.read(connection, 'product', {'product_id': product_id}, lambda: logic.product(product_id=product_id, connection=connection)))

    @application.post('/api/auth/register', status_code=201, tags=['用户'])
    async def register(payload: RegisterInput, response: Response, connection: Connection):
        return (await logic.register(payload=payload, response=response, connection=connection))

    @application.get('/api/auth/captcha', tags=['用户'])
    async def login_captcha(request: Request, connection: Connection):
        return (await logic.login_captcha(request=request, connection=connection))

    @application.post('/api/auth/login', tags=['用户'])
    async def login(payload: LoginInput, response: Response, request: Request, connection: Connection):
        return (await logic.login(payload=payload, response=response, request=request, connection=connection))

    @application.get('/api/auth/me', tags=['用户'])
    async def me(user: User):
        return logic.me(user=user)

    @application.post('/api/auth/logout', tags=['用户'])
    async def logout(request: Request, response: Response, connection: Connection):
        return (await logic.logout(request=request, response=response, connection=connection))

    @application.get('/api/cart', tags=['购物车'])
    async def get_cart(user: User, connection: Connection):
        return (await logic.get_cart(user=user, connection=connection))

    @application.post('/api/cart', tags=['购物车'])
    async def add_cart(payload: CartInput, user: User, connection: Connection):
        return (await commerce.add_cart(payload=payload, user=user, connection=connection))

    @application.patch('/api/cart/{product_id}', tags=['购物车'])
    async def update_cart(product_id: ProductId, payload: QuantityInput, user: User, connection: Connection):
        return (await commerce.update_cart(product_id=product_id, payload=payload, user=user, connection=connection))

    @application.delete('/api/cart/{product_id}', tags=['购物车'])
    async def remove_cart(product_id: ProductId, user: User, connection: Connection):
        return (await commerce.remove_cart(product_id=product_id, user=user, connection=connection))

    @application.post('/api/orders', status_code=201, tags=['订单'])
    async def checkout(payload: CheckoutInput, user: User, connection: Connection):
        return (await commerce.checkout(payload=payload, user=user, connection=connection))

    @application.get('/api/orders', tags=['订单'])
    async def orders(user: User, connection: Connection, page: int=Query(1, ge=1, le=2147483647), page_size: int=Query(10, ge=1, le=50)):
        return (await logic.orders(user=user, connection=connection, page=page, page_size=page_size))

    @application.get('/api/orders/{order_id}', tags=['订单'])
    async def order(order_id: str, user: User, connection: Connection):
        return (await logic.order(order_id=order_id, user=user, connection=connection))

    @application.post('/api/orders/{order_id}/pay', tags=['订单'])
    async def pay(order_id: str, user: User, connection: Connection):
        return (await commerce.pay(order_id=order_id, user=user, connection=connection))

    @application.post('/api/orders/{order_id}/cancel', tags=['订单'])
    async def cancel(order_id: str, user: User, connection: Connection):
        return (await commerce.cancel(order_id=order_id, user=user, connection=connection))

    @application.post('/api/orders/{order_id}/receive', tags=['订单'])
    async def receive(order_id: str, user: User, connection: Connection):
        return (await commerce.receive(order_id, user, connection))

    @application.get('/api/admin/setup', tags=['后台登录'])
    async def admin_setup_status(request: Request, connection: Connection):
        return (await admin.admin_setup_status(request, connection))

    @application.post('/api/admin/setup', status_code=201, tags=['后台登录'])
    async def admin_setup(payload: RegisterInput, response: Response, request: Request, connection: Connection):
        return (await admin.admin_setup(payload, response, request, connection))

    @application.post('/api/admin/auth/login', tags=['后台登录'])
    async def admin_login(payload: LoginInput, response: Response, request: Request, connection: Connection):
        return (await admin.admin_login(payload, response, request, connection))

    @application.get('/api/admin/auth/me', tags=['后台登录'])
    async def admin_me(user: Admin):
        return admin.admin_me(user)

    @application.get('/api/admin/dashboard', tags=['后台管理'])
    async def admin_dashboard(user: Admin, connection: Connection):
        return (await admin.admin_dashboard(user, connection))

    @application.get('/api/admin/options', tags=['后台管理'])
    async def admin_options(user: Admin, connection: Connection):
        return (await admin.admin_options(user, connection))

    @application.get('/api/admin/products', tags=['后台商品'])
    async def admin_products(request: Request, user: Admin, connection: Connection):
        return (await admin.admin_products(user, connection, **admin_query(request, 'admin_products')))

    @application.post('/api/admin/products', status_code=201, tags=['后台商品'])
    async def admin_create_product(payload: AdminProductInput, request: Request, user: Admin, connection: Connection):
        return (await admin.admin_create_product(payload, user, request, connection))

    @application.get('/api/admin/products/{product_id}', tags=['后台商品'])
    async def admin_product(product_id: ProductId, user: Admin, connection: Connection):
        return (await admin.admin_product(product_id, user, connection))

    @application.put('/api/admin/products/{product_id}', tags=['后台商品'])
    async def admin_update_product(product_id: ProductId, payload: AdminProductUpdateInput, request: Request, user: Admin, connection: Connection):
        return (await admin.admin_update_product(product_id, payload, user, request, connection))

    @application.patch('/api/admin/products/{product_id}/status', tags=['后台商品'])
    async def admin_product_status(product_id: ProductId, payload: ProductStatusInput, user: Admin, connection: Connection):
        return (await admin.admin_product_status(product_id, payload, user, connection))

    @application.post('/api/admin/images', status_code=201, tags=['后台商品'])
    async def admin_upload_image(payload: ImageUploadInput, request: Request, user: Admin):
        return await asyncio.to_thread(admin.admin_upload_image, payload, user, request)

    @application.get('/api/admin/orders', tags=['后台订单'])
    async def admin_orders(request: Request, user: Admin, connection: Connection):
        return (await admin.admin_orders(user, connection, **admin_query(request, 'admin_orders')))

    @application.get('/api/admin/orders/{order_id}', tags=['后台订单'])
    async def admin_order(order_id: str, user: Admin, connection: Connection):
        return (await admin.admin_order(order_id, user, connection))

    @application.post('/api/admin/orders/{order_id}/ship', tags=['后台订单'])
    async def admin_ship_order(order_id: str, payload: ShipmentInput, user: Admin, connection: Connection):
        return (await admin.admin_ship_order(order_id, payload, user, connection))

    @application.post('/api/admin/orders/{order_id}/cancel', tags=['后台订单'])
    async def admin_cancel_order(order_id: str, user: Admin, connection: Connection):
        return (await admin.admin_cancel_order(order_id, user, connection))

    @application.get('/api/admin/users', tags=['后台用户'])
    async def admin_users(request: Request, user: Admin, connection: Connection):
        return (await admin.admin_users(user, connection, **admin_query(request, 'admin_users')))

    @application.patch('/api/admin/users/{user_id}/status', tags=['后台用户'])
    async def admin_user_status(user_id: UserId, payload: UserStatusInput, user: Admin, connection: Connection):
        return (await admin.admin_user_status(user_id, payload, user, connection))
    def feature_endpoint(route):
        handler=getattr(shops if route.module=='shops' else features,route.operation)
        path_names=set(re.findall(r'\{(\w+)\}',route.path))
        async def endpoint(**arguments):
            if route.operation in CatalogCache.OPERATIONS:
                cache_arguments = {key: value for key, value in arguments.items() if key != 'connection'}
                return (await catalog_cache.read(arguments['connection'], route.operation, cache_arguments, lambda: handler(**arguments)))
            return (await handler(**arguments))
        parameters=[]
        for name,param in inspect.signature(handler).parameters.items():
            if name.endswith('_id') and name not in path_names and name not in {'after_id','before_id','store_id'}:
                continue
            default=inspect.Parameter.empty
            if name=='payload': annotation=route.model
            elif name=='connection': annotation=Connection
            elif name=='user': annotation=Admin if route.admin else User
            elif name=='response': annotation=Response
            elif name=='request': annotation=Request
            elif name in path_names:
                annotation=str if name=='order_id' else Annotated[int,APIPath(ge=1,le=2**63-1)]
            elif name=='kind':
                annotation=str;default=Query('comments',pattern='^(comments|reviews)$')
            elif name=='q':
                annotation=str;default=Query('',max_length=100)
            elif name=='status':
                annotation=str;default=Query('all',pattern='^(all|active|inactive)$')
            elif name=='store_id':
                annotation=int | None if param.default is None else int
                default=Query(param.default,ge=1,le=2**63-1)
            else:
                annotation=int
                default=Query(param.default,ge=0 if name in {'after_id','before_id'} else 1,le=50 if name=='page_size' else 2**63-1)
            parameters.append(inspect.Parameter(name,inspect.Parameter.KEYWORD_ONLY,default=default,annotation=annotation))
        endpoint.__signature__=inspect.Signature(parameters)
        endpoint.__name__=route.operation
        return endpoint
    for route in ROUTES:
        if route.module:
            application.add_api_route(route.path,feature_endpoint(route),methods=[route.method],status_code=route.status,tags=[route.tag],summary=route.summary)
    application.state.login_attempts = []
    application.state.captcha_attempts = []
    application.state.login_lock = threading.Lock()
    application.state.dummy_password = hash_password(secrets.token_urlsafe(24))
    return application
app = create_app()
