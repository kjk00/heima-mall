"""Shared request models, usable by both the standard-library server and FastAPI."""
import re
import hashlib
import json
from dataclasses import dataclass
from dataclasses import asdict, field


def checkout_fingerprint(payload, store_id):
    values = asdict(payload)
    values['store_id'] = store_id
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def text(value, label, minimum, maximum, pattern=None, strip=True):
    if not isinstance(value, str):
        raise ValueError(f'{label}必须为字符串')
    value = value.strip() if strip else value
    if not minimum <= len(value) <= maximum:
        raise ValueError(f'{label}长度应为 {minimum}–{maximum} 个字符')
    if pattern and not re.fullmatch(pattern, value):
        raise ValueError(f'{label}格式不正确')
    return value


def number(value, label, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'{label}应为 {minimum}–{maximum} 的整数')
    return value


def boolean(value, label):
    if type(value) is not bool:
        raise ValueError(f'{label}必须为布尔值')
    return value


def product_detail_html(value):
    from app.html_sanitize import sanitize_html
    if not isinstance(value, str):
        raise ValueError('详情HTML必须为字符串')
    if len(value) > 20000:
        raise ValueError('详情HTML长度不能超过 20000 个字符')
    return sanitize_html(value)


def product_params(value):
    if not isinstance(value, list):
        raise ValueError('商品参数必须为数组')
    if len(value) > 30:
        raise ValueError('商品参数最多 30 项')
    result = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError('商品参数项必须为对象')
        name = text(item.get('name', ''), '参数名', 1, 40)
        val = text(item.get('value', ''), '参数值', 1, 200)
        result.append({'name': name, 'value': val})
    return result


@dataclass
class LoginInput:
    phone: str
    password: str
    captcha_id: str | None = field(default=None, kw_only=True)
    captcha_code: str | None = field(default=None, kw_only=True)
    __pydantic_config__ = {'extra': 'forbid'}

    def __post_init__(self):
        self.phone = text(self.phone, '手机号', 11, 11, r'1[3-9]\d{9}')
        self.password = text(self.password, '密码', 8, 128, strip=False)
        if self.captcha_id is not None:
            self.captcha_id = text(self.captcha_id, '验证码编号', 16, 80, r'[a-zA-Z0-9_-]+')
        if self.captcha_code is not None:
            self.captcha_code = text(self.captcha_code, '验证码', 4, 8, r'[a-zA-Z0-9]+')


@dataclass
class RegisterInput(LoginInput):
    name: str

    def __post_init__(self):
        super().__post_init__()
        self.name = text(self.name, '昵称', 1, 30)


@dataclass
class CartInput:
    product_id: int
    quantity: int = 1
    __pydantic_config__ = {'extra': 'forbid'}

    def __post_init__(self):
        self.product_id = number(self.product_id, '商品编号', 1, 2**63 - 1)
        self.quantity = number(self.quantity, '数量', 1, 99)


@dataclass
class QuantityInput:
    quantity: int
    __pydantic_config__ = {'extra': 'forbid'}

    def __post_init__(self):
        self.quantity = number(self.quantity, '数量', 1, 99)


@dataclass
class CheckoutInput:
    recipient: str
    phone: str
    address: str
    idempotency_key: str
    coupon_id: int | None = None
    address_id: int | None = None
    save_address: bool = False
    store_id: int | None = None
    __pydantic_config__ = {'extra': 'forbid'}

    def __post_init__(self):
        self.recipient = text(self.recipient, '收货人', 1, 30)
        self.phone = text(self.phone, '手机号', 11, 11, r'1[3-9]\d{9}')
        self.address = text(self.address, '地址', 5, 200)
        self.idempotency_key = text(self.idempotency_key, '幂等键', 16, 80, r'[a-zA-Z0-9_-]+')
        for name in ('coupon_id','address_id','store_id'):
            if getattr(self,name) is not None: number(getattr(self,name), name, 1, 2**63-1)
        self.save_address = boolean(self.save_address, '保存地址')


@dataclass
class AddressInput:
    recipient: str
    phone: str
    address: str
    label: str = ''
    is_default: bool = False
    __pydantic_config__ = {'extra':'forbid'}
    def __post_init__(self):
        self.recipient = text(self.recipient,'收货人',1,30)
        self.phone = text(self.phone,'手机号',11,11,r'1[3-9]\d{9}')
        self.address = text(self.address,'详细地址',5,200)
        self.label = text(self.label,'地址标签',0,20)
        self.is_default = boolean(self.is_default,'默认地址')


