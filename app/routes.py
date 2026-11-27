"""Route metadata for the dependency-free HTTP adapter and offline API explorer."""
from dataclasses import MISSING, dataclass, fields
import inspect
import re
from app import schemas
from app.schemas import (AdminProductInput, AdminProductUpdateInput, CartInput, CheckoutInput,
                         ImageUploadInput, LoginInput, ProductStatusInput, QuantityInput,
                         RegisterInput, ShipmentInput, UserStatusInput)


@dataclass(frozen=True)
class Route:
    method: str
    path: str
    operation: str
    summary: str
    tag: str
    model: type | None = None
    auth: bool = False
    status: int = 200
    admin: bool = False
    module: str = ''


ROUTES = [
    Route('GET', '/api/health', 'health', '健康检查', '系统'),
    Route('GET', '/api/categories', 'categories', '商品分类', '商品'),
    Route('GET', '/api/products', 'products', '搜索与筛选商品', '商品'),
    Route('GET', '/api/products/{product_id}', 'product', '商品详情', '商品'),
    Route('POST', '/api/auth/register', 'register', '注册并登录', '用户', RegisterInput, status=201),
    Route('GET', '/api/auth/captcha', 'login_captcha', '获取登录图形验证码', '用户'),
    Route('POST', '/api/auth/login', 'login', '登录', '用户', LoginInput),
    Route('GET', '/api/auth/me', 'me', '当前用户', '用户', auth=True),
    Route('POST', '/api/auth/logout', 'logout', '退出登录', '用户'),
    Route('GET', '/api/cart', 'get_cart', '查看购物车', '购物车', auth=True),
    Route('POST', '/api/cart', 'add_cart', '添加商品到购物车', '购物车', CartInput, True),
    Route('PATCH', '/api/cart/{product_id}', 'update_cart', '修改数量', '购物车', QuantityInput, True),
    Route('DELETE', '/api/cart/{product_id}', 'remove_cart', '移除商品', '购物车', auth=True),
    Route('POST', '/api/orders', 'checkout', '创建订单（幂等）', '订单', CheckoutInput, True, 201),
    Route('GET', '/api/orders', 'orders', '订单列表', '订单', auth=True),
    Route('GET', '/api/orders/{order_id}', 'order', '订单详情', '订单', auth=True),
    Route('POST', '/api/orders/{order_id}/pay', 'pay', '演示支付（不扣款）', '订单', auth=True),
    Route('POST', '/api/orders/{order_id}/cancel', 'cancel', '取消未支付订单', '订单', auth=True),
    Route('POST', '/api/orders/{order_id}/receive', 'receive', '确认收货', '订单', auth=True),
    Route('GET', '/api/admin/setup', 'admin_setup_status', '管理员初始化状态', '后台登录'),
    Route('POST', '/api/admin/setup', 'admin_setup', '首次创建管理员（仅本机）', '后台登录', RegisterInput, status=201),
    Route('POST', '/api/admin/auth/login', 'admin_login', '管理员登录', '后台登录', LoginInput),
    Route('GET', '/api/admin/auth/me', 'admin_me', '当前管理员', '后台登录', admin=True),
    Route('GET', '/api/admin/dashboard', 'admin_dashboard', '经营概览', '后台管理', admin=True),
    Route('GET', '/api/admin/options', 'admin_options', '商品分类与图片素材', '后台管理', admin=True),
    Route('GET', '/api/admin/products', 'admin_products', '管理商品列表', '后台商品', admin=True),
    Route('POST', '/api/admin/products', 'admin_create_product', '新增商品', '后台商品', AdminProductInput, status=201, admin=True),
    Route('GET', '/api/admin/products/{product_id}', 'admin_product', '管理商品详情', '后台商品', admin=True),
    Route('PUT', '/api/admin/products/{product_id}', 'admin_update_product', '编辑商品与库存', '后台商品', AdminProductUpdateInput, admin=True),
    Route('PATCH', '/api/admin/products/{product_id}/status', 'admin_product_status', '商品上下架', '后台商品', ProductStatusInput, admin=True),
    Route('POST', '/api/admin/images', 'admin_upload_image', '上传商品图片（最大 2 MB）', '后台商品', ImageUploadInput, status=201, admin=True),
    Route('GET', '/api/admin/orders', 'admin_orders', '管理订单列表', '后台订单', admin=True),
    Route('GET', '/api/admin/orders/{order_id}', 'admin_order', '管理订单详情', '后台订单', admin=True),
    Route('POST', '/api/admin/orders/{order_id}/ship', 'admin_ship_order', '发货并填写物流', '后台订单', ShipmentInput, admin=True),
    Route('POST', '/api/admin/orders/{order_id}/cancel', 'admin_cancel_order', '关闭未支付订单', '后台订单', admin=True),
    Route('GET', '/api/admin/users', 'admin_users', '管理用户列表', '后台用户', admin=True),
    Route('PATCH', '/api/admin/users/{user_id}/status', 'admin_user_status', '启用或停用用户', '后台用户', UserStatusInput, admin=True),
]

