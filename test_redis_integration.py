#!/usr/bin/env python3
"""
Test Redis integration with the project
"""
import os
import sys
import json
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

def test_project_redis():
    """Test Redis as used in the project"""
    print("=== Project Redis Integration Test ===\n")

    # Set environment variables
    os.environ['REDIS_URL'] = 'redis://127.0.0.1:6379/0'
    os.environ['REDIS_CACHE_TTL_SECONDS'] = '30'

    # Test 1: Import and initialize CatalogCache
    try:
        from app.cache import CatalogCache
        print("✓ CatalogCache imported successfully")

        # Create a mock database path
        db_path = Path('/tmp/test_db')
        db_path.mkdir(exist_ok=True)

        # Initialize cache
        cache = CatalogCache.from_environment(db_path)
        print("✓ CatalogCache initialized successfully")

        if cache.backend:
            print("✓ Redis backend connected")
        else:
            print("⚠ Redis backend not available (this is expected if REDIS_URL is not set)")
            return False

    except Exception as e:
        print(f"✗ CatalogCache failed: {e}")
        return False

    # Test 2: Test Redis operations used in the project
    if cache.backend:
        try:
            # Test catalog cache operations
            test_key = f'{cache.namespace}:test:123'
            test_value = {'products': [], 'stores': []}

            # Put cache
            cache.backend.put(test_key, json.dumps({'format': 1, 'value': test_value}), 30)
            print("✓ Cache put operation successful")

            # Get cache
            cached = cache._cached(test_key)
            if cached == test_value:
                print("✓ Cache get operation successful")
            else:
                print("✗ Cache get failed")
                return False

            # Test lock operations
            import secrets
            token = secrets.token_hex(16)
            lock_key = f'{test_key}:lock'

            if cache.backend.acquire(lock_key, token):
                print("✓ Lock acquire successful")

                if cache.backend.release(lock_key, token):
                    print("✓ Lock release successful")
                else:
                    print("✗ Lock release failed")
                    return False
            else:
                print("✗ Lock acquire failed")
                return False

            # Clean up
            cache.backend.client.delete(test_key)
            print("✓ Test data cleaned up")

        except Exception as e:
            print(f"✗ Cache operations failed: {e}")
            return False

    # Test 3: Test Redis with database simulation
    try:
        from app.database import Database
        from app.mysql import MySQLDatabase

        # Set environment for MySQL
        os.environ['MYSQL_HOST'] = '127.0.0.1'
        os.environ['MYSQL_PORT'] = '3306'
        os.environ['MYSQL_DATABASE'] = 'heima_mall'
        os.environ['MYSQL_USER'] = 'root'
        os.environ['MYSQL_PASSWORD'] = '123456'
        os.environ['MYSQL_INITIALIZE_SCHEMA'] = '0'

        # Create database instance
        db = MySQLDatabase()

        # Test connection
        with db.connection() as conn:
            # Test catalog_cache_state
            result = conn.execute('SELECT generation, revision FROM catalog_cache_state WHERE id=1')
            if result.fetchone():
                print("✓ Catalog cache state accessible")
            else:
                print("✗ Catalog cache state not found")
                return False

            # Test catalog cache with real data
            def loader():
                # Simulate loading products
                result = conn.execute('SELECT id, name, category FROM products LIMIT 5')
                return [dict(row) for row in result.fetchall()]

            # Test cache read operation
            if cache.backend:
                cached_result = cache.read(conn, 'products', (), loader)
                if cached_result:
                    print(f"✓ Catalog cache read: {len(cached_result)} products")
                else:
                    print("⚠ Catalog cache returned empty (might be normal)")

    except Exception as e:
        print(f"✗ Database integration test failed: {e}")
        return False

    print("\n🎉 All Redis integration tests passed!")
    return True

def test_performance():
    """Test Redis performance for catalog operations"""
    print("\n=== Redis Performance Test ===\n")

    try:
        import redis
        import time

        # Create test data
        r = redis.Redis(host='127.0.0.1', port=6379, decode_responses=True)

        # Test bulk operations
        test_keys = [f"perf_catalog_{i}" for i in range(1000)]
        test_values = [{"id": i, "name": f"商品{i}", "price": 10000 + i} for i in range(1000)]

        # Pipeline for bulk set
        start_time = time.time()
        pipe = r.pipeline()
        for i in range(1000):
            pipe.setex(test_keys[i], 3600, json.dumps(test_values[i]))
        pipe.execute()
        end_time = time.time()

        ops_per_sec = 1000 / (end_time - start_time)
        print(f"✓ Bulk SETEX: {ops_per_sec:.2f} ops/sec")

        # Bulk get
        start_time = time.time()
        pipe = r.pipeline()
        for key in test_keys[:100]:  # Test 100 gets
            pipe.get(key)
        results = pipe.execute()
        end_time = time.time()

        ops_per_sec = 100 / (end_time - start_time)
        print(f"✓ Bulk GET: {ops_per_sec:.2f} ops/sec")

        # Clean up
        for key in test_keys:
            r.delete(key)
        print("✓ Performance test data cleaned up")

        return True

    except Exception as e:
        print(f"✗ Performance test failed: {e}")
        return False

def main():
    success = True

    # Test integration
    if not test_project_redis():
        success = False

    # Test performance
    if not test_performance():
        success = False

    if success:
        print("\n🎉 All tests completed successfully!")
    else:
        print("\n❌ Some tests failed!")

    return success

if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)