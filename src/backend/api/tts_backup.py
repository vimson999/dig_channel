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
from utils.api_utils import handle_api_error

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
        'description': '英语语音合成，自然度较高',
        'type': 'single_speaker'
    },
    'glow_tts': {
        'name': 'Glow-TTS',
        'model_name': 'tts_models/en/ljspeech/glow-tts',
        'language': 'en',
        'description': '英语语音合成，速度较快',
        'type': 'single_speaker'
    },
    'chinese': {
        'name': 'Chinese TTS',
        'model_name': 'tts_models/zh-CN/baker/tacotron2-DDC-GST',
        'language': 'zh-CN',
        'description': '中文语音合成，质量稳定',
        'type': 'single_speaker'
    },
    'chinese_alt': {
        'name': 'Chinese TTS (快速)',
        'model_name': 'tts_models/multilingual/multi-dataset/your_tts',
        'language': 'zh-CN',
        'description': '多语言TTS模型，支持中文',
        'type': 'single_speaker'
    },
    'multi_speaker': {
        'name': 'Multi-Speaker FastSpeech2',
        'model_name': 'tts_models/en/ljspeech/fast_pitch',
        'language': 'en',
        'description': '快速英语语音合成',
        'type': 'single_speaker'
    },
    'voice_clone': {
        'name': 'YourTTS Clone',
        'model_name': 'tts_models/multilingual/multi-dataset/your_tts',
        'language': 'multilingual',
        'description': '声音克隆模型，支持多语言',
        'type': 'voice_cloning'
    },
    'dialogue_mode': {
        'name': '多人对话模式',
        'model_name': 'tts_models/zh-CN/baker/tacotron2-DDC-GST',  # 使用稳定的中文模型
        'language': 'zh-CN', 
        'description': '多人对话模式，自动为不同说话人分配语音',
        'type': 'dialogue_mode',
        'supports_dialogue': True
    }
}

# 全局TTS实例缓存
tts_instances = {}

def detect_language(text):
    """检测文本语言"""
    import re
    
    # 检测中文字符
    chinese_chars = re.findall(r'[\u4e00-\u9fff]+', text)
    chinese_ratio = sum(len(chars) for chars in chinese_chars) / len(text) if text else 0
    
    # 检测英文字符
    english_chars = re.findall(r'[a-zA-Z]+', text)
    english_ratio = sum(len(chars) for chars in english_chars) / len(text) if text else 0
    
    logger.debug(f"语言检测: 中文比例={chinese_ratio:.2f}, 英文比例={english_ratio:.2f}")
    
    # 根据比例判断语言
    if chinese_ratio > 0.3:  # 中文字符超过30%认为是中文
        return 'zh-CN'
    elif english_ratio > 0.5:  # 英文字符超过50%认为是英文
        return 'en'
    else:
        # 默认根据字符类型判断
        if chinese_ratio > english_ratio:
            return 'zh-CN'
        else:
            return 'en'

def get_suitable_model(text, requested_model='auto', is_dialogue_mode=False):
    """根据文本内容获取合适的模型"""
    # 如果用户明确选择了某个模型（不是auto），尊重用户的选择
    if requested_model != 'auto' and requested_model in TTS_MODELS:
        model_config = TTS_MODELS[requested_model]
        detected_lang = detect_language(text)
        model_lang = model_config.get('language', 'en')
        
        # 多人对话模式或多语言模型不需要语言兼容性检查
        if (is_dialogue_mode or 
            model_config.get('type') == 'voice_cloning' or 
            model_lang == 'multilingual'):
            logger.info(f"🎯 用户选择模型: {requested_model} ({model_config['name']}) - 多语言/对话模式")
            return requested_model
        
        # 对于单语言模型，检查语言兼容性
        if detected_lang == 'zh-CN' and model_lang == 'en':
            logger.warning(f"⚠️ 检测到中文文本但用户选择了英文单语言模型 {requested_model}，建议使用中文模型或多语言模型")
            # 只给出建议，但仍然使用用户选择的模型
            return requested_model
        elif detected_lang == 'en' and model_lang == 'zh-CN':
            logger.warning(f"⚠️ 检测到英文文本但用户选择了中文单语言模型 {requested_model}，建议使用英文模型或多语言模型")
            # 只给出建议，但仍然使用用户选择的模型
            return requested_model
        else:
            logger.info(f"🎯 用户选择模型: {requested_model} ({model_config['name']})")
            return requested_model
    
    # 只有在用户选择 'auto' 时才进行智能选择
    detected_lang = detect_language(text)
    logger.info(f"📝 智能选择模式 - 检测到文本语言: {detected_lang}")
    
    # 根据检测到的语言选择最佳模型
    if detected_lang == 'zh-CN':
        selected_model = 'chinese'  # 使用中文模型
    else:
        selected_model = 'tacotron2'  # 使用英文模型
    
    logger.info(f"🤖 智能选择结果: {selected_model} ({TTS_MODELS[selected_model]['name']})")
    return selected_model

