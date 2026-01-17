import logging
from google import genai
from google.genai import types
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
        self.model_name = "gemini-2.0-flash" # Default to latest efficient model

    def _get_client(self):
        """Create a GenAI client with the current key."""
        current_key = self.key_manager.get_key()
        return genai.Client(api_key=current_key)

    def generate_content(self, prompt: str, model_name: str = None) -> str:
        """
        Generate content using Gemini with automatic key rotation on quota errors.
        """
        target_model = model_name or self.model_name
        attempt = 0
        max_attempts = self.key_manager.get_status()['total_keys'] * 2 # Allow each key to be tried twice if needed
        
        while attempt < max_attempts:
            current_key = self.key_manager.get_key()
            key_status = self.key_manager.get_status()
            logger.info(f"Attempting Gemini generation (attempt {attempt+1}) using key index {key_status['current_index']} with model {target_model}")
            
            try:
                client = genai.Client(api_key=current_key)
                response = client.models.generate_content(
                    model=target_model,
                    contents=prompt
                )
                
                if response and response.text:
                    return response.text
                return ""

            except Exception as e:
                error_str = str(e)
                attempt += 1
                
                # Check for Quota Exceeded (429)
                if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                    logger.warning(f"Gemini quota exceeded for key index {key_status['current_index']}: {e}")
                    if not self.key_manager.report_quota_exceeded():
                        logger.error("All Gemini API keys exhausted.")
                        raise
                
                # Check for Auth Errors (403, API_KEY_INVALID)
                elif "403" in error_str or "API_KEY_INVALID" in error_str:
                     logger.warning(f"Invalid API Key at index {key_status['current_index']}: {e}")
                     if not self.key_manager.report_failure("invalid_key"):
                         raise
                else:
                    # For non-auth/quota errors, re-raise immediately
                    logger.error(f"Gemini generation failed on key index {key_status['current_index']}: {e}")
                    raise
        
        raise RuntimeError("Failed to generate content after exhausting all retry options.")

gemini_service = GeminiService()
