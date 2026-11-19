"""Typed ORM mappings for the existing business schema (amounts in integer cents)."""
from sqlalchemy import BigInteger, Text, ForeignKey, FetchedValue
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass


class CatalogCacheState(Base):
    __tablename__ = 'catalog_cache_state'
    __mapper_args__ = {"eager_defaults": True}
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, nullable=False)
    generation: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger, primary_key=False, nullable=False, server_default=FetchedValue())

class Store(Base):
    __tablename__ = 'stores'
    __mapper_args__ = {"eager_defaults": True}
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False)
    description: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False, server_default=FetchedValue())
    phone: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False, server_default=FetchedValue())
    is_active: Mapped[int] = mapped_column(BigInteger, primary_key=False, nullable=False, server_default=FetchedValue())
    shipping_fee: Mapped[int] = mapped_column(BigInteger, primary_key=False, nullable=False, server_default=FetchedValue())
    free_shipping_threshold: Mapped[int] = mapped_column(BigInteger, primary_key=False, nullable=False, server_default=FetchedValue())
    version: Mapped[int] = mapped_column(BigInteger, primary_key=False, nullable=False, server_default=FetchedValue())
    created_at: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False, server_default=FetchedValue())

class User(Base):
    __tablename__ = 'users'
    __mapper_args__ = {"eager_defaults": True}
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, nullable=False)
    phone: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False)
    name: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False, server_default=FetchedValue())
    role: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False, server_default=FetchedValue())
    is_active: Mapped[int] = mapped_column(BigInteger, primary_key=False, nullable=False, server_default=FetchedValue())
    notify_support: Mapped[int] = mapped_column(BigInteger, primary_key=False, nullable=False, server_default=FetchedValue())
    store_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('stores.id'), primary_key=False, nullable=True)
    is_super_admin: Mapped[int] = mapped_column(BigInteger, primary_key=False, nullable=False, server_default=FetchedValue())

class Session(Base):
    __tablename__ = 'sessions'
    __mapper_args__ = {"eager_defaults": True}
    token_hash: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('users.id'), primary_key=False, nullable=False)
    expires_at: Mapped[int] = mapped_column(BigInteger, primary_key=False, nullable=False)

class Product(Base):
    __tablename__ = 'products'
    __mapper_args__ = {"eager_defaults": True}
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False)
    subtitle: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False)
    category: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False)
    price: Mapped[int] = mapped_column(BigInteger, primary_key=False, nullable=False)
    original_price: Mapped[int] = mapped_column(BigInteger, primary_key=False, nullable=False)
    stock: Mapped[int] = mapped_column(BigInteger, primary_key=False, nullable=False)
    image: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False)
    badge: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False, server_default=FetchedValue())
    color: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False)
    description: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False)
    sales: Mapped[int] = mapped_column(BigInteger, primary_key=False, nullable=False, server_default=FetchedValue())
    is_active: Mapped[int] = mapped_column(BigInteger, primary_key=False, nullable=False, server_default=FetchedValue())
    version: Mapped[int] = mapped_column(BigInteger, primary_key=False, nullable=False, server_default=FetchedValue())
    store_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('stores.id'), primary_key=False, nullable=True)

class CartItem(Base):
    __tablename__ = 'cart_items'
    __mapper_args__ = {"eager_defaults": True}
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('users.id'), primary_key=True, nullable=False)
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('products.id'), primary_key=True, nullable=False)
    quantity: Mapped[int] = mapped_column(BigInteger, primary_key=False, nullable=False)

class Order(Base):
    __tablename__ = 'orders'
    __mapper_args__ = {"eager_defaults": True}
    id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('users.id'), primary_key=False, nullable=False)
    status: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False, server_default=FetchedValue())
    subtotal: Mapped[int] = mapped_column(BigInteger, primary_key=False, nullable=False)
    shipping_fee: Mapped[int] = mapped_column(BigInteger, primary_key=False, nullable=False)
    total: Mapped[int] = mapped_column(BigInteger, primary_key=False, nullable=False)
    recipient: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False)
    phone: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False)
    address: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False, server_default=FetchedValue())
    request_hash: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False, server_default=FetchedValue())
    fulfillment_status: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False, server_default=FetchedValue())
    carrier: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False, server_default=FetchedValue())
    tracking_no: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False, server_default=FetchedValue())
    shipped_at: Mapped[str | None] = mapped_column(Text, primary_key=False, nullable=True)
    completed_at: Mapped[str | None] = mapped_column(Text, primary_key=False, nullable=True)
    delivery_stage: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False, server_default=FetchedValue())
    discount: Mapped[int] = mapped_column(BigInteger, primary_key=False, nullable=False, server_default=FetchedValue())
    coupon_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('coupons.id'), primary_key=False, nullable=True)
    store_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('stores.id'), primary_key=False, nullable=True)
    store_name: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False, server_default=FetchedValue())

class OrderItem(Base):
    __tablename__ = 'order_items'
    __mapper_args__ = {"eager_defaults": True}
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, nullable=False)
    order_id: Mapped[str] = mapped_column(Text, ForeignKey('orders.id'), primary_key=False, nullable=False)
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('products.id'), primary_key=False, nullable=False)
    name: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False)
    image: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False)
    price: Mapped[int] = mapped_column(BigInteger, primary_key=False, nullable=False)
    quantity: Mapped[int] = mapped_column(BigInteger, primary_key=False, nullable=False)