def synthesize_dialogue_audio(dialogue_segments, tts, audio_path, model_key, data, dialogue_info):
    """合成多人对话音频"""
    import os
    import tempfile
    import wave
    import numpy as np
    from pydub import AudioSegment
    
    temp_dir = tempfile.gettempdir()
    segment_files = []
    
    try:
        # 为每个说话人分配不同的参数
        speaker_configs = {}
        unique_speakers = list(set([seg['speaker'] for seg in dialogue_segments]))
        
        # 为不同说话人设置不同的语音参数
        base_configs = [
            {'speed': 1.0, 'pitch': 0},      # 说话人1：正常
            {'speed': 0.9, 'pitch': -20},    # 说话人2：稍慢，音调低
            {'speed': 1.1, 'pitch': 20},     # 说话人3：稍快，音调高  
            {'speed': 0.95, 'pitch': 10},    # 说话人4：稍慢，音调稍高
            {'speed': 1.05, 'pitch': -10},   # 说话人5：稍快，音调稍低
        ]
        
        for i, speaker in enumerate(unique_speakers):
            speaker_configs[speaker] = base_configs[i % len(base_configs)]
        
        logger.info(f"🎭 开始合成 {len(dialogue_segments)} 段对话，涉及 {len(unique_speakers)} 个说话人")
        
        # 逐一合成每段对话
        combined_audio = AudioSegment.empty()
        
        for i, segment in enumerate(dialogue_segments):
            speaker = segment['speaker']
            text = segment['text']
            
            # 生成当前段落的音频文件
            segment_filename = f"dialogue_seg_{i}_{uuid.uuid4().hex[:6]}.wav"
            segment_path = os.path.join(temp_dir, segment_filename)
            
            logger.info(f"🗣️  合成片段 {i+1}: [{speaker}] {text[:30]}...")
            
            # 合成音频
            tts.tts_to_file(text=text, file_path=segment_path)
            
            # 加载音频并应用说话人特定的效果
            if os.path.exists(segment_path):
                segment_audio = AudioSegment.from_wav(segment_path)
                
                # 根据说话人配置调整音频
                config = speaker_configs.get(speaker, base_configs[0])
                
                # 调整语速
                if config['speed'] != 1.0:
                    segment_audio = segment_audio._spawn(
                        segment_audio.raw_data,
                        overrides={"frame_rate": int(segment_audio.frame_rate * config['speed'])}
                    ).set_frame_rate(segment_audio.frame_rate)
                
                # 调整音调（简单的音调变化）
                if config['pitch'] != 0:
                    # 这里只是一个简单的示例，实际音调调整需要更复杂的处理
                    new_sample_rate = int(segment_audio.frame_rate * (1 + config['pitch'] / 1000.0))
                    segment_audio = segment_audio._spawn(
                        segment_audio.raw_data,
                        overrides={"frame_rate": new_sample_rate}
                    ).set_frame_rate(segment_audio.frame_rate)
                
                # 添加停顿
                pause = AudioSegment.silent(duration=500)  # 0.5秒停顿
                
                # 拼接到总音频
                if i == 0:
                    combined_audio = segment_audio
                else:
                    combined_audio = combined_audio + pause + segment_audio
                
                segment_files.append(segment_path)
                logger.debug(f"✅ 片段 {i+1} 合成完成")
        
        # 导出最终音频
        combined_audio.export(audio_path, format="wav")
        logger.info(f"🎉 多人对话音频合成完成: {len(dialogue_segments)} 段，总时长 {len(combined_audio)/1000:.1f} 秒")
        
        # 更新对话信息
        dialogue_info['success'] = True
        dialogue_info['total_duration'] = len(combined_audio) / 1000.0
        dialogue_info['speaker_configs'] = speaker_configs
        
        return True
        
    except Exception as e:
        logger.error(f"❌ 多人对话合成失败: {str(e)}")
        # 如果失败，回退到单人模式
        combined_text = " ".join([seg['text'] for seg in dialogue_segments])
        tts.tts_to_file(text=combined_text, file_path=audio_path)
        return False
    finally:
        # 清理临时文件
        for seg_file in segment_files:
            try:
                if os.path.exists(seg_file):
                    os.remove(seg_file)
            except:
                pass

