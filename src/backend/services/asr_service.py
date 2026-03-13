import os
import logging
from faster_whisper import WhisperModel
import torch

logger = logging.getLogger(__name__)

class ASRService:
    def __init__(self, model_size="base"):
        """
        Initialize the ASR service with a specific Whisper model size.
        Sizes: tiny, base, small, medium, large-v3
        """
        self.model_size = model_size
        self.model = None
        
        # Derive project root to store models locally
        self.base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        self.model_path = os.path.join(self.base_dir, 'data', 'models', 'whisper')
        os.makedirs(self.model_path, exist_ok=True)

    def _ensure_model_loaded(self):
        if self.model is None:
            logger.info(f"Loading Whisper model: {self.model_size}...")
            # faster-whisper does not support 'mps', force to 'cpu' for stability on Mac
            device = "cpu"
            if torch.cuda.is_available():
                device = "cuda"
            
            logger.info(f"Using device: {device}")
            # compute_type can be float16 on GPU, but int8 is efficient on CPU
            self.model = WhisperModel(
                self.model_size, 
                device=device, 
                compute_type="int8" if device == "cpu" else "float16",
                download_root=self.model_path
            )
            logger.info("Whisper model loaded successfully.")

    def transcribe(self, audio_path):
        """
        Transcribe audio file to text.
        """
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"Audio file not found: {audio_path}")
            
        self._ensure_model_loaded()
        
        logger.info(f"Transcribing: {audio_path}")
        # beam_size=5 is standard for better accuracy
        # initial_prompt 强制要求简体中文
        segments, info = self.model.transcribe(
            audio_path, 
            beam_size=5, 
            language="zh", 
            initial_prompt="以下是简体中文内容："
        )
        
        text_parts = []
        for segment in segments:
            text_parts.append(segment.text)
            
        full_text = "".join(text_parts).strip()
        logger.info(f"Transcription complete: {full_text[:50]}...")
        return full_text

# Singleton instance
asr_service = ASRService(model_size="base")
