"""
TTS (Text-to-Speech) API 端点
提供文本转语音功能
"""

import os
import uuid
import tempfile
from datetime import datetime
from flask import Blueprint, request, jsonify, send_file
from flask_cors import cross_origin
from werkzeug.utils import secure_filename
import logging

from utils.colored_logger import setup_colored_logger
from utils.auth_utils import require_login
from middleware.error_handler import handle_api_error

logger = setup_colored_logger("TTS_API")

# 创建蓝图
tts_bp = Blueprint('tts', __name__, url_prefix='/api/v1/tts')

# 导入TTS库（延迟导入避免启动时错误）
try:
    from TTS.api import TTS
    TTS_AVAILABLE = True
    logger.info("✅ TTS库加载成功")
except ImportError as e:
    TTS_AVAILABLE = False
    logger.error(f"❌ TTS库导入失败: {str(e)}")

# TTS模型配置
TTS_MODELS = {
    'tacotron2': {
        'name': 'Tacotron2',
        'model_name': 'tts_models/en/ljspeech/tacotron2-DDC',
        'language': 'en',
        'description': '英语语音合成，自然度较高'
    },
    'glow_tts': {
        'name': 'Glow-TTS',
        'model_name': 'tts_models/en/ljspeech/glow-tts',
        'language': 'en',
        'description': '英语语音合成，速度较快'
    },
    'chinese': {
        'name': 'Chinese TTS',
        'model_name': 'tts_models/zh-CN/baker/tacotron2-DDC-GST',
        'language': 'zh-CN',
        'description': '中文语音合成'
    }
}

# 全局TTS实例缓存
tts_instances = {}

def get_tts_instance(model_key='tacotron2'):
    """获取TTS实例（带缓存）"""
    if not TTS_AVAILABLE:
        raise Exception("TTS库未安装或导入失败")
    
    if model_key not in TTS_MODELS:
        raise ValueError(f"不支持的模型: {model_key}")
    
    if model_key not in tts_instances:
        model_config = TTS_MODELS[model_key]
        logger.info(f"🔄 初始化TTS模型: {model_config['name']}")
        try:
            tts_instances[model_key] = TTS(model_config['model_name'])
            logger.info(f"✅ TTS模型初始化成功: {model_config['name']}")
        except Exception as e:
            logger.error(f"❌ TTS模型初始化失败: {str(e)}")
            raise
    
    return tts_instances[model_key]

@tts_bp.route('/models', methods=['GET'])
@cross_origin()
def get_models():
    """获取可用的TTS模型列表"""
    try:
        if not TTS_AVAILABLE:
            return jsonify({
                'success': False,
                'message': 'TTS功能不可用，请检查TTS库安装',
                'data': {}
            }), 503
        
        models = []
        for key, config in TTS_MODELS.items():
            models.append({
                'key': key,
                'name': config['name'],
                'language': config['language'],
                'description': config['description']
            })
        
        return jsonify({
            'success': True,
            'message': '获取模型列表成功',
            'data': {
                'models': models,
                'default_model': 'tacotron2'
            }
        })
        
    except Exception as e:
        logger.error(f"获取TTS模型列表失败: {str(e)}")
        return handle_api_error(e)

