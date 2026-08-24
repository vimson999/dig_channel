import json
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

from api.report_script_material_workflow import _visible_user_ids
from models import ReportPdfEvidenceClaim, ReportPdfEvidenceOutput, db
from models.task import PublicationTask
from services.report_pdf_evidence import (
    PdfEvidenceError,
    bulk_set_claim_visuals,
    claim_payload,
    output_payload,
    render_claim_screenshot,
    set_visuals_enabled,
    update_claim,
)
from utils.auth_utils import require_login


report_pdf_evidence_bp = Blueprint(
    "report_pdf_evidence",
    __name__,
    url_prefix="/api/report-pdf-evidence",
)


@report_pdf_evidence_bp.route("/tasks/<source_task_id>", methods=["GET"])
@require_login
def evidence_for_task(current_user, source_task_id):
    output = _owned_output(current_user, source_task_id=source_task_id)
    if not output:
        source_task = _owned_source_task(current_user, source_task_id)
        if not source_task:
            return jsonify({"success": False, "message": "任务不存在"}), 404
        task_status = str(source_task.status or "").strip().lower()
        evidence_summary = (
            (source_task.result or {}).get("pdf_evidence")
            if isinstance(source_task.result, dict)
            else None
        )
        task_params = source_task.params if isinstance(source_task.params, dict) else {}
        workflow_params = next((
            task_params.get(key)
            for key in ("report_audio_workflow", "report_script_material_workflow")
            if isinstance(task_params.get(key), dict)
        ), {})
        explicitly_disabled = (
            "pdf_evidence_enabled" in workflow_params
            and not _bool_value(workflow_params.get("pdf_evidence_enabled"), False)
        ) or (
            isinstance(evidence_summary, dict)
            and evidence_summary.get("status") == "disabled"
        )
        artifact_summary = _orphaned_artifact_summary(source_task_id)
        if explicitly_disabled:
            evidence_status = "disabled"
        elif task_status in {"pending", "processing", "queued", "running"}:
            evidence_status = "pending_generation"
        elif artifact_summary:
            evidence_status = "persistence_failed"
        elif isinstance(evidence_summary, dict) and evidence_summary.get("status") == "failed":
            evidence_status = "generation_failed"
        else:
            evidence_status = "legacy_unavailable"
        return jsonify({
            "success": True,
            "data": {
                "source_task_id": str(source_task_id),
                "status": evidence_status,
                "source_task_status": task_status,
                "source_task_step": source_task.current_step,
                "source_task_progress": int(source_task.progress or 0),
                "visuals_enabled": False,
                "claim_count": 0,
                "verified_claim_count": 0,
                "claims": [],
                "error": (
                    "证据文件已经生成，但数据库记录保存失败；可安全补写，无需重跑 Gemini、文案或 TTS。"
                    if evidence_status == "persistence_failed"
                    else (
                        str(evidence_summary.get("error") or "PDF 证据映射生成失败")
                        if evidence_status == "generation_failed"
                        else None
                    )
                ),
                "artifact_summary": artifact_summary,
            },
        })
    return jsonify({"success": True, "data": output_payload(output)})


@report_pdf_evidence_bp.route("/tasks/<source_task_id>", methods=["PATCH"])
@require_login
def update_evidence_output(current_user, source_task_id):
    output = _owned_output(current_user, source_task_id=source_task_id)
    if not output:
        return jsonify({"success": False, "message": "该任务没有证据映射"}), 404
    data = request.get_json(silent=True) or {}
    try:
        bulk_action = str(data.get("bulk_action") or "").strip().lower()
        if bulk_action == "select_all_visuals":
            changed = bulk_set_claim_visuals(output, enabled=True)
        elif bulk_action == "clear_all_visuals":
            changed = bulk_set_claim_visuals(output, enabled=False)
        else:
            changed = None
            set_visuals_enabled(output, _bool_value(data.get("visuals_enabled"), output.visuals_enabled))
    except PdfEvidenceError as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": str(exc)}), 400
    payload = output_payload(output)
    if changed is not None:
        payload["changed_claim_count"] = changed
    return jsonify({"success": True, "data": payload})


