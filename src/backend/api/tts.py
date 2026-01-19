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
TTS_COQUI_AVAILABLE = False
CHAT_TTS_AVAILABLE = False

try:
    from TTS.api import TTS
    TTS_COQUI_AVAILABLE = True
    logger.info("✅ Coqui TTS库加载成功")
except ImportError as e:
    logger.error(f"❌ Coqui TTS库导入失败: {str(e)}")

try:
    from services.chat_tts_service import chat_tts_service
    CHAT_TTS_AVAILABLE = True
    logger.info("✅ ChatTTS服务加载成功")
except ImportError as e:
    logger.warning(f"⚠️ ChatTTS服务导入失败: {str(e)}")

TTS_AVAILABLE = TTS_COQUI_AVAILABLE or CHAT_TTS_AVAILABLE

# TTS模型配置
TTS_MODELS = {
    'chattts_v1': {
        'name': '[中/英] [多人] ChatTTS (V1)',
        'model_name': 'chattts',
        'language': 'zh-CN',
        'description': '专为对话优化的TTS，音质自然，支持多人',
        'is_chattts': True
    },
    'tacotron2': {
        'name': '[英文] [单人] Tacotron2 (English)',
        'model_name': 'tts_models/en/ljspeech/tacotron2-DDC',
        'language': 'en',
        'description': '经典英语语音，自然度一般'
    },
    'your_tts': {
        'name': '[多语] [多人] YourTTS (高质量多语言)',
        'model_name': 'tts_models/multilingual/multi-dataset/your_tts',
        'language': 'multilingual',
        'description': '基于VITS架构，支持中英等多语言，音质极佳且支持多说话人',
        'is_vits': True
    },
    'chinese': {
        'name': '[中文] [单人] 中文女声 (Baker)',
        'model_name': 'tts_models/zh-CN/baker/tacotron2-DDC-GST',
        'language': 'zh-CN',
        'description': '标准中文合成（标贝女声）'
    },
    'dialogue_mode': {
        'name': '[多语] [多人] YourTTS 对话模式',
        'model_name': 'tts_models/multilingual/multi-dataset/your_tts',
        'language': 'multilingual',
        'description': '使用YourTTS原生支持的多说话人对话',
        'is_dialogue': True,
        'is_vits': True
    }
}

# 全局TTS实例缓存
tts_instances = {}

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

def apply_speaker_effects(audio_path, speaker_name, speaker_index):
    """为不同说话人应用音频效果来模拟多人对话"""
    try:
        from pydub import AudioSegment
        import tempfile
        import uuid
        
        # 加载音频
        audio = AudioSegment.from_wav(audio_path)
        
        # 根据说话人索引应用不同效果
        if speaker_index % 4 == 0:
            # 说话人1：原声，稍微提高音调
            modified_audio = audio._spawn(audio.raw_data, overrides={
                "frame_rate": int(audio.frame_rate * 1.1)
            }).set_frame_rate(audio.frame_rate)
            logger.info(f"🎵 [{speaker_name}] 应用音调提升效果")
            
        elif speaker_index % 4 == 1:
            # 说话人2：降低音调
            modified_audio = audio._spawn(audio.raw_data, overrides={
                "frame_rate": int(audio.frame_rate * 0.9)
            }).set_frame_rate(audio.frame_rate)
            logger.info(f"🎵 [{speaker_name}] 应用音调降低效果")
            
        elif speaker_index % 4 == 2:
            # 说话人3：加快语速
            modified_audio = audio.speedup(playback_speed=1.15)
            logger.info(f"🎵 [{speaker_name}] 应用语速加快效果")
            
        else:
            # 说话人4：减慢语速
            modified_audio = audio.speedup(playback_speed=0.9)
            logger.info(f"🎵 [{speaker_name}] 应用语速减慢效果")
        
        # 保存处理后的音频
        temp_dir = tempfile.gettempdir()
        processed_filename = f"processed_{speaker_index}_{uuid.uuid4().hex[:6]}.wav"
        processed_path = os.path.join(temp_dir, processed_filename)
        
        modified_audio.export(processed_path, format="wav")
        logger.info(f"✅ [{speaker_name}] 音频效果处理完成")
        
        return processed_path
        
    except ImportError:
        logger.warning("⚠️ pydub未安装，跳过音频效果处理")
        return audio_path
    except Exception as e:
        logger.error(f"❌ 音频效果处理失败: {str(e)}")
        return audio_path

