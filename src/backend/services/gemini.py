import logging
import google.generativeai as genai
from google.api_core import exceptions as google_exceptions
from config import get_config
from utils.google_key_manager import GoogleApiKeyManager

logger = logging.getLogger(__name__)

class GeminiService:
    def __init__(self):
        config = get_config()
        # Initialize Key Manager with Gemini-specific keys (falling back to general Google keys)
        self.key_manager = GoogleApiKeyManager(
            keys=[k.strip() for k in (config.GOOGLE_GEMINI_KEYS or "").split(',') if k.strip()],
            logger=logger
        )
        self.model_name = "gemini-2.5-flash" # Default to latest efficient model

    def _configure_client(self):
        """Configure the GenAI client with the current key."""
        current_key = self.key_manager.get_key()
        genai.configure(api_key=current_key)
        return current_key

    def generate_content(self, prompt: str, model_name: str = None) -> str:
        """
        Generate content using Gemini with automatic key rotation on quota errors.
        """
        target_model = model_name or self.model_name
        
        while True:
            try:
                self._configure_client()
                model = genai.GenerativeModel(target_model)
                response = model.generate_content(prompt)
                
                if response and response.text:
                    return response.text
                return ""

            except google_exceptions.ResourceExhausted as e:
                logger.warning(f"Gemini quota exceeded for current key: {e}")
                if not self.key_manager.report_quota_exceeded():
                    logger.error("All Gemini API keys exhausted.")
                    raise
            
            except Exception as e:
                # Handle other potential auth errors that might require key switching
                if "403" in str(e) or "API_KEY_INVALID" in str(e):
                     logger.warning(f"Invalid API Key encountered: {e}")
                     if not self.key_manager.report_failure("invalid_key"):
                         raise
                else:
                    # For non-auth/quota errors, re-raise immediately
                    logger.error(f"Gemini generation failed: {e}")
                    raise

gemini_service = GeminiService()
