#!/usr/bin/env python3
"""
简单的数据库表创建脚本
使用 cappa_rw 用户直接创建表，不依赖 Flask-SQLAlchemy 的权限检查
"""

import psycopg2
import os
import sys

# 数据库连接参数
DB_CONFIG = {
    'host': '101.35.56.140',
    'port': 5432,
    'database': 'show_ytb_data',
    'user': 'cappa_rw',
    'password': 'RWcappaDb!!!2025'
}

def create_tables():
    """直接使用 psycopg2 创建表"""
    try:
        # 连接数据库
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
        print("成功连接到数据库")
        
        # 读取 SQL 文件
        sql_file = os.path.join(os.path.dirname(__file__), 'create_tables_fixed.sql')
        with open(sql_file, 'r', encoding='utf-8') as f:
            sql_content = f.read()
        
        # 分割 SQL 语句并执行
        sql_statements = sql_content.split(';')
        
        print(f"发现 {len(sql_statements)} 个SQL语句")
        
        for i, statement in enumerate(sql_statements):
            statement = statement.strip()
            print(f"语句 {i+1}: {'[空白]' if not statement else ('[注释]' if statement.startswith('--') else statement[:80])}")
            
            if statement and not statement.startswith('--'):
                try:
                    cursor.execute(statement)
                    conn.commit()
                    print("✅ 成功")
                except Exception as e:
                    print(f"❌ 错误: {e}")
                    if "already exists" in str(e):
                        print("  (对象已存在，跳过)")
                        conn.rollback()
                        continue
                    else:
                        conn.rollback()
                        raise
        
        cursor.close()
        conn.close()
        print("\n🎉 数据库表创建完成！")
        return True
        
    except Exception as e:
        print(f"数据库初始化失败: {e}")
        return False

def verify_tables():
    """验证表是否创建成功"""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
        
        # 检查所有表
        cursor.execute("""
            SELECT table_name FROM information_schema.tables 
            WHERE table_schema = 'public' 
            ORDER BY table_name;
        """)
        
        tables = cursor.fetchall()
        table_names = [t[0] for t in tables]
        
        expected_tables = ['users', 'channels', 'channel_data', 'videos', 'video_data', 'favorite_videos']
        
        print("\n📋 数据库表状态:")
        for table in expected_tables:
            if table in table_names:
                print(f"✅ {table}")
            else:
                print(f"❌ {table} (缺失)")
        
        cursor.close()
        conn.close()
        
        return len([t for t in expected_tables if t in table_names])
        
    except Exception as e:
        print(f"验证表结构失败: {e}")
        return 0

if __name__ == '__main__':
    print("开始数据库初始化...")
    
    # 创建表
    if create_tables():
        # 验证表
        created_count = verify_tables()
        if created_count == 6:
            print(f"\n🎉 所有 {created_count} 个表创建成功！")
        else:
            print(f"\n⚠️  只有 {created_count}/6 个表创建成功")
    else:
        print("\n❌ 数据库初始化失败")
        sys.exit(1)