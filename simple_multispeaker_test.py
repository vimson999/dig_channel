#!/usr/bin/env python3
"""
简单测试多人对话修复效果
"""

import requests
import json

def test_simple_dialogue():
    """简单的多人对话测试"""
    
    # 简单启动本地服务器进行测试
    base_url = "http://localhost:5004"  # 假设有个服务器在运行
    
    # 简单的测试数据
    test_data = {
        "text": "[小明]你好[小红]你好小明",
        "model": "dialogue_mode"
    }
    
    print("🎭 测试简单多人对话")
    print(f"📝 测试文本: {test_data['text']}")
    print("-" * 40)
    
    try:
        response = requests.post(
            f"{base_url}/api/v1/tts/dialogue",
            json=test_data,
            headers={'Content-Type': 'application/json'},
            timeout=30
        )
        
        print(f"📡 响应状态: {response.status_code}")
        
        if response.status_code == 200:
            result = response.json()
            if result.get('success'):
                data = result.get('data', {})
                print(f"✅ 成功!")
                print(f"   文件: {data.get('audio_filename', 'N/A')}")
                print(f"   大小: {data.get('file_size', 0)} bytes")
                print(f"   说话人数: {len(data.get('speakers', []))}")
                print(f"   说话人: {', '.join(data.get('speakers', []))}")
                return True
            else:
                print(f"❌ 失败: {result.get('message', '未知错误')}")
        else:
            print(f"❌ HTTP错误: {response.status_code}")
            print(f"   响应: {response.text[:200]}")
        
        return False
        
    except requests.exceptions.ConnectionError:
        print("❌ 连接失败 - 确保TTS服务器在端口5004运行")
        print("💡 运行: PYTHONPATH=. venv-3.11/bin/python test_main_tts_server.py")
        return False
    except Exception as e:
        print(f"❌ 异常: {str(e)}")
        return False

if __name__ == '__main__':
    print("🚀 多人对话功能测试")
    test_simple_dialogue()