#!/usr/bin/env python3
"""
详细检查8月14日到8月24日间的数据分布
"""

import sqlite3
import psycopg2
from datetime import datetime, date, timedelta

def get_all_dates_with_counts(cursor, is_postgresql=False):
    """获取所有日期及其记录数"""
    if is_postgresql:
        cursor.execute('SELECT date, COUNT(*) FROM channel_data GROUP BY date ORDER BY date')
    else:
        cursor.execute('SELECT date, COUNT(*) FROM channel_data GROUP BY date ORDER BY date')
    
    return {row[0] if is_postgresql else row[0]: row[1] for row in cursor.fetchall()}

def check_date_range():
    print("详细检查8月14日到8月24日的数据分布...")
    print("=" * 60)
    
    try:
        # SQLite连接
        sqlite_conn = sqlite3.connect('instance/youtube_tracker.db')
        sqlite_cursor = sqlite_conn.cursor()
        
        # PostgreSQL连接
        pg_conn = psycopg2.connect(
            host='101.35.56.140', 
            port=5432, 
            database='show_ytb_data', 
            user='cappa_rw', 
            password='RWcappaDb!!!2025'
        )
        pg_cursor = pg_conn.cursor()
        
        # 获取所有日期数据
        sqlite_dates = get_all_dates_with_counts(sqlite_cursor, False)
        pg_dates = get_all_dates_with_counts(pg_cursor, True)
        
        print("SQLite中的所有日期及记录数:")
        for date_str, count in sorted(sqlite_dates.items()):
            print(f"  {date_str}: {count} 条记录")
        
        print(f"\nSQLite总计: {len(sqlite_dates)} 个不同日期")
        
        print("\nPostgreSQL中的所有日期及记录数:")
        for date_obj, count in sorted(pg_dates.items()):
            print(f"  {date_obj}: {count} 条记录")
        
        print(f"\nPostgreSQL总计: {len(pg_dates)} 个不同日期")
        
        # 转换PostgreSQL日期为字符串进行比较
        pg_dates_str = {str(k): v for k, v in pg_dates.items()}
        
        # 找出差异
        sqlite_only = set(sqlite_dates.keys()) - set(pg_dates_str.keys())
        pg_only = set(pg_dates_str.keys()) - set(sqlite_dates.keys())
        
        if sqlite_only:
            print(f"\n❌ SQLite独有的日期 ({len(sqlite_only)} 个):")
            for date_str in sorted(sqlite_only):
                print(f"  {date_str}: {sqlite_dates[date_str]} 条记录")
        
        if pg_only:
            print(f"\n⚠️ PostgreSQL独有的日期 ({len(pg_only)} 个):")
            for date_str in sorted(pg_only):
                print(f"  {date_str}: {pg_dates_str[date_str]} 条记录")
        
        # 重点检查8月14日到8月25日
        print(f"\n🔍 重点检查8月14日到8月25日的数据:")
        start_date = date(2025, 8, 14)
        end_date = date(2025, 8, 25)
        current_date = start_date
        
        missing_dates = []
        while current_date <= end_date:
            date_str = str(current_date)
            sqlite_count = sqlite_dates.get(date_str, 0)
            pg_count = pg_dates_str.get(date_str, 0)
            
            status = "✅" if sqlite_count == pg_count and sqlite_count > 0 else "❌"
            if sqlite_count == 0 and pg_count == 0:
                status = "⚪"  # 两边都没有数据
            elif sqlite_count > 0 and pg_count == 0:
                status = "❌ 缺失"
                missing_dates.append(date_str)
            
            print(f"  {date_str}: SQLite={sqlite_count}, PostgreSQL={pg_count} {status}")
            current_date += timedelta(days=1)
        
        if missing_dates:
            print(f"\n⚠️ 需要迁移的日期: {missing_dates}")
            return missing_dates
        else:
            print(f"\n✅ 8月14-25日数据完整")
            return []
        
        sqlite_conn.close()
        pg_conn.close()
        
    except Exception as e:
        print(f"❌ 检查失败: {e}")
        return []

def migrate_missing_dates(missing_dates):
    """迁移缺失日期的数据"""
    if not missing_dates:
        print("没有需要迁移的数据")
        return
    
    print(f"\n开始迁移 {len(missing_dates)} 个日期的数据...")
    
    try:
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
        
        total_migrated = 0
        
        for date_str in sorted(missing_dates):
            print(f"\n迁移 {date_str} 的数据...")
            
            # 获取该日期的数据
            sqlite_cursor.execute('SELECT * FROM channel_data WHERE date = ? ORDER BY id', (date_str,))
            records = sqlite_cursor.fetchall()
            
            if not records:
                print(f"  跳过 - {date_str} 没有数据")
                continue
            
            print(f"  找到 {len(records)} 条记录")
            
            # 插入到PostgreSQL
            insert_sql = '''
                INSERT INTO channel_data 
                (id, channel_id, date, subscribers, total_views, video_count, 
                 subscriber_change, view_change, video_change, estimated_revenue, created_at) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
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
        pg_cursor.execute("SELECT setval('channel_data_id_seq', (SELECT COALESCE(MAX(id), 1) FROM channel_data))")
        pg_conn.commit()
        
        print(f"\n🎉 迁移完成！总计迁移 {total_migrated} 条记录")
        
        sqlite_conn.close()
        pg_conn.close()
        
    except Exception as e:
        print(f"❌ 迁移失败: {e}")

if __name__ == '__main__':
    missing = check_date_range()
    if missing:
        migrate_missing_dates(missing)