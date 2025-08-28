#!/usr/bin/env python3
"""
补充迁移缺失的channel_data数据
"""

import sqlite3
import psycopg2
from datetime import datetime

def sync_missing_data():
    print("开始补充迁移缺失的channel_data数据...")
    
    try:
        # 连接数据库
        sqlite_conn = sqlite3.connect('instance/youtube_tracker.db')
        sqlite_cursor = sqlite_conn.cursor()
        
        pg_conn = psycopg2.connect(
            host='101.35.56.140', 
            port=5432, 
            database='show_ytb_data', 
            user='cappa_rw', 
            password='RWcappaDb!!!2025'
        )
        pg_cursor = pg_conn.cursor()
        
        print("✅ 数据库连接成功")
        
        # 获取PostgreSQL中缺失的日期
        sqlite_cursor.execute('SELECT DISTINCT date FROM channel_data ORDER BY date')
        sqlite_dates = {row[0] for row in sqlite_cursor.fetchall()}
        
        pg_cursor.execute('SELECT DISTINCT date FROM channel_data ORDER BY date')
        pg_dates = {str(row[0]) for row in pg_cursor.fetchall()}
        
        missing_dates = sqlite_dates - pg_dates
        
        if not missing_dates:
            print("✅ 没有缺失的数据需要迁移")
            return True
        
        print(f"发现 {len(missing_dates)} 个日期的缺失数据: {sorted(missing_dates)}")
        
        # 获取缺失日期的所有数据
        total_migrated = 0
        for date in sorted(missing_dates):
            print(f"\\n迁移 {date} 的数据...")
            
            # 从SQLite获取该日期的数据
            sqlite_cursor.execute('SELECT * FROM channel_data WHERE date = ? ORDER BY id', (date,))
            records = sqlite_cursor.fetchall()
            
            if not records:
                print(f"  跳过 - {date} 没有数据")
                continue
            
            print(f"  找到 {len(records)} 条记录")
            
            # 插入到PostgreSQL
            insert_sql = '''
                INSERT INTO channel_data 
                (id, channel_id, date, subscribers, total_views, video_count, 
                 subscriber_change, view_change, video_change, estimated_revenue, created_at) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            '''
            
            success_count = 0
            for record in records:
                try:
                    pg_cursor.execute(insert_sql, record)
                    success_count += 1
                except Exception as e:
                    print(f"    插入失败 ID={record[0]}: {e}")
            
            pg_conn.commit()
            print(f"  ✅ 成功插入 {success_count}/{len(records)} 条记录")
            total_migrated += success_count
        
        # 更新序列
        print("\\n更新序列...")
        pg_cursor.execute("SELECT setval('channel_data_id_seq', (SELECT COALESCE(MAX(id), 1) FROM channel_data))")
        pg_conn.commit()
        print("✅ 序列已更新")
        
        sqlite_conn.close()
        pg_conn.close()
        
        print(f"\\n🎉 补充迁移完成！总计迁移 {total_migrated} 条记录")
        return True
        
    except Exception as e:
        print(f"❌ 补充迁移失败: {e}")
        return False

def verify_sync():
    """验证同步结果"""
    print("\\n验证同步结果...")
    
    try:
        # SQLite数据
        sqlite_conn = sqlite3.connect('instance/youtube_tracker.db')
        sqlite_cursor = sqlite_conn.cursor()
        sqlite_cursor.execute('SELECT COUNT(*) FROM channel_data')
        sqlite_count = sqlite_cursor.fetchone()[0]
        sqlite_cursor.execute('SELECT MAX(date) FROM channel_data')
        sqlite_max_date = sqlite_cursor.fetchone()[0]
        
        # PostgreSQL数据
        pg_conn = psycopg2.connect(
            host='101.35.56.140', 
            port=5432, 
            database='show_ytb_data', 
            user='cappa_rw', 
            password='RWcappaDb!!!2025'
        )
        pg_cursor = pg_conn.cursor()
        pg_cursor.execute('SELECT COUNT(*) FROM channel_data')
        pg_count = pg_cursor.fetchone()[0]
        pg_cursor.execute('SELECT MAX(date) FROM channel_data')
        pg_max_date = pg_cursor.fetchone()[0]
        
        print("\\n📊 同步后数据对比:")
        print(f"SQLite:     {sqlite_count} 条记录，最新日期: {sqlite_max_date}")
        print(f"PostgreSQL: {pg_count} 条记录，最新日期: {pg_max_date}")
        
        if sqlite_count == pg_count and str(sqlite_max_date) == str(pg_max_date):
            print("✅ 数据完全同步！")
            return True
        else:
            print("⚠️ 数据仍有差异")
            return False
        
        sqlite_conn.close()
        pg_conn.close()
        
    except Exception as e:
        print(f"验证失败: {e}")
        return False

if __name__ == '__main__':
    print("=" * 50)
    if sync_missing_data():
        verify_sync()
    print("=" * 50)