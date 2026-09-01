"""Fail-closed state machine for the formal report-video director workflow."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Sequence

from services.report_video_director import (
    build_director_package,
    evaluate_full_render_gate,
    human_approval,
)
from services.report_video_director_enrichment import (
    attach_audio_timeline,
    build_mixed_media_asset_plan,
    enrich_package_evidence,
)
from services.report_video_hyperframes_adapter import (
    authorize_hyperframes_render,
    build_hyperframes_spec,
)


WORKFLOW_SCHEMA_VERSION = "report_video_director_workflow_v1"


class DirectorWorkflowError(ValueError):
    """Raised when a workflow transition is invalid or unsafe."""


def create_workflow_state(
    material: Dict[str, Any],
    evidence_map: Sequence[Dict[str, Any]],
    *,
    pdf_path: str,
    execution_mode: str,
    workflow_task_id: str,
    target_segment_chars: int = 130,
) -> Dict[str, Any]:
    package = build_director_package(
        material,
        evidence_map,
        source_task_id=str(material.get("job_id") or ""),
        package_id=f"director-workflow:{workflow_task_id}",
        execution_mode=execution_mode,
        target_segment_chars=target_segment_chars,
    )
    if not package["validation"]["valid"]:
        raise DirectorWorkflowError("导演包校验失败，工作流保持失败关闭")
    enriched = enrich_package_evidence(package, pdf_path=pdf_path)
    if enriched["evidence_enrichment"]["fail_closed"]:
        raise DirectorWorkflowError("真实 PDF 证据核验失败，工作流保持失败关闭")
    return {
        "schema_version": WORKFLOW_SCHEMA_VERSION,
        "workflow_task_id": workflow_task_id,
        "execution_mode": execution_mode,
        "status": "processing",
        "current_step": "awaiting_audio_alignment",
        "director_package": enriched,
        "asset_plan": None,
        "hyperframes_spec": None,
        "preview_artifacts": {"keyframes": [], "preview_clips": []},
        "human_review": None,
        "full_render_gate": {
            "allowed": False,
            "status": "blocked",
            "blockers": [{"code": "audio_alignment_required"}],
        },
        "dispatch_bundle": None,
        "publish_allowed": False,
        "rollback": {
            "strategy": "append_only_revision_and_hash_bound_approval",
            "existing_production_tasks_mutated": False,
        },
        "audit": [_audit("workflow_created")],
    }


def attach_workflow_alignment(
    state: Mapping[str, Any],
    *,
    alignment: Mapping[str, Any],
    audio_path: str,
    audio_seconds: float,
) -> Dict[str, Any]:
    payload = _state(state)
    if payload["current_step"] != "awaiting_audio_alignment":
        raise DirectorWorkflowError("当前状态不接受音频时间线")
    package = attach_audio_timeline(
        payload["director_package"],
        audio_path=audio_path,
        audio_seconds=audio_seconds,
        alignment=alignment,
    )
    asset_plan = build_mixed_media_asset_plan(package)
    if not asset_plan["production_ready"]:
        raise DirectorWorkflowError("素材计划未达到生产准备状态")
    spec = build_hyperframes_spec(
        package,
        asset_plan,
        execution_mode=payload["execution_mode"],
    )
    payload["director_package"] = package
    payload["asset_plan"] = asset_plan
    payload["hyperframes_spec"] = spec
    payload["current_step"] = "awaiting_previews"
    payload["audit"].append(_audit("audio_alignment_attached"))
    return payload


def record_workflow_previews(
    state: Mapping[str, Any],
    *,
    preview_artifacts: Mapping[str, Sequence[Mapping[str, Any]]],
    test_authorization: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    payload = _state(state)
    if payload["current_step"] not in {
        "awaiting_previews",
        "awaiting_human_review",
    }:
        raise DirectorWorkflowError("当前状态不接受预览产物")
    artifacts = {
        "keyframes": [dict(item) for item in preview_artifacts.get("keyframes") or []],
        "preview_clips": [
            dict(item) for item in preview_artifacts.get("preview_clips") or []
        ],
    }
    gate = evaluate_full_render_gate(
        payload["director_package"],
        preview_artifacts=artifacts,
        human_review=payload.get("human_review"),
        execution_mode=payload["execution_mode"],
        test_authorization=(
            dict(test_authorization)
            if isinstance(test_authorization, Mapping)
            else None
        ),
    )
    payload["preview_artifacts"] = artifacts
    payload["full_render_gate"] = gate
    if payload["execution_mode"] == "production":
        payload["current_step"] = "awaiting_human_review"
    else:
        payload["current_step"] = (
            "full_render_authorized" if gate["allowed"] else "awaiting_previews"
        )
    payload["audit"].append(
        _audit(
            "previews_recorded",
            {"gate_allowed": gate["allowed"]},
        )
    )
    return payload


def record_workflow_human_review(
    state: Mapping[str, Any],
    *,
    reviewer_id: str,
    decision: str,
    notes: str = "",
) -> Dict[str, Any]:
    payload = _state(state)
    if payload["execution_mode"] != "production":
        raise DirectorWorkflowError("测试模式不接受生产人工放行")
    if payload["current_step"] != "awaiting_human_review":
        raise DirectorWorkflowError("必须先完成代表预览")
    review = human_approval(
        payload["director_package"],
        reviewer_id=reviewer_id,
        decision=decision,
        notes=notes,
    )
    gate = evaluate_full_render_gate(
        payload["director_package"],
        preview_artifacts=payload["preview_artifacts"],
        human_review=review,
        execution_mode="production",
    )
    payload["human_review"] = review
    payload["full_render_gate"] = gate
    if decision == "terminated":
        payload["status"] = "canceled"
        payload["current_step"] = "terminated"
    elif decision == "modify":
        payload["current_step"] = "revision_required"
    else:
        payload["current_step"] = (
            "full_render_authorized"
            if gate["allowed"]
            else "awaiting_human_review"
        )
    payload["audit"].append(
        _audit(
            "human_review_recorded",
            {"decision": decision, "gate_allowed": gate["allowed"]},
        )
    )
    return payload


def build_workflow_dispatch_bundle(
    state: Mapping[str, Any],
) -> Dict[str, Any]:
    payload = _state(state)
    if payload["current_step"] != "full_render_authorized":
        raise DirectorWorkflowError("完整素材下发尚未获授权")
    spec = payload.get("hyperframes_spec")
    spec = spec if isinstance(spec, Mapping) else {}
    authorization = authorize_hyperframes_render(
        spec,
        full_render_gate=payload["full_render_gate"],
    )
    if not authorization["allowed"]:
        raise DirectorWorkflowError(
            "HyperFrames 下发授权失败：" + ",".join(authorization["blockers"])
        )
    bundle = {
        "schema_version": "report_video_director_dispatch_bundle_v1",
        "workflow_task_id": payload["workflow_task_id"],
        "package_id": payload["director_package"]["package_id"],
        "package_hash": payload["full_render_gate"]["package_hash"],
        "execution_mode": payload["execution_mode"],
        "asset_plan": payload["asset_plan"],
        "hyperframes_spec": spec,
        "authorization": authorization,
        "child_modules": sorted(
            {
                item["module"]
                for item in payload["asset_plan"]["items"]
                if item.get("module")
            }
        ),
        "publish_allowed": False,
        "production_task_mutation_scope": "new_child_tasks_only",
        "rollback": {
            "on_child_failure": "mark_workflow_failed_and_stop_downstream",
            "on_package_hash_change": "invalidate_approval_and_dispatch",
            "existing_task_mutation": False,
        },
    }
    payload["dispatch_bundle"] = bundle
    payload["current_step"] = "dispatch_ready"
    payload["audit"].append(_audit("dispatch_bundle_created"))
    return payload


def _state(state: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(state, Mapping):
        raise DirectorWorkflowError("工作流状态必须是对象")
    payload = deepcopy(dict(state))
    if payload.get("schema_version") != WORKFLOW_SCHEMA_VERSION:
        raise DirectorWorkflowError("工作流 schema 不受支持")
    if payload.get("publish_allowed") is not False:
        raise DirectorWorkflowError("工作流必须禁止发布")
    if not isinstance(payload.get("audit"), list):
        payload["audit"] = []
    return payload


def _audit(event: str, details: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    return {
        "event": event,
        "at": datetime.now(timezone.utc).isoformat(),
        "details": dict(details or {}),
    }
