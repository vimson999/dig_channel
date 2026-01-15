import asyncio
import logging
from pathlib import Path

logger = logging.getLogger("bilibili")

async def extract_cover(video_path: Path) -> str:
    """
    Uses ffmpeg to extract the first frame of the video as a cover image.
    Returns the absolute path of the generated image.
    """
    cover_path = video_path.with_suffix(".jpg")
    
    # If already exists, return it
    if cover_path.exists():
        return str(cover_path.absolute())

    cmd = [
        "ffmpeg", 
        "-y", 
        "-i", str(video_path), 
        "-ss", "00:00:01", 
        "-vframes", "1", 
        str(cover_path)
    ]
    
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE
        )
        
        _, stderr = await process.communicate()
        
        if process.returncode != 0:
            logger.error(f"FFmpeg failed to extract cover: {stderr.decode()}")
            raise RuntimeError("Failed to generate default cover")
            
        logger.info(f"Auto-generated cover: {cover_path.name}")
        return str(cover_path.absolute())
    except FileNotFoundError:
        logger.error("FFmpeg not found. Please install ffmpeg.")
        raise RuntimeError("FFmpeg not found")
