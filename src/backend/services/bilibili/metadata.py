import json
from pathlib import Path
from typing import Optional, List
from pydantic import BaseModel

class BiliVideoMetadata(BaseModel):
    """Bilibili video metadata model."""
    title: str
    desc: str = ""
    tid: int = 171 # Default to Lifestyle (生活/电子竞技)
    tags: List[str] = ["BiliFlow"]
    cover_path: Optional[str] = None
    copyright: int = 1  # 1=Original, 2=Reprint
    dynamic: str = ""   # Dynamic feed text

def get_metadata_path(video_path: Path) -> Path:
    """Get the path to the metadata JSON file for a video."""
    return video_path.with_suffix(video_path.suffix + ".json")

def load_metadata(video_path: Path) -> BiliVideoMetadata:
    """Load metadata from a JSON file, or return default if not found."""
    meta_path = get_metadata_path(video_path)
    if meta_path.exists():
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return BiliVideoMetadata(**data)
        except Exception:
            # Fallback to default on error
            pass
    
    # Default metadata based on filename
    return BiliVideoMetadata(
        title=video_path.stem
    )

def save_metadata(video_path: Path, meta: BiliVideoMetadata):
    """Save metadata to a JSON file."""
    meta_path = get_metadata_path(video_path)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta.model_dump(), f, ensure_ascii=False, indent=2)
