"""Shared content admission gate for all content-pipeline upstreams."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy.exc import IntegrityError, OperationalError

from models import db
from models.content_pipeline_asset import ContentPipelineAsset


logger = logging.getLogger(__name__)

# A failed clean is safe to retry. Everything else is considered canonical and
# must not create another clean task for the same media type and content hash.
RECLAIMABLE_STATUSES = {"failed", "cancelled", "released"}
STALE_RESERVATION_STATUSES = {"reserved", "cleaning"}
RESERVATION_TTL = timedelta(hours=6)


def fingerprint_file(path):
    """Return a stable content fingerprint for one validated local file."""
    source = Path(str(path)).expanduser().resolve()
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "path": str(source),
        "filename": source.name,
        "size": source.stat().st_size,
        "sha256": digest.hexdigest(),
    }


def fingerprint_files(files):
    """Fingerprint files in input order, preserving caller ordering."""
    return [fingerprint_file(path) for path in files or []]


def reserve_files(*, task_id, media_type, files, source_mode=None):
    """Reserve unique content hashes for one submitted clean task.

    The database unique constraint is the final concurrency guard. A nested
    transaction lets a racing insert fall back to reading the canonical row
    without poisoning the task submission transaction.
    """
    normalized_type = str(media_type or "").strip().lower()
    if not normalized_type:
        raise ValueError("media_type 不能为空")

    fingerprints = fingerprint_files(files)
    unique = []
    duplicates = []
    reserved_by_hash = {}
    for fingerprint in fingerprints:
        prior = reserved_by_hash.get(fingerprint["sha256"])
        if prior is not None:
            duplicates.append(
                {
                    **fingerprint,
                    "status": "duplicate",
                    "reason": "duplicate_content_in_submission",
                    "duplicate_of": prior["path"],
                    "canonical_asset_id": prior.get("asset_id"),
                    "canonical_task_id": str(task_id),
                    "canonical_status": "reserved",
                }
            )
            continue
        canonical = (
            ContentPipelineAsset.query
            .filter(
                ContentPipelineAsset.media_type == normalized_type,
                ContentPipelineAsset.content_sha256 == fingerprint["sha256"],
            )
            .with_for_update()
            .one_or_none()
        )

        if canonical is None:
            canonical = _insert_or_load_canonical(
                task_id=task_id,
                media_type=normalized_type,
                fingerprint=fingerprint,
                source_mode=source_mode,
            )

        if canonical.canonical_task_id != str(task_id):
            if canonical.status in RECLAIMABLE_STATUSES or _is_stale_reservation(canonical):
                canonical.canonical_task_id = str(task_id)
                canonical.source_path = fingerprint["path"]
                canonical.filename = fingerprint["filename"]
                canonical.source_mode = str(source_mode or "")[:80] or None
                canonical.status = "reserved"
                db.session.flush()
            else:
                duplicates.append(
                    _duplicate_entry(fingerprint, canonical, reason="duplicate_content")
                )
                continue

        unique.append(
            {
                **fingerprint,
                "asset_id": canonical.id,
                "status": "reserved",
            }
        )
        reserved_by_hash[fingerprint["sha256"]] = unique[-1]

    return {
        "strategy": "sha256",
        "media_type": normalized_type,
        "received_count": len(fingerprints),
        "unique_count": len(unique),
        "duplicate_count": len(duplicates),
        "unique_files": unique,
        "duplicates": duplicates,
    }


def mark_task_assets(task_id, *, status):
    """Advance all canonical assets reserved by a clean task."""
    normalized = str(status or "").strip().lower()
    if not normalized:
        return 0
    try:
        updated = (
            ContentPipelineAsset.query
            .filter(ContentPipelineAsset.canonical_task_id == str(task_id))
            .update(
                {
                    ContentPipelineAsset.status: normalized,
                    ContentPipelineAsset.updated_at: db.func.now(),
                },
                synchronize_session=False,
            )
        )
        db.session.commit()
        return int(updated or 0)
    except OperationalError as exc:
        # Older isolated task tests and pre-migration maintenance workers may
        # not have the optional projection table yet. The clean task itself is
        # still authoritative; the admission path requires the migration.
        db.session.rollback()
        if "content_pipeline_assets" not in str(exc):
            raise
        logger.warning("content pipeline asset table unavailable; status not updated | task=%s", task_id)
        return 0


def _insert_or_load_canonical(*, task_id, media_type, fingerprint, source_mode):
    try:
        with db.session.begin_nested():
            asset = ContentPipelineAsset(
                media_type=media_type,
                content_sha256=fingerprint["sha256"],
                canonical_task_id=str(task_id),
                source_path=fingerprint["path"],
                filename=fingerprint["filename"],
                source_mode=str(source_mode or "")[:80] or None,
                status="reserved",
            )
            db.session.add(asset)
            db.session.flush()
            return asset
    except IntegrityError:
        # Another submitter won the unique hash race. Its row is now visible
        # after the savepoint rollback and is the canonical answer.
        canonical = (
            ContentPipelineAsset.query
            .filter(
                ContentPipelineAsset.media_type == media_type,
                ContentPipelineAsset.content_sha256 == fingerprint["sha256"],
            )
            .with_for_update()
            .one_or_none()
        )
        if canonical is None:
            raise
        return canonical


def _duplicate_entry(fingerprint, canonical, *, reason):
    return {
        **fingerprint,
        "status": "duplicate",
        "reason": reason,
        "duplicate_of": canonical.source_path,
        "canonical_asset_id": canonical.id,
        "canonical_task_id": canonical.canonical_task_id,
        "canonical_status": canonical.status,
    }


def _is_stale_reservation(canonical):
    return (
        canonical.status in STALE_RESERVATION_STATUSES
        and canonical.updated_at is not None
        and canonical.updated_at < datetime.utcnow() - RESERVATION_TTL
    )


__all__ = [
    "fingerprint_file",
    "fingerprint_files",
    "reserve_files",
    "mark_task_assets",
]
