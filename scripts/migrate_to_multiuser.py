#!/usr/bin/env python3
"""
数据库迁移脚本：将单用户系统迁移到多用户系统

这个脚本会：
1. 创建users表
2. 为现有表添加user_id字段
3. 创建默认管理员用户
4. 将现有数据分配给默认用户
5. 更新索引和约束
"""

import sys
import os
from datetime import datetime

# 添加项目路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'backend'))

from models import db, User, Channel, Video
from sqlalchemy import text
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def create_app():
    """创建Flask应用实例"""
    from flask import Flask
    from config import Config
    
    app = Flask(__name__)
    app.config.from_object(Config)
    
    db.init_app(app)
    
    return app

def backup_database():
    """备份数据库"""
    import shutil
    from datetime import datetime
    
    db_path = 'instance/youtube_tracker.db'
    if os.path.exists(db_path):
        backup_path = f'instance/youtube_tracker_backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.db'
        shutil.copy2(db_path, backup_path)
        logger.info(f"数据库已备份到: {backup_path}")
        return backup_path
    else:
        logger.warning("数据库文件不存在，跳过备份")
        return None

def check_existing_data():
    """检查现有数据和迁移状态"""
    try:
        # 检查是否已经有用户表
        result = db.session.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='users'"))
        users_table_exists = result.fetchone() is not None
        
        if users_table_exists:
            logger.info("用户表已存在，检查迁移完成状态")
            user_count = db.session.execute(text("SELECT COUNT(*) FROM users")).scalar()
            logger.info(f"当前用户数量: {user_count}")
            
            # 检查channels表是否有user_id字段
            try:
                db.session.execute(text("SELECT user_id FROM channels LIMIT 1"))
                channels_migrated = True
                logger.info("channels表已经有user_id字段")
            except:
                channels_migrated = False
                logger.info("channels表还没有user_id字段，需要迁移")
            
            # 检查videos表是否有user_id字段
            try:
                db.session.execute(text("SELECT user_id FROM videos LIMIT 1"))
                videos_migrated = True
                logger.info("videos表已经有user_id字段")
            except:
                videos_migrated = False
                logger.info("videos表还没有user_id字段，需要迁移")
            
            # 只有当用户数量>0且所有表结构都已迁移时才认为完成
            if user_count > 0 and channels_migrated and videos_migrated:
                logger.info("迁移已完成")
                return True
            else:
                logger.info("迁移未完成，继续执行迁移")
                return False
        
        # 检查现有频道数据
        channel_count = db.session.execute(text("SELECT COUNT(*) FROM channels")).scalar()
        video_count = db.session.execute(text("SELECT COUNT(*) FROM videos")).scalar()
        
        logger.info(f"现有数据统计: {channel_count} 个频道, {video_count} 个视频")
        return False
        
    except Exception as e:
        logger.error(f"检查现有数据失败: {e}")
        return False

def create_users_table():
    """创建用户表"""
    try:
        logger.info("创建用户表...")
        
        # 创建用户表的SQL
        create_users_sql = """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            username VARCHAR(80) NOT NULL UNIQUE,
            email VARCHAR(120) NOT NULL UNIQUE,
            password_hash VARCHAR(255) NOT NULL,
            display_name VARCHAR(100),
            avatar_url VARCHAR(500),
            is_active BOOLEAN NOT NULL DEFAULT 1,
            is_verified BOOLEAN NOT NULL DEFAULT 0,
            is_admin BOOLEAN NOT NULL DEFAULT 0,
            timezone VARCHAR(50) NOT NULL DEFAULT 'UTC',
            language VARCHAR(10) NOT NULL DEFAULT 'zh-CN',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_login_at DATETIME
        )
        """
        
        db.session.execute(text(create_users_sql))
        db.session.commit()
        logger.info("用户表创建成功")
        
    except Exception as e:
        logger.error(f"创建用户表失败: {e}")
        raise

def create_default_user():
    """创建默认管理员用户"""
    try:
        logger.info("创建默认管理员用户...")
        
        # 检查是否已存在用户
        existing_user = db.session.execute(text("SELECT COUNT(*) FROM users")).scalar()
        if existing_user > 0:
            logger.info("用户已存在，跳过创建")
            return db.session.execute(text("SELECT id FROM users LIMIT 1")).scalar()
        
        # 创建默认用户
        from werkzeug.security import generate_password_hash
        
        password_hash = generate_password_hash('admin123')
        
        insert_user_sql = """
        INSERT INTO users (username, email, password_hash, display_name, is_admin, is_active, is_verified)
        VALUES (:username, :email, :password_hash, :display_name, :is_admin, :is_active, :is_verified)
        """
        
        db.session.execute(text(insert_user_sql), {
            'username': 'admin',
            'email': 'admin@example.com', 
            'password_hash': password_hash,
            'display_name': '系统管理员',
            'is_admin': True,
            'is_active': True,
            'is_verified': True
        })
        db.session.commit()
        
        # 获取创建的用户ID
        user_id = db.session.execute(text("SELECT id FROM users WHERE username = 'admin'")).scalar()
        
        logger.info(f"默认管理员用户创建成功 (ID: {user_id})")
        logger.info("默认登录信息: 用户名=admin, 密码=admin123")
        
        return user_id
        
    except Exception as e:
        logger.error(f"创建默认用户失败: {e}")
        raise

