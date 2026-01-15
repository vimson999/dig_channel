import asyncio
from typing import List, Optional, Union
from pathlib import Path

from bilibili_api import Credential
from bilibili_api.video_uploader import VideoUploader, VideoUploaderPage, VideoMeta

from biliflow.config.settings import settings
from biliflow.utils.logger import log

class BiliUploader:
    def __init__(self):
        self.credential = Credential(
            sessdata=settings.SESSDATA,
            bili_jct=settings.BILI_JCT,
            buvid3=settings.BUVID3,
            dedeuserid=settings.DEDEUSERID
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
            log.error(f"Video file not found: {video_path}")
            raise FileNotFoundError(f"Video file not found: {video_path}")
            
        log.info(f"Preparing upload for: {title}")

        # Map copyright (1=Original, 2=Reprint) to VideoMeta 'original' (bool)
        is_original = (copyright == 1)

        # Construct Meta
        # Note: tags must be a list of strings
        meta = VideoMeta(
            title=title,
            desc=desc,
            tid=tid,
            tags=tags,
            cover=cover_path, # Pass None if no cover
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
            cover=cover_path # Pass None if no cover
        )
        
        # Add event listeners for logging
        @uploader.on("UPLOAD_MSG")
        async def on_upload_msg(data):
            log.debug(f"Upload MSG: {data}")
            
        log.info("Starting upload...")
        try:
            result = await uploader.start()
            log.success(f"Upload successful! Result: {result}")
            return result
        except Exception as e:
            log.error(f"Upload failed: {e}")
            raise
