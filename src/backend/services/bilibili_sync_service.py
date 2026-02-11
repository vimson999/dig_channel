import asyncio
import shutil
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

from .bilibili import BiliUploader, load_metadata, extract_cover
from config.base import BaseConfig
from services.config_service import ConfigService

logger = logging.getLogger("bilibili")

class BilibiliSyncService:
    """
    Bilibili sync service for batch processing and automated uploads.
    """
    
    SUPPORTED_EXTENSIONS = {".mp4", ".mkv", ".mov", ".flv", ".avi"}

    def __init__(self, config: Optional[BaseConfig] = None):
        self.config = config or BaseConfig()
        
        # Directories
        self.base_dir = Path(getattr(self.config, 'basedir', Path.cwd()))
        self.input_dir = self.base_dir / "data" / "input"
        self.processed_dir = self.base_dir / "data" / "processed"
        self.failed_dir = self.base_dir / "data" / "failed"
        
        # Ensure directories exist
        for d in [self.input_dir, self.processed_dir, self.failed_dir]:
            d.mkdir(parents=True, exist_ok=True)
            
        # Initialize Uploader
        self.uploader = BiliUploader(
            sessdata=ConfigService.get('sessdata', 'bilibili_auth'),
            bili_jct=ConfigService.get('bili_jct', 'bilibili_auth'),
            buvid3=ConfigService.get('buvid3', 'bilibili_auth'),
            dedeuserid=ConfigService.get('dedeuserid', 'bilibili_auth')
        )

    async def process_video(self, video_path: Path):
        """
        Process a single video file: Extract cover -> Upload -> Move.
        """
        filename = video_path.name
        meta = load_metadata(video_path)
        
        logger.info(f"Processing video: {filename}")
        
        # Ensure cover exists
        if not meta.cover_path or not Path(meta.cover_path).exists():
            try:
                meta.cover_path = await extract_cover(video_path)
            except Exception as e:
                logger.error(f"Could not generate cover for {filename}: {e}")
                # We can still try to upload without cover or with default
                meta.cover_path = None

        try:
            # Upload
            logger.info(f"Uploading '{meta.title}' to Bilibili...")
            await self.uploader.upload_single(
                video_path=str(video_path),
                title=meta.title,
                desc=meta.desc or f"Uploaded via Automation. Filename: {filename}",
                tid=meta.tid,
                tags=meta.tags,
                cover_path=meta.cover_path,
                copyright=meta.copyright,
                dynamic=meta.dynamic
            )
            
            # On Success: Move to processed
            destination = self.processed_dir / filename
            shutil.move(str(video_path), str(destination))
            # Also move metadata and cover if they exist
            self._cleanup_side_files(video_path, self.processed_dir)
            
            logger.info(f"Successfully processed and moved: {filename}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to upload {filename} to Bilibili: {e}")
            destination = self.failed_dir / filename
            try:
                shutil.move(str(video_path), str(destination))
                self._cleanup_side_files(video_path, self.failed_dir)
                logger.warning(f"Moved failed video to: {destination}")
            except Exception as move_error:
                logger.error(f"Could not move failed file {filename}: {move_error}")
            return False

    def _cleanup_side_files(self, video_path: Path, target_dir: Path):
        """Move metadata (.json) and auto-generated cover (.jpg) if they exist."""
        for suffix in [".json", ".jpg"]:
            side_file = video_path.with_suffix(video_path.suffix + suffix if suffix == ".json" else suffix)
            if side_file.exists():
                try:
                    shutil.move(str(side_file), str(target_dir / side_file.name))
                except Exception:
                    pass

    async def run_batch(self) -> Dict[str, Any]:
        """
        Scans input directory and processes all videos.
        """
        # Scan for files
        video_files = [
            p for p in self.input_dir.iterdir() 
            if p.is_file() and p.suffix.lower() in self.SUPPORTED_EXTENSIONS
        ]
        
        if not video_files:
            logger.info(f"No video files found in {self.input_dir}")
            return {"status": "success", "count": 0, "message": "No files found"}

        logger.info(f"Found {len(video_files)} videos. Starting Bilibili batch upload...")
        
        results = {"success": 0, "failed": 0, "total": len(video_files)}
        
        for video_file in video_files:
            success = await self.process_video(video_file)
            if success:
                results["success"] += 1
            else:
                results["failed"] += 1
                
        logger.info(f"Batch complete. Results: {results}")
        return {
            "status": "success",
            "data": results
        }