def parse_dialogue_text(text):
    """解析多人对话文本格式"""
    import re
    
    # 对话格式：[说话人名]对话内容
    dialogue_pattern = r'\[([^\]]+)\]([^[]+?)(?=\[|$)'
    matches = re.findall(dialogue_pattern, text)
    
    if not matches:
        # 如果没有找到对话格式，作为普通文本处理
        return [{'speaker': 'default', 'text': text.strip()}]
    
    dialogue_segments = []
    for speaker, content in matches:
        speaker_name = speaker.strip()
        speech_content = content.strip()
        
        if speech_content:  # 忽略空内容
            dialogue_segments.append({
                'speaker': speaker_name,
                'text': speech_content
            })
    
    return dialogue_segments

def clean_text_for_tts(text):
    """清理文本，避免TTS问题"""
    import re
    
    if not text:
        return ""
    
    # 移除多余的空白字符
    text = re.sub(r'\s+', ' ', text.strip())
    
    # 检测和移除文本重复（常见的前端问题）
    # 如果文本长度超过50字符，检查是否有重复的部分
    if len(text) > 50:
        # 尝试检测重复模式
        half_length = len(text) // 2
        first_half = text[:half_length].strip()
        second_half = text[half_length:].strip()
        
        # 如果前半部分和后半部分相似度很高，只保留前半部分
        if first_half and len(first_half) > 10:
            # 检查是否有明显的重复
            if first_half in second_half or second_half in first_half:
                text = first_half
                logger.info(f"📝 检测到重复文本，已清理: '{text[:50]}...'")
    
    # 限制文本长度，避免模型计算问题
    if len(text) > 500:
        text = text[:500]
        logger.warning(f"⚠️ 文本过长已截断至500字符")
    
    # 移除特殊字符，但保留基本标点
    # 保留中文字符、英文字符、数字、基本标点
    text = re.sub(r'[^\u4e00-\u9fff\w\s.,!?;:"\'()（）、。，！？；：""''—-]', '', text)
    
    # 确保句子以标点符号结尾，有助于TTS正确断句
    if text and not re.search(r'[.!?。！？]$', text.strip()):
        # 根据语言添加合适的结尾标点
        if detect_language(text) == 'zh-CN':
            text = text.strip() + '。'
        else:
            text = text.strip() + '.'
    
    # 移除可能导致重复的字符序列
    text = re.sub(r'(.)\1{3,}', r'\1', text)  # 移除连续4个以上相同字符
    
    # 最终检查：确保文本不为空且合理
    if not text or len(text.strip()) < 2:
        text = "请输入有效的文本内容。" if detect_language(text) == 'zh-CN' else "Please enter valid text content."
    
    return text.strip()

def estimate_duration(text, model_key):
    """估计语音时长"""
    if not text:
        return 0
    
    model_config = TTS_MODELS.get(model_key, {})
    language = model_config.get('language', 'en')
    
    # 根据语言使用不同的估算方式
    if language == 'zh-CN':
        # 中文：每个汉字约0.25秒，标点和空格约0.05秒
        chinese_chars = len([c for c in text if '\u4e00' <= c <= '\u9fff'])
        other_chars = len(text) - chinese_chars
        duration = chinese_chars * 0.25 + other_chars * 0.05
    else:
        # 英文：每个单词约0.35秒，每个字符约0.04秒
        words = len(text.split())
        chars = len(text)
        duration = words * 0.35 + chars * 0.02
    
    return max(duration, 0.5)  # 最少0.5秒