def combine_audio_segments(segment_paths, output_path):
    """合并多个音频片段为一个完整的对话音频"""
    try:
        from pydub import AudioSegment
        
        if not segment_paths:
            raise Exception("没有音频片段需要合并")
        
        # 加载第一个片段
        combined_audio = AudioSegment.from_wav(segment_paths[0])
        logger.info(f"🔗 开始合并 {len(segment_paths)} 个音频片段")
        
        # 依次合并其他片段（添加短暂间隔）
        silence = AudioSegment.silent(duration=300)  # 300ms间隔
        
        for i, segment_path in enumerate(segment_paths[1:], 1):
            segment_audio = AudioSegment.from_wav(segment_path)
            combined_audio = combined_audio + silence + segment_audio
            logger.debug(f"   合并片段 {i+1}/{len(segment_paths)}")
        
        # 导出最终音频
        combined_audio.export(output_path, format="wav")
        logger.info(f"✅ 音频合并完成: {output_path}")
        
        return output_path
        
    except ImportError:
        logger.error("❌ pydub未安装，无法合并音频片段")
        # 如果无法合并，至少返回第一个片段
        if segment_paths:
            import shutil
            shutil.copy2(segment_paths[0], output_path)
            return output_path
        raise Exception("音频合并失败：缺少必要库")
        
    except Exception as e:
        logger.error(f"❌ 音频合并失败: {str(e)}")
        raise

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
        return handle_api_error(500, f"获取TTS模型列表失败: {str(e)}")

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
        
        # 调试：记录接收到的原始文本
        logger.info(f"🔍 [DEBUG] 接收到的原始文本: '{text}'")
        
        # 文本清理：移除可能导致TTS异常的字符
        import re
        # 移除多余的空白字符和换行符
        text = re.sub(r'\s+', ' ', text).strip()
        # 移除特殊的Unicode字符（如零宽字符）
        text = re.sub(r'[\u200b-\u200d\ufeff]', '', text)
        # 确保文本不为空且合理
        if len(text.replace(' ', '')) == 0:
            return jsonify({
                'success': False,
                'message': '文本内容无效或为空',
                'data': {}
            }), 400
        
        logger.info(f"🔍 [DEBUG] 清理后的文本: '{text}'")
        
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
        
        # 获取TTS实例 (仅非ChatTTS模型需要)
        tts = None
        if model_key != 'chattts_v1':
            tts = get_tts_instance(model_key)
        elif not CHAT_TTS_AVAILABLE:
             raise Exception("ChatTTS 服务不可用")
        
        # 生成临时文件名
        temp_dir = tempfile.gettempdir()
        audio_filename = f"tts_{uuid.uuid4().hex[:8]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"
        audio_path = os.path.join(temp_dir, audio_filename)
        
        # 语音合成
        try:
            logger.info(f"🔍 [DEBUG] 开始合成文本: '{text}' (长度: {len(text)})")
            
            if model_key == 'chattts_v1':
                # ChatTTS 合成逻辑
                chat_tts_service.synthesize(text, output_path=audio_path)
            else:
                # 强化文本处理
                clean_text = text.strip()
                if len(clean_text) > 0:
                    clean_text = re.sub(r'[。！？.!?]{2,}', '。', clean_text)
                    if not any(clean_text.endswith(p) for p in '。！？.!?'):
                        clean_text += '。'
                    
                    # 准备合成参数
                    synth_kwargs = {'text': clean_text, 'file_path': audio_path}
                    
                    # 特殊处理 YourTTS 参数
                    if model_key in ['your_tts', 'dialogue_mode']:
                        # 检查模型支持的语言
                        target_lang = 'zh-cn'
                        if hasattr(tts, 'languages') and tts.languages:
                            if target_lang not in tts.languages:
                                logger.warning(f"⚠️ 模型不支持中文 ({target_lang})，可用语言: {tts.languages}")
                                # 尝试回退到英语
                                if 'en' in tts.languages:
                                    target_lang = 'en'
                                else:
                                    target_lang = tts.languages[0]
                                logger.info(f"🔄 回退到语言: {target_lang}")
                        
                        synth_kwargs['language'] = target_lang
                        
                        # 尝试获取默认说话人
                        if hasattr(tts, 'speakers') and tts.speakers:
                            synth_kwargs['speaker'] = tts.speakers[0]
                        
                        logger.info(f"✨ 使用 VITS 增强合成: speaker={synth_kwargs.get('speaker')}, lang={synth_kwargs.get('language')}")
                    
                    # 执行合成
                    tts.tts_to_file(**synth_kwargs)
                else:
                    tts.tts_to_file(text="测试。", file_path=audio_path)
                
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
        return handle_api_error(500, f"语音合成失败: {str(e)}")

