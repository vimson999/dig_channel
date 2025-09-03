#!/usr/bin/env python3
"""
调试TTS文本重复问题
"""

import sys
import os
sys.path.insert(0, 'src/backend')

def test_tts_text_processing():
    """测试TTS文本处理和合成"""
    try:
        from TTS.api import TTS
        import tempfile
        
        # 初始化中文TTS模型
        print("🔄 初始化中文TTS模型...")
        tts = TTS('tts_models/zh-CN/baker/tacotron2-DDC-GST')
        print("✅ 模型加载成功")
        
        # 测试文本
        test_texts = [
            "测试文本",
            "测试文本。",
            "这是一个测试，看看会不会重复",
            "这是一个测试，看看会不会重复。",
        ]
        
        for i, text in enumerate(test_texts):
            print(f"\n🎯 测试文本 {i+1}: '{text}'")
            
            # 生成音频文件
            temp_dir = tempfile.gettempdir()
            audio_path = os.path.join(temp_dir, f"debug_test_{i+1}.wav")
            
            print(f"🔍 开始合成...")
            tts.tts_to_file(text=text, file_path=audio_path)
            
            # 检查文件大小
            if os.path.exists(audio_path):
                file_size = os.path.getsize(audio_path)
                print(f"📁 生成文件: {audio_path}")
                print(f"📊 文件大小: {file_size} bytes")
                
                # 删除测试文件
                os.remove(audio_path)
            else:
                print("❌ 文件生成失败")
                
    except Exception as e:
        print(f"❌ 测试失败: {str(e)}")

def test_current_text_cleaning():
    """测试当前的文本清理逻辑"""
    print("\n" + "="*50)
    print("🧹 测试当前文本清理逻辑")
    print("="*50)
    
    import re
    
    test_cases = [
        "测试文本",
        "测试文本。",
        "测试文本！",
        "测试文本？",
        "测试文本。。。",
        "测试 文本",
        "测试\n文本",
        "测试\t文本"
    ]
    
    for text in test_cases:
        print(f"\n原始: {repr(text)}")
        
        # 当前的清理逻辑
        cleaned = re.sub(r'\s+', ' ', text).strip()
        cleaned = re.sub(r'[\u200b-\u200d\ufeff]', '', cleaned)
        print(f"清理后: {repr(cleaned)}")
        
        # 当前的句号添加逻辑
        final = cleaned.strip().rstrip('。！？.!?') + '。'
        print(f"添加句号: {repr(final)}")

if __name__ == '__main__':
    print("🔍 调试TTS文本重复问题")
    
    # 测试文本清理
    test_current_text_cleaning()
    
    # 测试TTS合成
    print("\n" + "="*50)
    print("🎤 测试TTS语音合成")
    print("="*50)
    test_tts_text_processing()