import asyncio
import logging
from typing import List, Optional
from pathlib import Path

from bilibili_api import Credential
from bilibili_api.video_uploader import VideoUploader, VideoUploaderPage, VideoMeta
from .utils import extract_cover

logger = logging.getLogger("bilibili")

class BiliUploader:
    """Bilibili video uploader service."""

    def __init__(self, sessdata: str = None, bili_jct: str = None, 
                 buvid3: str = None, dedeuserid: str = None):
        """
        Initialize the uploader with credentials.
        """
        self.credential = Credential(
            sessdata=sessdata,
            bili_jct=bili_jct,
            buvid3=buvid3,
            dedeuserid=dedeuserid
        )

    async def upload_single(
        self,
        video_path: str,
        title: str,
        desc: str = "",
        tid: int = 171,  # 171 = 生活/电子竞技
        tags: List[str] = None,
        cover_path: Optional[str] = None,
        copyright: int = 1,
        no_reprint: int = 1, # Default allow reprint? No, usually 1 means NO reprint (forbidden). Let's check logic.
        dynamic: str = ""
    ):
        """
        Uploads a single video file to Bilibili.
        """
        if tags is None:
            tags = ["BiliFlow"]
            
        path_obj = Path(video_path)
        if not path_obj.exists():
            logger.error(f"Video file not found: {video_path}")
            raise FileNotFoundError(f"Video file not found: {video_path}")
            
        logger.info(f"Preparing Bilibili upload for: {title}")

        # Bilibili title limit is 80 characters
        if len(title) > 80:
            logger.warning(f"Title too long ({len(title)} chars), truncating for Bilibili.")
            title = title[:77] + "..."

        # Bilibili description limit is 2000 characters
        if desc and len(desc) > 2000:
            logger.warning(f"Description too long ({len(desc)} chars), truncating for Bilibili.")
            desc = desc[:1997] + "..."

        # Map copyright (1=Original, 2=Reprint) to VideoMeta 'original' (bool)
        is_original = (copyright == 1)

        # Fallback for cover: if not provided, extract from video
        # This avoids 'NoneType' or 'missing argument' errors in bilibili_api
        actual_cover = cover_path
        temp_cover_created = False
        if not actual_cover:
            try:
                logger.info("No custom cover provided, extracting frame from video...")
                actual_cover = await extract_cover(path_obj)
                temp_cover_created = True
            except Exception as e:
                logger.warning(f"Failed to extract cover: {e}. Trying to continue without cover (may fail).")

        # Construct Meta
        meta = VideoMeta(
            title=title,
            desc=desc,
            tid=tid,
            tags=tags,
            cover=actual_cover or "", # Ensure it's never None
            original=is_original,
            dynamic=dynamic
        )
        
        # Construct Page
        page = VideoUploaderPage(
            path=str(path_obj),
            title=title
        )
        
        # Initialize Uploader
        uploader = VideoUploader(
            pages=[page],
            meta=meta,
            credential=self.credential,
            cover=actual_cover or ""
        )
        
        # Add event listeners for logging
        @uploader.on("UPLOAD_MSG")
        async def on_upload_msg(data):
            logger.debug(f"Bilibili Upload MSG: {data}")
            
        logger.info(f"Starting Bilibili upload for {title}...")
        
        # Retry logic for network stability
        max_retries = 3
        for attempt in range(max_retries):
            try:
                result = await uploader.start()
                logger.info(f"Bilibili upload successful for {title}! Result: {result}")
                return result
            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = 5
                    logger.warning(f"Upload attempt {attempt + 1}/{max_retries} failed: {e}. Retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"Bilibili upload failed for {title} after {max_retries} attempts: {e}")
                    raise
