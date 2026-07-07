import asyncio
import logging
from typing import Any, Dict, List, Optional
from pathlib import Path

import requests
from bilibili_api import Credential
from bilibili_api.video_uploader import VideoUploader, VideoUploaderPage, VideoMeta
from .utils import extract_cover

logger = logging.getLogger("bilibili")

DEFAULT_NEUTRAL_MARK = "个人观点，仅供参考"
DEFAULT_CHARGING_PREVIEW_END = 5
CREATION_STATEMENT_IDS = {
    "内容无需标注": -1,
    "含AI生成内容": 1,
    "含虚构演绎内容": 2,
    "内容含营销信息": 3,
    "个人观点，仅供参考": 4,
    "内容为转载": -2,
}

class BiliUploader:
    """Bilibili video uploader service."""

    def __init__(self, sessdata: str = None, bili_jct: str = None, 
                 buvid3: str = None, dedeuserid: str = None):
        """
        Initialize the uploader with credentials.
        """
        self.sessdata = sessdata
        self.bili_jct = bili_jct
        self.buvid3 = buvid3
        self.dedeuserid = dedeuserid
        self.credential = Credential(
            sessdata=sessdata,
            bili_jct=bili_jct,
            buvid3=buvid3,
            dedeuserid=dedeuserid
        )

    @staticmethod
    def _truthy(value: Any) -> bool:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @staticmethod
    def _base_meta_dict(
        *,
        title: str,
        desc: str,
        tid: int,
        tags: List[str],
        copyright: int,
        no_reprint: int,
        dynamic: str,
        neutral_mark: Optional[str],
        delay_time: Optional[int] = None,
        season_id: Optional[int] = None,
        is_only_self: int = 0,
        space_hidden: Optional[int] = None,
        creation_statement_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        meta = {
            "title": title,
            "copyright": copyright,
            "tid": tid,
            "tag": ",".join(tags),
            "desc_format_id": 9999,
            "desc": desc,
            "recreate": -1,
            "dynamic": dynamic or "",
            "interactive": 0,
            "act_reserve_create": 0,
            "no_disturbance": 0,
            "porder": None,
            "adorder_type": 9,
            "no_reprint": 1 if int(no_reprint or 0) else 0,
            "subtitle": {"open": 0, "lan": ""},
            "neutral_mark": None if creation_statement_id is not None else (neutral_mark or None),
            "dolby": 0,
            "lossless_music": 0,
            "up_selection_reply": False,
            "up_close_reply": False,
            "up_close_danmu": False,
            "web_os": 1,
            "source": None,
            "watermark": {"state": 0},
            "is_only_self": 1 if int(is_only_self or 0) else 0,
        }
        if creation_statement_id is not None:
            meta["creation_statement"] = {"id": int(creation_statement_id)}
        if space_hidden is not None:
            meta["space_hidden"] = int(space_hidden)
        if delay_time:
            meta["dtime"] = int(delay_time)
        if season_id:
            meta["season_id"] = int(season_id)
            meta["no_disturbance"] = 0
        return {key: value for key, value in meta.items() if value is not None}

    def _fetch_charging_pay_info(self) -> Dict[str, Any]:
        cookies = {
            "SESSDATA": self.sessdata,
            "bili_jct": self.bili_jct,
            "buvid3": self.buvid3,
            "DedeUserID": self.dedeuserid,
        }
        cookies = {key: value for key, value in cookies.items() if value}
        response = requests.get(
            "https://member.bilibili.com/x/vupre/web/archive/pre",
            params={"lang": "cn"},
            cookies=cookies,
            headers={
                "user-agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
                "referer": "https://member.bilibili.com/platform/upload/video/frame",
            },
            timeout=30,
        )
        response.raise_for_status()
        body = response.json()
        if body.get("code") != 0:
            raise RuntimeError(f"Bilibili archive/pre failed: {body.get('message') or body.get('code')}")

        info = ((body.get("data") or {}).get("charging_pay_info") or {})
        if not self._truthy(info.get("upower_auth")):
            raise RuntimeError("当前账号未开放充电专属视频权限")
        return info

    def charging_pay_options(self) -> Dict[str, Any]:
        info = self._fetch_charging_pay_info()
        levels = [item for item in (info.get("upower_level_list") or info.get("levels") or []) if item]
        if not levels and isinstance(info.get("upower_level"), dict):
            levels = [info.get("upower_level")]
        if not levels and isinstance(info.get("default_upower_level"), dict):
            levels = [info.get("default_upower_level")]
        output = []
        for level in levels:
            if not isinstance(level, dict):
                continue
            publish_info = level.get("publish_info") or level.get("publishInfo") or {}
            level_id = (
                level.get("id")
                or level.get("level_id")
                or level.get("upower_level_id")
                or publish_info.get("id")
                or publish_info.get("level_id")
                or publish_info.get("upower_level_id")
            )
            if not level_id:
                continue
            label = (
                publish_info.get("level_title_str")
                or publish_info.get("level_title")
                or publish_info.get("title")
                or level.get("level_title_str")
                or level.get("level_title")
                or level.get("name")
                or level.get("title")
                or f"充电档位 {level_id}"
            )
            unit_price = (
                level.get("upower_unit_price")
                or level.get("unit_price")
                or level.get("price")
                or publish_info.get("upower_unit_price")
                or publish_info.get("unit_price")
                or publish_info.get("price")
                or 0
            )
            output.append({
                "id": str(level_id),
                "label": str(label),
                "upower_unit_price": int(unit_price or 0),
                "raw": {
                    "publish_info": publish_info,
                },
            })
        if not output:
            raise RuntimeError("当前账号没有可用的充电档位")
        return {
            "upower_auth": True,
            "levels": output,
            "default_level_id": str(
                info.get("upower_level_id")
                or info.get("default_upower_level_id")
                or output[0]["id"]
            ),
        }

    def _fetch_default_upower_level_id(self) -> str:
        options = self.charging_pay_options()
        level = (options.get("levels") or [{}])[0]
        level_id = level.get("id") if isinstance(level, dict) else None
        if not level_id:
            raise RuntimeError("当前账号没有可用的充电档位")

        logger.info(
            "Using Bilibili charging level: %s (%s)",
            level.get("label") or "default",
            level_id,
        )
        return str(level_id)

    def _charging_pay_fields(self, charging_pay: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not charging_pay or not self._truthy(charging_pay.get("enabled", charging_pay.get("charging_pay"))):
            return None

        preview = charging_pay.get("preview") or {}
        qa = charging_pay.get("qa") or {}
        member_first = charging_pay.get("member_first") or {}
        upower_mode = int(charging_pay.get("upower_mode") or 0)
        upower_level_id = charging_pay.get("upower_level_id") or self._fetch_default_upower_level_id()

        return {
            "charging_pay": 1,
            "upower_level_id": str(upower_level_id),
            "upower_mode": upower_mode,
            "upower_unit_price": int(charging_pay.get("upower_unit_price") or 0),
            "preview": {
                "need_preview": 1 if self._truthy(preview.get("need_preview", True)) else 0,
                "start_time": int(preview.get("start_time") or 0),
                "end_time": int(
                    preview.get("end_time")
                    or charging_pay.get("preview_end_time")
                    or DEFAULT_CHARGING_PREVIEW_END
                ),
            },
            "qa": {
                "qa_enabled": 1 if self._truthy(qa.get("qa_enabled") or qa.get("enabled")) else 0,
                "qa_id": qa.get("qa_id"),
            },
            "member_first": {
                "enabled": 1 if self._truthy(member_first.get("enabled")) else 0,
                "exp_time": int(member_first.get("exp_time") or 0),
            },
            "limited_free": None,
        }

    @staticmethod
    def _normalize_delay_time(value: Any) -> Optional[int]:
        if value in (None, "", 0, "0", False):
            return None
        try:
            return int(float(value))
        except (TypeError, ValueError):
            raise ValueError("Bilibili delay_time must be a Unix timestamp in seconds")

    @staticmethod
    def _normalize_season_id(value: Any) -> Optional[int]:
        if value in (None, "", 0, "0", False):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            raise ValueError("Bilibili season_id must be an integer")

    @staticmethod
    def _normalize_creation_statement_id(value: Any, neutral_mark: Optional[str]) -> Optional[int]:
        if value not in (None, ""):
            try:
                return int(value)
            except (TypeError, ValueError):
                raise ValueError("Bilibili creation_statement_id must be an integer")
        if isinstance(neutral_mark, str):
            return CREATION_STATEMENT_IDS.get(neutral_mark.strip())
        return None

    async def upload_single(
        self,
        video_path: str,
        title: str,
        desc: str = "",
        tid: int = 207,  # 207 = 知识 / 财经商业
        tags: List[str] = None,
        cover_path: Optional[str] = None,
        copyright: int = 1,
        no_reprint: int = 1, # Default allow reprint? No, usually 1 means NO reprint (forbidden). Let's check logic.
        dynamic: str = "",
        neutral_mark: Optional[str] = DEFAULT_NEUTRAL_MARK,
        charging_pay: Optional[Dict[str, Any]] = None,
        delay_time: Optional[int] = None,
        season_id: Optional[int] = None,
        is_only_self: int = 0,
        space_hidden: Optional[int] = None,
        creation_statement_id: Optional[int] = None,
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
        neutral_mark = neutral_mark.strip() if isinstance(neutral_mark, str) else None
        creation_statement_id = self._normalize_creation_statement_id(
            creation_statement_id,
            neutral_mark,
        )
        charging_pay_fields = self._charging_pay_fields(charging_pay)
        delay_time = self._normalize_delay_time(delay_time)
        season_id = self._normalize_season_id(season_id)
        is_only_self = 1 if int(is_only_self or 0) else 0
        space_hidden = int(space_hidden) if space_hidden not in (None, "") else None
        if charging_pay_fields and not is_original:
            raise ValueError("Bilibili charging pay videos must be original")

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

        if (
            charging_pay_fields
            or season_id
            or is_only_self
            or space_hidden is not None
            or creation_statement_id is not None
        ):
            meta = self._base_meta_dict(
                title=title,
                desc=desc,
                tid=tid,
                tags=tags,
                copyright=copyright,
                no_reprint=no_reprint,
                dynamic=dynamic,
                neutral_mark=neutral_mark,
                delay_time=delay_time,
                season_id=season_id,
                is_only_self=is_only_self,
                space_hidden=space_hidden,
                creation_statement_id=creation_statement_id,
            )
            if charging_pay_fields:
                meta.update(charging_pay_fields)
        else:
            # Construct Meta
            meta = VideoMeta(
                title=title,
                desc=desc,
                tid=tid,
                tags=tags,
                cover=actual_cover or "", # Ensure it's never None
                original=is_original,
                dynamic=dynamic,
                no_reprint=bool(int(no_reprint)) if no_reprint is not None else False,
                neutral_mark=neutral_mark or None,
                delay_time=delay_time
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
