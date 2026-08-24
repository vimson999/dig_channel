import logging
from datetime import datetime

from sqlalchemy.orm import load_only
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.exc import OperationalError

from app import create_app
from celery_config import celery_app
from models import db
from models.task import PublicationTask
from services.gemini_browser import (
    FEATURE_NAME,
    LEGACY_FEATURE_NAME,
    GeminiBrowserOptions,
    GeminiBrowserWorker,
    storage_path_for_job,
)
from services.report_audio_gem_v2 import (
    SCHEMA_VERSION as REPORT_AUDIO_GEM_V2_SCHEMA_VERSION,
    build_gem_v2_repair_prompt,
    validate_gem_v2_response,
)
from services.runtime_guard import release_db_session_quietly


logger = logging.getLogger("REPORT_PDF_GEMINI_TASK")

TASK_UPDATE_COLUMNS = (
    PublicationTask.id,
    PublicationTask.celery_task_id,
    PublicationTask.status,
    PublicationTask.current_step,
    PublicationTask.progress,
    PublicationTask.result,
    PublicationTask.error_message,
    PublicationTask.completed_at,
)

TASK_PAYLOAD_COLUMNS = (
    *TASK_UPDATE_COLUMNS,
    PublicationTask.params,
    PublicationTask.feature,
    PublicationTask.created_at,
)


def _load_task(task_id, *columns):
    attrs = columns or TASK_PAYLOAD_COLUMNS
    return (
        PublicationTask.query.options(load_only(*attrs))
        .filter(PublicationTask.id == str(task_id))
        .first()
    )


def _update_active_task(task_id, values):
    updated = (
        PublicationTask.query
        .filter(PublicationTask.id == str(task_id))
        .filter(PublicationTask.status != "canceled")
        .update(values, synchronize_session=False)
    )
    db.session.commit()
    return bool(updated)


def _run_with_app_context(task_id):
    app = create_app()
    with app.app_context():
        return _process_job(task_id)