@tts_bp.route('/synthesize', methods=['POST'])
@cross_origin()
@require_login
def synthesize_speech(current_user):
    """合成语音"""
    try:
        if not TTS_AVAILABLE:
            return jsonify({
                'success': False,
                'message': 'TTS功能不可用，请检查TTS库安装',
                'data': {}
            }), 503
        
        # 获取请求数据
        data = request.get_json()
        if not data:
            return jsonify({
                'success': False,
                'message': '请求数据格式错误',
                'data': {}
            }), 400
        
        text = data.get('text', '').strip()
        model_key = data.get('model', 'tacotron2')
        voice_speed = data.get('speed', 1.0)
        
        # 验证输入
        if not text:
            return jsonify({
                'success': False,
                'message': '文本内容不能为空',
                'data': {}
            }), 400
        
        if len(text) > 1000:
            return jsonify({
                'success': False,
                'message': '文本长度不能超过1000字符',
                'data': {}
            }), 400
        
        logger.info(f"🎤 开始语音合成: 用户={current_user.username}, 模型={model_key}, 文本长度={len(text)}")
        
        # 获取TTS实例
        tts = get_tts_instance(model_key)
        
        # 生成临时文件名
        temp_dir = tempfile.gettempdir()
        audio_filename = f"tts_{uuid.uuid4().hex[:8]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"
        audio_path = os.path.join(temp_dir, audio_filename)
        
        # 语音合成
        try:
            tts.tts_to_file(text=text, file_path=audio_path)
            logger.info(f"✅ 语音合成成功: {audio_path}")
        except Exception as e:
            logger.error(f"❌ 语音合成失败: {str(e)}")
            raise Exception(f"语音合成失败: {str(e)}")
        
        # 检查文件是否生成
        if not os.path.exists(audio_path):
            raise Exception("语音文件生成失败")
        
        file_size = os.path.getsize(audio_path)
        logger.info(f"📁 生成音频文件: {audio_filename}, 大小: {file_size} bytes")
        
        return jsonify({
            'success': True,
            'message': '语音合成成功',
            'data': {
                'audio_filename': audio_filename,
                'file_size': file_size,
                'text': text,
                'model': model_key,
                'model_name': TTS_MODELS[model_key]['name'],
                'duration_estimate': len(text) * 0.1  # 粗略估计时长
            }
        })
        
    except Exception as e:
        logger.error(f"语音合成失败: {str(e)}")
        return handle_api_error(e)

@tts_bp.route('/download/<filename>', methods=['GET'])
@cross_origin()
@require_login
def download_audio(current_user, filename):
    """下载音频文件"""
    try:
        # 安全检查文件名
        safe_filename = secure_filename(filename)
        if not safe_filename or not safe_filename.endswith('.wav'):
            return jsonify({
                'success': False,
                'message': '无效的文件名',
                'data': {}
            }), 400
        
        # 检查文件是否存在
        temp_dir = tempfile.gettempdir()
        file_path = os.path.join(temp_dir, safe_filename)
        
        if not os.path.exists(file_path):
            return jsonify({
                'success': False,
                'message': '文件不存在或已过期',
                'data': {}
            }), 404
        
        logger.info(f"📥 下载音频文件: {safe_filename}, 用户: {current_user.username}")
        
        # 发送文件
        return send_file(
            file_path,
            as_attachment=True,
            download_name=safe_filename,
            mimetype='audio/wav'
        )
        
    except Exception as e:
        logger.error(f"下载音频文件失败: {str(e)}")
        return handle_api_error(e)

@tts_bp.route('/cleanup', methods=['POST'])
@cross_origin()
@require_login
def cleanup_temp_files(current_user):
    """清理临时文件"""
    try:
        temp_dir = tempfile.gettempdir()
        cleaned_count = 0
        
        # 清理超过1小时的TTS临时文件
        import time
        current_time = time.time()
        
        for filename in os.listdir(temp_dir):
            if filename.startswith('tts_') and filename.endswith('.wav'):
                file_path = os.path.join(temp_dir, filename)
                try:
                    # 检查文件修改时间
                    if os.path.getmtime(file_path) < current_time - 3600:  # 1小时
                        os.remove(file_path)
                        cleaned_count += 1
                        logger.debug(f"🗑️ 清理过期文件: {filename}")
                except Exception as e:
                    logger.warning(f"清理文件失败: {filename}, 错误: {str(e)}")
        
        logger.info(f"🧹 临时文件清理完成: 清理了 {cleaned_count} 个文件")
        
        return jsonify({
            'success': True,
            'message': f'清理完成，删除了 {cleaned_count} 个过期文件',
            'data': {
                'cleaned_count': cleaned_count
            }
        })
        
    except Exception as e:
        logger.error(f"清理临时文件失败: {str(e)}")
        return handle_api_error(e)

@tts_bp.route('/status', methods=['GET'])
@cross_origin()
def get_tts_status():
    """获取TTS服务状态"""
    try:
        status = {
            'tts_available': TTS_AVAILABLE,
            'models_loaded': len(tts_instances),
            'available_models': list(TTS_MODELS.keys())
        }
        
        if TTS_AVAILABLE:
            try:
                # 测试默认模型
                test_tts = get_tts_instance('tacotron2')
                status['default_model_ready'] = True
            except Exception:
                status['default_model_ready'] = False
        else:
            status['default_model_ready'] = False
        
        return jsonify({
            'success': True,
            'message': 'TTS状态获取成功',
            'data': status
        })
        
    except Exception as e:
        logger.error(f"获取TTS状态失败: {str(e)}")
        return handle_api_error(e)