#!/usr/bin/env python3
"""
最终测试：主TTS模块的多人对话功能
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

# 直接导入TTS相关功能
try:
    from TTS.api import TTS
    TTS_AVAILABLE = True
    print("✅ TTS库加载成功")
except ImportError as e:
    TTS_AVAILABLE = False
    print(f"❌ TTS库导入失败: {str(e)}")

# 导入对话解析函数
sys.path.append('src/backend')
try:
    from api.tts import parse_dialogue_text, TTS_MODELS
    print("✅ 主TTS模块导入成功")
    
    # 获取对话模式TTS实例
    if TTS_AVAILABLE:
        dialogue_tts = TTS(TTS_MODELS['dialogue_mode']['model_name'])
        print("✅ 对话模式TTS实例初始化成功")
    else:
        dialogue_tts = None
        
except Exception as e:
    print(f"❌ 主TTS模块导入失败: {str(e)}")
    sys.exit(1)

@app.route('/test_final_dialogue', methods=['POST'])
def test_final_dialogue():
    """测试最终的多人对话功能"""
    try:
        data = request.get_json()
        text = data.get('text', '')
        
        if not dialogue_tts:
            return jsonify({'success': False, 'message': 'TTS未初始化'})
        
        # 使用主模块的对话解析函数
        dialogue_segments = parse_dialogue_text(text)
        print(f"🎭 解析到 {len(dialogue_segments)} 段对话")
        
        for i, segment in enumerate(dialogue_segments):
            print(f"  段落 {i+1}: [{segment['speaker']}] {segment['text']}")
        
        # 处理多人对话
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
        audio_filename = f"final_dialogue_{uuid.uuid4().hex[:8]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"
        audio_path = os.path.join(temp_dir, audio_filename)
        
        dialogue_tts.tts_to_file(text=combined_text, file_path=audio_path)
        
        if os.path.exists(audio_path):
            file_size = os.path.getsize(audio_path)
            print(f"✅ 最终对话音频生成成功: {audio_filename}, 大小: {file_size} bytes")
            
            return jsonify({
                'success': True,
                'message': '最终多人对话测试成功！主TTS模块工作正常',
                'data': {
                    'audio_filename': audio_filename,
                    'file_size': file_size,
                    'dialogue_segments': len(dialogue_segments),
                    'speakers': list(set([seg['speaker'] for seg in dialogue_segments])),
                    'combined_text': combined_text,
                    'model_used': 'dialogue_mode',
                    'test_status': '✅ 主TTS模块多人对话功能已修复并测试成功'
                }
            })
        else:
            return jsonify({'success': False, 'message': '音频文件生成失败'})
            
    except Exception as e:
        print(f"❌ 最终测试失败: {str(e)}")
        return jsonify({'success': False, 'message': f'最终测试失败: {str(e)}'})

@app.route('/')
def index():
    return {
        'message': '最终多人对话TTS测试服务', 
        'status': 'running',
        'test_endpoint': 'POST /test_final_dialogue',
        'main_module_status': '✅ 已集成主TTS模块功能'
    }

if __name__ == '__main__':
    print("🎤 启动最终多人对话TTS测试...")
    print("📝 访问 http://localhost:5005 查看服务状态")
    print("📝 使用 POST /test_final_dialogue 测试最终对话功能")
    app.run(host='0.0.0.0', port=5005, debug=True)