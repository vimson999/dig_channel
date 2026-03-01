import asyncio
import sys
import os
from pathlib import Path

# Add src/backend to path
sys.path.append(os.path.join(os.getcwd(), 'src', 'backend'))

async def test_imports():
    print("Testing Bilibili service imports...")
    try:
        from services.bilibili import BiliUploader, BiliVideoMetadata
        from services.bilibili_sync_service import BilibiliSyncService
        print("✅ Imports successful!")
        
        print("\nTesting service initialization...")
        service = BilibiliSyncService()
        print(f"✅ Service initialized. Input dir: {service.input_dir}")
        
        # Check if directories were created
        if service.input_dir.exists() and service.processed_dir.exists() and service.failed_dir.exists():
            print("✅ Data pipeline directories created successfully.")
        else:
            print("❌ Directory creation failed.")
            
        return True
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    asyncio.run(test_imports())

