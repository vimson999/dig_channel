import sys
import os
import logging

# Add src/backend to python path
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
backend_path = os.path.join(project_root, 'src', 'backend')
sys.path.insert(0, backend_path)

# Configure logging
logging.basicConfig(level=logging.INFO)

from services.gemini import gemini_service

def test_service():
    print("🤖 Testing Gemini Service...")
    try:
        response = gemini_service.generate_content("Summarize the benefits of Python in one sentence.")
        print(f"✅ Service Response: {response}")
    except Exception as e:
        print(f"❌ Service Failed: {e}")

if __name__ == "__main__":
    test_service()