def add_user_id_to_channels(default_user_id):
    """为频道表添加user_id字段"""
    try:
        logger.info("为频道表添加user_id字段...")
        
        # 检查是否已有user_id字段
        columns = db.session.execute(text("PRAGMA table_info(channels)")).fetchall()
        has_user_id = any(col[1] == 'user_id' for col in columns)
        
        if not has_user_id:
            # 添加user_id字段
            db.session.execute(text("ALTER TABLE channels ADD COLUMN user_id INTEGER"))
            db.session.commit()
            logger.info("user_id字段添加成功")
        
        # 更新现有数据
        db.session.execute(text("UPDATE channels SET user_id = :user_id WHERE user_id IS NULL"), {'user_id': default_user_id})
        db.session.commit()
        
        # 添加外键约束（SQLite限制，需要重建表）
        logger.info("重建频道表以添加外键约束...")
        rebuild_channels_table(default_user_id)
        
    except Exception as e:
        logger.error(f"为频道表添加user_id失败: {e}")
        raise

def rebuild_channels_table(default_user_id):
    """重建频道表以添加外键约束"""
    try:
        # 清理可能存在的临时表
        db.session.execute(text("DROP TABLE IF EXISTS channels_new"))
        db.session.commit()
        
        # 创建新表结构
        create_new_channels_sql = """
        CREATE TABLE channels_new (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name VARCHAR(100) NOT NULL,
            channel_id VARCHAR(100) NOT NULL,
            url VARCHAR(255) NOT NULL,
            cpm NUMERIC(5, 2) NOT NULL DEFAULT 1.00,
            category VARCHAR(50),
            sort INTEGER NOT NULL DEFAULT 0,
            is_active BOOLEAN NOT NULL DEFAULT 1,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            UNIQUE(user_id, channel_id)
        )
        """
        
        # 复制数据
        copy_data_sql = """
        INSERT INTO channels_new (id, user_id, name, channel_id, url, cpm, category, sort, is_active, created_at, updated_at)
        SELECT id, :user_id, name, channel_id, url, cpm, category, sort, is_active, created_at, updated_at
        FROM channels
        """
        
        db.session.execute(text(create_new_channels_sql))
        db.session.execute(text(copy_data_sql), {'user_id': default_user_id})
        
        # 删除旧表，重命名新表
        db.session.execute(text("DROP TABLE channels"))
        db.session.execute(text("ALTER TABLE channels_new RENAME TO channels"))
        
        # 创建索引
        db.session.execute(text("CREATE INDEX idx_channel_user_id ON channels(user_id)"))
        
        db.session.commit()
        logger.info("频道表重建成功")
        
    except Exception as e:
        logger.error(f"重建频道表失败: {e}")
        raise

def add_user_id_to_videos(default_user_id):
    """为视频表添加user_id字段"""
    try:
        logger.info("为视频表添加user_id字段...")
        
        # 检查是否已有user_id字段
        columns = db.session.execute(text("PRAGMA table_info(videos)")).fetchall()
        has_user_id = any(col[1] == 'user_id' for col in columns)
        
        if not has_user_id:
            # 重建视频表
            rebuild_videos_table(default_user_id)
        else:
            # 更新现有数据
            db.session.execute(text("UPDATE videos SET user_id = :user_id WHERE user_id IS NULL"), {'user_id': default_user_id})
            db.session.commit()
            logger.info("视频表user_id字段更新成功")
        
    except Exception as e:
        logger.error(f"为视频表添加user_id失败: {e}")
        raise