def get_tts_instance(model_key='tacotron2'):
    """获取TTS实例（带缓存）"""
    if not TTS_AVAILABLE:
        raise Exception("TTS库未安装或导入失败")
    
    if model_key not in TTS_MODELS:
        raise ValueError(f"不支持的模型: {model_key}")
    
    if model_key not in tts_instances:
        model_config = TTS_MODELS[model_key]
        logger.info(f"🔄 初始化TTS模型: {model_config['name']}")
        
        # 尝试多个可能的模型名称
        possible_models = [model_config['model_name']]
        
        # 根据模型类型添加备用选项
        if model_key == 'chinese':
            possible_models.extend([
                'tts_models/zh-CN/baker/tacotron2-DDC-GST',
                'tts_models/zh-CN/baker/tacotron2-DDC'
            ])
        elif model_key == 'chinese_alt':
            possible_models.extend([
                'tts_models/zh-CN/baker/tacotron2-DDC-GST'
            ])
        elif model_key in ['tacotron2', 'glow_tts', 'multi_speaker']:
            # 英语模型备选
            possible_models.extend([
                'tts_models/en/ljspeech/tacotron2-DDC',
                'tts_models/en/ljspeech/glow-tts',
                'tts_models/en/ljspeech/fast_pitch'
            ])
        
        success = False
        last_error = None
        
        for model_name in possible_models:
            try:
                logger.info(f"🔄 尝试模型: {model_name}")
                tts_instances[model_key] = TTS(model_name)
                logger.info(f"✅ TTS模型初始化成功: {model_config['name']} ({model_name})")
                success = True
                break
            except Exception as e:
                logger.warning(f"⚠️ 模型 {model_name} 初始化失败: {str(e)}")
                last_error = e
                continue
        
        if not success:
            logger.error(f"❌ 所有备选模型都初始化失败")
            raise last_error or Exception(f"无法初始化模型 {model_key}")
    
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
        # 添加自动选择选项
        models.append({
            'key': 'auto',
            'name': '智能选择 (推荐)',
            'language': 'auto',
            'description': '根据文本内容自动选择最合适的语音模型',
            'type': 'auto',
            'voice_options': [],
            'supports_emotions': False,
            'emotion_options': [],
            'supports_dialogue': False
        })
        
        for key, config in TTS_MODELS.items():
            models.append({
                'key': key,
                'name': config['name'],
                'language': config['language'],
                'description': config['description'],
                'type': config['type'],
                'voice_options': config.get('voice_options', []),
                'supports_emotions': config.get('supports_emotions', False),
                'emotion_options': config.get('emotion_options', []),
                'supports_dialogue': config.get('supports_dialogue', False)
            })
        
        return jsonify({
            'success': True,
            'message': '获取模型列表成功',
            'data': {
                'models': models,
                'default_model': 'auto'
            }
        })
        
    except Exception as e:
        logger.error(f"获取TTS模型列表失败: {str(e)}")
        return handle_api_error(500, '获取模型列表失败', {'error_details': str(e)})

@tts_bp.route('/speakers/<model_key>', methods=['GET'])
@cross_origin()
def get_speakers(model_key):
    """获取指定模型的说话人列表"""
    try:
        if not TTS_AVAILABLE:
            return jsonify({
                'success': False,
                'message': 'TTS功能不可用',
                'data': {}
            }), 503
        
        if model_key not in TTS_MODELS:
            return jsonify({
                'success': False,
                'message': '不支持的模型',
                'data': {}
            }), 400
        
        model_config = TTS_MODELS[model_key]
        if model_config['type'] != 'multi_speaker':
            return jsonify({
                'success': True,
                'message': '单说话人模型，无说话人列表',
                'data': {'speakers': []}
            })
        
        # 获取TTS实例和说话人列表
        tts = get_tts_instance(model_key)
        speakers = []
        
        if hasattr(tts, 'speakers') and tts.speakers:
            speakers = list(tts.speakers)
        
        return jsonify({
            'success': True,
            'message': '获取说话人列表成功',
            'data': {
                'speakers': speakers,
                'model': model_key,
                'model_name': model_config['name']
            }
        })
        
    except Exception as e:
        logger.error(f"获取说话人列表失败: {str(e)}")
        return handle_api_error(500, '获取说话人列表失败', {'error_details': str(e)})

