import asyncio
import logging
from typing import List, Optional
from pathlib import Path

from bilibili_api import Credential
from bilibili_api.video_uploader import VideoUploader, VideoUploaderPage, VideoMeta

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

        # Map copyright (1=Original, 2=Reprint) to VideoMeta 'original' (bool)
        is_original = (copyright == 1)

        # Construct Meta
        meta = VideoMeta(
            title=title,
            desc=desc,
            tid=tid,
            tags=tags,
            cover=cover_path,
            original=is_original,
            dynamic=dynamic
        )
        
        # FIX: bilibili-api's VideoMeta doesn't set 'cover' attr if it's None,
        # but VideoUploader expects it to exist. We manually patch it.
        if not hasattr(meta, "cover"):
            meta.cover = None
        
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
            cover=cover_path
        )
        
        # Add event listeners for logging
        @uploader.on("UPLOAD_MSG")
        async def on_upload_msg(data):
            logger.debug(f"Bilibili Upload MSG: {data}")
            
        logger.info(f"Starting Bilibili upload for {title}...")
        try:
            result = await uploader.start()
            logger.info(f"Bilibili upload successful for {title}! Result: {result}")
            return result
        except Exception as e:
            logger.error(f"Bilibili upload failed for {title}: {e}")
            raise
