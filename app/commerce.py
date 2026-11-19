"""Async ORM shopping transactions. Lock the buyer and affected product rows."""
import secrets
import time

from sqlalchemy import select
from app import logic, features
from app.errors import MallError
from app.order_expiration import EXPIRED_MESSAGE, expire_locked_order
from app.models import User, Product, CartItem, Store, Order, OrderItem, Coupon, UserCoupon
from app.schemas import AddressInput, checkout_fingerprint


class Commerce:
    OPERATIONS = frozenset({'add_cart','update_cart','remove_cart','checkout','pay','cancel','receive'})

    @staticmethod
    async def buyer(session, user_id):
        user = await session.scalar(select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True))
        if not user or not user.is_active:
            raise MallError(403, '账号已停用，无法继续操作')
        return user

    @staticmethod
    async def owned_order(session, order_id, user_id):
        order = await session.scalar(select(Order).where(Order.id == order_id, Order.user_id == user_id)
            .with_for_update().execution_options(populate_existing=True))
        if not order:
            raise MallError(404, '订单不存在')
        return order

    @staticmethod
    async def active_store(session, store_id):
        store = await session.get(Store, store_id, populate_existing=True)
        if not store:
            raise MallError(404, '店铺不存在')
        if not store.is_active:
            raise MallError(409, '店铺已暂停营业')
        return store

    async def add_cart(self, payload, user, connection):
        async with connection.atomic() as session:
            await self.buyer(session, user['id'])
            product = await session.scalar(select(Product).where(Product.id == payload.product_id).with_for_update().execution_options(populate_existing=True))
            if not product:
                raise MallError(404, '商品不存在')
            if not product.is_active:
                raise MallError(409, '商品已下架')
            await self.active_store(session, product.store_id)
            item = await session.get(CartItem, (user['id'], product.id), populate_existing=True)
            quantity = (item.quantity if item else 0) + payload.quantity
            if quantity > min(product.stock, 99):
                raise MallError(409, '库存不足，或购物车数量已达到上限')
            if item:
                item.quantity = quantity
            else:
                session.add(CartItem(user_id=user['id'], product_id=product.id, quantity=quantity))
            await session.flush()
            return await logic.cart_data(connection, user['id'])

    async def update_cart(self, product_id, payload, user, connection):
        async with connection.atomic() as session:
            await self.buyer(session, user['id'])
            item = await session.get(CartItem, (user['id'], product_id), populate_existing=True)
            product = await session.scalar(select(Product).where(Product.id == product_id).with_for_update().execution_options(populate_existing=True))
            if not item or not product:
                raise MallError(404, '购物车中没有该商品')
            if not product.is_active:
                raise MallError(409, '商品已下架，请从购物车移除')
            await self.active_store(session, product.store_id)
            if payload.quantity > product.stock:
                raise MallError(409, '商品库存不足')
            item.quantity = payload.quantity
            await session.flush()
            return await logic.cart_data(connection, user['id'])

    async def remove_cart(self, product_id, user, connection):
        async with connection.atomic() as session:
            await self.buyer(session, user['id'])
            item = await session.get(CartItem, (user['id'], product_id), populate_existing=True)
            if item:
                await session.delete(item)
                await session.flush()
            return await logic.cart_data(connection, user['id'])

    async def checkout(self, payload, user, connection):
        async with connection.atomic() as session:
            await self.buyer(session, user['id'])
            existing = await session.scalar(select(Order).where(Order.user_id == user['id'], Order.idempotency_key == payload.idempotency_key)
                .with_for_update().execution_options(populate_existing=True))
            if existing:
                store_id = payload.store_id or existing.store_id
                if store_id != existing.store_id or existing.request_hash and existing.request_hash != checkout_fingerprint(payload, store_id):
                    raise MallError(409, '幂等键已用于不同的下单请求')
                return await logic.order_data(connection, existing.id, user['id'])
            items = (await session.scalars(select(CartItem).where(CartItem.user_id == user['id'])
                .order_by(CartItem.product_id).with_for_update().execution_options(populate_existing=True))).all()
            if not items:
                raise MallError(400, '购物车为空，请先选购商品')
            # Acquire every product lock before any write/flush can advance the
            # shared catalog revision. Do not rely on a JOIN's query-plan order.
            products = await connection.lock_products(item.product_id for item in items)
            store_ids = {product.store_id for product in products.values()}
            if payload.store_id is None and len(store_ids) > 1:
                raise MallError(409, '购物车包含多家店铺，请选择店铺分别结算')
            store_id = payload.store_id or next(iter(store_ids))
            rows = [(item,products[item.product_id]) for item in items if products[item.product_id].store_id == store_id]
            if not rows:
                raise MallError(400, '所选店铺的购物车为空')
            store = await self.active_store(session, store_id)
            for item, product in rows:
                if not product.is_active:
                    raise MallError(409, f'{product.name} 已下架')
                if item.quantity > product.stock:
                    raise MallError(409, f'{product.name} 库存不足')
            subtotal = sum(product.price * item.quantity for item, product in rows)
            shipping = 0 if subtotal >= store.free_shipping_threshold else store.shipping_fee
            coupon = None
            if payload.coupon_id:
                await session.scalar(select(Coupon).where(Coupon.id == payload.coupon_id).with_for_update().execution_options(populate_existing=True))
                coupon = await session.scalar(select(UserCoupon).where(UserCoupon.user_id == user['id'], UserCoupon.coupon_id == payload.coupon_id)
                    .with_for_update().execution_options(populate_existing=True))
            discount = await features.coupon_discount(payload.coupon_id, subtotal, user, connection, store_id)
            address = await features.address_row(payload.address_id, user, connection) if payload.address_id else {
                'recipient':payload.recipient,'phone':payload.phone,'address':payload.address}
            order_id = 'HM' + time.strftime('%Y%m%d') + secrets.token_hex(6).upper()
            order = Order(id=order_id, user_id=user['id'], subtotal=subtotal, shipping_fee=shipping,
                total=subtotal+shipping-discount, discount=discount, coupon_id=payload.coupon_id,
                store_id=store_id, store_name=store.name, recipient=address['recipient'],
                phone=address['phone'], address=address['address'], idempotency_key=payload.idempotency_key,
                request_hash=checkout_fingerprint(payload, store_id))
            session.add(order)
            await session.flush()
            if coupon:
                coupon.used_order_id = order_id
            for item, product in rows:
                session.add(OrderItem(order_id=order_id, product_id=product.id, name=product.name,
                    image=product.image, price=product.price, quantity=item.quantity))
                product.stock -= item.quantity
                product.version += 1
                await session.delete(item)
            await session.flush()
            if payload.save_address and not payload.address_id and not (await connection.execute(
                'SELECT 1 FROM addresses WHERE user_id=? AND recipient=? AND phone=? AND address=?',
                (user['id'],payload.recipient,payload.phone,payload.address))).fetchone():
                await features.save_address(AddressInput(payload.recipient,payload.phone,payload.address),user,connection)
            await features.tracking_event(connection, order_id, 'awaiting', '订单已创建，等待支付')
            return await logic.order_data(connection, order_id, user['id'])

    async def pay(self, order_id, user, connection):
        async with connection.atomic() as session:
            await self.buyer(session, user['id'])
            order = await self.owned_order(session, order_id, user['id'])
            expired = await expire_locked_order(connection, {'id':order.id, 'status':order.status, 'created_at':order.created_at})
            if await features.refund_open(order_id, connection):
                raise MallError(409, '订单正在退款或已退款')
            if order.status == 'cancelled':
                saved = await logic.order_data(connection, order_id, user['id'])
                raise MallError(409, EXPIRED_MESSAGE if saved['is_expired'] else '订单已取消，无法支付')
            if not expired and order.status == 'pending':
                await self.active_store(session, order.store_id)
                items = (await session.scalars(select(OrderItem).where(OrderItem.order_id == order_id).order_by(OrderItem.product_id))).all()
                products = await connection.lock_products(item.product_id for item in items)
                expired = await expire_locked_order(connection, {'id':order.id, 'status':order.status, 'created_at':order.created_at})
                if not expired:
                    for item in items:
                        product = products[item.product_id]
                        product.sales += item.quantity
                        product.version += 1
                    order.status = 'paid'
                    await session.flush()
                    await features.tracking_event(connection, order_id, 'awaiting', '支付成功，等待店铺处理')
            result = await logic.order_data(connection, order_id, user['id'])
        if expired:
            raise MallError(409, EXPIRED_MESSAGE)
        return result | {'message':'演示支付成功，未发生真实扣款'}

    async def cancel(self, order_id, user, connection):
        async with connection.atomic() as session:
            await self.buyer(session, user['id'])
            order = await self.owned_order(session, order_id, user['id'])
            if order.status == 'paid':
                raise MallError(409, '已支付订单暂不支持取消')
            expired = await expire_locked_order(connection, {'id':order.id, 'status':order.status, 'created_at':order.created_at})
            if not expired and order.status == 'pending':
                items = (await session.scalars(select(OrderItem).where(OrderItem.order_id == order_id).order_by(OrderItem.product_id))).all()
                products = await connection.lock_products(item.product_id for item in items)
                for item in items:
                    product = products[item.product_id]
                    product.stock += item.quantity
                    product.version += 1
                order.status = 'cancelled'
                await session.flush()
                await connection.execute('UPDATE user_coupons SET used_order_id=NULL WHERE used_order_id=?', (order_id,))
            return await logic.order_data(connection, order_id, user['id'])

    async def receive(self, order_id, user, connection):
        async with connection.atomic() as session:
            await self.buyer(session, user['id'])
            order = await self.owned_order(session, order_id, user['id'])
            if await features.refund_open(order_id, connection):
                raise MallError(409, '退款中的订单无法确认收货')
            if order.status != 'paid' or order.fulfillment_status not in {'shipped','completed'}:
                raise MallError(409, '订单尚未发货，无法确认收货')
            if order.fulfillment_status == 'shipped':
                order.fulfillment_status = 'completed'
                order.delivery_stage = 'delivered'
                order.completed_at = features.now()
                await session.flush()
                await features.tracking_event(connection, order_id, 'delivered', '用户已确认收货')
            return await logic.order_data(connection, order_id, user['id'])
