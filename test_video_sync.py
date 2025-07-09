#!/usr/bin/env python3
"""
视频同步测试脚本
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src', 'backend'))

from app import create_app
from services.video_sync_service import VideoSyncService

def test_video_sync():
    """测试视频同步功能"""
    app = create_app()
    
    with app.app_context():
        service = VideoSyncService()
        
        print("开始测试视频同步...")
        result = service.sync_all_channels_videos(days_back=3)
        
        print(f"同步结果: {result}")
        
        if result['success']:
            data = result['data']
            print(f"处理频道: {data['processed']} 个")
            print(f"新增视频: {data['new_videos']} 个")
            print(f"更新视频: {data['updated_videos']} 个")
            print(f"错误: {data['errors']} 个")
            
            if data['details']:
                print("\n详细结果:")
                for detail in data['details']:
                    status = detail['status']
                    name = detail['channel_name']
                    if status == 'success':
                        print(f"  ✅ {name}: 新增{detail['new_videos']} 更新{detail['updated_videos']}")
                    else:
                        print(f"  ❌ {name}: {detail['message']}")

if __name__ == '__main__':
    test_video_sync()