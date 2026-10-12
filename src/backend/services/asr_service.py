import os
import logging
import platform
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_FINANCE_ASR_PROMPT = (
    "这是一段专业财经研报解读音频，请使用简体中文输出。"
    "请尽量准确识别公司名称、机构名称、行业术语、英文缩写和金融表达。"
)

# Try to import specialized libraries for Mac M-series
HAS_MLX = False
try:
    if platform.system() == 'Darwin' and platform.machine() == 'arm64':
        import mlx_whisper
        HAS_MLX = True
        logger.info("Found mlx-whisper, will use Apple Silicon acceleration.")
except ImportError:
    pass

try:
    from ctranslate2 import get_cuda_device_count
    from faster_whisper import WhisperModel
    HAS_FASTER_WHISPER = True
except ImportError:
    HAS_FASTER_WHISPER = False

class ASRService:
    def __init__(self, model_size="medium"):
        """
        Initialize the ASR service with a specific Whisper model size.
        Sizes: tiny, base, small, medium, large-v3
        """
        self.model_size = model_size
        self.current_loaded_size = None
        self.model = None
        self.use_mlx = HAS_MLX
        
        # Derive project root to store models locally
        self.base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        self.model_path = os.path.join(self.base_dir, 'data', 'models', 'whisper')
        os.makedirs(self.model_path, exist_ok=True)

    def _get_mlx_repo_name(self, size=None):
        model_size = size or self.model_size
        return f"mlx-community/whisper-{model_size}-mlx-8bit"

    def _resolve_local_mlx_model_path(self, repo_name):
        """
        Resolve an already-cached HuggingFace model snapshot.
        Passing the local snapshot path prevents mlx-whisper from contacting
        huggingface.co just to resolve the repo revision.
        """
        repo_cache_name = f"models--{repo_name.replace('/', '--')}"
        cache_roots = []

        hf_home = os.environ.get("HF_HOME")
        if hf_home:
            cache_roots.append(Path(hf_home) / "hub")

        hf_hub_cache = os.environ.get("HUGGINGFACE_HUB_CACHE")
        if hf_hub_cache:
            cache_roots.append(Path(hf_hub_cache))

        cache_roots.append(Path.home() / ".cache" / "huggingface" / "hub")

        for cache_root in cache_roots:
            model_root = cache_root / repo_cache_name
            ref_path = model_root / "refs" / "main"
            snapshots_dir = model_root / "snapshots"
            candidates = []

            if ref_path.exists():
                revision = ref_path.read_text(encoding="utf-8").strip()
                if revision:
                    candidates.append(snapshots_dir / revision)

            if snapshots_dir.exists():
                candidates.extend(sorted(snapshots_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True))

            for candidate in candidates:
                if not candidate.is_dir():
                    continue
                has_config = (candidate / "config.json").exists()
                has_weights = (candidate / "weights.npz").exists() or (candidate / "weights.safetensors").exists()
                if has_config and has_weights:
                    return str(candidate)

        return None

    def set_model_size(self, size):
        """Change the model size for the next transcription."""
        if size != self.model_size:
            logger.info(f"🔄 [ASR] Switching model size from {self.model_size} to {size}")
            self.model_size = size
            # If model already loaded and size changed, we need to reload
            if self.current_loaded_size and self.current_loaded_size != size:
                self.model = None
                self.current_loaded_size = None

    def _ensure_model_loaded(self):
        if self.model is None:
            if self.use_mlx:
                # mlx-whisper uses specific model names from HuggingFace
                self.mlx_model_name = self._get_mlx_repo_name()
                self.mlx_model_path = self._resolve_local_mlx_model_path(self.mlx_model_name)
                if self.mlx_model_path:
                    logger.info(f"🚀 [ASR] Loading local MLX-Whisper model: {self.mlx_model_path}")
                else:
                    logger.warning(f"⚠️ [ASR] 本地未找到模型缓存，将尝试从 HuggingFace 解析: {self.mlx_model_name}")
                self.model = "MLX_ACTIVE" 
                self.current_loaded_size = self.model_size
            elif HAS_FASTER_WHISPER:
                logger.info(f"🚀 [ASR] Loading Faster-Whisper model: {self.model_size}...")
                device = "cpu"
                # Query the inference engine directly. Importing PyTorch first
                # loads a competing OpenMP runtime and can crash Intel macOS.
                if get_cuda_device_count() > 0:
                    device = "cuda"
                
                self.model = WhisperModel(
                    self.model_size, 
                    device=device, 
                    compute_type="int8" if device == "cpu" else "float16",
                    download_root=self.model_path
                )
                self.current_loaded_size = self.model_size
            else:
                raise RuntimeError("No Whisper backend (mlx or faster-whisper) available.")
            
            logger.info(f"✅ [ASR] Whisper model ({self.model_size}) initialized.")

    def transcribe(self, audio_path, return_segments=False, model_size=None, initial_prompt=None):
        """
        Transcribe audio file to text.
        """
        if model_size:
            self.set_model_size(model_size)
            
        import time
        from pydub.utils import mediainfo
        start_time = time.time()
        
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"Audio file not found: {audio_path}")
            
        # 获取音频时长信息
        try:
            info = mediainfo(audio_path)
            duration_sec = float(info.get('duration', 0))
            logger.info(f"📊 [ASR] 音频信息: {os.path.basename(audio_path)} | 时长: {duration_sec:.2f}s")
        except:
            duration_sec = 0
            logger.warning(f"⚠️ [ASR] 无法获取音频时长: {audio_path}")

        self._ensure_model_loaded()
        logger.info(f"🚀 [ASR] 开始识别任务 (开启词级时间戳)...")

        prompt = (initial_prompt or DEFAULT_FINANCE_ASR_PROMPT).strip()
        logger.info("🧭 [ASR] initial_prompt=%s", prompt[:180])

        if self.use_mlx:
            try:
                # 开启 word_timestamps 以获得每个字的精确时间
                result = mlx_whisper.transcribe(
                    audio_path, 
                    path_or_hf_repo=self.mlx_model_path or self.mlx_model_name,
                    language="zh",
                    initial_prompt=prompt,
                    word_timestamps=True 
                )
                raw_segments = result.get('segments', [])
                full_text = result.get('text', '').strip()
            except Exception as e:
                if "snapshot" in str(e) or "connection" in str(e):
                    logger.error("❌ [ASR] 模型下载失败。")
                raise e
        else:
            # faster-whisper 也支持 word_timestamps
            segments_gen, info = self.model.transcribe(
                audio_path, 
                beam_size=5, 
                language="zh", 
                initial_prompt=prompt,
                word_timestamps=True
            )
            raw_segments = []
            text_parts = []
            for s in segments_gen:
                # 转换格式以保持一致性
                words = []
                if hasattr(s, 'words') and s.words:
                    words = [{'start': w.start, 'end': w.end, 'word': w.word} for w in s.words]
                raw_segments.append({'start': s.start, 'end': s.end, 'text': s.text, 'words': words})
                text_parts.append(s.text)
            full_text = "".join(text_parts).strip()

        # --- 繁简转换后处理 (强制简体) ---
        TRADITIONAL_MAP = {
            "個": "个", "為": "为", "這": "这", "於": "于", "後": "后", "開": "开", "實": "实", "業": "业",
            "對": "对", "發": "发", "會": "会", "國": "国", "學": "学", "義": "义", "戰": "战", "經": "经",
            "進": "进", "動": "动", "從": "从", "質": "质", "現": "现", "點": "点", "無": "无", "報": "报",
            "錄": "录", "製": "制", "産": "产", "種": "种", "樣": "样", "將": "将", "應": "应", "定": "定",
            "機": "机", "頭": "头", "體": "体", "選": "选", "題": "题", "養": "养", "類": "类", "認": "认",
            "識": "识", "說": "说", "話": "话", "語": "语", "調": "调", "證": "证", "論": "论", "讓": "让",
            "劃": "划", "辦": "办", "較": "较", "輸": "输", "過": "过", "連": "连", "進": "进", "運": "运",
            "邊": "边", "遠": "远", "選": "选", "還": "还", "錢": "钱", "鐘": "钟", "鐵": "铁", "長": "长",
            "門": "门", "問": "问", "間": "间", "聞": "闻", "閱": "阅", "隊": "队", "階": "阶", "隱": "隐",
            "隻": "只", "雙": "双", "雜": "杂", "雞": "鸡", "離": "离", "難": "难", "雲": "云", "電": "电",
            "靈": "灵", "響": "响", "顧": "顾", "飲": "饮", "館": "馆", "馬": "马", "驅": "驱", "驗": "验",
            "驚": "惊", "體": "体", "高": "高", "髮": "发", "鬆": "松", "鬥": "斗", "鬱": "郁", "鳥": "鸟",
            "鳴": "鸣", "龍": "龙", "龜": "龟", "點": "点", "號": "号", "碼": "码", "軍": "军", "農": "农",
            "衆": "众", "裏": "里", "製": "制", "復": "复", "獲": "获", "與": "与", "萬": "万", "專": "专"
        }
        
        def force_simplified(text):
            if not text: return text
            for t, s in TRADITIONAL_MAP.items():
                text = text.replace(t, s)
            return text

        # --- 智能切分逻辑：利用 word_timestamps 将长句切碎 ---
        final_cleaned_segments = []
        MAX_CHARS_PER_SUBTITLE = 12 # 每屏最多显示的字数，模拟剪映节奏

        for seg in raw_segments:
            words = seg.get('words', [])
            if not words:
                final_cleaned_segments.append({
                    'start': float(seg.get('start', 0)),
                    'end': float(seg.get('end', 0)),
                    'text': force_simplified(str(seg.get('text', '')).strip())
                })
                continue

            current_sub_words = []
            current_chars_count = 0
            
            for w in words:
                word_text = w.get('word', '').strip()
                if not word_text: continue
                
                current_sub_words.append(w)
                current_chars_count += len(word_text)
                
                should_split = current_chars_count >= MAX_CHARS_PER_SUBTITLE or \
                               any(p in word_text for p in ["，", "。", "？", "！", ",", ".", "?", "!"])
                
                if should_split and current_sub_words:
                    final_cleaned_segments.append({
                        'start': float(current_sub_words[0]['start']),
                        'end': float(current_sub_words[-1]['end']),
                        'text': force_simplified("".join([x['word'] for x in current_sub_words]).strip())
                    })
                    current_sub_words = []
                    current_chars_count = 0
            
            if current_sub_words:
                final_cleaned_segments.append({
                    'start': float(current_sub_words[0]['start']),
                    'end': float(current_sub_words[-1]['end']),
                    'text': force_simplified("".join([x['word'] for x in current_sub_words]).strip())
                })

        duration = time.time() - start_time
        logger.info(f"✅ [ASR] 识别并切分完成! 耗时: {duration:.2f}s | 原始段落: {len(raw_segments)} -> 精细片段: {len(final_cleaned_segments)}")
        
        if return_segments:
            if final_cleaned_segments:
                s = final_cleaned_segments[0]
                logger.info(f"📝 [ASR] 片段示例: start={s['start']}, end={s['end']}, text={s['text'][:20]}")
            return final_cleaned_segments
        return full_text

# Singleton instance
# 默认使用 medium，兼顾中文转写质量和本地推理成本。
# 任务开始时可根据参数动态切换至其他模型。
asr_service = ASRService(model_size="medium")
