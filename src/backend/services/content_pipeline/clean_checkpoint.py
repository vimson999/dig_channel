"""Validated durable clean-output checkpoints used after task redelivery."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Iterable


SUCCESS_STATUSES = {"success", "skipped"}


def load_completed_clean_result(
    manifest_path: str | Path,
    *,
    batch_id: str,
    input_files: Iterable[str | Path],
    output_dir: str | Path,
    allowed_suffixes: set[str],
) -> dict | None:
    """Return a completed clean result only when every durable fact still matches.

    A Celery database retry can arrive after the expensive local work finished
    but before its database commit was acknowledged.  The manifest is reusable
    only for the same batch and exact input multiset, and only while every
    successful output is still a non-empty file below the expected output root.
    """
    manifest = Path(manifest_path).expanduser().resolve()
    expected_output_dir = Path(output_dir).expanduser().resolve()
    try:
        if not manifest.is_file():
            return None
        document = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(document, dict):
        return None

    nested = document.get("result")
    result = nested if isinstance(nested, dict) else document
    if str(result.get("batch_id") or "").strip() != str(batch_id or "").strip():
        return None
    try:
        recorded_output_dir = Path(str(result.get("local_output_dir") or "")).expanduser().resolve()
    except (OSError, TypeError, ValueError):
        return None
    if recorded_output_dir != expected_output_dir:
        return None

    expected_sources = Counter(_normalized_path(path) for path in input_files)
    result_files = result.get("files")
    if not expected_sources or not isinstance(result_files, list):
        return None
    recorded_sources = Counter(
        source
        for item in result_files
        if isinstance(item, dict)
        for source in [_normalized_path(item.get("source_path"))]
        if source
    )
    if recorded_sources != expected_sources:
        return None

    cleaned_files = result.get("cleaned_files")
    if not isinstance(cleaned_files, list) or not cleaned_files:
        return None
    normalized_suffixes = {str(value).lower() for value in allowed_suffixes}
    successful_result_items = [
        item
        for item in result_files
        if isinstance(item, dict)
        and str(item.get("status") or "").strip().lower() in SUCCESS_STATUSES
    ]
    result_signatures = _validated_output_signatures(
        successful_result_items,
        output_dir=expected_output_dir,
        allowed_suffixes=normalized_suffixes,
    )
    cleaned_signatures = _validated_output_signatures(
        cleaned_files,
        output_dir=expected_output_dir,
        allowed_suffixes=normalized_suffixes,
    )
    if result_signatures is None or cleaned_signatures is None:
        return None
    if Counter(result_signatures) != Counter(cleaned_signatures):
        return None
    try:
        if int(result.get("success_count")) != len(cleaned_files):
            return None
    except (TypeError, ValueError):
        return None

    resumed = dict(result)
    resumed["resumed_from_checkpoint"] = True
    resumed["resume_checkpoint_path"] = str(manifest)
    return resumed


def _normalized_path(value: str | Path | None) -> str:
    try:
        return str(Path(str(value or "")).expanduser().resolve())
    except (OSError, TypeError, ValueError):
        return ""


def _validated_output_signatures(items, *, output_dir: Path, allowed_suffixes: set[str]):
    signatures = []
    for item in items:
        if not isinstance(item, dict):
            return None
        status = str(item.get("status") or "").strip().lower()
        source = _normalized_path(item.get("source_path"))
        output = item.get("output_path") or item.get("cleaner_output_path") or item.get("path")
        if status not in SUCCESS_STATUSES or not source:
            return None
        try:
            candidate = Path(str(output or "")).expanduser().resolve()
            candidate.relative_to(output_dir)
            if (
                not candidate.is_file()
                or candidate.stat().st_size <= 0
                or candidate.suffix.lower() not in allowed_suffixes
            ):
                return None
        except (OSError, TypeError, ValueError):
            return None
        if candidate.suffix.lower() == ".pdf":
            try:
                with candidate.open("rb") as stream:
                    if stream.read(5) != b"%PDF-":
                        return None
            except OSError:
                return None
        signatures.append((source, str(candidate)))
    return signatures


__all__ = ["load_completed_clean_result"]