@celery_app.task(
    bind=True,
    name="tasks.report_pdf_gemini.process_job",
    store_errors_even_if_ignored=True,
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def process_report_pdf_gemini_job(self, task_id):
    app = create_app()
    with app.app_context():
        return _process_job(task_id, celery_task=self)


@celery_app.task(
    bind=True,
    name="tasks.gemini_browser.process_job",
    store_errors_even_if_ignored=True,
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def process_gemini_browser_job(self, task_id):
    app = create_app()
    with app.app_context():
        return _process_job(task_id, celery_task=self)


def _process_job(task_id, celery_task=None):
    task = _load_task(task_id, *TASK_PAYLOAD_COLUMNS)
    if not task:
        logger.error("Report PDF Gemini task not found: %s", task_id)
        return False
    if not isinstance(task.params, dict) or (task.feature or task.params.get("feature")) not in {FEATURE_NAME, LEGACY_FEATURE_NAME}:
        logger.error("Invalid report PDF Gemini task params: %s", task_id)
        return False
    request_id = str(
        getattr(getattr(celery_task, "request", None), "id", "") or ""
    )
    current_celery_id = str(getattr(task, "celery_task_id", "") or "")
    if request_id and current_celery_id and request_id != current_celery_id:
        logger.info(
            "Report PDF Gemini task received stale delivery; task=%s request=%s current=%s",
            task_id,
            request_id,
            current_celery_id,
        )
        return False
    if task.status == "completed":
        logger.info("Report PDF Gemini task already completed; skip duplicate: %s", task_id)
        return True
    if task.status in {"failed", "blocked_by_captcha", "canceled"}:
        return False

    job_id = task.id
    job_created_at = task.created_at or datetime.utcnow()
    payload = task.params.get("report_pdf_gemini") or task.params.get("gemini_browser") or {}
    prompt = (payload.get("prompt") or "").strip()
    followups = [str(item).strip() for item in (payload.get("followups") or []) if str(item).strip()]
    target_url = (payload.get("url") or "").strip() or None
    pdf_path = (payload.get("pdf_path") or "").strip() or None
    if not prompt and not pdf_path:
        _mark_failed(task, "Prompt 和 PDF 不能同时为空")
        return False

    result = dict(task.result or {})
    result["started_at"] = datetime.utcnow().isoformat() + "Z"
    result["logs"] = result.get("logs") or []
    task.status = "processing"
    task.current_step = "prepare_browser"
    task.progress = 3
    task.result = result
    flag_modified(task, "result")
    db.session.commit()
    db.session.expunge_all()
    release_db_session_quietly()
    progress_result = result

    def update_progress(event):
        nonlocal progress_result
        current_result = dict(progress_result)
        logs = list(progress_result.get("logs") or [])
        logs.append({
            "step": event.get("step"),
            "message": event.get("message"),
            "progress": event.get("progress"),
            "updated_at": event.get("updated_at"),
        })
        current_result["logs"] = logs[-80:]
        if event.get("result"):
            current_result.update(event["result"])
        progress_result = current_result
        if not _update_active_task(job_id, {
            PublicationTask.current_step: event.get("step") or "processing",
            PublicationTask.progress: int(event.get("progress") or 0),
        }):
            raise RuntimeError("任务已取消或不存在")

    try:
        output_path = storage_path_for_job(job_id, job_created_at)
        options = GeminiBrowserOptions.from_env()
        if "prefer_pro_model" in payload:
            options.prefer_pro_model = _truthy(payload.get("prefer_pro_model"), True)
        options.require_pro_model = _truthy(payload.get("require_pro_model"), False)
        contract_validator, contract_repair_builder, contract_max_repairs = _response_contract_callbacks(payload)
        timeout_rounds = list(followups) + (["response_contract_repair"] * contract_max_repairs)
        _apply_total_timeout_budget(options, timeout_rounds)
        worker = GeminiBrowserWorker(options)
        gemini_result = worker.generate(
            job_id=job_id,
            prompt=prompt,
            output_path=output_path,
            pdf_path=pdf_path,
            target_url=target_url,
            followup_prompts=followups,
            response_contract_validator=contract_validator,
            response_contract_repair_prompt_builder=contract_repair_builder,
            response_contract_max_repairs=contract_max_repairs,
            progress_callback=update_progress,
        )

        final_result = dict(progress_result)
        final_result.update({
            "response_text": gemini_result.get("response_text"),
            "final_response_text": gemini_result.get("final_response_text") or gemini_result.get("response_text"),
            "initial_response_text": gemini_result.get("initial_response_text"),
            "turns": gemini_result.get("turns") or [],
            "followup_prompts": gemini_result.get("followup_prompts") or followups,
            "pdf": gemini_result.get("pdf"),
            "page_url": gemini_result.get("page_url"),
            "target_url": gemini_result.get("target_url"),
            "output_path": gemini_result.get("output_path"),
            "response_contract_validation": gemini_result.get("response_contract_validation"),
            "completed_at": datetime.utcnow().isoformat() + "Z",
            "generation_requirements": {
                "prefer_pro_model": bool(options.prefer_pro_model),
                "require_pro_model": bool(options.require_pro_model),
                "minimum_final_response_chars": _bounded_int(
                    payload.get("minimum_final_response_chars"),
                    default=0,
                    lower=0,
                    upper=20000,
                ),
                "response_contract": payload.get("response_contract"),
                "contract_max_repairs": contract_max_repairs,
            },
        })
        final_response = final_result.get("final_response_text") or final_result.get("response_text") or ""
        final_chars = len("".join(str(final_response).split()))
        progress_result = final_result
        minimum_final_chars = final_result["generation_requirements"]["minimum_final_response_chars"]
        if final_chars < minimum_final_chars:
            raise RuntimeError(
                f"Gemini 最终文案仅 {final_chars} 字，低于本次要求的 {minimum_final_chars} 字"
            )
        if not _update_active_task(job_id, {
            PublicationTask.status: "completed",
            PublicationTask.current_step: "done",
            PublicationTask.progress: 100,
            PublicationTask.completed_at: datetime.utcnow(),
            PublicationTask.error_message: None,
            PublicationTask.result: final_result,
            PublicationTask.gemini_response_chars: final_chars,
        }):
            logger.warning("Report PDF Gemini task was canceled before final update: %s", job_id)
            return False
        logger.info("Report PDF Gemini task completed: %s", job_id)
        return True
    except Exception as exc:
        db.session.rollback()
        failure_result = dict(progress_result)
        failure_result["error"] = str(exc)
        failure_result["completed_at"] = datetime.utcnow().isoformat() + "Z"
        _update_active_task(job_id, {
            PublicationTask.status: "failed",
            PublicationTask.current_step: "failed",
            PublicationTask.error_message: str(exc),
            PublicationTask.completed_at: datetime.utcnow(),
            PublicationTask.result: failure_result,
            PublicationTask.gemini_response_chars: 0,
        })
        logger.error("Report PDF Gemini task failed: %s", exc, exc_info=True)
        return False


def _response_contract_callbacks(payload):
    request = payload if isinstance(payload, dict) else {}
    contract = str(request.get("response_contract") or "").strip()
    if not contract:
        return None, None, 0
    if contract != REPORT_AUDIO_GEM_V2_SCHEMA_VERSION:
        raise ValueError(f"Unsupported Gemini response contract: {contract}")

    expected_filename = str(request.get("contract_expected_pdf_filename") or "").strip()
    minimum_chars = _bounded_int(
        request.get("contract_minimum_script_chars"),
        default=0,
        lower=0,
        upper=20000,
    )
    actual_page_count = _bounded_int(
        request.get("contract_actual_pdf_page_count"),
        default=0,
        lower=0,
        upper=100000,
    ) or None
    allow_external = _truthy(request.get("contract_allow_external_information"), False)

    def validate(raw_text):
        validation = validate_gem_v2_response(
            raw_text,
            minimum_script_chars=minimum_chars,
            expected_pdf_filename=expected_filename,
            actual_pdf_page_count=actual_page_count,
            allow_external_information=allow_external,
        )
        return validation.diagnostics()

    def build_repair(diagnostics):
        errors = diagnostics.get("errors") if isinstance(diagnostics, dict) else []
        return build_gem_v2_repair_prompt(errors or [])

    max_repairs = _bounded_int(
        request.get("contract_max_repairs"),
        default=1,
        lower=0,
        upper=1,
    )
    return validate, build_repair, max_repairs


def _mark_failed(task, message):
    result = dict(task.result or {})
    result["error"] = message
    result["completed_at"] = datetime.utcnow().isoformat() + "Z"
    task.status = "failed"
    task.current_step = "failed"
    task.progress = task.progress or 0
    task.error_message = message
    task.completed_at = datetime.utcnow()
    task.result = result
    flag_modified(task, "result")
    db.session.commit()


def _truthy(value, default=False):
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _bounded_int(value, *, default, lower, upper):
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = int(default)
    return max(int(lower), min(int(upper), number))


def _apply_total_timeout_budget(options, followups):
    """Reserve one complete response window for every submitted Gemini turn.

    ``total_timeout_seconds`` starts before the PDF upload and used to default
    to 900 seconds, while a single response may consume 600 seconds.  A task
    with one explicit follow-up therefore had less than one full response
    window left for its second turn and could fail while Gemini was still
    actively generating.  Size the hard deadline from the actual number of
    turns, without shortening an operator-provided larger deadline.
    """

    turn_count = 1 + len(followups or [])
    response_budget = max(30, int(getattr(options, "response_timeout_seconds", 0) or 0))
    setup_budget = (
        max(0, int(getattr(options, "ready_timeout_seconds", 0) or 0))
        + max(0, int(getattr(options, "upload_timeout_seconds", 0) or 0))
        + 120
    )
    required_total = setup_budget + turn_count * response_budget
    options.total_timeout_seconds = max(
        int(getattr(options, "total_timeout_seconds", 0) or 0),
        required_total,
    )
    return options
