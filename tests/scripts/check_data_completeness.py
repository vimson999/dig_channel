#!/usr/bin/env python3
"""
检查SQLite和PostgreSQL数据完整性
"""

import sqlite3
import psycopg2
from datetime import datetime

def check_sqlite():
    print('=== SQLite 数据检查 ===')
    try:
        conn = sqlite3.connect('instance/youtube_tracker.db')
        cursor = conn.cursor()
        
        cursor.execute('SELECT COUNT(*) FROM channel_data')
        count = cursor.fetchone()[0]
        print(f'总记录数: {count}')
        
        cursor.execute('SELECT MIN(date), MAX(date) FROM channel_data')
        date_range = cursor.fetchone()
        print(f'日期范围: {date_range[0]} 到 {date_range[1]}')
        
        cursor.execute('SELECT COUNT(DISTINCT date) FROM channel_data')
        unique_dates = cursor.fetchone()[0]
        print(f'不同日期数: {unique_dates}')
        
        cursor.execute('SELECT date, COUNT(*) FROM channel_data GROUP BY date ORDER BY date DESC LIMIT 10')
        recent_data = cursor.fetchall()
        print('最近10天数据:')
        for date, count in recent_data:
            print(f'  {date}: {count} 条记录')
        
        conn.close()
        return True
    except Exception as e:
        print(f'SQLite查询失败: {e}')
        return False

def check_postgresql():
    print('\n=== PostgreSQL 数据检查 ===')
    try:
        conn = psycopg2.connect(
            host='101.35.56.140', 
            port=5432, 
            database='show_ytb_data', 
            user='cappa_rw', 
            password='RWcappaDb!!!2025'
        )
        cursor = conn.cursor()
        
        cursor.execute('SELECT COUNT(*) FROM channel_data')
        count = cursor.fetchone()[0]
        print(f'总记录数: {count}')
        
        cursor.execute('SELECT MIN(date), MAX(date) FROM channel_data')
        date_range = cursor.fetchone()
        print(f'日期范围: {date_range[0]} 到 {date_range[1]}')
        
        cursor.execute('SELECT COUNT(DISTINCT date) FROM channel_data')
        unique_dates = cursor.fetchone()[0]
        print(f'不同日期数: {unique_dates}')
        
        cursor.execute('SELECT date, COUNT(*) FROM channel_data GROUP BY date ORDER BY date DESC LIMIT 10')
        recent_data = cursor.fetchall()
        print('最近10天数据:')
        for date, count in recent_data:
            print(f'  {date}: {count} 条记录')
        
        conn.close()
        return True
    except Exception as e:
        print(f'PostgreSQL查询失败: {e}')
        return False

def find_missing_dates():
    print('\n=== 查找缺失的数据 ===')
    try:
        # SQLite数据
        sqlite_conn = sqlite3.connect('instance/youtube_tracker.db')
        sqlite_cursor = sqlite_conn.cursor()
        sqlite_cursor.execute('SELECT DISTINCT date FROM channel_data ORDER BY date')
        sqlite_dates = {row[0] for row in sqlite_cursor.fetchall()}
        
        # PostgreSQL数据
        pg_conn = psycopg2.connect(
            host='101.35.56.140', 
            port=5432, 
            database='show_ytb_data', 
            user='cappa_rw', 
            password='RWcappaDb!!!2025'
        )
        pg_cursor = pg_conn.cursor()
        pg_cursor.execute('SELECT DISTINCT date FROM channel_data ORDER BY date')
        pg_dates = {str(row[0]) for row in pg_cursor.fetchall()}
        
        # 找出缺失的日期
        missing_dates = sqlite_dates - pg_dates
        
        if missing_dates:
            print(f'PostgreSQL缺失 {len(missing_dates)} 个日期的数据:')
            for date in sorted(missing_dates)[-10:]:  # 显示最近10个缺失日期
                print(f'  {date}')
                
            # 检查这些缺失日期有多少条记录
            for date in sorted(missing_dates)[-5:]:  # 检查最近5个
                sqlite_cursor.execute('SELECT COUNT(*) FROM channel_data WHERE date = ?', (date,))
                count = sqlite_cursor.fetchone()[0]
                print(f'    {date}: {count} 条记录待迁移')
        else:
            print('✅ 没有发现缺失的日期')
        
        sqlite_conn.close()
        pg_conn.close()
        
        return missing_dates
        
    except Exception as e:
        print(f'查找缺失数据失败: {e}')
        return set()

if __name__ == '__main__':
    print("检查数据完整性...")
    print("=" * 50)
    
    sqlite_ok = check_sqlite()
    pg_ok = check_postgresql()
    
    if sqlite_ok and pg_ok:
        missing = find_missing_dates()
        if missing:
            print(f'\n⚠️ 发现 {len(missing)} 个日期的数据需要补充迁移')
        else:
            print('\n✅ 数据迁移完整')