@report_pdf_evidence_bp.route("/claims/<claim_id>", methods=["PATCH"])
@require_login
def edit_claim(current_user, claim_id):
    claim, output = _owned_claim(current_user, claim_id)
    if not claim:
        return jsonify({"success": False, "message": "证据不存在"}), 404
    data = request.get_json(silent=True) or {}
    allowed = {
        "claim_text", "script_quote", "pdf_page", "pdf_quote", "claim_type",
        "evidence_kind", "crop_x", "crop_y", "crop_width", "crop_height",
        "enabled", "use_as_visual", "manual_confirmed",
    }
    try:
        update_claim(claim, {key: data[key] for key in allowed if key in data})
    except PdfEvidenceError as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": str(exc)}), 400
    return jsonify({
        "success": True,
        "data": {
            "claim": claim_payload(claim),
            "evidence": output_payload(output, include_claims=False),
        },
    })


@report_pdf_evidence_bp.route("/claims/<claim_id>/image", methods=["GET"])
@require_login
def claim_image(current_user, claim_id):
    claim, output = _owned_claim(current_user, claim_id)
    if not claim:
        return jsonify({"success": False, "message": "证据不存在"}), 404
    try:
        path = claim.screenshot_path
        if not path or not Path(path).is_file():
            path = render_claim_screenshot(output, claim)
            claim.screenshot_path = path
            db.session.commit()
    except PdfEvidenceError as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": str(exc)}), 400
    return send_file(
        path,
        mimetype="image/png",
        as_attachment=request.args.get("inline") not in {"1", "true"},
        download_name=f"pdf-evidence-{claim.sequence:03d}.png",
        conditional=True,
    )


def _owned_output(current_user, *, source_task_id=None, output_id=None):
    query = (
        db.session.query(ReportPdfEvidenceOutput)
        .join(PublicationTask, PublicationTask.id == ReportPdfEvidenceOutput.source_task_id)
        .filter(PublicationTask.user_id.in_(_visible_user_ids(current_user)))
    )
    if source_task_id:
        query = query.filter(ReportPdfEvidenceOutput.source_task_id == str(source_task_id))
    if output_id:
        query = query.filter(ReportPdfEvidenceOutput.id == str(output_id))
    return query.one_or_none()


def _owned_source_task(current_user, source_task_id):
    return (
        PublicationTask.query
        .filter(
            PublicationTask.id == str(source_task_id),
            PublicationTask.user_id.in_(_visible_user_ids(current_user)),
        )
        .one_or_none()
    )


def _owned_claim(current_user, claim_id):
    claim = ReportPdfEvidenceClaim.query.filter_by(id=str(claim_id)).one_or_none()
    if not claim:
        return None, None
    output = _owned_output(current_user, output_id=claim.output_id)
    return (claim, output) if output else (None, None)


def _bool_value(value, fallback=False):
    if value is None:
        return bool(fallback)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _orphaned_artifact_summary(source_task_id):
    root = (
        Path(__file__).resolve().parents[3]
        / "data"
        / "processed"
        / "report_pdf_evidence"
        / str(source_task_id)
    )
    raw_path = root / "candidate_evidence.json"
    normalized_path = root / "verified_evidence.json"
    if not raw_path.is_file() and not normalized_path.is_file():
        return None
    summary = {
        "candidate_artifact": raw_path.is_file(),
        "verified_artifact": normalized_path.is_file(),
        "candidate_count": 0,
        "claim_count": 0,
    }
    for path, key in ((raw_path, "candidate_count"), (normalized_path, "claim_count")):
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if key == "candidate_count":
                summary[key] = max(0, int(payload.get("candidate_count") or 0))
            else:
                summary[key] = len(payload.get("claims") or [])
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return summary
