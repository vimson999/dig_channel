#!/usr/bin/env python3
"""
测试频道API的响应内容和性能问题
"""
import sys
import requests
import json
import time

# 添加src/backend到Python路径（自动回溯到项目根目录）
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve()
while PROJECT_ROOT.parent != PROJECT_ROOT and not (PROJECT_ROOT / 'src' / 'backend').exists():
    PROJECT_ROOT = PROJECT_ROOT.parent

sys.path.insert(0, str((PROJECT_ROOT / 'src' / 'backend').resolve()))

def test_api_response():
    """测试API响应内容"""
    
    try:
        base_url = "http://127.0.0.1:5001"
        
        print("🔍 测试频道API响应...")
        
        # 测试获取频道列表API
        start_time = time.time()
        response = requests.get(f"{base_url}/api/v1/channels?page=1&per_page=10&active_only=true")
        end_time = time.time()
        
        print(f"⏱️ API响应时间: {(end_time - start_time)*1000:.2f}ms")
        print(f"🌐 HTTP状态码: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            
            print(f"📊 API响应结构:")
            print(f"  success: {data.get('success', 'N/A')}")
            
            if 'data' in data:
                channels = data['data'].get('channels', [])
                pagination = data['data'].get('pagination', {})
                
                print(f"  频道数量: {len(channels)}")
                print(f"  分页信息: {pagination}")
                
                # 检查第一个频道的数据结构
                if channels:
                    first_channel = channels[0]
                    print(f"\n📝 第一个频道数据结构:")
                    print(f"  ID: {first_channel.get('id')}")
                    print(f"  名称: {first_channel.get('name')}")
                    print(f"  是否活跃: {first_channel.get('is_active')}")
                    print(f"  是否包含latest_data: {'latest_data' in first_channel}")
                    
                    if 'latest_data' in first_channel:
                        latest_data = first_channel['latest_data']
                        if latest_data:
                            print(f"    订阅数: {latest_data.get('subscribers')}")
                            print(f"    视频数: {latest_data.get('video_count')}")
                            print(f"    总观看数: {latest_data.get('total_views')}")
                        else:
                            print(f"    latest_data为空")
                    
                    print(f"\n📋 完整第一个频道数据:")
                    print(json.dumps(first_channel, indent=2, ensure_ascii=False))
                
        else:
            print(f"❌ API请求失败: {response.text}")
            
    except requests.exceptions.ConnectionError:
        print("❌ 无法连接到服务器 (127.0.0.1:5001)")
        print("请确保应用程序正在运行")
    except Exception as e:
        print(f"❌ 测试失败: {str(e)}")

if __name__ == "__main__":
    print("🚀 开始测试频道API...\n")
    test_api_response()
    print("\n✅ 测试完成！")