@dataclass
class SettingsInput:
    name: str
    notify_support: bool = True
    __pydantic_config__ = {'extra':'forbid'}
    def __post_init__(self):
        self.name = text(self.name,'昵称',1,30)
        self.notify_support = boolean(self.notify_support,'客服消息提醒')


@dataclass
class PasswordInput:
    old_password: str
    new_password: str
    __pydantic_config__ = {'extra':'forbid'}
    def __post_init__(self):
        self.old_password = text(self.old_password,'原密码',8,128,strip=False)
        self.new_password = text(self.new_password,'新密码',8,128,strip=False)
        if self.old_password == self.new_password: raise ValueError('新密码不能与原密码相同')


@dataclass
class ProductReferenceInput:
    product_id: int
    __pydantic_config__ = {'extra':'forbid'}
    def __post_init__(self): number(self.product_id,'商品编号',1,2**63-1)


@dataclass
class CouponInput:
    code: str
    name: str
    kind: str
    value: int
    threshold: int
    max_discount: int
    claim_limit: int
    starts_at: str
    expires_at: str
    is_active: bool = True
    store_id: int | None = None
    __pydantic_config__ = {'extra':'forbid'}
    def __post_init__(self):
        from datetime import datetime
        self.code = text(self.code,'券码',1,30).upper()
        text(self.code,'券码',1,30,r'[A-Z0-9_-]+')
        self.name = text(self.name,'优惠券名称',1,60)
        if self.kind not in {'amount','rate'}: raise ValueError('优惠类型不正确')
        number(self.value,'优惠值',1,99 if self.kind=='rate' else 1_000_000_000)
        number(self.threshold,'使用门槛',0,1_000_000_000)
        number(self.max_discount,'优惠上限',0,1_000_000_000)
        number(self.claim_limit,'领取数量',1,1_000_000)
        for name in ('starts_at','expires_at'):
            raw=text(getattr(self,name),'有效期',20,20,r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z')
            datetime.fromisoformat(raw)
        if self.expires_at<=self.starts_at: raise ValueError('结束时间必须晚于开始时间')
        boolean(self.is_active,'启用状态')
        if self.store_id is not None: number(self.store_id,'所属店铺',1,2**63-1)


@dataclass
class RefundInput:
    reason: str
    idempotency_key: str
    __pydantic_config__ = {'extra':'forbid'}
    def __post_init__(self):
        self.reason=text(self.reason,'退款原因',1,500)
        self.idempotency_key=text(self.idempotency_key,'幂等键',16,80,r'[a-zA-Z0-9_-]+')


@dataclass
class RefundDecisionInput:
    approve: bool
    note: str = ''
    restock: bool = False
    __pydantic_config__ = {'extra':'forbid'}
    def __post_init__(self):
        boolean(self.approve,'审核结果');boolean(self.restock,'退回库存')
        self.note=text(self.note,'审核说明',0,500)
        if not self.approve and not self.note: raise ValueError('拒绝退款时请填写原因')


@dataclass
class TrackingInput:
    stage: str
    note: str = ''
    __pydantic_config__ = {'extra':'forbid'}
    def __post_init__(self):
        if self.stage not in {'packing','transit','delivered'}: raise ValueError('物流阶段不正确')
        self.note=text(self.note,'物流说明',0,200)


@dataclass
class ReviewInput:
    rating: int
    content: str
    __pydantic_config__ = {'extra':'forbid'}
    def __post_init__(self):
        number(self.rating,'评分',1,5);self.content=text(self.content,'评价',1,1000)


@dataclass
class CommentInput:
    content: str
    __pydantic_config__ = {'extra':'forbid'}
    def __post_init__(self): self.content=text(self.content,'评论',1,1000)


@dataclass
class VisibilityInput:
    is_visible: bool
    __pydantic_config__ = {'extra':'forbid'}
    def __post_init__(self): boolean(self.is_visible,'显示状态')


@dataclass
class ChatMessageInput:
    content: str
    client_message_id: str
    store_id: int = 1
    __pydantic_config__ = {'extra':'forbid'}
    def __post_init__(self):
        self.content=text(self.content,'消息',1,2000)
        self.client_message_id=text(self.client_message_id,'消息编号',16,80,r'[a-zA-Z0-9_-]+')
        number(self.store_id,'店铺编号',1,2**63-1)


@dataclass
class ChatReadInput:
    last_message_id: int
    store_id: int = 1
    __pydantic_config__ = {'extra':'forbid'}
    def __post_init__(self):
        number(self.last_message_id,'已读消息编号',0,2**63-1)
        number(self.store_id,'店铺编号',1,2**63-1)


@dataclass
class StoreInput:
    name: str
    description: str = ''
    phone: str = ''
    shipping_fee: int = 1000
    free_shipping_threshold: int = 19900
    is_active: bool = True
    __pydantic_config__ = {'extra':'forbid'}
    def __post_init__(self):
        self.name=text(self.name,'店铺名称',1,60)
        self.description=text(self.description,'店铺介绍',0,500)
        self.phone=text(self.phone,'客服电话',0,30)
        if self.phone:text(self.phone,'客服电话',5,30,r'[0-9+() -]+')
        number(self.shipping_fee,'运费（分）',0,1_000_000_000)
        number(self.free_shipping_threshold,'包邮门槛（分）',0,1_000_000_000)
        boolean(self.is_active,'营业状态')


@dataclass(kw_only=True)
class StoreUpdateInput(StoreInput):
    version: int
    def __post_init__(self):
        super().__post_init__()
        number(self.version,'店铺版本',1,2**31-1)


@dataclass
class AdminProductInput:
    name: str
    subtitle: str
    category: str
    price: int
    original_price: int
    stock: int
    image: str
    badge: str
    color: str
    description: str
    detail_html: str = ''
    params: list = field(default_factory=list)
    is_active: bool = True
    store_id: int | None = None
    __pydantic_config__ = {'extra': 'forbid'}

    def __post_init__(self):
        self.name = text(self.name, '商品名称', 1, 60)
        self.subtitle = text(self.subtitle, '商品短介绍', 0, 100)
        self.category = text(self.category, '分类', 1, 30)
        self.price = number(self.price, '售价（分）', 1, 1_000_000_000)
        self.original_price = number(self.original_price, '划线价（分）', self.price, 1_000_000_000)
        self.stock = number(self.stock, '库存', 0, 1_000_000)
        self.image = text(self.image, '商品图片', 1, 100, r'(?:[a-z0-9_-]+|/media/[a-f0-9]{64}\.(?:png|jpg|webp))')
        self.badge = text(self.badge, '标签', 0, 20)
        self.color = text(self.color, '背景色', 7, 7, r'#[a-fA-F0-9]{6}')
        self.description = text(self.description, '商品详情', 1, 2000)
        self.detail_html = product_detail_html(self.detail_html)
        self.params = product_params(self.params)
        self.is_active = boolean(self.is_active, '上架状态')
        if self.store_id is not None:number(self.store_id,'所属店铺',1,2**63-1)


@dataclass(kw_only=True)
class AdminProductUpdateInput(AdminProductInput):
    version: int

    def __post_init__(self):
        super().__post_init__()
        self.version = number(self.version, '商品版本', 1, 2**31 - 1)


@dataclass
class ProductStatusInput:
    is_active: bool
    version: int
    __pydantic_config__ = {'extra': 'forbid'}

    def __post_init__(self):
        self.is_active = boolean(self.is_active, '上架状态')
        self.version = number(self.version, '商品版本', 1, 2**31 - 1)


@dataclass
class UserStatusInput:
    is_active: bool
    __pydantic_config__ = {'extra': 'forbid'}

    def __post_init__(self):
        self.is_active = boolean(self.is_active, '账号状态')


@dataclass
class ShipmentInput:
    carrier: str
    tracking_no: str
    __pydantic_config__ = {'extra': 'forbid'}

    def __post_init__(self):
        self.carrier = text(self.carrier, '物流公司', 1, 40)
        self.tracking_no = text(self.tracking_no, '物流单号', 4, 60, r'[a-zA-Z0-9-]+')


@dataclass
class ImageUploadInput:
    filename: str
    data_base64: str
    category: str = field(default='', kw_only=True)
    store_id: int | None = field(default=None, kw_only=True)
    __pydantic_config__ = {'extra': 'forbid'}

    def __post_init__(self):
        self.filename = text(self.filename, '图片文件名', 1, 200)
        self.data_base64 = text(self.data_base64, '图片内容', 1, 2_800_000, strip=False)
        self.category = text(self.category, '商品分类', 0, 30)
        if self.store_id is not None:
            self.store_id = number(self.store_id, '店铺编号', 1, 2**63 - 1)

