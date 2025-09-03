#!/usr/bin/env python3
"""
测试所有TTS模型是否存在重复问题
"""

import requests
import json
import time

def test_all_models():
    """测试所有可用的TTS模型"""
    
    base_url = "http://localhost:5004"
    
    # 获取所有可用模型
    try:
        response = requests.get(f"{base_url}/api/v1/tts/models", timeout=10)
        if response.status_code != 200:
            print("❌ 无法获取模型列表")
            return
            
        models_result = response.json()
        if not models_result.get('success'):
            print("❌ 获取模型失败")
            return
            
        available_models = models_result['data']['models']
        print("📋 可用模型:")
        for model in available_models:
            print(f"   - {model['key']}: {model['name']} ({model['language']})")
    
    except Exception as e:
        print(f"❌ 获取模型列表失败: {str(e)}")
        return
    
    # 测试文本
    test_text = "测试文本内容"
    
    print(f"\n🧪 测试文本: '{test_text}'")
    print("="*60)
    
    for model in available_models:
        model_key = model['key']
        model_name = model['name']
        
        print(f"\n🎯 测试模型: {model_key} ({model_name})")
        print("-" * 40)
        
        # 准备请求数据
        request_data = {
            "text": test_text,
            "model": model_key
        }
        
        try:
            start_time = time.time()
            
            response = requests.post(
                f"{base_url}/api/v1/tts/synthesize",
                json=request_data,
                headers={'Content-Type': 'application/json'},
                timeout=60  # 增加超时时间
            )
            
            elapsed = time.time() - start_time
            
            if response.status_code == 200:
                result = response.json()
                if result.get('success'):
                    data = result.get('data', {})
                    file_size = data.get('file_size', 0)
                    filename = data.get('audio_filename', '')
                    
                    print(f"✅ 成功 ({elapsed:.2f}s)")
                    print(f"   文件: {filename}")
                    print(f"   大小: {file_size} bytes")
                    
                    # 分析结果
                    if file_size > 500000:
                        print("🚨 检测到异常：文件过大，可能是重复播放问题！")
                    elif file_size < 10000:
                        print("⚠️  文件较小")
                    else:
                        print("✅ 文件大小正常")
                else:
                    print(f"❌ 失败: {result.get('message', '未知错误')}")
            else:
                print(f"❌ HTTP错误: {response.status_code}")
                print(f"   响应: {response.text[:200]}")
                
        except requests.exceptions.Timeout:
            print(f"⏰ 请求超时 (>{60}s)")
        except requests.exceptions.RequestException as e:
            print(f"❌ 请求异常: {str(e)}")
        except Exception as e:
            print(f"❌ 其他错误: {str(e)}")
        
        # 避免请求过于频繁
        time.sleep(1)

def test_dialogue_mode():
    """测试多人对话模式"""
    print("\n" + "="*60)
    print("🎭 测试多人对话模式")
    print("="*60)
    
    base_url = "http://localhost:5004"
    
    dialogue_text = "[张三]你好世界[李四]这是测试内容"
    
    request_data = {
        "text": dialogue_text,
        "model": "dialogue_mode"
    }
    
    try:
        start_time = time.time()
        
        response = requests.post(
            f"{base_url}/api/v1/tts/dialogue",
            json=request_data,
            headers={'Content-Type': 'application/json'},
            timeout=60
        )
        
        elapsed = time.time() - start_time
        
        if response.status_code == 200:
            result = response.json()
            if result.get('success'):
                data = result.get('data', {})
                file_size = data.get('file_size', 0)
                filename = data.get('audio_filename', '')
                
                print(f"✅ 对话模式成功 ({elapsed:.2f}s)")
                print(f"   文件: {filename}")
                print(f"   大小: {file_size} bytes")
                
                if file_size > 500000:
                    print("🚨 检测到异常：文件过大！")
                else:
                    print("✅ 文件大小正常")
            else:
                print(f"❌ 对话模式失败: {result.get('message', '未知错误')}")
        else:
            print(f"❌ HTTP错误: {response.status_code}")
            print(f"   响应: {response.text[:200]}")
            
    except Exception as e:
        print(f"❌ 对话模式测试失败: {str(e)}")

if __name__ == '__main__':
    print("🔬 全模型TTS测试")
    
    # 测试所有模型
    test_all_models()
    
    # 测试多人对话模式
    test_dialogue_mode()
    
    print("\n" + "="*60)
    print("🏁 全模型测试完成")
    print("="*60)