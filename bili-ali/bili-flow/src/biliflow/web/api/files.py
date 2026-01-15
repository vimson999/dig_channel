from fastapi import APIRouter, HTTPException, UploadFile, File, Body
from fastapi.responses import FileResponse
from pathlib import Path
from typing import List
import shutil
from biliflow.core.queue_manager import load_queue_order, save_queue_order
from biliflow.core.metadata import load_metadata, save_metadata, VideoMetadata

router = APIRouter()

router = APIRouter()

INPUT_DIR = Path("data/input")

SUPPORTED_VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".flv", ".avi"}

@router.get("/files/input")
async def list_input_files():
    """
    List files in the input directory, sorted by user-defined queue order.
    """
    if not INPUT_DIR.exists():
        return []
    
    # Get physical files
    physical_files = {}
    for p in INPUT_DIR.iterdir():
        if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in SUPPORTED_VIDEO_EXTS:
            physical_files[p.name] = {
                "name": p.name,
                "size": p.stat().st_size,
                "created": p.stat().st_ctime
            }
            
    # Load order
    order = load_queue_order()
    
    # Construct result list based on order
    sorted_files = []
    
    # 1. Add files present in order
    for name in order:
        if name in physical_files:
            sorted_files.append(physical_files[name])
            del physical_files[name] # Mark as processed
            
    # 2. Append remaining new files (not yet sorted)
    for file_info in physical_files.values():
        sorted_files.append(file_info)
        
    return sorted_files

@router.post("/files/reorder")
async def reorder_files(order: List[str] = Body(...)):
    """
    Update the priority order of files.
    """
    try:
        save_queue_order(order)
        return {"status": "success", "message": "Queue order updated"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/files/input/{filename}")
async def delete_input_file(filename: str):
    """
    Delete a specific file from the input directory.
    """
    file_path = INPUT_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    
    try:
        file_path.unlink()
        return {"status": "success", "message": f"Deleted {filename}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/files/input")
async def delete_all_input_files():
    """
    Clear all files in the input directory.
    """
    if not INPUT_DIR.exists():
        return {"status": "success", "message": "Directory empty"}
    
    deleted_count = 0
    errors = []
    
    for p in INPUT_DIR.iterdir():
        if p.is_file():
            try:
                p.unlink()
                deleted_count += 1
            except Exception as e:
                errors.append(f"{p.name}: {e}")
    
    return {
        "status": "success", 
        "deleted": deleted_count, 
        "errors": errors
    }

@router.post("/files/upload")
async def upload_video(file: UploadFile = File(...)):
    """
    Upload a video file to the input directory (Task Registration).
    """
    try:
        file_location = INPUT_DIR / file.filename
        with open(file_location, "wb+") as file_object:
            shutil.copyfileobj(file.file, file_object)
        return {"status": "success", "filename": file.filename, "message": "Task registered"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/files/input/{filename}/metadata")
async def get_file_metadata(filename: str):
    file_path = INPUT_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    
    meta = load_metadata(file_path)
    return meta

@router.post("/files/input/{filename}/metadata")
async def update_file_metadata(filename: str, meta: VideoMetadata):
    file_path = INPUT_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    
    try:
        save_metadata(file_path, meta)
        return {"status": "success", "message": "Metadata saved"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/files/input/{filename}/cover")
async def upload_cover(filename: str, file: UploadFile = File(...)):
    """
    Upload a cover image for the specified video.
    """
    video_path = INPUT_DIR / filename
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video file not found")
        
    # Determine cover filename (e.g. video.mp4 -> video.mp4.cover.jpg)
    cover_ext = Path(file.filename).suffix
    cover_filename = f"{filename}.cover{cover_ext}"
    cover_path = INPUT_DIR / cover_filename
    
    try:
        with open(cover_path, "wb+") as file_object:
            shutil.copyfileobj(file.file, file_object)
            
        # Update metadata automatically
        meta = load_metadata(video_path)
        meta.cover_path = str(cover_path.absolute())
        save_metadata(video_path, meta)
        
        return {
            "status": "success", 
            "message": "Cover uploaded", 
            "cover_path": str(cover_path.absolute())
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/files/cover-preview")
async def get_cover_preview(path: str):
    """
    Serve the cover image for preview.
    Security Note: In production, validate that path is within allowed dirs.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(file_path)
