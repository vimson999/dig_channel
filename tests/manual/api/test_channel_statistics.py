#!/usr/bin/env python3
"""
测试频道管理页面统计数据
"""
import requests
import json

def test_channel_statistics():
    """测试频道管理页面的统计数据是否正确显示"""
    
    print("🔍 测试频道管理页面统计数据...")
    
    # 测试API端点
    base_url = "http://localhost:5000"
    
    try:
        # 1. 获取所有频道数据（用于全局统计）
        print("\n1. 测试全局频道统计API...")
        response = requests.get(f"{base_url}/api/v1/channels", params={
            'page': 1,
            'per_page': 1000,
            'active_only': 'false'
        })
        
        if response.status_code == 200:
            data = response.json()
            if data.get('success'):
                all_channels = data.get('data', {}).get('channels', [])
                if isinstance(data.get('data'), list):
                    all_channels = data.get('data', [])
                
                print(f"✅ API响应成功，获取到 {len(all_channels)} 个频道")
                
                # 计算统计信息
                total_channels = len(all_channels)
                active_channels = len([ch for ch in all_channels if ch.get('is_active')])
                
                total_subscribers = 0
                total_videos = 0
                
                # 检查频道数据结构
                print("\n📊 分析频道数据结构...")
                for i, channel in enumerate(all_channels[:3]):  # 只查看前3个频道
                    print(f"频道 {i+1}: {channel.get('name', 'Unknown')}")
                    print(f"  - ID: {channel.get('id')}")
                    print(f"  - 是否活跃: {channel.get('is_active')}")
                    print(f"  - 最新数据: {channel.get('latest_data')}")
                    
                    if channel.get('latest_data'):
                        latest = channel['latest_data']
                        subscribers = latest.get('subscribers', 0)
                        video_count = latest.get('video_count', 0)
                        total_subscribers += subscribers
                        total_videos += video_count
                        print(f"  - 订阅数: {subscribers:,}")
                        print(f"  - 视频数: {video_count:,}")
                    else:
                        print("  - ⚠️ 缺少latest_data字段")
                
                print(f"\n📈 计算得出的统计数据:")
                print(f"  - 总频道数: {total_channels}")
                print(f"  - 活跃频道数: {active_channels}")
                print(f"  - 总订阅数: {total_subscribers:,}")
                print(f"  - 总视频数: {total_videos:,}")
                
                # 验证数据是否合理
                if total_channels > 0:
                    print("\n✅ 频道数据正常")
                else:
                    print("\n❌ 无频道数据")
                    
                if total_subscribers > 0:
                    print("✅ 订阅数据正常")
                else:
                    print("❌ 订阅数据为0，可能latest_data字段缺失")
                    
                if total_videos > 0:
                    print("✅ 视频数据正常")
                else:
                    print("❌ 视频数据为0，可能latest_data字段缺失")
                    
            else:
                print(f"❌ API返回失败: {data.get('message', 'Unknown error')}")
        else:
            print(f"❌ API请求失败，状态码: {response.status_code}")
            print(f"响应内容: {response.text}")
            
    except requests.exceptions.ConnectionError:
        print("❌ 无法连接到后端服务，请确保后端正在运行在 http://localhost:5000")
    except Exception as e:
        print(f"❌ 测试失败: {str(e)}")

def test_channel_management_js_logic():
    """模拟前端JavaScript的统计计算逻辑"""
    print("\n🧪 模拟前端JavaScript统计计算逻辑...")
    
    # 模拟API响应数据
    mock_channels = [
        {
            'id': 1,
            'name': 'Test Channel 1',
            'is_active': True,
            'latest_data': {
                'subscribers': 10000,
                'video_count': 50
            }
        },
        {
            'id': 2,
            'name': 'Test Channel 2',
            'is_active': True,
            'latest_data': {
                'subscribers': 25000,
                'video_count': 75
            }
        },
        {
            'id': 3,
            'name': 'Test Channel 3',
            'is_active': False,
            'latest_data': {
                'subscribers': 5000,
                'video_count': 20
            }
        },
        {
            'id': 4,
            'name': 'Test Channel 4',
            'is_active': True,
            'latest_data': None  # 模拟缺失数据
        }
    ]
    
    # 计算统计信息（模拟前端loadGlobalStatistics方法）
    total_channels = len(mock_channels)
    active_channels = len([ch for ch in mock_channels if ch.get('is_active')])
    
    total_subscribers = 0
    total_videos = 0
    
    for channel in mock_channels:
        if channel.get('latest_data'):
            total_subscribers += channel['latest_data'].get('subscribers', 0)
            total_videos += channel['latest_data'].get('video_count', 0)
    
    print(f"模拟计算结果:")
    print(f"  - 总频道数: {total_channels}")
    print(f"  - 活跃频道数: {active_channels}")
    print(f"  - 总订阅数: {total_subscribers:,}")
    print(f"  - 总视频数: {total_videos:,}")
    
    # 验证计算是否正确
    expected_total = 4
    expected_active = 3
    expected_subscribers = 40000  # 10000 + 25000 + 5000
    expected_videos = 145  # 50 + 75 + 20
    
    print(f"\n验证计算结果:")
    print(f"  - 总频道数: {'✅' if total_channels == expected_total else '❌'} (期望: {expected_total}, 实际: {total_channels})")
    print(f"  - 活跃频道数: {'✅' if active_channels == expected_active else '❌'} (期望: {expected_active}, 实际: {active_channels})")
    print(f"  - 总订阅数: {'✅' if total_subscribers == expected_subscribers else '❌'} (期望: {expected_subscribers:,}, 实际: {total_subscribers:,})")
    print(f"  - 总视频数: {'✅' if total_videos == expected_videos else '❌'} (期望: {expected_videos:,}, 实际: {total_videos:,})")

if __name__ == "__main__":
    print("🚀 开始测试频道管理页面统计功能...\n")
    
    # 测试实际API
    test_channel_statistics()
    
    # 测试JavaScript逻辑
    test_channel_management_js_logic()
    
    print("\n✅ 测试完成！")