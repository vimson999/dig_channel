import json
from pathlib import Path
from typing import Optional, List, Dict
from pydantic import BaseModel

class VideoMetadata(BaseModel):
    title: str
    desc: str = ""
    tid: int = 171 # Default to Lifestyle
    tags: List[str] = ["BiliFlow"]
    cover_path: Optional[str] = None
    copyright: int = 1  # 1=Original, 2=Reprint
    dynamic: str = ""   # Dynamic feed text

def get_metadata_path(video_path: Path) -> Path:
    return video_path.with_suffix(video_path.suffix + ".json")

def load_metadata(video_path: Path) -> VideoMetadata:
    meta_path = get_metadata_path(video_path)
    if meta_path.exists():
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return VideoMetadata(**data)
        except:
            pass
    
    # Default metadata
    return VideoMetadata(
        title=video_path.stem
    )

def save_metadata(video_path: Path, meta: VideoMetadata):
    meta_path = get_metadata_path(video_path)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta.model_dump(), f, ensure_ascii=False, indent=2)
