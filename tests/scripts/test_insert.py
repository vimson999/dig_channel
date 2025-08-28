#!/usr/bin/env python3
"""
测试向PostgreSQL插入channel_data数据
"""

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'src', 'backend'))

from datetime import date, datetime
from app import create_app
from models import db, Channel, ChannelData

def test_insert():
    app = create_app()
    
    with app.app_context():
        try:
            print("测试PostgreSQL数据插入...")
            
            # 获取第一个频道
            channel = Channel.query.first()
            if not channel:
                print("❌ 没有找到频道数据")
                return False
            
            print(f"使用频道: {channel.name} (ID: {channel.id})")
            
            # 检查今天是否已有数据
            today = date.today()
            existing = ChannelData.query.filter_by(
                channel_id=channel.id, 
                date=today
            ).first()
            
            if existing:
                print(f"今天已有数据: subscribers={existing.subscribers}")
                # 更新数据
                existing.subscribers += 1
                existing.updated_at = datetime.utcnow()
                db.session.commit()
                print("✅ 更新成功")
            else:
                # 创建新数据
                new_data = ChannelData(
                    channel_id=channel.id,
                    date=today,
                    subscribers=10000,
                    total_views=1000000,
                    video_count=100,
                    subscriber_change=100,
                    view_change=10000,
                    video_change=1,
                    estimated_revenue=50.00
                )
                db.session.add(new_data)
                db.session.commit()
                print("✅ 插入成功")
            
            # 验证数据
            count = ChannelData.query.filter_by(date=today).count()
            total_count = ChannelData.query.count()
            print(f"今天的数据条数: {count}")
            print(f"总数据条数: {total_count}")
            
            return True
            
        except Exception as e:
            print(f"❌ 测试失败: {e}")
            db.session.rollback()
            return False

if __name__ == '__main__':
    test_insert()