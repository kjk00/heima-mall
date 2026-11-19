#!/usr/bin/env python3
"""
Initialize the database with schema and seed data
"""
import os
import sys
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

from app.mysql import MySQLDatabase
from app.seed import PRODUCTS

def main():
    # Set environment variables for MySQL connection
    os.environ['MYSQL_HOST'] = '127.0.0.1'
    os.environ['MYSQL_PORT'] = '3306'
    os.environ['MYSQL_DATABASE'] = 'heima_mall'
    os.environ['MYSQL_USER'] = 'root'
    # Set password from environment variable for security
    os.environ['MYSQL_PASSWORD'] = os.getenv('MYSQL_PASSWORD', '123456')

    # Create database instance
    db = MySQLDatabase()

    print("正在初始化数据库...")

    # Initialize database with schema and seed data
    db.initialize(seed=True)

    print("数据库初始化完成！")
    print("\n已创建的表：")

    # Show all tables
    with db.connection() as conn:
        result = conn.execute("SHOW TABLES")
        tables = [row['Tables_in_heima_mall'] for row in result.fetchall()]
        for table in tables:
            print(f"  - {table}")

        print(f"\n总表数量: {len(tables)}")

        # Show record counts for main tables
        main_tables = ['stores', 'users', 'products', 'coupons']
        for table in main_tables:
            result = conn.execute(f"SELECT COUNT(*) as count FROM {table}")
            count = result.fetchone()['count']
            print(f"  {table}: {count} 条记录")

if __name__ == '__main__':
    main()