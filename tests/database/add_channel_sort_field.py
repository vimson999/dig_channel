#!/usr/bin/env python3
"""
为Channel表添加sort字段的数据库迁移脚本
"""

import sys
import os

# 添加项目根目录到路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)
sys.path.insert(0, os.path.join(project_root, 'src', 'backend'))

from app import create_app
from models import db
from models.channel import Channel

def add_sort_field():
    """添加sort字段并为现有频道设置初始值"""
    app = create_app()
    
    with app.app_context():
        try:
            # 检查是否已存在sort字段
            inspector = db.inspect(db.engine)
            columns = [column['name'] for column in inspector.get_columns('channels')]
            
            if 'sort' not in columns:
                print("添加sort字段到channels表...")
                
                # 添加sort字段 - 使用新的SQLAlchemy语法
                with db.engine.connect() as conn:
                    conn.execute(db.text('ALTER TABLE channels ADD COLUMN sort INTEGER NOT NULL DEFAULT 0'))
                    conn.commit()
                print("✅ sort字段添加成功")
            else:
                print("sort字段已存在，跳过添加")
            
            # 为现有频道设置初始sort值
            channels = Channel.query.order_by(Channel.id).all()
            if channels:
                print(f"为 {len(channels)} 个频道设置初始sort值...")
                
                for i, channel in enumerate(channels, 1):
                    if channel.sort == 0:  # 只更新sort为0的频道
                        channel.sort = i * 10  # 设置为10, 20, 30...方便后续调整
                
                db.session.commit()
                print("✅ 初始sort值设置完成")
                
                # 显示设置结果
                print("\n频道排序设置结果:")
                print("-" * 60)
                updated_channels = Channel.query.order_by(Channel.sort).all()
                for channel in updated_channels:
                    print(f"Sort: {channel.sort:3d} | ID: {channel.id:2d} | {channel.name}")
                print("-" * 60)
            else:
                print("没有找到频道数据")
                
        except Exception as e:
            print(f"❌ 迁移失败: {e}")
            db.session.rollback()
            raise

if __name__ == '__main__':
    add_sort_field()