#!/usr/bin/env python3
"""
Simulate 10,000 people buying the same product
"""
import os
import sys
import json
import random
from datetime import datetime, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

# Add project root to Python path
project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

# Set environment variables
os.environ['MYSQL_HOST'] = '127.0.0.1'
os.environ['MYSQL_PORT'] = '3306'
os.environ['MYSQL_DATABASE'] = 'heima_mall'
os.environ['MYSQL_USER'] = 'root'
os.environ['MYSQL_PASSWORD'] = '123456'
os.environ['REDIS_URL'] = 'redis://127.0.0.1:6379/0'

from app.mysql import MySQLDatabase
from app.cache import CatalogCache

def main():
    print("=== 模拟1万人购买同一个商品 ===\n")

    # Initialize database
    db = MySQLDatabase()
    db.initialize(seed=True)

    # Get database connection
    with db.connection() as conn:
        print("1. 检查当前商品库存...")

        # Select a popular product (the first one)
        result = conn.execute("SELECT id, name, price, stock FROM products WHERE is_active = 1 LIMIT 1")
        product = result.fetchone()

        if not product:
            print("❌ 没有找到活跃的商品")
            return

        print(f"✓ 选中商品: {product['name']} (ID: {product['id']})")
        print(f"✓ 当前价格: ¥{product['price']/100:.2f}")
        print(f"✓ 当前库存: {product['stock']} 件")

        # Increase stock significantly
        target_stock = 100000  # 10万件库存
        print(f"\n2. 增加商品库存到 {target_stock} 件...")

        conn.execute("""
            UPDATE products
            SET stock = %s,
                original_price = %s,
                sales = sales + 10000,
                version = version + 1
            WHERE id = %s
        """, (target_stock, product['price'], product['id']))

        # Get updated product
        result = conn.execute("SELECT id, name, price, stock, sales FROM products WHERE id = %s", (product['id'],))
        updated_product = result.fetchone()

        print(f"✓ 新库存: {updated_product['stock']} 件")
        print(f"✓ 新销量: {updated_product['sales']} 件")

        # Create 10,000 users
        print(f"\n3. 创建 10,000 个测试用户...")

        # Generate phone numbers
        def generate_phone(index):
            return f"138{random.randint(10000000, 99999999)}"

        # Generate users
        users = []
        for i in range(10000):
            phone = generate_phone(i)
            users.append({
                'phone': phone,
                'name': f'用户{i+1}',
                'password_hash': 'hash_for_test',  # In real app, this would be hashed
                'role': 'customer',
                'is_active': 1,
                'notify_support': 1,
                'store_id': 1
            })

        # Insert users in batches
        batch_size = 100
        for i in range(0, len(users), batch_size):
            batch = users[i:i+batch_size]
            conn.executemany("""
                INSERT IGNORE INTO users
                (phone, name, password_hash, created_at, role, is_active, notify_support, store_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, [
                (u['phone'], u['name'], u['password_hash'],
                 datetime.now().strftime('%Y-%m-%dT%H:%i:%sZ'),
                 u['role'], u['is_active'], u['notify_support'], u['store_id'])
                for u in batch
            ])

        print(f"✓ 已创建 {len(users)} 个用户")

        # Create addresses for users
        print(f"\n4. 为用户创建收货地址...")

        addresses = []
        for i, user in enumerate(users):
            addresses.append({
                'user_id': i + 1,  # Assuming sequential IDs
                'recipient': user['name'],
                'phone': user['phone'],
                'address': f'测试地址{i+1}号',
                'label': '默认地址',
                'is_default': 1
            })

        # Insert addresses in batches
        for i in range(0, len(addresses), batch_size):
            batch = addresses[i:i+batch_size]
            conn.executemany("""
                INSERT IGNORE INTO addresses
                (user_id, recipient, phone, address, label, is_default, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, [
                (a['user_id'], a['recipient'], a['phone'], a['address'],
                 a['label'], a['is_default'], datetime.now().strftime('%Y-%m-%dT%H:%i:%sZ'))
                for a in batch
            ])

        print(f"✓ 已创建 {len(addresses)} 个地址")

        # Add product to users' carts
        print(f"\n5. 将商品添加到所有用户的购物车...")

        cart_items = []
        for i, user in enumerate(users):
            cart_items.append({
                'user_id': i + 1,
                'product_id': product['id'],
                'quantity': 1  # Each user buys 1
            })

        # Insert cart items in batches
        for i in range(0, len(cart_items), batch_size):
            batch = cart_items[i:i+batch_size]
            conn.executemany("""
                INSERT IGNORE INTO cart_items
                (user_id, product_id, quantity)
                VALUES (%s, %s, %s)
            """, [
                (c['user_id'], c['product_id'], c['quantity'])
                for c in batch
            ])

        print(f"✓ 已添加 {len(cart_items)} 个购物车项")

        # Summary
        print(f"\n=== 数据准备完成 ===")
        print(f"✓ 商品: {updated_product['name']}")
        print(f"✓ 库存: {updated_product['stock']} 件")
        print(f"✓ 销量: {updated_product['sales']} 件")
        print(f"✓ 用户: {len(users)} 个")
        print(f"✓ 购物车: {len(cart_items)} 项")

        return {
            'product_id': product['id'],
            'product_name': updated_product['name'],
            'total_users': len(users),
            'total_orders': len(users)  # Each user will create one order
        }

def simulate_concurrent_purchases():
    """Simulate concurrent purchases"""
    print("\n=== 模拟并发购买 ===\n")

    # Initialize database
    db = MySQLDatabase()
    db.initialize(seed=True)

    # Get database connection
    with db.connection() as conn:
        # Get product
        result = conn.execute("SELECT id, name, stock FROM products WHERE is_active = 1 LIMIT 1")
        product = result.fetchone()

        if not product:
            print("❌ 没有找到活跃的商品")
            return

        print(f"开始模拟购买商品: {product['name']} (当前库存: {product['stock']})")

        # Initialize catalog cache
        cache = CatalogCache.from_environment(Path('/tmp'))

        # Simulate 10,000 concurrent purchases
        def purchase(user_id, product_id):
            """Simulate a single purchase"""
            try:
                with db.connection() as conn:
                    # Check stock (this would be done with proper transaction in real app)
                    result = conn.execute("SELECT stock FROM products WHERE id = %s FOR UPDATE", (product_id,))
                    stock_info = result.fetchone()

                    if stock_info and stock_info['stock'] > 0:
                        # Create order
                        order_id = f"ORD{user_id}{int(time.time())}{random.randint(1000, 9999)}"

                        # Get user info
                        user_result = conn.execute("SELECT name, phone FROM users WHERE id = %s", (user_id,))
                        user = user_result.fetchone()

                        # Create order
                        conn.execute("""
                            INSERT INTO orders
                            (id, user_id, status, subtotal, shipping_fee, total, recipient, phone, address,
                             idempotency_key, created_at, fulfillment_status, store_id, store_name)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """, (
                            order_id, user_id, 'paid', product['price'] - 100, 100, product['price'],
                            user['name'], user['phone'], f'测试地址{user_id}',
                            f"idemp_{user_id}", datetime.now().strftime('%Y-%m-%dT%H:%i:%sZ'),
                            'unfulfilled', 1, '黑马自营店'
                        ))

                        # Create order item
                        conn.execute("""
                            INSERT INTO order_items
                            (order_id, product_id, name, image, price, quantity)
                            VALUES (%s, %s, %s, %s, %s, %s)
                        """, (
                            order_id, product_id, product['name'], '/static/product.jpg',
                            product['price'] - 100, 1
                        ))

                        # Update stock
                        conn.execute("""
                            UPDATE products
                            SET stock = stock - 1, sales = sales + 1, version = version + 1
                            WHERE id = %s
                        """, (product_id,))

                        return True, order_id
                    else:
                        return False, "Out of stock"

            except Exception as e:
                return False, str(e)

        # Track results
        success_count = 0
        failure_count = 0
        order_ids = []

        # Simulate purchases in batches to avoid overwhelming the database
        batch_size = 100
        total_batches = 100  # 100 * 100 = 10,000 purchases

        for batch_num in range(total_batches):
            print(f"\n处理批次 {batch_num + 1}/{total_batches}...")

            with ThreadPoolExecutor(max_workers=50) as executor:
                # Create tasks for this batch
                start_user_id = batch_num * batch_size + 1
                tasks = []

                for i in range(batch_size):
                    user_id = start_user_id + i
                    if user_id <= 10000:
                        task = executor.submit(purchase, user_id, product['id'])
                        tasks.append(task)

                # Process results
                for future in as_completed(tasks):
                    success, result = future.result()
                    if success:
                        if isinstance(result, str):
                            order_ids.append(result)
                        success_count += 1
                    else:
                        failure_count += 1

                # Small delay between batches
                if batch_num < total_batches - 1:
                    time.sleep(0.1)  # 100ms between batches

        # Final stock check
        result = conn.execute("SELECT stock, sales FROM products WHERE id = %s", (product['id'],))
        final_info = result.fetchone()

        print(f"\n=== 并发购买模拟完成 ===")
        print(f"✓ 成功订单: {success_count}")
        print(f"✓ 失败订单: {failure_count}")
        print(f"✓ 最终库存: {final_info['stock']} 件")
        print(f"✓ 总销量: {final_info['sales']} 件")

        return {
            'success_orders': success_count,
            'failed_orders': failure_count,
            'final_stock': final_info['stock'],
            'final_sales': final_info['sales']
        }

def main_full_simulation():
    """Run full simulation with data preparation and concurrent purchases"""
    print("🚀 开始完整模拟测试：1万人购买同一个商品\n")

    # Phase 1: Prepare data
    data_prep = main()
    if not data_prep:
        return

    # Phase 2: Simulate concurrent purchases
    purchase_results = simulate_concurrent_purchases()

    # Final summary
    print("\n" + "="*50)
    print("📊 模拟测试完整结果")
    print("="*50)
    print(f"1. 数据准备:")
    print(f"   - 商品: {data_prep['product_name']}")
    print(f"   - 初始用户: {data_prep['total_users']} 人")
    print(f"2. 购买模拟:")
    print(f"   - 成功订单: {purchase_results['success_orders']} 单")
    print(f"   - 失败订单: {purchase_results['failed_orders']} 单")
    print(f"   - 最终库存: {purchase_results['final_stock']} 件")
    print(f"   - 总销量: {purchase_results['final_sales']} 件")
    print("\n🎉 模拟测试完成！")

if __name__ == '__main__':
    main_full_simulation()