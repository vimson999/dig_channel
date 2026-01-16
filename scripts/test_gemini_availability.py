import os
import sys
import warnings
from dotenv import load_dotenv

# Suppress warnings from google.generativeai
warnings.filterwarnings("ignore", category=FutureWarning)

import google.generativeai as genai

# Add src to python path to reuse key manager logic
# We need to add 'src/backend' to path to allow importing config
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
backend_path = os.path.join(project_root, 'src', 'backend')
sys.path.insert(0, backend_path)

def test_gemini_availability():
    """Test availability of Gemini API with configured keys."""
    
    # Load environment variables
    load_dotenv()
    
    # Get keys from environment
    # Try GOOGLE_GEMINI_KEYS first, then fallback to GOOGLE_API_KEYS
    keys_str = os.environ.get("GOOGLE_GEMINI_KEYS") or os.environ.get("GOOGLE_API_KEYS")
    
    if not keys_str:
        print("❌ No API Keys found! Please set GOOGLE_API_KEYS or GOOGLE_GEMINI_KEYS in .env")
        return

    keys = [k.strip() for k in keys_str.split(',') if k.strip()]
    print(f"🔑 Found {len(keys)} keys to test.")
    
    success_count = 0
    
    for i, key in enumerate(keys):
        masked_key = f"{key[:4]}...{key[-4:]}"
        print(f"\n🧪 Testing Key #{i+1}: {masked_key}")
        
        try:
            genai.configure(api_key=key)
            
            print("   📋 Listing available models...")
            try:
                available_models = []
                for m in genai.list_models():
                    if 'generateContent' in m.supported_generation_methods:
                        available_models.append(m.name)
                
                if available_models:
                    print(f"   ✅ Available models: {', '.join(available_models)}")
                    success_count += 1
                    
                    # Try with the first available model
                    model_name = available_models[0].replace('models/', '')
                    print(f"   🚀 Trying generation with: {model_name}")
                    model = genai.GenerativeModel(model_name)
                    response = model.generate_content("Hello")
                    print(f"   🎉 Response: {response.text.strip()}")
                    
                else:
                    print("   ⚠️  No models found with 'generateContent' capability.")
            except Exception as e:
                 print(f"   ❌ List models failed: {str(e)}")

        except Exception as e:
            print(f"   ❌ Configuration failed. Error: {str(e)}")

    print(f"\n📊 Summary: {success_count}/{len(keys)} keys are working for Gemini API.")

if __name__ == "__main__":
    test_gemini_availability()