@tts_bp.route('/stream/<filename>', methods=['GET'])
@cross_origin()
def stream_audio(filename):
    """流媒体播放音频文件（无需登录）"""
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
        
        logger.info(f"🎵 流媒体播放音频文件: {safe_filename}")
        
        # 流媒体方式发送文件
        return send_file(
            file_path,
            as_attachment=False,  # 不作为下载，而是流媒体
            download_name=safe_filename,
            mimetype='audio/wav'
        )
        
    except Exception as e:
        logger.error(f"流媒体播放失败: {str(e)}")
        return handle_api_error(500, f"流媒体播放失败: {str(e)}")

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
        return handle_api_error(500, f"下载音频文件失败: {str(e)}")

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
        return handle_api_error(500, f"清理临时文件失败: {str(e)}")

@tts_bp.route('/dialogue', methods=['POST'])
@cross_origin()
@require_login
def synthesize_dialogue(current_user):
    """多人对话语音合成"""
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
        model_key = data.get('model', 'dialogue_mode')
        
        # 验证输入
        if not text:
            return jsonify({
                'success': False,
                'message': '文本内容不能为空',
                'data': {}
            }), 400
        
        if len(text) > 2000:
            return jsonify({
                'success': False,
                'message': '对话文本长度不能超过2000字符',
                'data': {}
            }), 400
        
        logger.info(f"🎭 开始多人对话合成: 用户={current_user.username}, 文本长度={len(text)}")
        
        # 解析对话
        dialogue_segments = parse_dialogue_text(text)
        logger.info(f"🎭 解析到 {len(dialogue_segments)} 段对话")
        
        # 初始化合并文本
        combined_text = text.strip()
        
        # 统一生成输出文件名 (修复原代码中 audio_path 可能未定义的bug)
        temp_dir = tempfile.gettempdir()
        audio_filename = f"dialogue_{uuid.uuid4().hex[:8]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"
        audio_path = os.path.join(temp_dir, audio_filename)
        
        # 获取TTS实例
        tts = None
        if model_key != 'chattts_v1':
            tts = get_tts_instance(model_key)
        elif not CHAT_TTS_AVAILABLE:
            raise Exception("ChatTTS 服务不可用")
        
        # ChatTTS 处理逻辑
        if model_key == 'chattts_v1':
             result_path = chat_tts_service.synthesize_dialogue(dialogue_segments)
             if result_path:
                 import shutil
                 shutil.copy2(result_path, audio_path)
             else:
                 raise Exception("ChatTTS 对话合成失败")
        
        # 处理多人对话 - 支持原生多声音
        elif len(dialogue_segments) > 1:
            logger.info(f"🎭 处理多人对话: 使用模型 {model_key}")
            
            # 为每个说话人生成单独的音频片段
            audio_segments = []
            temp_dir = tempfile.gettempdir()
            
            # 如果是YourTTS，尝试获取原生说话人列表和语言列表
            available_speakers = []
            available_languages = []
            if model_key in ['your_tts', 'dialogue_mode']:
                try:
                    if hasattr(tts, 'speakers') and tts.speakers:
                        available_speakers = tts.speakers
                        logger.info(f"👥 发现原生说话人列表: {len(available_speakers)}个")
                    if hasattr(tts, 'languages') and tts.languages:
                        available_languages = tts.languages
                except:
                    pass

            for i, segment in enumerate(dialogue_segments):
                speaker_name = segment['speaker']
                text = segment['text'].strip()
                clean_text = text.rstrip('。！？.!?') + '。'
                
                segment_filename = f"temp_segment_{i}_{uuid.uuid4().hex[:6]}.wav"
                segment_path = os.path.join(temp_dir, segment_filename)
                
                logger.info(f"🔄 生成 [{speaker_name}]: {clean_text}")
                
                try:
                    # 准备合成参数
                    synth_kwargs = {'text': clean_text, 'file_path': segment_path}
                    
                    # 如果是YourTTS且有多个说话人，为不同角色分配不同声音
                    if available_speakers:
                        # 简单地根据说话人名字或索引映射到不同的原生声音
                        speaker_idx = i % len(available_speakers)
                        # 尝试通过名字映射（可选）
                        synth_kwargs['speaker'] = available_speakers[speaker_idx]
                        
                        # 处理语言参数
                        if 'zh' in model_key or 'multilingual' in TTS_MODELS[model_key]['model_name']:
                            target_lang = 'zh-cn'
                            if available_languages and target_lang not in available_languages:
                                if 'en' in available_languages:
                                    target_lang = 'en'
                                else:
                                    target_lang = available_languages[0]
                            synth_kwargs['language'] = target_lang
                        
                        logger.info(f"👤 [{speaker_name}] 映射到原生说话人: {synth_kwargs['speaker']} (语言: {synth_kwargs.get('language')})")
                    
                    # 执行合成
                    tts.tts_to_file(**synth_kwargs)
                    
                    # 如果没有原生多说话人支持，才使用后期音效处理
                    if not available_speakers:
                        processed_path = apply_speaker_effects(segment_path, speaker_name, i)
                        audio_segments.append(processed_path)
                        if processed_path != segment_path and os.path.exists(segment_path):
                            os.remove(segment_path)
                    else:
                        audio_segments.append(segment_path)
                        
                except Exception as e:
                    logger.error(f"❌ 生成片段失败 [{speaker_name}]: {str(e)}")
                    if os.path.exists(segment_path):
                        os.remove(segment_path)
                    raise
            
            # 合并所有音频片段
            combined_path = combine_audio_segments(audio_segments, audio_path)
            
            # 清理临时片段文件
            for segment_path in audio_segments:
                if os.path.exists(segment_path):
                    os.remove(segment_path)
                    
            logger.info(f"✅ 多人对话合成成功: {combined_path}")
            
        else:
            # 单人对话，使用原逻辑
            combined_text = dialogue_segments[0]['text'].strip().rstrip('。！？.!?') + '。'
            logger.info(f"🔄 单人合成文本: {combined_text}")
            
            # 生成临时文件名
            temp_dir = tempfile.gettempdir()
            audio_filename = f"dialogue_{uuid.uuid4().hex[:8]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"
            audio_path = os.path.join(temp_dir, audio_filename)
            
            # 语音合成
            try:
                tts.tts_to_file(text=combined_text, file_path=audio_path)
                logger.info(f"✅ 单人对话合成成功: {audio_path}")
            except Exception as e:
                logger.error(f"❌ 单人对话合成失败: {str(e)}")
                raise Exception(f"单人对话合成失败: {str(e)}")
        
        # 检查文件是否生成
        if not os.path.exists(audio_path):
            raise Exception("对话音频文件生成失败")
        
        file_size = os.path.getsize(audio_path)
        logger.info(f"📁 生成对话音频文件: {audio_filename}, 大小: {file_size} bytes")
        
        return jsonify({
            'success': True,
            'message': '多人对话语音合成成功',
            'data': {
                'audio_filename': audio_filename,
                'file_size': file_size,
                'text': text,
                'combined_text': combined_text,
                'dialogue_segments': len(dialogue_segments),
                'speakers': list(set([seg['speaker'] for seg in dialogue_segments])),
                'model': model_key,
                'model_name': TTS_MODELS[model_key]['name'],
                'duration_estimate': len(combined_text) * 0.1
            }
        })
        
    except Exception as e:
        logger.error(f"多人对话合成失败: {str(e)}")
        return handle_api_error(500, f"多人对话合成失败: {str(e)}")

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
        return handle_api_error(500, f"获取TTS状态失败: {str(e)}")