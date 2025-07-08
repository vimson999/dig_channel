#!/usr/bin/env python3
"""
修复已导入频道的数据
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

def fix_channels():
    """修复已导入的频道数据"""
    app = create_app()
    
    with app.app_context():
        # 找到所有临时导入的频道（channel_id以handle_开头的）
        temp_channels = Channel.query.filter(Channel.channel_id.like('handle_%')).all()
        
        print(f"找到 {len(temp_channels)} 个需要修复的频道")
        
        for channel in temp_channels:
            print(f"修复频道: {channel.name}")
            
            # 更新CPM和category
            channel.cpm = 9.00
            channel.category = '科技'
            
            # 暂时保留临时的channel_id，后续可以通过YouTube API获取真实的channel_id
            # 这里我们先让这些频道可以正常使用，channel_id可以后续同步时更新
            
        try:
            db.session.commit()
            print(f"✅ 成功修复 {len(temp_channels)} 个频道的CPM和category")
        except Exception as e:
            print(f"❌ 修复失败: {e}")
            db.session.rollback()

if __name__ == '__main__':
    fix_channels()