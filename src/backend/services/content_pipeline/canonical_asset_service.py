"""Canonical cleaned-file registry for shared content-pipeline assets."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from models import db
from models.content_pipeline_asset import ContentPipelineAsset


PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "runtime" / "content_pipeline" / "batches"
MEDIA_EXTENSIONS = {
    "audio": {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg"},
    "pdf": {".pdf"},
}
SUCCESS_STATUSES = {"", "success", "skipped"}


@dataclass(frozen=True)
class CanonicalAssetResolution:
    path: Path | None
    state: str
    reason: str | None
    asset: ContentPipelineAsset | None


@dataclass
class CleanedOutputRegistration:
    registered_paths: list[str] = field(default_factory=list)
    registered_asset_ids: list[str] = field(default_factory=list)
    failed_asset_ids: list[str] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)

    @property
    def success_count(self) -> int:
        return len(self.registered_asset_ids)

    @property
    def failed_count(self) -> int:
        return len(self.failed_asset_ids)


def resolve_cleaned_asset(asset, *, media_type: str) -> CanonicalAssetResolution:
    """Return the verified cleaned file for a canonical media asset.

    A successful clean never falls back to the input download path.  This is
    intentionally strict so a deleted or damaged runtime output becomes a
    repairable state instead of silently serving an uncleaned source file.
    """
    normalized_type = _normalize_media_type(media_type)
    if asset is None or getattr(asset, "media_type", None) != normalized_type:
        return CanonicalAssetResolution(None, "missing", "asset_not_found", asset)
    if str(getattr(asset, "status", "") or "").lower() != "cleaned":
        return CanonicalAssetResolution(None, "processing", "clean_not_completed", asset)
    path = _validated_cleaned_path(getattr(asset, "cleaned_path", None), normalized_type)
    if path is None:
        return CanonicalAssetResolution(
            None,
            "unavailable",
            "cleaned_output_missing_or_invalid",
            asset,
        )
    return CanonicalAssetResolution(path, "ready", None, asset)


def resolve_asset_reference(
    reference: str,
    *,
    media_type: str,
    legacy_source_path: str | Path | None = None,
) -> CanonicalAssetResolution:
    """Resolve a pipeline reference or legacy source binding to a clean file."""
    normalized_type = _normalize_media_type(media_type)
    raw_reference = str(reference or "").strip()
    asset = None
    if raw_reference.startswith("pipeline:"):
        asset_id = raw_reference.split(":", 1)[1].strip()
        if asset_id:
            asset = (
                ContentPipelineAsset.query
                .filter(
                    ContentPipelineAsset.id == asset_id,
                    ContentPipelineAsset.media_type == normalized_type,
                )
                .one_or_none()
            )
    elif legacy_source_path:
        source_path = _normalized_path(legacy_source_path)
        if source_path:
            asset = (
                ContentPipelineAsset.query
                .filter(
                    ContentPipelineAsset.media_type == normalized_type,
                    ContentPipelineAsset.source_path == source_path,
                )
                .order_by(ContentPipelineAsset.updated_at.desc())
                .first()
            )
    return resolve_cleaned_asset(asset, media_type=normalized_type)


def register_cleaned_outputs(
    task_id: str,
    *,
    media_type: str,
    cleaned_files: Iterable[dict[str, Any]] | None,
    source_root: str | Path | None = None,
    dry_run: bool = False,
) -> CleanedOutputRegistration:
    """Persist verified cleaner outputs for each asset owned by one task.

    Each source path must identify exactly one reserved asset.  The function is
    idempotent and uses the same root and extension checks as runtime reads.
    """
    normalized_type = _normalize_media_type(media_type)
    normalized_task_id = str(task_id or "").strip()
    registration = CleanedOutputRegistration()
    if not normalized_task_id:
        return registration

    assets = (
        ContentPipelineAsset.query
        .filter(
            ContentPipelineAsset.canonical_task_id == normalized_task_id,
            ContentPipelineAsset.media_type == normalized_type,
        )
        .with_for_update()
        .all()
    )
    by_source: dict[str, list[ContentPipelineAsset]] = {}
    for asset in assets:
        source = _normalized_path(asset.source_path)
        if source:
            by_source.setdefault(source, []).append(asset)

    seen_asset_ids: set[str] = set()
    source_root_path = _normalized_path(source_root)
    for raw_item in cleaned_files or []:
        item = raw_item if isinstance(raw_item, dict) else {}
        source = _source_path_from_item(item, source_root_path)
        candidates = by_source.get(source or "", [])
        if len(candidates) != 1:
            registration.skipped.append({
                "source_path": source,
                "reason": "asset_not_found" if not candidates else "ambiguous_asset_source",
            })
            continue
        asset = candidates[0]
        seen_asset_ids.add(str(asset.id))
        status = str(item.get("status") or "").strip().lower()
        output = _output_path_from_item(item)
        output_path = _validated_cleaned_path(output, normalized_type)
        if status not in SUCCESS_STATUSES or output_path is None:
            _mark_asset_failed(asset, registration)
            registration.skipped.append({
                "asset_id": str(asset.id),
                "source_path": source,
                "reason": "cleaner_failed" if status not in SUCCESS_STATUSES else "invalid_cleaned_output",
            })
            continue

        digest, size = _fingerprint_output(output_path)
        asset.cleaned_path = str(output_path)
        asset.cleaned_sha256 = digest
        asset.cleaned_bytes = size
        asset.cleaned_at = datetime.utcnow()
        asset.status = "cleaned"
        registration.registered_paths.append(str(output_path))
        registration.registered_asset_ids.append(str(asset.id))

    for asset in assets:
        if str(asset.id) not in seen_asset_ids and str(asset.status or "").lower() != "cleaned":
            _mark_asset_failed(asset, registration)
            registration.skipped.append({
                "asset_id": str(asset.id),
                "source_path": _normalized_path(asset.source_path),
                "reason": "missing_cleaner_result",
            })

    if dry_run:
        # This registry is often used by a maintenance preview. Flush first so
        # validation covers the exact same SQL constraints, then discard every
        # mutation before returning the audit report.
        db.session.flush()
        db.session.rollback()
    else:
        db.session.commit()
    return registration


def backfill_task_cleaned_outputs(task, *, dry_run: bool = False) -> dict[str, Any]:
    """Register historical clean outputs without guessing missing source links.

    Old local PDF results store ``source`` as a path relative to ``source_root``;
    ``register_cleaned_outputs`` resolves that form only against the recorded
    task source root. A missing/ambiguous mapping remains in the report rather
    than being associated to a similarly named source file.
    """
    task_id = str(getattr(task, "id", "") or "").strip()
    params = getattr(task, "params", None)
    params = params if isinstance(params, dict) else {}
    result = getattr(task, "result", None)
    result = result if isinstance(result, dict) else {}
    media_type, source_root = _task_media_context(task, params)
    if not task_id or media_type is None:
        return {
            "task_id": task_id or None,
            "status": "skipped",
            "reason": "unsupported_content_pipeline_task",
            "registered_count": 0,
            "unresolved_count": 0,
            "registered_paths": [],
        }

    registration = register_cleaned_outputs(
        task_id,
        media_type=media_type,
        cleaned_files=_task_cleaned_file_items(result),
        source_root=source_root,
        dry_run=dry_run,
    )
    unresolved_count = registration.failed_count + len(registration.skipped)
    return {
        "task_id": task_id,
        "media_type": media_type,
        "status": "preview" if dry_run else "completed",
        "registered_count": registration.success_count,
        "failed_count": registration.failed_count,
        "unresolved_count": unresolved_count,
        "registered_paths": list(registration.registered_paths),
        "registered_asset_ids": list(registration.registered_asset_ids),
        "failed_asset_ids": list(registration.failed_asset_ids),
        "skipped": list(registration.skipped),
    }


def _mark_asset_failed(asset: ContentPipelineAsset, registration: CleanedOutputRegistration) -> None:
    asset.status = "failed"
    asset.cleaned_path = None
    asset.cleaned_sha256 = None
    asset.cleaned_bytes = None
    asset.cleaned_at = None
    asset_id = str(asset.id)
    if asset_id not in registration.failed_asset_ids:
        registration.failed_asset_ids.append(asset_id)


def _task_media_context(task, params: dict[str, Any]) -> tuple[str | None, str | None]:
    feature = str(getattr(task, "feature", None) or params.get("feature") or "").strip()
    pipeline = params.get("content_pipeline") if isinstance(params.get("content_pipeline"), dict) else {}
    if feature == "content_pdf_clean" or isinstance(pipeline.get("pdf_clean"), dict):
        config = pipeline.get("pdf_clean") if isinstance(pipeline.get("pdf_clean"), dict) else {}
        return "pdf", _normalized_path(config.get("source_root"))
    if feature == "content_audio_clean" or isinstance(pipeline.get("audio_clean"), dict):
        config = pipeline.get("audio_clean") if isinstance(pipeline.get("audio_clean"), dict) else {}
        return "audio", _normalized_path(config.get("source_root"))
    return None, None


def _task_cleaned_file_items(result: dict[str, Any]) -> list[dict[str, Any]]:
    files = result.get("files") if isinstance(result.get("files"), list) else []
    cleaned = result.get("cleaned_files") if isinstance(result.get("cleaned_files"), list) else []
    if not files:
        return [item for item in cleaned if isinstance(item, dict)]
    if cleaned and not any(
        isinstance(item, dict)
        and (item.get("output_path") or item.get("cleaner_output_path") or item.get("path"))
        for item in files
    ):
        return [item for item in cleaned if isinstance(item, dict)]
    return [item for item in files if isinstance(item, dict)]


def _normalize_media_type(media_type: str) -> str:
    normalized = str(media_type or "").strip().lower()
    if normalized not in MEDIA_EXTENSIONS:
        raise ValueError("media_type 必须是 audio 或 pdf")
    return normalized


def _output_root() -> Path:
    return Path(
        os.getenv("CONTENT_PIPELINE_OUTPUT_ROOT") or DEFAULT_OUTPUT_ROOT
    ).expanduser().resolve()


def _validated_cleaned_path(value: str | Path | None, media_type: str) -> Path | None:
    path = _normalized_path(value)
    if not path:
        return None
    candidate = Path(path)
    root = _output_root()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if not candidate.exists() or not candidate.is_file():
        return None
    if candidate.suffix.lower() not in MEDIA_EXTENSIONS[media_type]:
        return None
    try:
        if candidate.stat().st_size <= 0:
            return None
    except OSError:
        return None
    return candidate


def _normalized_path(value: str | Path | None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return str(Path(raw).expanduser().resolve())
    except OSError:
        return None


def _source_path_from_item(item: dict[str, Any], source_root: str | None) -> str | None:
    value = item.get("source_path") or item.get("source")
    raw = str(value or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute() and source_root:
        path = Path(source_root) / path
    return _normalized_path(path)


def _output_path_from_item(item: dict[str, Any]) -> str | Path | None:
    return (
        item.get("cleaner_output_path")
        or item.get("output_path")
        or item.get("path")
    )


def _fingerprint_output(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest(), path.stat().st_size


__all__ = [
    "backfill_task_cleaned_outputs",
    "CanonicalAssetResolution",
    "CleanedOutputRegistration",
    "register_cleaned_outputs",
    "resolve_asset_reference",
    "resolve_cleaned_asset",
]
