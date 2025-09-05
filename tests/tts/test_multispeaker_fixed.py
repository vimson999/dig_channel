#!/usr/bin/env python3
"""
测试修复后的多人对话功能
"""

import requests
import json
import time

def test_multispeaker_dialogue():
    """测试多人对话功能"""
    
    # 启动测试服务器
    import subprocess
    import sys
    import os
    
    # 启动TTS测试服务器
    print("🚀 启动TTS测试服务器...")
    server_cmd = [
        sys.executable,
        "test_main_tts_server.py"
    ]
    
    env = os.environ.copy()
    env['PYTHONPATH'] = '.'
    
    server_process = subprocess.Popen(
        server_cmd,
        cwd=os.getcwd(),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    
    # 等待服务器启动
    time.sleep(3)
    
    try:
        base_url = "http://localhost:5004"
        
        # 测试多人对话
        dialogue_texts = [
            "[张三]你好，我是张三[李四]我是李四，很高兴认识你",
            "[老师]今天我们学习语音合成[学生]老师，这很有趣[老师]是的，很实用",
            "[客服]您好，有什么可以帮您[客户]我想了解产品信息[客服]好的，我来为您介绍"
        ]
        
        print("🎭 测试多人对话功能")
        print("="*60)
        
        for i, dialogue_text in enumerate(dialogue_texts, 1):
            print(f"\n📝 测试 {i}: {dialogue_text}")
            print("-" * 40)
            
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
                        
                        print(f"✅ 多人对话成功 ({elapsed:.2f}s)")
                        print(f"   文件: {filename}")
                        print(f"   大小: {file_size} bytes")
                        
                        # 检查是否真的生成了多人对话效果
                        if file_size > 50000:  # 合理的音频文件大小
                            print("🎉 音频文件大小正常，多人对话可能成功！")
                        else:
                            print("⚠️  音频文件较小")
                            
                    else:
                        print(f"❌ 失败: {result.get('message', '未知错误')}")
                else:
                    print(f"❌ HTTP错误: {response.status_code}")
                    print(f"   响应: {response.text[:200]}")
                    
            except Exception as e:
                print(f"❌ 异常: {str(e)}")
            
            time.sleep(1)
        
        print(f"\n🎯 多人对话测试完成！")
        print(f"📝 检查点:")
        print(f"   1. 查看服务器日志是否显示音频效果处理")
        print(f"   2. 文件大小应该比单人对话更大（包含间隔和多个片段）")
        print(f"   3. 实际播放音频应该能听到不同的声音特征")
        
    finally:
        # 清理服务器进程
        try:
            server_process.terminate()
            server_process.wait(timeout=5)
        except:
            server_process.kill()
        print("🛑 TTS测试服务器已关闭")

if __name__ == '__main__':
    print("🎤 多人对话修复测试")
    test_multispeaker_dialogue()