def feature(method, path, operation, summary, model=None, public=False, staff=False):
    return Route(method, '/api'+path, operation, summary, '后台扩展' if staff else '商城扩展',
                 model, not public and not staff, admin=staff, module='features')

ROUTES += [
    Route('GET','/api/stores','stores','店铺列表','店铺',module='shops'),
    Route('GET','/api/stores/{store_id}','store','店铺详情','店铺',module='shops'),
    Route('GET','/api/search/suggestions','search_suggestions','最近浏览与热门商品店铺推荐','搜索',module='shops'),
    Route('GET','/api/search/photo/status','photo_search_status','图片搜索可用状态','搜索',module='photo_search'),
    Route('POST','/api/search/photo','search_photo','根据照片搜索相似在售商品（最大 2 MB）','搜索',ImageUploadInput,module='photo_search'),
    Route('GET','/api/admin/stores','admin_stores','店铺管理列表','后台店铺',admin=True,module='shops'),
    Route('POST','/api/admin/stores','admin_create_store','新增店铺','后台店铺',schemas.StoreInput,status=201,admin=True,module='shops'),
    Route('GET','/api/admin/stores/{store_id}','admin_store','管理店铺详情','后台店铺',admin=True,module='shops'),
    Route('PUT','/api/admin/stores/{store_id}','admin_update_store','编辑店铺','后台店铺',schemas.StoreUpdateInput,admin=True,module='shops'),
    Route('PATCH','/api/admin/stores/{store_id}/status','admin_store_status','店铺营业状态','后台店铺',ProductStatusInput,admin=True,module='shops'),
    feature('GET','/addresses','addresses','我的地址'),
    feature('POST','/addresses','create_address','新增地址',schemas.AddressInput),
    feature('PUT','/addresses/{address_id}','update_address','编辑地址',schemas.AddressInput),
    feature('DELETE','/addresses/{address_id}','delete_address','删除地址'),
    feature('GET','/settings','settings','账号设置'),
    feature('PUT','/settings','update_settings','修改账号设置',schemas.SettingsInput),
    feature('POST','/settings/password','change_password','修改密码并退出其他会话',schemas.PasswordInput),
    feature('GET','/coupons','coupons','优惠券中心与钱包'),
    feature('POST','/coupons/{coupon_id}/claim','claim_coupon','领取优惠券'),
    feature('GET','/history','history','浏览历史'),
    feature('POST','/history','record_history','记录商品浏览',schemas.ProductReferenceInput),
    feature('DELETE','/history','clear_history','清空浏览历史'),
    feature('POST','/orders/{order_id}/refund','request_refund','申请整单演示退款',schemas.RefundInput),
    feature('GET','/products/{product_id}/reviews','product_reviews','公开购买评价',public=True),
    feature('PUT','/products/{product_id}/reviews','write_review','发表或修改购买评价',schemas.ReviewInput),
    feature('GET','/products/{product_id}/comments','product_comments','公共商品评论',public=True),
    feature('POST','/products/{product_id}/comments','write_comment','发布公共评论',schemas.CommentInput),
    feature('GET','/support/messages','support_messages','店铺客服消息'),
    feature('GET','/support/conversations','support_conversations','我咨询过的店铺与未读消息'),
    feature('POST','/support/messages','support_send','发送客服消息',schemas.ChatMessageInput),
    feature('POST','/support/read','support_read','标记已展示的消息为已读',schemas.ChatReadInput),
    feature('GET','/admin/coupons','admin_coupons','优惠券管理',staff=True),
    feature('POST','/admin/coupons','admin_save_coupon','创建优惠券',schemas.CouponInput,staff=True),
    feature('PUT','/admin/coupons/{coupon_id}','admin_save_coupon','编辑优惠券',schemas.CouponInput,staff=True),
    feature('GET','/admin/refunds','admin_refunds','退款审核列表',staff=True),
    feature('POST','/admin/refunds/{refund_id}/decision','admin_decide_refund','审核演示退款',schemas.RefundDecisionInput,staff=True),
    feature('POST','/admin/orders/{order_id}/tracking','admin_tracking','更新配送阶段',schemas.TrackingInput,staff=True),
    feature('GET','/admin/discussions','admin_discussions','评论与评价管理',staff=True),
    feature('PATCH','/admin/discussions/{comment_id}','admin_visibility','显示或隐藏评论',schemas.VisibilityInput,staff=True),
    feature('GET','/admin/support','admin_support','客服会话列表',staff=True),
    feature('GET','/admin/support/{conversation_id}/messages','admin_support_messages','客服会话消息',staff=True),
    feature('POST','/admin/support/{conversation_id}/messages','admin_support_send','客服人工回复',schemas.ChatMessageInput,staff=True),
    feature('POST','/admin/support/{conversation_id}/read','admin_support_read','客服标记已读',schemas.ChatReadInput,staff=True),
]

