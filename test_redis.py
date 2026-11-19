#!/usr/bin/env python3
"""
Test Redis connection and basic operations
"""
import os
import sys
import json
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

def test_redis_connection():
    try:
        import redis
        print("✓ Redis Python module imported successfully")
    except ImportError as e:
        print(f"✗ Failed to import Redis module: {e}")
        return False

    # Test connection with default settings
    try:
        r = redis.Redis(host='127.0.0.1', port=6379, decode_responses=True)
        # Test connection
        r.ping()
        print("✓ Redis connection successful!")
        return r
    except redis.ConnectionError as e:
        print(f"✗ Redis connection failed: {e}")
        return None

def test_redis_operations(r):
    """Test basic Redis operations"""
    if not r:
        return False

    try:
        # Set and get a simple value
        test_key = "test_key"
        test_value = "Hello, Redis!"

        r.set(test_key, test_value)
        retrieved_value = r.get(test_key)

        if retrieved_value == test_value:
            print("✓ SET/GET operation successful")
        else:
            print(f"✗ SET/GET failed: expected {test_value}, got {retrieved_value}")
            return False

        # Test data types
        # Hash
        hash_key = "user:1"
        r.hset(hash_key, "name", "张三")
        r.hset(hash_key, "email", "zhangsan@example.com")
        r.hset(hash_key, "age", 25)

        user_data = r.hgetall(hash_key)
        print(f"✓ Hash operation: {user_data}")

        # List
        list_key = "shopping_cart:1"
        r.rpush(list_key, "product:1", "product:2", "product:3")
        cart_items = r.lrange(list_key, 0, -1)
        print(f"✓ List operation: {cart_items}")

        # Set
        set_key = "tags:product:1"
        r.sadd(set_key, "electronics", "audio", "wireless")
        tags = r.smembers(set_key)
        print(f"✓ Set operation: {tags}")

        # Test JSON operations if available
        try:
            json_key = "product:1"
            product_data = {
                "id": 1,
                "name": "示例商品",
                "price": 39900,
                "stock": 100,
                "category": "数码科技"
            }
            r.set(json_key, json.dumps(product_data))
            retrieved_json = r.get(json_key)
            parsed_data = json.loads(retrieved_json)
            print(f"✓ JSON operation: {parsed_data['name']}")
        except Exception as e:
            print(f"⚠ JSON operation failed: {e}")

        # Test performance
        import time
        start_time = time.time()
        for i in range(1000):
            r.set(f"perf:test:{i}", f"value_{i}")
        end_time = time.time()

        ops_per_second = 1000 / (end_time - start_time)
        print(f"✓ Performance: {ops_per_second:.2f} ops/sec")

        # Clean up test data
        r.delete(test_key, hash_key, list_key, set_key, json_key)
        for i in range(1000):
            r.delete(f"perf:test:{i}")

        print("✓ Test data cleaned up")
        return True

    except Exception as e:
        print(f"✗ Redis operations failed: {e}")
        return False

def test_redis_pool():
    """Test Redis connection pool"""
    try:
        import redis

        # Create connection pool
        pool = redis.ConnectionPool(host='127.0.0.1', port=6379, decode_responses=True)

        # Get connection from pool
        r = redis.Redis(connection_pool=pool)
        r.ping()

        print("✓ Redis connection pool successful")

        # Clean up
        pool.disconnect()

        return True

    except Exception as e:
        print(f"✗ Redis connection pool failed: {e}")
        return False

def main():
    print("=== Redis Connection Test ===\n")

    # Test basic connection
    r = test_redis_connection()
    if not r:
        print("\n❌ Redis tests failed")
        return False

    print()

    # Test operations
    if not test_redis_operations(r):
        print("\n❌ Redis operations failed")
        return False

    print()

    # Test connection pool
    if not test_redis_pool():
        print("\n❌ Redis connection pool failed")
        return False

    print("\n🎉 All Redis tests passed!")
    return True

if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)