from fastapi import APIRouter, BackgroundTasks
from biliflow.engine.batch import run_batch
from biliflow.utils.logger import log

router = APIRouter()

@router.post("/tasks/upload")
async def trigger_upload(background_tasks: BackgroundTasks):
    """
    Triggers the batch upload process in the background.
    """
    log.info("Received manual trigger for batch upload.")
    background_tasks.add_task(run_batch)
    return {"status": "started", "message": "Batch upload started in background."}