EXAMPLES = {
    'RegisterInput': {'phone': '13800138000', 'password': 'test123456', 'name': '测试用户'},
    'LoginInput': {'phone': '13800138000', 'password': 'test123456', 'captcha_id': '先调用 GET /api/auth/captcha 获取', 'captcha_code': '图片中的4位字符'},
    'CartInput': {'product_id': 1, 'quantity': 1},
    'QuantityInput': {'quantity': 2},
    'CheckoutInput': {'recipient': '测试用户', 'phone': '13800138000', 'address': '上海市浦东新区测试路 100 号', 'idempotency_key': 'replace-with-unique-key'},
    'AdminProductInput': {'name': '新品马克杯', 'subtitle': '给日常多一点温度', 'category': '家居生活',
                         'price': 6900, 'original_price': 9900, 'stock': 50, 'image': 'mug', 'badge': '新品',
                         'color': '#eae5ed', 'description': '380ml 陶瓷马克杯。', 'is_active': True,'store_id':1},
    'ProductStatusInput': {'is_active': False, 'version': 1},
    'UserStatusInput': {'is_active': False},
    'ShipmentInput': {'carrier': '顺丰速运', 'tracking_no': 'SF1234567890'},
    'ImageUploadInput': {'filename': 'product.png', 'data_base64': '替换为 PNG/JPEG/WebP 文件的 Base64 编码'},
}
EXAMPLES['AdminProductUpdateInput'] = dict(EXAMPLES['AdminProductInput'], version=1)
EXAMPLES.update({
    'StoreInput':{'name':'新店铺','description':'精选好物','phone':'13800138000','shipping_fee':1000,'free_shipping_threshold':19900,'is_active':True},
    'AddressInput': {'recipient':'测试用户','phone':'13800138000','address':'上海市浦东新区测试路100号','label':'家','is_default':True},
    'SettingsInput': {'name':'测试用户','notify_support':True},
    'PasswordInput': {'old_password':'test123456','new_password':'test654321'},
    'ProductReferenceInput': {'product_id':1},
    'CouponInput': {'code':'SPRING10','name':'满99减10','kind':'amount','value':1000,'threshold':9900,'max_discount':0,'claim_limit':200,'starts_at':'2020-01-01T00:00:00Z','expires_at':'2100-01-01T00:00:00Z','is_active':True},
    'RefundInput': {'reason':'不再需要此商品','idempotency_key':'replace-with-unique-key'},
    'RefundDecisionInput': {'approve':True,'note':'同意整单演示退款','restock':False},
    'TrackingInput': {'stage':'packing','note':'正在打包商品'},
    'ReviewInput': {'rating':5,'content':'使用体验很好'},
    'CommentInput': {'content':'想了解这款商品的使用体验'},
    'VisibilityInput': {'is_visible':False},
    'ChatMessageInput': {'content':'你好，想咨询一下商品','client_message_id':'replace-with-unique-key'},
    'ChatReadInput': {'last_message_id':0},
})
EXAMPLES['StoreUpdateInput']=dict(EXAMPLES['StoreInput'],version=1)