@tts_bp.route('/upload-reference', methods=['POST'])
@cross_origin()
@require_login
def upload_reference_audio(current_user):
    """上传参考音频文件用于声音克隆"""
    try:
        if not TTS_AVAILABLE:
            return jsonify({
                'success': False,
                'message': 'TTS功能不可用',
                'data': {}
            }), 503
        
        # 检查文件是否存在
        if 'audio' not in request.files:
            return jsonify({
                'success': False,
                'message': '没有上传音频文件',
                'data': {}
            }), 400
        
        file = request.files['audio']
        if file.filename == '':
            return jsonify({
                'success': False,
                'message': '没有选择文件',
                'data': {}
            }), 400
        
        # 检查文件类型
        allowed_extensions = {'wav', 'mp3', 'flac', 'm4a'}
        if not ('.' in file.filename and file.filename.rsplit('.', 1)[1].lower() in allowed_extensions):
            return jsonify({
                'success': False,
                'message': '只支持 WAV, MP3, FLAC, M4A 格式的音频文件',
                'data': {}
            }), 400
        
        # 保存参考音频文件
        filename = secure_filename(file.filename)
        temp_dir = tempfile.gettempdir()
        reference_filename = f"ref_{uuid.uuid4().hex[:8]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{filename}"
        reference_path = os.path.join(temp_dir, reference_filename)
        
        file.save(reference_path)
        
        file_size = os.path.getsize(reference_path)
        logger.info(f"📁 保存参考音频: {reference_filename}, 大小: {file_size} bytes")
        
        return jsonify({
            'success': True,
            'message': '参考音频上传成功',
            'data': {
                'reference_filename': reference_filename,
                'reference_path': reference_path,
                'file_size': file_size,
                'original_filename': filename
            }
        })
        
    except Exception as e:
        logger.error(f"上传参考音频失败: {str(e)}")
        return handle_api_error(500, '上传参考音频失败', {'error_details': str(e)})

