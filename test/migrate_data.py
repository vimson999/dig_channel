#!/usr/bin/env python3
"""
SQLite 到 PostgreSQL 数据迁移脚本
将现有的 SQLite 数据库中的数据迁移到 PostgreSQL 数据库
"""

import sqlite3
import psycopg2
import os
from datetime import datetime

# SQLite 数据库路径
SQLITE_DB_PATH = 'instance/youtube_tracker.db'

# PostgreSQL 连接参数
PG_CONFIG = {
    'host': '101.35.56.140',
    'port': 5432,
    'database': 'show_ytb_data',
    'user': 'cappa_rw',
    'password': 'RWcappaDb!!!2025'
}

def migrate_table(sqlite_cursor, pg_cursor, table_name, columns, transform_row=None):
    """迁移单个表的数据"""
    print(f"正在迁移 {table_name} 表...")
    
    # 从 SQLite 读取数据
    sqlite_cursor.execute(f"SELECT * FROM {table_name}")
    rows = sqlite_cursor.fetchall()
    
    if not rows:
        print(f"  {table_name} 表为空，跳过")
        return 0
    
    # 获取列信息
    column_names = [desc[0] for desc in sqlite_cursor.description]
    
    # 准备插入语句
    placeholders = ','.join(['%s'] * len(column_names))
    insert_sql = f"INSERT INTO {table_name} ({','.join(column_names)}) VALUES ({placeholders})"
    
    # 转换并插入数据
    migrated_count = 0
    for row in rows:
        try:
            # 应用行转换函数（如果提供）
            if transform_row:
                row = transform_row(row, column_names)
            
            pg_cursor.execute(insert_sql, row)
            migrated_count += 1
        except Exception as e:
            print(f"  插入行失败: {e}")
            print(f"  行数据: {row}")
            # 继续处理下一行
            continue
    
    print(f"  ✅ 成功迁移 {migrated_count}/{len(rows)} 行数据")
    return migrated_count

def transform_user_row(row, columns):
    """转换用户表的行数据"""
    # 确保布尔值正确转换
    row = list(row)
    is_active_idx = columns.index('is_active') if 'is_active' in columns else None
    is_verified_idx = columns.index('is_verified') if 'is_verified' in columns else None
    is_admin_idx = columns.index('is_admin') if 'is_admin' in columns else None
    
    if is_active_idx is not None:
        row[is_active_idx] = bool(row[is_active_idx])
    if is_verified_idx is not None:
        row[is_verified_idx] = bool(row[is_verified_idx])
    if is_admin_idx is not None:
        row[is_admin_idx] = bool(row[is_admin_idx])
        
    return tuple(row)

def transform_channel_row(row, columns):
    """转换频道表的行数据"""
    row = list(row)
    is_active_idx = columns.index('is_active') if 'is_active' in columns else None
    
    if is_active_idx is not None:
        row[is_active_idx] = bool(row[is_active_idx])
        
    return tuple(row)

def transform_video_row(row, columns):
    """转换视频表的行数据"""
    row = list(row)
    is_active_idx = columns.index('is_active') if 'is_active' in columns else None
    
    if is_active_idx is not None:
        row[is_active_idx] = bool(row[is_active_idx])
        
    return tuple(row)

def main():
    try:
        # 连接 SQLite 数据库
        sqlite_conn = sqlite3.connect(SQLITE_DB_PATH)
        sqlite_cursor = sqlite_conn.cursor()
        print("✅ 成功连接 SQLite 数据库")
        
        # 连接 PostgreSQL 数据库
        pg_conn = psycopg2.connect(**PG_CONFIG)
        pg_cursor = pg_conn.cursor()
        print("✅ 成功连接 PostgreSQL 数据库")
        
        # 开始事务
        pg_conn.autocommit = False
        
        total_migrated = 0
        
        # 按依赖顺序迁移表
        migration_tasks = [
            ('users', None, transform_user_row),
            ('channels', None, transform_channel_row),
            ('channel_data', None, None),
            ('videos', None, transform_video_row),
            ('video_data', None, None),
            ('favorite_videos', None, None),
        ]
        
        for table_name, columns, transform_func in migration_tasks:
            try:
                count = migrate_table(sqlite_cursor, pg_cursor, table_name, columns, transform_func)
                total_migrated += count
            except Exception as e:
                print(f"❌ 迁移 {table_name} 表失败: {e}")
                pg_conn.rollback()
                return False
        
        # 提交事务
        pg_conn.commit()
        
        # 更新序列值
        print("\n正在更新序列值...")
        sequence_tables = ['users', 'channels', 'channel_data', 'videos', 'video_data', 'favorite_videos']
        for table in sequence_tables:
            try:
                pg_cursor.execute(f"SELECT setval('{table}_id_seq', (SELECT COALESCE(MAX(id), 1) FROM {table}))")
                print(f"  ✅ 更新 {table}_id_seq 序列")
            except Exception as e:
                print(f"  ⚠️ 更新 {table}_id_seq 序列失败: {e}")
        
        pg_conn.commit()
        
        sqlite_cursor.close()
        sqlite_conn.close()
        pg_cursor.close()
        pg_conn.close()
        
        print(f"\n🎉 数据迁移完成！总计迁移 {total_migrated} 行数据")
        return True
        
    except Exception as e:
        print(f"❌ 数据迁移失败: {e}")
        if 'pg_conn' in locals():
            pg_conn.rollback()
        return False

def verify_migration():
    """验证迁移结果"""
    try:
        # 连接数据库
        sqlite_conn = sqlite3.connect(SQLITE_DB_PATH)
        pg_conn = psycopg2.connect(**PG_CONFIG)
        
        sqlite_cursor = sqlite_conn.cursor()
        pg_cursor = pg_conn.cursor()
        
        tables = ['users', 'channels', 'channel_data', 'videos', 'video_data', 'favorite_videos']
        
        print("\n📊 迁移验证结果:")
        print("表名".ljust(15) + "SQLite".ljust(10) + "PostgreSQL".ljust(12) + "状态")
        print("-" * 45)
        
        all_match = True
        for table in tables:
            # SQLite 计数
            sqlite_cursor.execute(f"SELECT COUNT(*) FROM {table}")
            sqlite_count = sqlite_cursor.fetchone()[0]
            
            # PostgreSQL 计数
            pg_cursor.execute(f"SELECT COUNT(*) FROM {table}")
            pg_count = pg_cursor.fetchone()[0]
            
            status = "✅ 匹配" if sqlite_count == pg_count else "❌ 不匹配"
            if sqlite_count != pg_count:
                all_match = False
            
            print(f"{table.ljust(15)}{str(sqlite_count).ljust(10)}{str(pg_count).ljust(12)}{status}")
        
        sqlite_cursor.close()
        sqlite_conn.close()
        pg_cursor.close()
        pg_conn.close()
        
        return all_match
        
    except Exception as e:
        print(f"验证失败: {e}")
        return False

if __name__ == '__main__':
    print("开始 SQLite 到 PostgreSQL 数据迁移...")
    print("=" * 50)
    
    if main():
        print("\n验证迁移结果...")
        if verify_migration():
            print("\n🎉 数据迁移验证成功！所有数据已正确迁移")
        else:
            print("\n⚠️ 数据迁移验证发现问题，请检查日志")
    else:
        print("\n❌ 数据迁移失败")