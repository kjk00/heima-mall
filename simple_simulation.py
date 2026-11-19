#!/usr/bin/env python3
"""
Simple simulation of mass purchase
"""
import os
import sys
import random
from datetime import datetime
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

# Set environment variables
os.environ['MYSQL_HOST'] = '127.0.0.1'
os.environ['MYSQL_PORT'] = '3306'
os.environ['MYSQL_DATABASE'] = 'heima_mall'
os.environ['MYSQL_USER'] = 'root'
os.environ['MYSQL_PASSWORD'] = '123456'

from app.mysql import MySQLDatabase

def main():
    print("=== 增加商品库存和测试数据 ===\n")

    # Initialize database
    db = MySQLDatabase()
    db.initialize(seed=True)

    # Get database connection
    with db.connection() as conn:
        print("1. 增加热销商品库存...")

        # Select the most popular product (highest sales)
        result = conn.execute("SELECT id, name, price, stock, sales FROM products ORDER BY sales DESC LIMIT 1")
        product = result.fetchone()

        if not product:
            print("❌ 没有找到商品")
            return

        print(f"选中商品: {product['name']}")
        print(f"当前价格: ¥{product['price']/100:.2f}")
        print(f"当前库存: {product['stock']} 件")
        print(f"当前销量: {product['sales']} 件")

        # Increase stock to 10,000
        new_stock = 10000
        increase_sales = 5000  # Increase sales by 5000

        conn.execute("""
            UPDATE products
            SET stock = ?,
                sales = sales + ?,
                version = version + 1
            WHERE id = ?
        """, (new_stock, increase_sales, product['id']))

        # Verify update
        result = conn.execute("SELECT id, name, price, stock, sales FROM products WHERE id = ?s", (product['id'],))
        updated = result.fetchone()

        print(f"\n✓ 库存更新成功:")
        print(f"  - 新库存: {updated['stock']} 件 (+{new_stock - product['stock']})")
        print(f"  - 新销量: {updated['sales']} 件 (+{increase_sales})")

        print("\n2. 创建测试用户...")

        # Create 10,000 users
        batch_size = 1000
        total_users = 10000

        for batch in range(0, total_users, batch_size):
            users_to_insert = []
            start_id = batch + 1
            end_id = min(batch + batch_size, total_users)

            for i in range(start_id, end_id + 1):
                phone = f"138{random.randint(10000000, 99999999)}"
                users_to_insert.append({
                    'id': i,
                    'phone': phone,
                    'name': f'测试用户{i}',
                    'password_hash': 'test123',
                    'created_at': datetime.now().strftime('?Y-?m-?dT?H:?i:?sZ'),
                    'role': 'customer',
                    'is_active': 1,
                    'store_id': 1
                })

            # Insert users
            conn.executemany("""
                INSERT INTO users (id, phone, name, password_hash, created_at, role, is_active, store_id)
                VALUES (?s, ?s, ?s, ?s, ?s, ?s, ?s, ?s)
                ON DUPLICATE KEY UPDATE name=name
            """, [
                (u['id'], u['phone'], u['name'], u['password_hash'],
                 u['created_at'], u['role'], u['is_active'], u['store_id'])
                for u in users_to_insert
            ])

            print(f"  已创建 {len(users_to_insert)} 个用户 ({start_id}-{end_id})")

        print(f"\n✓ 总共创建 {total_users} 个用户")

        print("\n3. 创建收货地址...")

        # Create addresses for users
        for batch in range(0, total_users, batch_size):
            addresses_to_insert = []
            start_id = batch + 1
            end_id = min(batch + batch_size, total_users)

            for i in range(start_id, end_id + 1):
                addresses_to_insert.append({
                    'user_id': i,
                    'recipient': f'用户{i}',
                    'phone': f"138{random.randint(10000000, 99999999)}",
                    'address': f'测试市测试区测试街道{i}号',
                    'label': '默认地址',
                    'is_default': 1,
                    'created_at': datetime.now().strftime('?Y-?m-?dT?H:?i:?sZ')
                })

            # Insert addresses
            conn.executemany("""
                INSERT INTO addresses (user_id, recipient, phone, address, label, is_default, created_at)
                VALUES (?s, ?s, ?s, ?s, ?s, ?s, ?s)
            """, [
                (a['user_id'], a['recipient'], a['phone'], a['address'],
                 a['label'], a['is_default'], a['created_at'])
                for a in addresses_to_insert
            ])

        print(f"✓ 总共创建 {total_users} 个地址")

        print("\n4. 将商品加入购物车...")

        # Add product to carts
        for batch in range(0, total_users, batch_size):
            cart_items_to_insert = []
            start_id = batch + 1
            end_id = min(batch + batch_size, total_users)

            for i in range(start_id, end_id + 1):
                cart_items_to_insert.append({
                    'user_id': i,
                    'product_id': product['id'],
                    'quantity': 1
                })

            # Insert cart items
            conn.executemany("""
                INSERT IGNORE INTO cart_items (user_id, product_id, quantity)
                VALUES (?s, ?s, ?s)
            """, [
                (c['user_id'], c['product_id'], c['quantity'])
                for c in cart_items_to_insert
            ])

        print(f"✓ 总共添加 {total_users} 个购物车项")

        # Final check
        print("\n" + "="*50)
        print("📊 数据准备完成")
        print("="*50)

        # Check product
        result = conn.execute("SELECT id, name, stock, sales FROM products WHERE id = ?s", (product['id'],))
        final_product = result.fetchone()
        print(f"商品: {final_product['name']}")
        print(f"库存: {final_product['stock']} 件")
        print(f"销量: {final_product['sales']} 件")

        # Check users
        result = conn.execute("SELECT COUNT(*) as count FROM users")
        user_count = result.fetchone()['count']
        print(f"用户: {user_count} 个")

        # Check addresses
        result = conn.execute("SELECT COUNT(*) as count FROM addresses")
        address_count = result.fetchone()['count']
        print(f"地址: {address_count} 个")

        # Check cart items
        result = conn.execute("SELECT COUNT(*) as count FROM cart_items")
        cart_count = result.fetchone()['count']
        print(f"购物车: {cart_count} 项")

        print(f"\n🎉 准备完成！可以开始测试1万人购买同一个商品了。")

        return {
            'product_id': product['id'],
            'product_name': product['name'],
            'current_stock': final_product['stock'],
            'total_users': user_count
        }

if __name__ == '__main__':
    main()