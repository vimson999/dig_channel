#!/usr/bin/env python3
"""
使用YouTube API修正频道的channel_id
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
from utils.youtube_api import YouTubeAPI
import time

def fix_channel_ids():
    """使用YouTube API修正频道的channel_id"""
    app = create_app()
    
    with app.app_context():
        # 找到所有临时导入的频道（channel_id以handle_开头的）
        temp_channels = Channel.query.filter(Channel.channel_id.like('handle_%')).all()
        
        print(f"找到 {len(temp_channels)} 个需要修正channel_id的频道")
        
        if not temp_channels:
            print("没有需要修正的频道")
            return
        
        try:
            # 初始化YouTube API
            youtube_api = YouTubeAPI()
            print("YouTube API 初始化成功")
            
            success_count = 0
            failed_count = 0
            
            for i, channel in enumerate(temp_channels, 1):
                print(f"\n[{i}/{len(temp_channels)}] 处理频道: {channel.name}")
                print(f"  URL: {channel.url}")
                print(f"  当前错误的channel_id: {channel.channel_id}")
                
                try:
                    # 使用YouTube API解析频道URL获取真实的channel_id
                    real_channel_id = youtube_api.parse_channel_url(channel.url)
                    
                    if real_channel_id:
                        print(f"  ✅ 获取到真实channel_id: {real_channel_id}")
                        
                        # 获取频道详细信息并更新
                        channel_info = youtube_api.get_channel_info(real_channel_id)
                        if channel_info:
                            # 更新频道信息
                            channel.channel_id = real_channel_id
                            channel.name = channel_info['title']
                            
                            print(f"  📝 更新频道名称: {channel_info['title']}")
                            success_count += 1
                        else:
                            print(f"  ⚠️  无法获取频道详细信息，仅更新channel_id")
                            channel.channel_id = real_channel_id
                            success_count += 1
                    else:
                        print(f"  ❌ 无法获取真实的channel_id")
                        failed_count += 1
                    
                    # 避免API限制，稍微延迟
                    time.sleep(0.5)
                    
                except Exception as e:
                    print(f"  ❌ 处理失败: {e}")
                    failed_count += 1
            
            # 提交所有更改
            if success_count > 0:
                db.session.commit()
                print(f"\n✅ 成功修正 {success_count} 个频道的channel_id")
            
            if failed_count > 0:
                print(f"❌ {failed_count} 个频道修正失败")
                
        except Exception as e:
            print(f"YouTube API 初始化失败: {e}")
            print("请检查配置文件中的YouTube API密钥")
            db.session.rollback()

if __name__ == '__main__':
    fix_channel_ids()