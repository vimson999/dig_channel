import os
import logging
import torch
import soundfile as sf
import tempfile
import uuid
import numpy as np
from pydub import AudioSegment

# 配置日志
from utils.colored_logger import setup_colored_logger
logger = setup_colored_logger("ChatTTS_Service")

class ChatTTSService:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ChatTTSService, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self.chat = None
        self.speakers = {}  # 缓存说话人 embedding: {speaker_id: spk_emb}
        self.model_loaded = False
        self._initialized = True

    def load_model(self):
        """加载 ChatTTS 模型"""
        if self.model_loaded:
            return

        try:
            import ChatTTS
            logger.info("正在加载 ChatTTS 模型...")
            self.chat = ChatTTS.Chat()

            # 根据硬件选择 device
            device = 'cuda' if torch.cuda.is_available() else 'cpu'
            logger.info(f"使用计算设备: {device}")

            # 尝试加载模型
            # 某些版本可能需要 download=True 或者默认参数
            logger.info(f"开始加载 ChatTTS 模型 (device={device})...")

            # 使用 compile=False 避免动态编译开销和潜在兼容性问题
            self.chat.load(compile=False, device=device)

            # 验证加载状态
            is_loaded = False
            try:
                is_loaded = self.chat.has_loaded()
            except:
                pass

            if not is_loaded:
                # 再次检查
                if hasattr(self.chat, 'pretrain_models') and self.chat.pretrain_models:
                     self.model_loaded = True
                     logger.info(f"✅ ChatTTS 模型加载成功 (通过 pretrain_models 验证)")
                else:
                     logger.warning("⚠️ ChatTTS 加载状态验证失败，但未抛出异常，尝试继续...")
                     self.model_loaded = True
            else:
                self.model_loaded = True
                logger.info(f"✅ ChatTTS 模型加载成功")

        except Exception as e:
            logger.error(f"❌ ChatTTS 模型加载失败: {str(e)}")
            raise Exception(f"ChatTTS 初始化失败: {str(e)}")

    def get_speaker_embedding(self, speaker_id):
        """获取或创建说话人 embedding"""
        if not self.model_loaded:
            self.load_model()

        if speaker_id not in self.speakers:
            logger.info(f"🎲 为角色 [{speaker_id}] 生成新的声音特征...")
            # 随机采样一个 speaker
            try:
                # 尝试不同的 API 调用方式，ChatTTS API 变动较快
                if hasattr(self.chat, 'sample_random_speaker'):
                    spk_emb = self.chat.sample_random_speaker()
                else:
                    logger.warning("未找到 sample_random_speaker 方法，尝试使用默认采样")
                    spk_emb = None

                self.speakers[speaker_id] = spk_emb
            except Exception as e:
                logger.error(f"采样说话人失败: {e}")
                # Fallback: 不传 spk_emb
                self.speakers[speaker_id] = None

        return self.speakers[speaker_id]

    def synthesize(self, text, speaker_id="default", output_path=None):
        """合成单段语音"""
        if not self.model_loaded:
            self.load_model()

        if not text:
            raise ValueError("文本不能为空")

        # 文本预处理：替换可能导致问题的全角标点
        import re
        # 暴力清洗：先替换常见全角标点
        text = text.replace('？', '?').replace('！', '!').replace('，', ',').replace('。', '.')
        text = text.replace('：', ':').replace('；', ';').replace('“', '"').replace('”', '"')
        text = text.replace('、', ',')

        # 使用正则移除其他潜在的非法字符，只保留中英文、数字和基本标点
        # 暂时只允许 ChatTTS 支持的控制符通过 (如果未来支持)
        text = re.sub(r'[\x00-\x1f\x7f]', '', text)
        text = text.strip()

        # 记录清洗后的文本
        logger.info(f"🧼 清洗后文本: {text}")

        # 获取 speaker embedding (已恢复)
        spk_emb = self.get_speaker_embedding(speaker_id)

        # 推理
        logger.info(f"🗣️ 正在合成 (角色: {speaker_id}): {text[:20]}...")

        try:
            # 准备参数
            params_infer_code = {}
            if spk_emb is not None:
                params_infer_code['spk_emb'] = spk_emb
            else:
                 # 如果不传 spk_emb，ChatTTS 应该会使用默认随机或者固定音色
                 pass

            # 执行推理
            # 关键修复：添加 skip_refine_text=True 以规避 narrow(): length must be non-negative 错误
            wavs = self.chat.infer(
                [text],
                use_decoder=True,
                skip_refine_text=True,
                params_infer_code=params_infer_code
            )

            if not wavs or len(wavs) == 0:
                raise Exception("ChatTTS 未返回音频数据")

            # 处理音频数据格式
            audio_data = wavs[0]

            # 如果是 list (通常是 list of float)，转 numpy
            if isinstance(audio_data, list):
                 audio_data = np.array(audio_data)
            elif isinstance(audio_data, torch.Tensor):
                audio_data = audio_data.cpu().numpy()

            # 确保是一维数组
            if len(audio_data.shape) > 1:
                audio_data = audio_data.flatten()

            # ChatTTS 默认采样率 24000
            sample_rate = 24000

            sf.write(output_path, audio_data, sample_rate)
            logger.info(f"💾 语音已保存至: {output_path}")

            return output_path

        except Exception as e:
            # 使用 exception 打印完整堆栈
            logger.exception(f"❌ 合成失败: {str(e)}")
            raise

    def synthesize_dialogue(self, dialogue_segments):
        """
        合成对话
        dialogue_segments: [{'speaker': 'A', 'text': '你好'}, {'speaker': 'B', 'text': '你好呀'}]
        """
        temp_files = []
        try:
            logger.info(f"🎭 开始处理对话，共 {len(dialogue_segments)} 个片段")

            # 1. 生成每一段
            for i, seg in enumerate(dialogue_segments):
                speaker = seg.get('speaker', 'default')
                text = seg.get('text', '')
                if not text.strip():
                    continue

                path = os.path.join(
                    tempfile.gettempdir(),
                    f"chattts_segment_{i}_{uuid.uuid4().hex}.wav",
                )
                temp_files.append(path)
                self.synthesize(text, speaker, output_path=path)

            # 2. 合并
            if not temp_files:
                return None

            logger.info("🔗 正在合并音频片段...")
            combined = AudioSegment.from_wav(temp_files[0])
            silence = AudioSegment.silent(duration=300) # 300ms 间隔

            for path in temp_files[1:]:
                next_seg = AudioSegment.from_wav(path)
                combined = combined + silence + next_seg

            # 3. 导出最终文件
            temp_dir = tempfile.gettempdir()
            final_filename = f"chattts_dialogue_{uuid.uuid4().hex}_{len(dialogue_segments)}segs.wav"
            final_path = os.path.join(temp_dir, final_filename)

            combined.export(final_path, format="wav")
            logger.info(f"✅ 对话合并完成: {final_path}")

            return final_path

        finally:
            # 清理中间文件
            for f in temp_files:
                try:
                    if os.path.exists(f):
                        os.remove(f)
                except:
                    pass

# 全局单例
chat_tts_service = ChatTTSService()
