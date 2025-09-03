#!/usr/bin/env python3
"""
简化的多人对话TTS测试
"""

import sys
import os
sys.path.insert(0, 'src/backend')

from flask import Flask, request, jsonify
from flask_cors import CORS

# 创建Flask应用
app = Flask(__name__)
app.config['SECRET_KEY'] = 'test-secret-key'
CORS(app, origins="*")

# 导入TTS库
try:
    from TTS.api import TTS
    TTS_AVAILABLE = True
    print("✅ TTS库加载成功")
except ImportError as e:
    TTS_AVAILABLE = False
    print(f"❌ TTS库导入失败: {str(e)}")

# 简化的TTS实例
tts_instance = None

def init_tts():
    global tts_instance
    if not TTS_AVAILABLE:
        return False
    
    try:
        # 使用稳定的中文模型
        tts_instance = TTS('tts_models/zh-CN/baker/tacotron2-DDC-GST')
        print("✅ 中文TTS模型初始化成功")
        return True
    except Exception as e:
        print(f"❌ TTS模型初始化失败: {e}")
        return False

def parse_dialogue_text(text):
    """解析多人对话文本"""
    import re
    
    # 对话格式：[说话人名]对话内容
    dialogue_pattern = r'\[([^\]]+)\]([^[]+?)(?=\[|$)'
    matches = re.findall(dialogue_pattern, text)
    
    if not matches:
        return [{'speaker': 'default', 'text': text.strip()}]
    
    dialogue_segments = []
    for speaker, content in matches:
        speaker_name = speaker.strip()
        speech_content = content.strip()
        
        if speech_content:
            dialogue_segments.append({
                'speaker': speaker_name,
                'text': speech_content
            })
    
    return dialogue_segments

@app.route('/test_dialogue', methods=['POST'])
def test_dialogue():
    """测试多人对话功能"""
    try:
        data = request.get_json()
        text = data.get('text', '')
        
        if not tts_instance:
            return jsonify({'success': False, 'message': 'TTS未初始化'})
        
        # 解析对话
        dialogue_segments = parse_dialogue_text(text)
        print(f"🎭 解析到 {len(dialogue_segments)} 段对话")
        
        for i, segment in enumerate(dialogue_segments):
            print(f"  段落 {i+1}: [{segment['speaker']}] {segment['text']}")
        
        # 简化处理：合并所有文本
        if len(dialogue_segments) > 1:
            # 多人对话：为每个说话人添加标识
            combined_parts = []
            for segment in dialogue_segments:
                combined_parts.append(f"{segment['speaker']}说：{segment['text']}")
            
            combined_text = "。 ".join(combined_parts) + "。"
        else:
            combined_text = dialogue_segments[0]['text']
        
        print(f"🔄 合成文本: {combined_text}")
        
        # 生成音频
        import tempfile
        import uuid
        from datetime import datetime
        
        temp_dir = tempfile.gettempdir()
        audio_filename = f"dialogue_test_{uuid.uuid4().hex[:8]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"
        audio_path = os.path.join(temp_dir, audio_filename)
        
        tts_instance.tts_to_file(text=combined_text, file_path=audio_path)
        
        if os.path.exists(audio_path):
            file_size = os.path.getsize(audio_path)
            print(f"✅ 音频生成成功: {audio_filename}, 大小: {file_size} bytes")
            
            return jsonify({
                'success': True,
                'message': '多人对话音频生成成功',
                'data': {
                    'audio_filename': audio_filename,
                    'file_size': file_size,
                    'dialogue_segments': len(dialogue_segments),
                    'speakers': list(set([seg['speaker'] for seg in dialogue_segments])),
                    'combined_text': combined_text
                }
            })
        else:
            return jsonify({'success': False, 'message': '音频文件生成失败'})
            
    except Exception as e:
        print(f"❌ 测试失败: {str(e)}")
        return jsonify({'success': False, 'message': f'测试失败: {str(e)}'})

@app.route('/')
def index():
    return {
        'message': '简化的多人对话TTS测试服务', 
        'status': 'running',
        'tts_available': TTS_AVAILABLE,
        'tts_ready': tts_instance is not None
    }

if __name__ == '__main__':
    print("🎤 启动简化的多人对话TTS测试服务器...")
    
    if not init_tts():
        print("❌ TTS初始化失败，服务器仍将启动但功能受限")
    
    print("📝 访问 http://localhost:5003 查看服务状态")
    print("📝 使用 POST /test_dialogue 测试对话功能")
    app.run(host='0.0.0.0', port=5003, debug=True)