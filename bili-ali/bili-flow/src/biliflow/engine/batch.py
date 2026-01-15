import asyncio
import shutil
from pathlib import Path
from typing import List

from biliflow.core.uploader import BiliUploader
from biliflow.utils.logger import log
from biliflow.core.queue_manager import load_queue_order
from biliflow.core.metadata import load_metadata
from biliflow.utils.media import extract_cover
import shutil

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
    
    # Load Metadata
    meta = load_metadata(video_path)
    
    log.info(f"Found video: {filename}")
    
    # Ensure Cover Exists (Auto-Generate -> Default -> Fail)
    if not meta.cover_path or not Path(meta.cover_path).exists():
        log.info(f"No cover provided for {filename}. Generating default cover...")
        try:
            meta.cover_path = await extract_cover(video_path)
        except Exception as e:
            log.error(f"Could not generate cover: {e}")
            
            # Fallback to default asset
            default_cover = Path("assets/default_cover.jpg")
            if default_cover.exists():
                log.warning("Using fallback default cover.")
                meta.cover_path = str(default_cover.absolute())
            else:
                log.error("No cover available and fallback missing. Skipping upload to prevent crash.")
                # Move to failed directly to avoid crashing the uploader
                destination = FAILED_DIR / filename
                shutil.move(str(video_path), str(destination))
                return # Abort this video

    try:
        # Upload
        log.info(f"Uploading '{meta.title}'...")
        await uploader.upload_single(
            video_path=str(video_path),
            title=meta.title,
            desc=meta.desc or f"Uploaded via BiliFlow Automation. Filename: {filename}",
            tid=meta.tid,
            tags=meta.tags,
            cover_path=meta.cover_path,
            copyright=meta.copyright,
            dynamic=meta.dynamic
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

async def run_batch():
    """
    Scans input directory and processes all videos.
    """
    # Ensure directories exist
    for d in [INPUT_DIR, PROCESSED_DIR, FAILED_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    # Scan for files
    physical_files = {
        p.name: p 
        for p in INPUT_DIR.iterdir() 
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    }
    
    if not physical_files:
        log.warning(f"No video files found in {INPUT_DIR}")
        return "No files found"

    # Determine execution order
    queue_order = load_queue_order()
    ordered_files = []
    
    # 1. Add ordered files first
    for name in queue_order:
        if name in physical_files:
            ordered_files.append(physical_files[name])
            del physical_files[name]
            
    # 2. Add remaining files
    ordered_files.extend(physical_files.values())

    log.info(f"Found {len(ordered_files)} videos in input queue. Starting batch...")
    
    # Initialize Uploader
    try:
        # Re-import Settings to ensure we have latest env vars if they changed
        from biliflow.config.settings import Settings
        # HACK: Reload settings manually or just rely on env vars being set
        # For now, let's assume env vars are set or .env is correct on disk.
        # Ideally we pass credentials explicitly, but BiliUploader reads from global settings.
        # We might need to restart the process to pick up new env vars if they were just saved.
        # OR: We can update the global `settings` object.
        
        uploader = BiliUploader()
    except Exception as e:
        log.critical(f"Failed to initialize uploader: {e}")
        raise e

    # Process queue
    for video_file in ordered_files:
        await process_video(uploader, video_file)
    
    return "Batch complete"
