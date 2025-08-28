#!/usr/bin/env python3
"""
创建收藏视频表的迁移脚本
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'backend'))

from models import db, FavoriteVideo
from config import get_config
from flask import Flask

def create_app():
    """创建Flask应用实例"""
    app = Flask(__name__)
    config = get_config()
    app.config.from_object(config)
    
    # 初始化数据库
    db.init_app(app)
    
    return app

def create_favorites_table():
    """创建收藏视频表"""
    app = create_app()
    
    with app.app_context():
        try:
            # 创建表
            db.create_all()
            print("✅ 收藏视频表创建成功")
            
            # 验证表是否存在
            inspector = db.inspect(db.engine)
            tables = inspector.get_table_names()
            
            if 'favorite_videos' in tables:
                print("✅ favorite_videos 表已存在")
                
                # 显示表结构
                columns = inspector.get_columns('favorite_videos')
                print("\n📋 表结构:")
                for col in columns:
                    print(f"  - {col['name']}: {col['type']}")
                
                # 显示索引
                indexes = inspector.get_indexes('favorite_videos')
                if indexes:
                    print("\n📊 索引:")
                    for idx in indexes:
                        print(f"  - {idx['name']}: {idx['column_names']}")
            else:
                print("❌ favorite_videos 表创建失败")
                
        except Exception as e:
            print(f"❌ 创建表时出错: {e}")
            import traceback
            traceback.print_exc()

if __name__ == '__main__':
    create_favorites_table()