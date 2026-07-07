import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from services.report_audio_workflow import (
    DEFAULT_INITIAL_PROMPT,
    DEFAULT_REVIEW_PROMPT,
    status_payload as audio_workflow_status_payload,
)


FEATURE_NAME = "report_script_material_workflow"
QUEUE_NAME = "report_script_material_workflow"


def storage_path_for_job(job_id: str, created_at: Optional[datetime] = None) -> str:
    created_at = created_at or datetime.utcnow()
    root = Path(_project_root()) / "data" / "processed" / FEATURE_NAME / created_at.strftime("%Y%m%d")
    root.mkdir(parents=True, exist_ok=True)
    return str(root / f"{job_id}.json")


def write_result_file(
    *,
    job_id: str,
    created_at: Optional[datetime],
    structured_result: Dict[str, Any],
    script_material: Dict[str, Any],
    pdf: Dict[str, Any],
    gemini: Optional[Dict[str, Any]] = None,
) -> str:
    output_path = storage_path_for_job(job_id, created_at)
    payload = {
        "workflow_task_id": job_id,
        "structured_result": structured_result,
        "script_material": script_material,
        "pdf": pdf,
        "gemini": gemini,
        "audio": None,
        "video": {
            "enabled": False,
            "status": "skipped",
            "message": FEATURE_NAME,
        },
        "completed_at": script_material.get("completed_at"),
    }
    with open(output_path, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
    return output_path


def status_payload() -> Dict[str, Any]:
    normalizer = audio_workflow_status_payload()
    return {
        "configured": bool(normalizer.get("configured")),
        "mode": "celery_orchestrator",
        "feature": FEATURE_NAME,
        "queue": QUEUE_NAME,
        "output": "structured_result.final_script_for_tts",
        "normalizer": normalizer,
        "normalizer_provider": normalizer.get("normalizer_provider"),
        "normalizer_available": normalizer.get("normalizer_available"),
        "local_fallback_allowed": normalizer.get("local_fallback_allowed"),
    }


def _project_root() -> str:
    return str(Path(__file__).resolve().parents[3])
