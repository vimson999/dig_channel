#!/usr/bin/env python3
"""
调试TTS文本处理问题
"""

import sys
import os
sys.path.insert(0, 'src/backend')

try:
    from TTS.api import TTS
    print("✅ TTS库加载成功")
    
    # 初始化中文TTS模型
    tts = TTS('tts_models/zh-CN/baker/tacotron2-DDC-GST')
    print("✅ 中文TTS模型初始化成功")
    
    # 测试简单文本
    test_text = "这是一个测试文本"
    print(f"🔤 测试文本: '{test_text}'")
    
    # 生成临时文件
    import tempfile
    import uuid
    from datetime import datetime
    
    temp_dir = tempfile.gettempdir()
    audio_filename = f"debug_tts_{uuid.uuid4().hex[:8]}.wav"
    audio_path = os.path.join(temp_dir, audio_filename)
    
    print(f"📁 输出文件: {audio_path}")
    
    # 进行语音合成（捕获TTS内部日志）
    print("🔄 开始语音合成...")
    tts.tts_to_file(text=test_text, file_path=audio_path)
    print("✅ 语音合成完成")
    
    # 检查文件
    if os.path.exists(audio_path):
        file_size = os.path.getsize(audio_path)
        print(f"📈 生成文件大小: {file_size} bytes")
        
        # 清理文件
        os.remove(audio_path)
        print("🗑️ 临时文件已清理")
    else:
        print("❌ 文件生成失败")

except Exception as e:
    print(f"❌ 调试失败: {str(e)}")
    import traceback
    traceback.print_exc()