def openapi_document():
    document = {'openapi': '3.1.0', 'info': {'title': '黑马商城 API', 'version': '1.0.0',
        'description': '所有金额以人民币分为单位。Cookie 会话认证。写请求必须携带 X-Requested-With: HeimaMall。支付仅用于演示。'},
        'paths': {}, 'components': {'securitySchemes': {'session': {'type': 'apiKey', 'in': 'cookie', 'name': 'heima_session'}}, 'schemas': {}}}
    for route in ROUTES:
        spec = {'summary': route.summary, 'operationId': route.operation, 'tags': [route.tag], 'responses': {str(route.status): {'description': '成功'}}}
        parameters = []
        for name in re.findall(r'\{(\w+)\}',route.path):
            if '{' + name + '}' in route.path:
                parameters.append({'name': name, 'in': 'path', 'required': True, 'schema': {'type': 'string' if name == 'order_id' else 'integer'}})
        if route.operation in {'products', 'orders', 'admin_products', 'admin_orders', 'admin_users'}:
            parameters.extend([{'name': 'page', 'in': 'query', 'schema': {'type': 'integer', 'minimum': 1, 'default': 1}},
                               {'name': 'page_size', 'in': 'query', 'schema': {'type': 'integer', 'minimum': 1, 'maximum': 50, 'default': 12 if route.operation == 'products' else 10}}])
        if route.module:
            from app import features, shops, photo_search
            module = {'shops': shops, 'features': features, 'photo_search': photo_search}[route.module]
            handler_parameters=inspect.signature(getattr(module,route.operation)).parameters
            for key in ('page','page_size'):
                if key in handler_parameters:
                    schema={'type':'integer','minimum':1,'default':handler_parameters[key].default}
                    schema['maximum']=50 if key=='page_size' else 2**31-1
                    parameters.append({'name':key,'in':'query','schema':schema})
            if 'store_id' in handler_parameters and '{store_id}' not in route.path:
                schema={'type':'integer','minimum':1}
                if handler_parameters['store_id'].default is not None:schema['default']=handler_parameters['store_id'].default
                parameters.append({'name':'store_id','in':'query','schema':schema})
            if 'q' in handler_parameters:
                parameters.append({'name':'q','in':'query','schema':{'type':'string','maxLength':100}})
            if route.operation=='admin_stores':
                parameters.append({'name':'status','in':'query','schema':{'type':'string','enum':['all','active','inactive'],'default':'all'}})
        if route.operation in {'products','admin_products','admin_orders'}:
            parameters.append({'name':'store_id','in':'query','schema':{'type':'integer','minimum':1}})
        if route.operation == 'products':
            parameters.append({'name':'on_sale','in':'query','schema':{'type':'boolean','default':False}})
            parameters.extend([{'name': 'q', 'in': 'query', 'schema': {'type': 'string', 'maxLength': 100}},
                               {'name': 'category', 'in': 'query', 'schema': {'type': 'string', 'maxLength': 30}},
                               {'name': 'sort', 'in': 'query', 'schema': {'type': 'string', 'enum': ['recommended', 'newest', 'popular', 'price_asc', 'price_desc'], 'default': 'recommended'}}])
        if route.operation in {'admin_products', 'admin_orders', 'admin_users'}:
            choices = {'admin_products': ['all', 'active', 'inactive', 'low_stock'],
                       'admin_orders': ['all', 'pending', 'paid', 'cancelled', 'unfulfilled', 'shipped', 'completed'],
                       'admin_users': ['all', 'active', 'inactive']}[route.operation]
            parameters.extend([{'name': 'q', 'in': 'query', 'schema': {'type': 'string', 'maxLength': 100}},
                               {'name': 'status', 'in': 'query', 'schema': {'type': 'string', 'enum': choices, 'default': 'all'}}])
            if route.operation == 'admin_products':
                parameters.append({'name': 'category', 'in': 'query', 'schema': {'type': 'string', 'maxLength': 30}})
        if route.method != 'GET':
            parameters.append({'name': 'X-Requested-With', 'in': 'header', 'required': True, 'schema': {'type': 'string', 'const': 'HeimaMall'}})
        if parameters:
            spec['parameters'] = parameters
        if route.auth or route.admin:
            spec['security'] = [{'session': []}]
        if route.model:
            properties, required = {}, []
            for field in fields(route.model):
                properties[field.name] = {'type': ['integer','null'] if field.type == int | None else 'integer' if field.type is int else 'boolean' if field.type is bool else 'string'}
                if field.default is MISSING:
                    required.append(field.name)
                else:
                    properties[field.name]['default'] = field.default
            name = route.model.__name__
            schema = {'type': 'object', 'properties': properties, 'required': required, 'additionalProperties': False}
            document['components']['schemas'][name] = schema
            spec['requestBody'] = {'required': True, 'content': {'application/json': {'schema': {'$ref': f'#/components/schemas/{name}'}, 'example': EXAMPLES.get(name,{})}}}
        if route.operation in {'admin_discussions','admin_visibility'}:
            spec.setdefault('parameters',[]).append({'name':'kind','in':'query','schema':{'type':'string','enum':['comments','reviews'],'default':'comments'}})
        if route.operation in {'support_messages','admin_support_messages'}:
            spec.setdefault('parameters',[]).extend({'name':k,'in':'query','schema':{'type':'integer','minimum':0,'default':0}} for k in ('after_id','before_id'))
        document['paths'].setdefault(route.path, {})[route.method.lower()] = spec
    return document