@tts_bp.route('/synthesize', methods=['POST'])
@cross_origin()
def synthesize_speech():
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
        
        original_text = data.get('text', '').strip()
        requested_model = data.get('model', 'auto')  # 默认为auto
        is_dialogue_mode = data.get('is_dialogue', False)  # 获取对话模式标志
        
        # 清理文本，避免TTS问题
        text = clean_text_for_tts(original_text)
        
        if text != original_text:
            logger.info(f"📝 文本已清理: '{original_text}' -> '{text}'")
        
        # 智能选择模型，传递对话模式参数
        model_key = get_suitable_model(text, requested_model, is_dialogue_mode)
        
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
        
        logger.info(f"🎤 开始语音合成: 模型={model_key}, 文本长度={len(text)}, 对话模式={is_dialogue_mode}")
        
        # 获取TTS实例
        tts = get_tts_instance(model_key)
        
        # 生成临时文件名
        temp_dir = tempfile.gettempdir()
        audio_filename = f"tts_{uuid.uuid4().hex[:8]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"
        audio_path = os.path.join(temp_dir, audio_filename)
        
        # 如果是对话模式，解析对话文本
        dialogue_segments = None
        if is_dialogue_mode:
            dialogue_segments = parse_dialogue_text(text)
            logger.info(f"🎭 解析到 {len(dialogue_segments)} 段对话")
            for i, segment in enumerate(dialogue_segments):
                logger.debug(f"  段落 {i+1}: [{segment['speaker']}] {segment['text'][:30]}...")
        
        # 记录对话信息，用于后续处理
        dialogue_info = {
            'is_dialogue': is_dialogue_mode,
            'segments': dialogue_segments or [],
            'segment_count': len(dialogue_segments) if dialogue_segments else 0
        }
        
        # 处理多人对话模式
        if is_dialogue_mode and dialogue_segments and len(dialogue_segments) > 1:
            # 使用多人对话合成
            try:
                dialogue_success = synthesize_dialogue_audio(dialogue_segments, tts, audio_path, model_key, data, dialogue_info)
                if dialogue_success:
                    logger.info("🎭 多人对话合成完成")
                    # 直接跳转到文件检查
                else:
                    logger.warning("⚠️ 多人对话合成失败，使用单人模式")
                    # 合并所有文本用单人模式合成
                    combined_text = " ".join([seg['text'] for seg in dialogue_segments])
                    tts.tts_to_file(text=combined_text, file_path=audio_path)
            except Exception as e:
                logger.error(f"❌ 对话合成异常，回退单人模式: {str(e)}")
                combined_text = " ".join([seg['text'] for seg in dialogue_segments]) 
                tts.tts_to_file(text=combined_text, file_path=audio_path)
        else:
            # 单人模式：使用原有的TTS合成逻辑
            # 根据模型类型进行不同的语音合成  
            model_config = TTS_MODELS[model_key]
            
            # 准备TTS参数，添加质量控制参数
            tts_kwargs = {
                'text': text,
                'file_path': audio_path
            }
            
            # 根据模型类型添加特定参数
            logger.info(f"🎯 使用模型: {model_config['name']}, 文本: '{text}'")

            if model_config['type'] == 'multi_speaker':
                speaker = data.get('speaker', None)
                if speaker:
                    tts_kwargs['speaker'] = speaker
                    logger.info(f"👤 使用指定说话人: {speaker}")
                else:
                    speakers = tts.speakers if hasattr(tts, 'speakers') and tts.speakers else None
                    if speakers:
                        tts_kwargs['speaker'] = speakers[0]
                        logger.info(f"👤 使用默认说话人: {speakers[0]}")
            elif model_config['type'] == 'voice_cloning':
                # 声音克隆模式，需要参考音频
                reference_audio = data.get('reference_audio_path')
                if reference_audio and os.path.exists(reference_audio):
                    tts_kwargs['speaker_wav'] = reference_audio
                    logger.info(f"🎤 使用参考音频: {reference_audio}")
            
            # 执行TTS合成 - 只调用一次避免重复
            logger.info("🔄 开始语音合成...")
            
            # 尝试合成，包含模型回退机制
            fallback_models = []
            detected_lang = detect_language(text)
            
            if model_key == 'chinese':
                fallback_models = ['chinese_alt']  # 中文模型失败时优先尝试其他中文模型
            elif model_key == 'chinese_alt':
                fallback_models = ['chinese']  # 备用中文模型失败时尝试主中文模型
            elif model_key in ['tacotron2', 'glow_tts', 'multi_speaker']:
                # 英文模型失败时的回退，但如果是中文文本，改用中文模型
                if detected_lang == 'zh-CN':
                    fallback_models = ['chinese', 'chinese_alt']
                else:
                    # 英文文本的英文模型回退
                    if model_key == 'tacotron2':
                        fallback_models = ['glow_tts']
                    elif model_key == 'glow_tts':
                        fallback_models = ['tacotron2']
                    elif model_key == 'multi_speaker':
                        fallback_models = ['tacotron2', 'glow_tts']
            elif model_key == 'auto':
                # 智能模式失败，根据检测到的语言选择回退
                if detected_lang == 'zh-CN':
                    fallback_models = ['chinese', 'chinese_alt']
                else:
                    fallback_models = ['tacotron2', 'glow_tts']
            
            max_attempts = 1 + len(fallback_models)
            success = False
            current_tts = tts
            current_model_name = model_config['name']
            
            for attempt in range(max_attempts):
                try:
                    if attempt == 0:
                        # 第一次尝试使用原始模型
                        current_tts.tts_to_file(**tts_kwargs)
                    else:
                        # 后续尝试使用回退模型
                        fallback_key = fallback_models[attempt - 1]
                        logger.info(f"🔄 尝试回退模型: {TTS_MODELS[fallback_key]['name']}")
                        current_tts = get_tts_instance(fallback_key)
                        current_model_name = TTS_MODELS[fallback_key]['name']
                        current_tts.tts_to_file(**tts_kwargs)
                    
                    success = True
                    break
                except Exception as attempt_error:
                    error_msg = str(attempt_error)
                    logger.warning(f"⚠️ 合成尝试 {attempt + 1} 失败: {error_msg}")
                    
                    # 特殊处理：如果是计算错误，尝试缩短文本
                    if "Kernel size" in error_msg and "input size" in error_msg:
                        if len(text) > 20:
                            # 文本过长导致的计算问题，尝试缩短
                            shorter_text = text[:len(text)//2].strip()
                            if not shorter_text.endswith(('.', '。', '!', '！', '?', '？')):
                                shorter_text += '。' if detect_language(text) == 'zh-CN' else '.'
                            tts_kwargs['text'] = shorter_text
                            logger.info(f"📝 文本过长，尝试缩短版本: '{shorter_text}'")
                    
                    if attempt == max_attempts - 1:
                        raise attempt_error
            
            if success:
                logger.info(f"✅ 语音合成成功: {audio_path} (使用模型: {current_model_name})")
            else:
                raise Exception("所有合成尝试都失败")
        
        # 检查文件是否生成（对话模式和单人模式都需要检查）
        if not os.path.exists(audio_path):
            raise Exception("语音文件生成失败")
        
        
        file_size = os.path.getsize(audio_path)
        
        # 检查文件大小是否合理（避免过长的音频文件）
        expected_min_size = len(text) * 1000  # 每字符预期至少1KB
        expected_max_size = len(text) * 5000  # 每字符预期最多5KB
        
        if file_size < expected_min_size:
            logger.warning(f"⚠️ 音频文件可能过小: {file_size} bytes (预期最少: {expected_min_size} bytes)")
        elif file_size > expected_max_size:
            logger.warning(f"⚠️ 音频文件可能过大: {file_size} bytes (预期最多: {expected_max_size} bytes)")
        
        logger.info(f"📁 生成音频文件: {audio_filename}, 大小: {file_size} bytes")
        
        # 计算准确的时长估算
        duration_estimate = estimate_duration(text, model_key)
        
        # 准备返回数据
        response_data = {
            'audio_filename': audio_filename,
            'file_size': file_size,
            'text': text,
            'model': model_key,
            'model_name': TTS_MODELS[model_key]['name'],
            'duration_estimate': duration_estimate,
            'detected_language': detect_language(text) if requested_model == 'auto' else None,
            'original_model_request': requested_model,
            'auto_selected': requested_model == 'auto'
        }
        
        # 添加对话相关信息
        if dialogue_info['is_dialogue']:
            response_data['dialogue_segments'] = dialogue_info['segment_count']
            response_data['is_dialogue'] = True
            if dialogue_info['segments']:
                # 创建说话人映射（演示用）
                unique_speakers = list(set([seg['speaker'] for seg in dialogue_info['segments']]))
                response_data['speaker_mapping'] = {speaker: f"Voice_{i+1}" for i, speaker in enumerate(unique_speakers)}
        
        return jsonify({
            'success': True,
            'message': '语音合成成功',
            'data': response_data
        })
        
    except Exception as e:
        logger.error(f"语音合成失败: {str(e)}")
        return handle_api_error(500, '语音合成失败', {'error_details': str(e)})

@tts_bp.route('/download/<filename>', methods=['GET'])
@cross_origin()
def download_audio(filename):
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
        
        logger.info(f"📥 下载音频文件: {safe_filename}")
        
        # 发送文件
        return send_file(
            file_path,
            as_attachment=True,
            download_name=safe_filename,
            mimetype='audio/wav'
        )
        
    except Exception as e:
        logger.error(f"下载音频文件失败: {str(e)}")
        return handle_api_error(e, 500, '下载失败', {'error_details': str(e)})

@tts_bp.route('/stream/<filename>', methods=['GET'])
@cross_origin()
def stream_audio(filename):
    """流式播放音频文件（不需要认证）"""
    try:
        # 安全检查文件名
        safe_filename = secure_filename(filename)
        if not safe_filename or not safe_filename.endswith('.wav'):
            return 'Invalid filename', 400
        
        # 检查文件是否存在
        temp_dir = tempfile.gettempdir()
        file_path = os.path.join(temp_dir, safe_filename)
        
        if not os.path.exists(file_path):
            return 'File not found', 404
        
        logger.info(f"🎧 流式播放音频: {safe_filename}")
        
        # 流式发送文件，用于播放器
        return send_file(
            file_path,
            as_attachment=False,  # 不作为附件下载
            mimetype='audio/wav'
        )
        
    except Exception as e:
        logger.error(f"流式播放音频失败: {str(e)}")
        return 'Stream error', 500

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
        return handle_api_error(500, '清理临时文件失败', {'error_details': str(e)})

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
        return handle_api_error(500, '获取TTS状态失败', {'error_details': str(e)})
