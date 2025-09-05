#!/usr/bin/env python3
"""
测试真实的TTS问题，模拟API调用
"""

import sys
import os
sys.path.insert(0, 'src/backend')
import tempfile
import requests
import json

# 模拟用户输入的文本处理
def simulate_frontend_processing(text):
    """模拟前端可能的文本处理"""
    # 前端可能的处理
    processed = text.strip()
    return processed

def simulate_backend_processing(text):
    """模拟后端当前的文本处理逻辑"""
    import re
    
    print(f"🔍 [DEBUG] 接收到的原始文本: '{text}'")
    
    # 当前后端的处理逻辑
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'[\u200b-\u200d\ufeff]', '', text)
    print(f"🔍 [DEBUG] 清理后的文本: '{text}'")
    
    # 当前的句号处理逻辑
    clean_text = text.strip().rstrip('。！？.!?') + '。'
    print(f"🔍 [DEBUG] 最终合成文本: '{clean_text}'")
    
    return clean_text

def test_tts_synthesis_direct(text):
    """直接测试TTS合成"""
    try:
        from TTS.api import TTS
        
        print(f"\n🎤 测试TTS合成: '{text}'")
        
        # 使用中文模型
        tts = TTS('tts_models/zh-CN/baker/tacotron2-DDC-GST')
        
        # 生成音频文件
        temp_dir = tempfile.gettempdir()
        audio_path = os.path.join(temp_dir, f"test_direct.wav")
        
        print(f"🔍 开始合成...")
        tts.tts_to_file(text=text, file_path=audio_path)
        
        # 检查文件大小
        if os.path.exists(audio_path):
            file_size = os.path.getsize(audio_path)
            print(f"📊 文件大小: {file_size} bytes")
            
            # 分析结果
            if file_size > 500000:  # 异常大的文件
                print("❌ 检测到异常：文件过大，可能重复播放")
            else:
                print("✅ 文件大小正常")
                
            # 删除测试文件
            os.remove(audio_path)
        else:
            print("❌ 文件生成失败")
            
    except Exception as e:
        print(f"❌ 合成失败: {str(e)}")

def test_issue_scenarios():
    """测试用户报告的问题场景"""
    
    print("="*60)
    print("🚨 测试用户报告的问题场景")
    print("="*60)
    
    # 用户可能输入的各种文本
    test_cases = [
        "你好世界",
        "这是一个测试文本",
        "中文语音合成测试",
        "测试文本内容",
        "今天天气很好",
    ]
    
    for text in test_cases:
        print(f"\n" + "-"*40)
        print(f"测试用例: '{text}'")
        print("-"*40)
        
        # 模拟前端处理
        frontend_text = simulate_frontend_processing(text)
        print(f"前端处理后: '{frontend_text}'")
        
        # 模拟后端处理
        backend_text = simulate_backend_processing(frontend_text)
        
        # 直接测试TTS
        test_tts_synthesis_direct(backend_text)

if __name__ == '__main__':
    print("🔍 测试TTS重复播放问题的根本原因")
    test_issue_scenarios()