#!/usr/bin/env python3
"""
同步缺失的channel_data数据
"""

import sqlite3
import psycopg2

# 数据库配置
SQLITE_DB_PATH = 'instance/youtube_tracker.db'
PG_CONFIG = {
    'host': '101.35.56.140',
    'port': 5432,
    'database': 'show_ytb_data',
    'user': 'cappa_rw',
    'password': 'RWcappaDb!!!2025'
}

def sync_missing_channel_data():
    try:
        # 连接数据库
        sqlite_conn = sqlite3.connect(SQLITE_DB_PATH)
        pg_conn = psycopg2.connect(**PG_CONFIG)
        
        sqlite_cursor = sqlite_conn.cursor()
        pg_cursor = pg_conn.cursor()
        
        # 获取SQLite中的所有channel_data记录
        sqlite_cursor.execute("SELECT * FROM channel_data ORDER BY id")
        sqlite_records = sqlite_cursor.fetchall()
        
        print(f"SQLite中有 {len(sqlite_records)} 条channel_data记录")
        
        # 获取PostgreSQL中已存在的记录IDs
        pg_cursor.execute("SELECT id FROM channel_data")
        existing_ids = {row[0] for row in pg_cursor.fetchall()}
        
        print(f"PostgreSQL中已有 {len(existing_ids)} 条记录")
        
        # 找出缺失的记录
        missing_records = []
        for record in sqlite_records:
            if record[0] not in existing_ids:  # record[0] 是 id 字段
                missing_records.append(record)
        
        print(f"发现 {len(missing_records)} 条缺失记录")
        
        if missing_records:
            # 准备插入语句
            insert_sql = """
                INSERT INTO channel_data 
                (id, channel_id, date, subscribers, total_views, video_count, 
                 subscriber_change, view_change, video_change, estimated_revenue, created_at) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            
            # 插入缺失的记录
            inserted_count = 0
            for record in missing_records:
                try:
                    pg_cursor.execute(insert_sql, record)
                    inserted_count += 1
                except Exception as e:
                    print(f"插入记录失败 ID={record[0]}: {e}")
            
            pg_conn.commit()
            print(f"✅ 成功插入 {inserted_count} 条缺失记录")
        
        # 验证结果
        pg_cursor.execute("SELECT COUNT(*) FROM channel_data")
        final_count = pg_cursor.fetchone()[0]
        print(f"PostgreSQL最终记录数: {final_count}")
        
        # 更新序列值
        pg_cursor.execute("SELECT setval('channel_data_id_seq', (SELECT COALESCE(MAX(id), 1) FROM channel_data))")
        pg_conn.commit()
        print("✅ 序列值已更新")
        
        sqlite_cursor.close()
        sqlite_conn.close()
        pg_cursor.close()
        pg_conn.close()
        
        return True
        
    except Exception as e:
        print(f"❌ 同步失败: {e}")
        return False

if __name__ == '__main__':
    print("开始同步缺失的channel_data数据...")
    sync_missing_channel_data()