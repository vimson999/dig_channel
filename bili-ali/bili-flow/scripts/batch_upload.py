import asyncio
import os
import shutil
import sys
from pathlib import Path
from typing import List

# Add src to path
sys.path.append(str(Path(__file__).parent.parent / "src"))

from biliflow.core.uploader import BiliUploader
from biliflow.utils.logger import log

# Configuration
INPUT_DIR = Path("data/input")
PROCESSED_DIR = Path("data/processed")
FAILED_DIR = Path("data/failed")
SUPPORTED_EXTENSIONS = {".mp4", ".mkv", ".mov", ".flv", ".avi"}

async def process_video(uploader: BiliUploader, video_path: Path):
    """
    Process a single video file: Upload -> Move to appropriate directory.
    """
    filename = video_path.name
    title = video_path.stem # Use filename without extension as title
    
    log.info(f"Found video: {filename}")
    
    try:
        # Upload
        log.info(f"Uploading '{title}'...")
        await uploader.upload_single(
            video_path=str(video_path),
            title=title,
            desc=f"Uploaded via BiliFlow Automation. Filename: {filename}",
            tid=171, # default partition (Lifestyle/E-sports), can be configured later
            tags=["BiliFlow", "Automation"]
        )
        
        # On Success: Move to processed
        destination = PROCESSED_DIR / filename
        shutil.move(str(video_path), str(destination))
        log.success(f"Processed: {filename} -> {destination}")
        
    except Exception as e:
        # On Failure: Move to failed
        log.error(f"Failed to upload {filename}: {e}")
        destination = FAILED_DIR / filename
        try:
            shutil.move(str(video_path), str(destination))
            log.warning(f"Moved to failed: {destination}")
        except Exception as move_error:
            log.error(f"Could not move failed file: {move_error}")

async def main():
    # Ensure directories exist
    for d in [INPUT_DIR, PROCESSED_DIR, FAILED_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    # Scan for files
    files = [
        p for p in INPUT_DIR.iterdir() 
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    ]

    if not files:
        log.warning(f"No video files found in {INPUT_DIR}")
        return

    log.info(f"Found {len(files)} videos in input queue.")
    
    # Initialize Uploader (will load env vars)
    try:
        uploader = BiliUploader()
    except Exception as e:
        log.critical(f"Failed to initialize uploader (Check your .env file): {e}")
        return

    # Process queue
    for video_file in files:
        await process_video(uploader, video_file)

if __name__ == "__main__":
    asyncio.run(main())
