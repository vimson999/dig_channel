#!/usr/bin/env python3
"""
直接执行整个SQL文件的脚本
"""

import psycopg2
import os

# 数据库连接参数
DB_CONFIG = {
    'host': '101.35.56.140',
    'port': 5432,
    'database': 'show_ytb_data',
    'user': 'cappa_rw',
    'password': 'RWcappaDb!!!2025'
}

def main():
    try:
        # 连接数据库
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
        print("成功连接到数据库")
        
        # 读取并执行SQL文件
        sql_file = os.path.join(os.path.dirname(__file__), 'create_tables_fixed.sql')
        with open(sql_file, 'r', encoding='utf-8') as f:
            sql_content = f.read()
        
        print("执行完整的SQL脚本...")
        cursor.execute(sql_content)
        conn.commit()
        
        cursor.close()
        conn.close()
        print("✅ 数据库表创建完成！")
        
    except Exception as e:
        print(f"❌ 数据库初始化失败: {e}")
        return False
    
    # 验证表是否创建成功
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT table_name FROM information_schema.tables 
            WHERE table_schema = 'public' 
            ORDER BY table_name;
        """)
        
        tables = cursor.fetchall()
        table_names = [t[0] for t in tables]
        
        expected_tables = ['users', 'channels', 'channel_data', 'videos', 'video_data', 'favorite_videos']
        
        print("\n📋 数据库表状态:")
        created_count = 0
        for table in expected_tables:
            if table in table_names:
                print(f"✅ {table}")
                created_count += 1
            else:
                print(f"❌ {table} (缺失)")
        
        cursor.close()
        conn.close()
        
        print(f"\n🎉 成功创建 {created_count}/6 个表！")
        return created_count == 6
        
    except Exception as e:
        print(f"验证表结构失败: {e}")
        return False

if __name__ == '__main__':
    main()