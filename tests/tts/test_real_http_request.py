#!/usr/bin/env python3
"""
模拟真实HTTP请求测试TTS问题
"""

import requests
import json
import time

def test_tts_http_request():
    """测试真实HTTP请求"""
    
    base_url = "http://localhost:5004"
    
    # 测试文本
    test_cases = [
        {"text": "测试文本", "description": "简单测试文本"},
        {"text": "你好世界", "description": "问候语"},
        {"text": "这是一个测试，看看会不会重复", "description": "长一点的文本"},
        {"text": "中文语音合成测试内容", "description": "包含专业术语"},
    ]
    
    print("🚀 开始HTTP请求测试")
    print("="*60)
    
    for i, test_case in enumerate(test_cases, 1):
        print(f"\n📝 测试用例 {i}: {test_case['description']}")
        print(f"   文本: '{test_case['text']}'")
        print("-" * 40)
        
        # 准备请求数据
        request_data = {
            "text": test_case['text'],
            "model": "chinese"  # 使用中文模型
        }
        
        try:
            print(f"🔄 发送HTTP请求...")
            start_time = time.time()
            
            response = requests.post(
                f"{base_url}/api/v1/tts/synthesize",
                json=request_data,
                headers={'Content-Type': 'application/json'},
                timeout=30
            )
            
            elapsed = time.time() - start_time
            
            if response.status_code == 200:
                result = response.json()
                if result.get('success'):
                    data = result.get('data', {})
                    file_size = data.get('file_size', 0)
                    filename = data.get('audio_filename', '')
                    
                    print(f"✅ 请求成功 ({elapsed:.2f}s)")
                    print(f"   文件: {filename}")
                    print(f"   大小: {file_size} bytes")
                    
                    # 分析结果
                    if file_size > 500000:
                        print("❌ 检测到异常：文件过大，可能是重复播放问题！")
                    elif file_size < 10000:
                        print("⚠️  文件较小，可能内容过短")
                    else:
                        print("✅ 文件大小正常")
                else:
                    print(f"❌ 服务器返回失败: {result.get('message', '未知错误')}")
            else:
                print(f"❌ HTTP请求失败: {response.status_code}")
                print(f"   响应: {response.text[:200]}")
                
        except requests.exceptions.Timeout:
            print("❌ 请求超时")
        except requests.exceptions.RequestException as e:
            print(f"❌ 请求异常: {str(e)}")
        except Exception as e:
            print(f"❌ 其他错误: {str(e)}")

def test_status_endpoint():
    """测试状态端点"""
    print("\n" + "="*60)
    print("🔍 检查TTS服务状态")
    print("="*60)
    
    try:
        response = requests.get("http://localhost:5004/api/v1/tts/status", timeout=10)
        if response.status_code == 200:
            result = response.json()
            print(f"✅ 服务状态: {json.dumps(result, indent=2, ensure_ascii=False)}")
        else:
            print(f"❌ 状态检查失败: {response.status_code}")
    except Exception as e:
        print(f"❌ 无法连接到服务: {str(e)}")

if __name__ == '__main__':
    print("🧪 TTS HTTP请求测试")
    
    # 先检查服务状态
    test_status_endpoint()
    
    # 执行TTS测试
    test_tts_http_request()
    
    print("\n" + "="*60)
    print("🏁 测试完成")
    print("="*60)