class Address(Base):
    __tablename__ = 'addresses'
    __mapper_args__ = {"eager_defaults": True}
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('users.id'), primary_key=False, nullable=False)
    recipient: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False)
    phone: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False)
    address: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False)
    label: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False, server_default=FetchedValue())
    is_default: Mapped[int] = mapped_column(BigInteger, primary_key=False, nullable=False, server_default=FetchedValue())
    created_at: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False, server_default=FetchedValue())

class Coupon(Base):
    __tablename__ = 'coupons'
    __mapper_args__ = {"eager_defaults": True}
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, nullable=False)
    code: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False)
    name: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False)
    kind: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False)
    value: Mapped[int] = mapped_column(BigInteger, primary_key=False, nullable=False)
    threshold: Mapped[int] = mapped_column(BigInteger, primary_key=False, nullable=False, server_default=FetchedValue())
    max_discount: Mapped[int] = mapped_column(BigInteger, primary_key=False, nullable=False, server_default=FetchedValue())
    claim_limit: Mapped[int] = mapped_column(BigInteger, primary_key=False, nullable=False, server_default=FetchedValue())
    starts_at: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False)
    expires_at: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False)
    is_active: Mapped[int] = mapped_column(BigInteger, primary_key=False, nullable=False, server_default=FetchedValue())
    store_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('stores.id'), primary_key=False, nullable=True)

class UserCoupon(Base):
    __tablename__ = 'user_coupons'
    __mapper_args__ = {"eager_defaults": True}
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('users.id'), primary_key=True, nullable=False)
    coupon_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('coupons.id'), primary_key=True, nullable=False)
    used_order_id: Mapped[str | None] = mapped_column(Text, ForeignKey('orders.id'), primary_key=False, nullable=True)
    claimed_at: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False, server_default=FetchedValue())

class Refund(Base):
    __tablename__ = 'refunds'
    __mapper_args__ = {"eager_defaults": True}
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, nullable=False)
    order_id: Mapped[str] = mapped_column(Text, ForeignKey('orders.id'), primary_key=False, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('users.id'), primary_key=False, nullable=False)
    reason: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, primary_key=False, nullable=False)
    status: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False, server_default=FetchedValue())
    idempotency_key: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False)
    note: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False, server_default=FetchedValue())
    restocked: Mapped[int] = mapped_column(BigInteger, primary_key=False, nullable=False, server_default=FetchedValue())
    processed_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('users.id'), primary_key=False, nullable=True)
    processed_at: Mapped[str | None] = mapped_column(Text, primary_key=False, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False, server_default=FetchedValue())

class TrackingEvent(Base):
    __tablename__ = 'tracking_events'
    __mapper_args__ = {"eager_defaults": True}
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, nullable=False)
    order_id: Mapped[str] = mapped_column(Text, ForeignKey('orders.id'), primary_key=False, nullable=False)
    stage: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False)
    note: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False, server_default=FetchedValue())

class BrowsingHistory(Base):
    __tablename__ = 'browsing_history'
    __mapper_args__ = {"eager_defaults": True}
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('users.id'), primary_key=True, nullable=False)
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('products.id'), primary_key=True, nullable=False)
    viewed_at: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False)
    sort_key: Mapped[int] = mapped_column(BigInteger, primary_key=False, nullable=False)

class Review(Base):
    __tablename__ = 'reviews'
    __mapper_args__ = {"eager_defaults": True}
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('users.id'), primary_key=False, nullable=False)
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('products.id'), primary_key=False, nullable=False)
    order_id: Mapped[str] = mapped_column(Text, ForeignKey('orders.id'), primary_key=False, nullable=False)
    rating: Mapped[int] = mapped_column(BigInteger, primary_key=False, nullable=False)
    content: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False)
    is_visible: Mapped[int] = mapped_column(BigInteger, primary_key=False, nullable=False, server_default=FetchedValue())
    created_at: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False, server_default=FetchedValue())
    updated_at: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False, server_default=FetchedValue())

class Comment(Base):
    __tablename__ = 'comments'
    __mapper_args__ = {"eager_defaults": True}
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('users.id'), primary_key=False, nullable=False)
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('products.id'), primary_key=False, nullable=False)
    content: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False)
    is_visible: Mapped[int] = mapped_column(BigInteger, primary_key=False, nullable=False, server_default=FetchedValue())
    created_at: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False, server_default=FetchedValue())

class Conversation(Base):
    __tablename__ = 'conversations'
    __mapper_args__ = {"eager_defaults": True}
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('users.id'), primary_key=False, nullable=False)
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('stores.id'), primary_key=False, nullable=False)
    customer_read_id: Mapped[int] = mapped_column(BigInteger, primary_key=False, nullable=False, server_default=FetchedValue())
    staff_read_id: Mapped[int] = mapped_column(BigInteger, primary_key=False, nullable=False, server_default=FetchedValue())
    created_at: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False, server_default=FetchedValue())

class SupportMessage(Base):
    __tablename__ = 'support_messages'
    __mapper_args__ = {"eager_defaults": True}
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, nullable=False)
    conversation_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('conversations.id'), primary_key=False, nullable=False)
    sender_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('users.id'), primary_key=False, nullable=False)
    sender_side: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False)
    content: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False)
    client_message_id: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, primary_key=False, nullable=False, server_default=FetchedValue())