def rebuild_videos_table(default_user_id):
    """重建视频表以添加user_id字段和约束"""
    try:
        logger.info("重建视频表...")
        
        # 清理可能存在的临时表
        db.session.execute(text("DROP TABLE IF EXISTS videos_new"))
        db.session.commit()
        
        # 创建新表结构
        create_new_videos_sql = """
        CREATE TABLE videos_new (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            video_id VARCHAR(100) NOT NULL,
            channel_id INTEGER NOT NULL,
            title VARCHAR(500) NOT NULL,
            description TEXT,
            published_at DATETIME NOT NULL,
            duration VARCHAR(20),
            view_count BIGINT NOT NULL DEFAULT 0,
            like_count BIGINT NOT NULL DEFAULT 0,
            comment_count BIGINT NOT NULL DEFAULT 0,
            tags TEXT,
            thumbnail_url VARCHAR(500),
            category_id VARCHAR(10),
            category_name VARCHAR(100),
            language VARCHAR(10),
            is_active BOOLEAN NOT NULL DEFAULT 1,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(channel_id) REFERENCES channels(id),
            UNIQUE(user_id, video_id)
        )
        """
        
        # 复制数据
        copy_data_sql = """
        INSERT INTO videos_new (id, user_id, video_id, channel_id, title, description, published_at, 
                               duration, view_count, like_count, comment_count, tags, thumbnail_url, 
                               category_id, category_name, language, is_active, created_at, updated_at)
        SELECT id, :user_id, video_id, channel_id, title, description, published_at, 
               duration, view_count, like_count, comment_count, tags, thumbnail_url, 
               category_id, category_name, language, is_active, created_at, updated_at
        FROM videos
        """
        
        db.session.execute(text(create_new_videos_sql))
        db.session.execute(text(copy_data_sql), {'user_id': default_user_id})
        
        # 删除旧表，重命名新表
        db.session.execute(text("DROP TABLE videos"))
        db.session.execute(text("ALTER TABLE videos_new RENAME TO videos"))
        
        # 创建索引
        db.session.execute(text("CREATE INDEX idx_video_user_id ON videos(user_id)"))
        db.session.execute(text("CREATE INDEX idx_video_channel_id ON videos(channel_id)"))
        
        db.session.commit()
        logger.info("视频表重建成功")
        
    except Exception as e:
        logger.error(f"重建视频表失败: {e}")
        raise

def verify_migration():
    """验证迁移结果"""
    try:
        logger.info("验证迁移结果...")
        
        # 检查用户数据
        user_count = db.session.execute(text("SELECT COUNT(*) FROM users")).scalar()
        logger.info(f"用户数量: {user_count}")
        
        # 检查频道数据
        channel_count = db.session.execute(text("SELECT COUNT(*) FROM channels")).scalar()
        channels_with_user = db.session.execute(text("SELECT COUNT(*) FROM channels WHERE user_id IS NOT NULL")).scalar()
        logger.info(f"频道数量: {channel_count}, 已分配用户: {channels_with_user}")
        
        # 检查视频数据  
        video_count = db.session.execute(text("SELECT COUNT(*) FROM videos")).scalar()
        videos_with_user = db.session.execute(text("SELECT COUNT(*) FROM videos WHERE user_id IS NOT NULL")).scalar()
        logger.info(f"视频数量: {video_count}, 已分配用户: {videos_with_user}")
        
        if channel_count == channels_with_user and video_count == videos_with_user:
            logger.info("✅ 迁移验证成功！所有数据都已正确分配用户")
            return True
        else:
            logger.error("❌ 迁移验证失败！存在未分配用户的数据")
            return False
            
    except Exception as e:
        logger.error(f"验证迁移失败: {e}")
        return False

def main():
    """主迁移流程"""
    logger.info("开始数据库迁移：单用户 -> 多用户系统")
    logger.info("=" * 50)
    
    # 创建Flask应用上下文
    app = create_app()
    
    with app.app_context():
        try:
            # 1. 备份数据库
            backup_path = backup_database()
            
            # 2. 检查现有数据
            users_exist = check_existing_data()
            
            if users_exist:
                logger.info("用户表已存在，可能已经完成迁移")
                if not verify_migration():
                    logger.warning("验证失败，可能需要重新迁移")
                return
            
            # 3. 创建用户表
            create_users_table()
            
            # 4. 创建默认用户
            default_user_id = create_default_user()
            
            # 5. 迁移频道数据
            add_user_id_to_channels(default_user_id)
            
            # 6. 迁移视频数据
            add_user_id_to_videos(default_user_id)
            
            # 7. 验证迁移结果
            if verify_migration():
                logger.info("🎉 数据库迁移完成！")
                logger.info("=" * 50)
                logger.info("默认管理员账户信息:")
                logger.info("用户名: admin")
                logger.info("密码: admin123")
                logger.info("请登录后立即修改密码！")
                
                if backup_path:
                    logger.info(f"数据库备份位置: {backup_path}")
            else:
                logger.error("迁移验证失败，请检查日志")
                
        except Exception as e:
            logger.error(f"迁移过程中发生错误: {e}")
            db.session.rollback()
            raise

if __name__ == '__main__':
    main()