import sys
import os
import asyncio
from pathlib import Path

# Add src to path
sys.path.append(str(Path(__file__).parent.parent / "src"))

from biliflow.core.uploader import BiliUploader
from biliflow.utils.logger import log

async def main():
    video_path = "test_video.mp4"
    if len(sys.argv) > 1:
        video_path = sys.argv[1]
        
    if not os.path.exists(video_path):
        log.error(f"Video file {video_path} not found. Run scripts/generate_test_video.sh first.")
        return

    uploader = BiliUploader()
    
    try:
        await uploader.upload_single(
            video_path=video_path,
            title="BiliFlow Test Upload",
            desc="This is a test upload from BiliFlow automation toolkit.",
            tid=21, # 动画 -> 综合
            tags=["BiliFlow", "Test"]
        )
    except Exception as e:
        log.error(f"Upload failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())
