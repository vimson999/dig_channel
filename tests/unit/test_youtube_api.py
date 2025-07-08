#!/usr/bin/env python3
# YouTube API 集成测试脚本

import sys
import os
from datetime import date

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app
from models import db
from services.youtube_sync_service import YouTubeSyncService
from utils.youtube_api import validate_youtube_url, extract_video_id

def test_youtube_api():
    """测试YouTube API功能"""
    print("🔧 YouTube API 集成测试")
    print("=" * 50)
    
    # 创建应用上下文
    app = create_app()
    
    with app.app_context():
        # 1. 测试API连接
        print("\n1. 测试API连接...")
        sync_service = YouTubeSyncService()
        api_status = sync_service.get_api_status()
        
        if api_status['api_available']:
            print(f"✅ API连接正常: {api_status['message']}")
            quota_info = api_status['quota_info']
            print(f"   配额使用: {quota_info['quota_used']}/{quota_info['quota_limit']} ({quota_info['quota_percentage']:.1f}%)")
        else:
            print(f"❌ API连接失败: {api_status['message']}")
            if 'YOUTUBE_API_KEY' not in os.environ:
                print("   提示: 请在.env文件中设置YOUTUBE_API_KEY")
            return False
        
        # 2. 测试URL解析功能
        print("\n2. 测试URL解析功能...")
        test_urls = [
            "https://www.youtube.com/channel/UCBR8-60-B28hp2BmDPdntcQ",  # YouTube Spotlight
            "https://www.youtube.com/c/YouTubeCreators",
            "https://www.youtube.com/@YouTube",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",  # 著名视频
            "https://www.youtube.com/user/YouTube",
            "invalid-url"
        ]
        
        for url in test_urls:
            is_valid = validate_youtube_url(url)
            print(f"   {url}")
            print(f"   有效性: {'✅' if is_valid else '❌'}")
            
            if 'watch?v=' in url:
                video_id = extract_video_id(url)
                print(f"   视频ID: {video_id}")
            
            if is_valid:
                preview_result = sync_service.validate_and_preview_channel(url)
                if preview_result['success']:
                    channel_info = preview_result['data']['channel_info']
                    print(f"   频道: {channel_info['title']}")
                    print(f"   订阅数: {channel_info['subscriber_count']:,}")
                else:
                    print(f"   预览失败: {preview_result['message']}")
            print()
        
        # 3. 测试频道信息获取
        print("\n3. 测试频道信息获取...")
        test_channel_url = "https://www.youtube.com/channel/UCBR8-60-B28hp2BmDPdntcQ"
        
        preview_result = sync_service.validate_and_preview_channel(test_channel_url)
        if preview_result['success']:
            channel_info = preview_result['data']['channel_info']
            print(f"✅ 频道信息获取成功:")
            print(f"   频道名: {channel_info['title']}")
            print(f"   频道ID: {channel_info['channel_id']}")
            print(f"   订阅数: {channel_info['subscriber_count']:,}")
            print(f"   视频数: {channel_info['video_count']:,}")
            print(f"   总观看数: {channel_info['view_count']:,}")
            print(f"   创建时间: {channel_info['published_at']}")
            print(f"   国家: {channel_info.get('country', 'N/A')}")
        else:
            print(f"❌ 频道信息获取失败: {preview_result['message']}")
        
        # 4. 测试添加频道功能（如果数据库可用）
        print("\n4. 测试添加频道功能...")
        try:
            # 检查数据库连接
            from sqlalchemy import text
            db.session.execute(text('SELECT 1'))
            
            # 添加测试频道
            add_result = sync_service.add_channel_from_url(
                url=test_channel_url,
                name="YouTube Spotlight (测试)",
                cpm=2.0,
                category="官方"
            )
            
            if add_result['success']:
                print(f"✅ 频道添加成功: {add_result['message']}")
                channel_data = add_result['data']
                print(f"   数据库ID: {channel_data['id']}")
                print(f"   频道名: {channel_data['name']}")
                
                # 测试数据同步
                print("\n5. 测试数据同步...")
                sync_result = sync_service.sync_channel_data(channel_data['id'])
                if sync_result['success']:
                    print(f"✅ 数据同步成功: {sync_result['message']}")
                    data = sync_result['data']
                    print(f"   日期: {data['date']}")
                    print(f"   订阅数: {data['subscribers']:,}")
                    print(f"   观看数: {data['total_views']:,}")
                    print(f"   预估收入: ${data['estimated_revenue']:.2f}")
                else:
                    print(f"❌ 数据同步失败: {sync_result['message']}")
            else:
                print(f"⚠️ 频道添加失败: {add_result['message']}")
                if "已存在" in add_result['message']:
                    print("   (这是正常情况，说明频道已经存在)")
        
        except Exception as e:
            print(f"❌ 数据库操作失败: {e}")
            print("   提示: 请确保数据库已初始化")
        
        print("\n" + "=" * 50)
        print("🎯 YouTube API 集成测试完成")
        
        return True

if __name__ == '__main__':
    test_youtube_api()