#!/usr/bin/env python3
"""
SQLite到PostgreSQL数据迁移脚本

此脚本负责将现有的SQLite数据库迁移到PostgreSQL数据库：
1. 创建PostgreSQL数据库表结构
2. 从SQLite读取数据并转换为PostgreSQL格式
3. 批量插入数据到PostgreSQL
4. 验证数据迁移完整性
"""

import os
import sys
import sqlite3
import psycopg2
from psycopg2.extras import execute_values
from datetime import datetime, date
import json

# 添加项目路径
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src', 'backend'))

class DatabaseMigrator:
    def __init__(self):
        """初始化数据库迁移器"""
        # 获取SQLite数据库路径
        self.sqlite_db_path = os.path.join(
            os.path.dirname(__file__), '..', 'instance', 'youtube_tracker.db'
        )
        
        # PostgreSQL连接参数
        self.pg_params = {
            'host': os.getenv('POSTGRES_HOST', 'localhost'),
            'port': int(os.getenv('POSTGRES_PORT', 5432)),
            'database': os.getenv('POSTGRES_DB', 'youtube_analytics'),
            'user': os.getenv('POSTGRES_USER', 'postgres'),
            'password': os.getenv('POSTGRES_PASSWORD', '')
        }
        
        print(f"📂 SQLite数据库: {self.sqlite_db_path}")
        print(f"🐘 PostgreSQL: {self.pg_params['user']}@{self.pg_params['host']}:{self.pg_params['port']}/{self.pg_params['database']}")
    
    def check_sqlite_exists(self):
        """检查SQLite数据库是否存在"""
        if not os.path.exists(self.sqlite_db_path):
            print(f"❌ SQLite数据库文件不存在: {self.sqlite_db_path}")
            return False
        
        print(f"✅ 找到SQLite数据库文件")
        return True
    
    def create_postgresql_tables(self):
        """在PostgreSQL中创建表结构"""
        print("\n🔧 创建PostgreSQL表结构...")
        
        # 设置Flask应用环境以使用PostgreSQL
        os.environ['USE_POSTGRESQL'] = 'true'
        
        try:
            from app import create_app
            from models import db
            
            app = create_app()
            with app.app_context():
                # 删除所有现有表（谨慎操作）
                db.drop_all()
                print("🗑️  清空现有表结构")
                
                # 创建所有表
                db.create_all()
                print("✅ PostgreSQL表结构创建完成")
                
        except Exception as e:
            print(f"❌ 创建PostgreSQL表结构失败: {e}")
            raise
    
    def get_sqlite_data(self):
        """从SQLite读取所有数据"""
        print("\n📖 读取SQLite数据...")
        
        try:
            conn = sqlite3.connect(self.sqlite_db_path)
            conn.row_factory = sqlite3.Row  # 使用Row工厂以便按列名访问
            cursor = conn.cursor()
            
            data = {}
            
            # 定义表的迁移顺序（考虑外键依赖）
            tables = [
                'users',
                'channels', 
                'channel_data',
                'videos',
                'favorite_videos'
            ]
            
            for table in tables:
                try:
                    cursor.execute(f"SELECT * FROM {table}")
                    rows = cursor.fetchall()
                    data[table] = [dict(row) for row in rows]
                    print(f"  📊 {table}: {len(data[table])} 条记录")
                except sqlite3.OperationalError as e:
                    if "no such table" in str(e).lower():
                        print(f"  ⚠️  表 {table} 不存在，跳过")
                        data[table] = []
                    else:
                        raise
            
            conn.close()
            return data
            
        except Exception as e:
            print(f"❌ 读取SQLite数据失败: {e}")
            raise
    
    def migrate_users(self, pg_conn, users_data):
        """迁移用户数据"""
        if not users_data:
            return
            
        print(f"\n👥 迁移用户数据: {len(users_data)} 条")
        
        cursor = pg_conn.cursor()
        
        values = []
        for user in users_data:
            values.append((
                user['id'],
                user['username'],
                user['email'],
                user['password_hash'],
                user.get('display_name'),
                user.get('avatar_url'),
                user.get('is_active', True),
                user.get('is_verified', False),
                user.get('is_admin', False),
                user.get('timezone', 'UTC'),
                user.get('language', 'zh-CN'),
                self.parse_datetime(user.get('created_at')),
                self.parse_datetime(user.get('updated_at')),
                self.parse_datetime(user.get('last_login_at'))
            ))
        
        execute_values(
            cursor,
            """INSERT INTO users (
                id, username, email, password_hash, display_name, avatar_url,
                is_active, is_verified, is_admin, timezone, language,
                created_at, updated_at, last_login_at
            ) VALUES %s""",
            values,
            template=None,
            page_size=100
        )
        
        # 重置序列
        cursor.execute("SELECT setval('users_id_seq', (SELECT MAX(id) FROM users));")
        pg_conn.commit()
        print("✅ 用户数据迁移完成")
    
    def migrate_channels(self, pg_conn, channels_data):
        """迁移频道数据"""
        if not channels_data:
            return
            
        print(f"\n📺 迁移频道数据: {len(channels_data)} 条")
        
        cursor = pg_conn.cursor()
        
        values = []
        for channel in channels_data:
            values.append((
                channel['id'],
                channel['user_id'],
                channel['name'],
                channel['channel_id'],
                channel['url'],
                float(channel.get('cpm', 1.0)),
                channel.get('category'),
                int(channel.get('sort', 0)),
                channel.get('is_active', True),
                self.parse_datetime(channel.get('created_at')),
                self.parse_datetime(channel.get('updated_at'))
            ))
        
        execute_values(
            cursor,
            """INSERT INTO channels (
                id, user_id, name, channel_id, url, cpm, category, sort,
                is_active, created_at, updated_at
            ) VALUES %s""",
            values,
            template=None,
            page_size=100
        )
        
        # 重置序列
        cursor.execute("SELECT setval('channels_id_seq', (SELECT MAX(id) FROM channels));")
        pg_conn.commit()
        print("✅ 频道数据迁移完成")
    
    def migrate_channel_data(self, pg_conn, channel_data):
        """迁移频道数据统计"""
        if not channel_data:
            return
            
        print(f"\n📈 迁移频道统计数据: {len(channel_data)} 条")
        
        cursor = pg_conn.cursor()
        
        values = []
        for data in channel_data:
            values.append((
                data['id'],
                data['channel_id'],
                self.parse_date(data['date']),
                int(data.get('subscribers', 0)),
                int(data.get('total_views', 0)),
                int(data.get('video_count', 0)),
                int(data.get('subscriber_change', 0)),
                int(data.get('view_change', 0)),
                int(data.get('video_change', 0)),
                float(data.get('estimated_revenue', 0.0)),
                self.parse_datetime(data.get('created_at'))
            ))
        
        execute_values(
            cursor,
            """INSERT INTO channel_data (
                id, channel_id, date, subscribers, total_views, video_count,
                subscriber_change, view_change, video_change, estimated_revenue, created_at
            ) VALUES %s""",
            values,
            template=None,
            page_size=100
        )
        
        # 重置序列
        cursor.execute("SELECT setval('channel_data_id_seq', (SELECT MAX(id) FROM channel_data));")
        pg_conn.commit()
        print("✅ 频道统计数据迁移完成")
    
    def migrate_videos(self, pg_conn, videos_data):
        """迁移视频数据"""
        if not videos_data:
            return
            
        print(f"\n🎬 迁移视频数据: {len(videos_data)} 条")
        
        cursor = pg_conn.cursor()
        
        values = []
        for video in videos_data:
            values.append((
                video['id'],
                video['user_id'],
                video['video_id'],
                video['channel_id'],
                video['title'],
                video.get('description'),
                self.parse_datetime(video['published_at']),
                video.get('duration'),
                int(video.get('view_count', 0)),
                int(video.get('like_count', 0)),
                int(video.get('comment_count', 0)),
                video.get('tags'),
                video.get('thumbnail_url'),
                video.get('category_id'),
                video.get('category_name'),
                video.get('language'),
                video.get('is_active', True),
                self.parse_datetime(video.get('created_at')),
                self.parse_datetime(video.get('updated_at'))
            ))
        
        execute_values(
            cursor,
            """INSERT INTO videos (
                id, user_id, video_id, channel_id, title, description, published_at,
                duration, view_count, like_count, comment_count, tags, thumbnail_url,
                category_id, category_name, language, is_active, created_at, updated_at
            ) VALUES %s""",
            values,
            template=None,
            page_size=100
        )
        
        # 重置序列
        cursor.execute("SELECT setval('videos_id_seq', (SELECT MAX(id) FROM videos));")
        pg_conn.commit()
        print("✅ 视频数据迁移完成")
    
    def migrate_favorite_videos(self, pg_conn, favorites_data):
        """迁移收藏视频数据"""
        if not favorites_data:
            return
            
        print(f"\n⭐ 迁移收藏视频数据: {len(favorites_data)} 条")
        
        cursor = pg_conn.cursor()
        
        values = []
        for fav in favorites_data:
            values.append((
                fav['id'],
                fav['user_id'],
                fav['video_id'],
                fav['title'],
                fav.get('description'),
                fav.get('thumbnail_url'),
                fav.get('duration'),
                fav['channel_id'],
                fav['channel_title'],
                fav.get('channel_thumbnail_url'),
                self.parse_datetime(fav.get('channel_published_at')),
                int(fav.get('view_count', 0)),
                int(fav.get('like_count', 0)),
                int(fav.get('comment_count', 0)),
                int(fav.get('channel_subscriber_count', 0)),
                int(fav.get('channel_video_count', 0)),
                int(fav.get('channel_view_count', 0)),
                self.parse_datetime(fav['published_at']),
                self.parse_datetime(fav['favorited_at']),
                float(fav.get('hot_score', 0.0)),
                fav.get('note'),
                fav.get('tags')
            ))
        
        execute_values(
            cursor,
            """INSERT INTO favorite_videos (
                id, user_id, video_id, title, description, thumbnail_url, duration,
                channel_id, channel_title, channel_thumbnail_url, channel_published_at,
                view_count, like_count, comment_count, channel_subscriber_count,
                channel_video_count, channel_view_count, published_at, favorited_at,
                hot_score, note, tags
            ) VALUES %s""",
            values,
            template=None,
            page_size=100
        )
        
        # 重置序列
        cursor.execute("SELECT setval('favorite_videos_id_seq', (SELECT MAX(id) FROM favorite_videos));")
        pg_conn.commit()
        print("✅ 收藏视频数据迁移完成")
    
    def parse_datetime(self, dt_str):
        """解析日期时间字符串"""
        if not dt_str:
            return None
        
        try:
            if isinstance(dt_str, str):
                # 尝试不同的日期时间格式
                formats = [
                    '%Y-%m-%d %H:%M:%S.%f',  # SQLite默认格式
                    '%Y-%m-%d %H:%M:%S',
                    '%Y-%m-%dT%H:%M:%S.%f',
                    '%Y-%m-%dT%H:%M:%S'
                ]
                
                for fmt in formats:
                    try:
                        return datetime.strptime(dt_str, fmt)
                    except ValueError:
                        continue
                        
                # 如果都不匹配，返回当前时间
                print(f"⚠️  无法解析日期时间格式: {dt_str}")
                return datetime.utcnow()
            else:
                return dt_str
        except:
            return datetime.utcnow()
    
    def parse_date(self, date_str):
        """解析日期字符串"""
        if not date_str:
            return date.today()
        
        try:
            if isinstance(date_str, str):
                return datetime.strptime(date_str, '%Y-%m-%d').date()
            else:
                return date_str
        except:
            return date.today()
    
    def verify_migration(self, pg_conn, original_data):
        """验证迁移结果"""
        print("\n🔍 验证迁移结果...")
        
        cursor = pg_conn.cursor()
        
        for table_name, original_rows in original_data.items():
            if not original_rows:
                continue
                
            cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            migrated_count = cursor.fetchone()[0]
            original_count = len(original_rows)
            
            if migrated_count == original_count:
                print(f"  ✅ {table_name}: {migrated_count}/{original_count}")
            else:
                print(f"  ❌ {table_name}: {migrated_count}/{original_count} (数量不匹配)")
    
    def run_migration(self):
        """执行完整的数据迁移流程"""
        print("🚀 开始SQLite到PostgreSQL数据迁移\n")
        
        try:
            # 检查SQLite数据库
            if not self.check_sqlite_exists():
                return False
            
            # 读取SQLite数据
            sqlite_data = self.get_sqlite_data()
            
            # 计算总记录数
            total_records = sum(len(records) for records in sqlite_data.values())
            print(f"\n📊 总计需要迁移 {total_records} 条记录")
            
            if total_records == 0:
                print("⚠️  没有数据需要迁移")
                return True
            
            # 创建PostgreSQL表结构
            self.create_postgresql_tables()
            
            # 连接PostgreSQL
            print(f"\n🔌 连接PostgreSQL数据库...")
            pg_conn = psycopg2.connect(**self.pg_params)
            pg_conn.autocommit = False
            
            try:
                # 迁移数据（按依赖关系顺序）
                self.migrate_users(pg_conn, sqlite_data.get('users', []))
                self.migrate_channels(pg_conn, sqlite_data.get('channels', []))
                self.migrate_channel_data(pg_conn, sqlite_data.get('channel_data', []))
                self.migrate_videos(pg_conn, sqlite_data.get('videos', []))
                self.migrate_favorite_videos(pg_conn, sqlite_data.get('favorite_videos', []))
                
                # 验证迁移结果
                self.verify_migration(pg_conn, sqlite_data)
                
                print(f"\n🎉 数据迁移完成！")
                print(f"   📂 SQLite: {self.sqlite_db_path}")
                print(f"   🐘 PostgreSQL: {self.pg_params['database']}@{self.pg_params['host']}")
                
                return True
                
            finally:
                pg_conn.close()
                
        except Exception as e:
            print(f"\n❌ 迁移失败: {e}")
            import traceback
            traceback.print_exc()
            return False


def main():
    """主函数"""
    print("=" * 60)
    print("  YouTube Analytics - 数据库迁移工具")
    print("  SQLite → PostgreSQL")
    print("=" * 60)
    
    # 检查环境变量
    required_vars = ['POSTGRES_HOST', 'POSTGRES_DB', 'POSTGRES_USER', 'POSTGRES_PASSWORD']
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        print(f"❌ 缺少环境变量: {', '.join(missing_vars)}")
        print("请设置PostgreSQL连接参数或使用 .env.postgresql 配置文件")
        sys.exit(1)
    
    migrator = DatabaseMigrator()
    success = migrator.run_migration()
    
    if success:
        print("\n✅ 迁移成功完成！")
        print("\n下一步：")
        print("1. 设置环境变量 USE_POSTGRESQL=true")
        print("2. 重启应用程序以使用PostgreSQL数据库")
        print("3. 验证应用程序功能正常")
    else:
        print("\n❌ 迁移失败！")
        sys.exit(1)


if __name__ == "__main__":
    main()