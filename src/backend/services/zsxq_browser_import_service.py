"""Normalize and import Chrome sidebar Knowledge Planet download manifests.

The sidebar is deliberately treated as a second upstream.  This module keeps
the file-system and manifest contract separate from the existing API-backed
Knowledge Planet synchronizer.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


DEFAULT_GROUP_ID = "28888112822211"
DEFAULT_DOWNLOADS_ROOT = Path(
    os.environ.get("ZSXQ_BROWSER_DOWNLOADS_ROOT", "/Users/v9/Downloads")
).expanduser()

TAB_CONFIGS = {
    "pdf": {
        "kind": "pdf",
        "tab_id": "51184248544214",
        "tab_label": "外资研报",
        "download_count_gt": 30,
        "media_types": {"pdf", "application/pdf"},
        "extensions": {"pdf"},
    },
    "mp3": {
        "kind": "mp3",
        "tab_id": "88844545452542",
        "tab_label": "调研纪要",
        "download_count_gt": 10,
        "media_types": {"mp3", "audio/mpeg", "audio/mp3"},
        "extensions": {"mp3"},
    },
}

_CHROME_DUPLICATE_SUFFIX = re.compile(r"\s+\(\d+\)(?=\.[^.]+$)")
_RUN_DATE = re.compile(r"(?<!\d)(20\d{2})-?([01]\d)-?([0-3]\d)(?!\d)")


class BrowserImportValidationError(ValueError):
    """Raised when a sidebar manifest cannot be safely imported."""

    def __init__(
        self, message: str, *, details: Iterable[dict[str, Any]] | None = None
    ):
        super().__init__(message)
        self.details = list(details or [])


class IdempotencyConflictError(BrowserImportValidationError):
    """Raised when an idempotency key is reused for different content."""

    code = "idempotency_conflict"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_resolve(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _strip_chrome_duplicate_suffix(filename: str) -> str:
    return _CHROME_DUPLICATE_SUFFIX.sub("", filename)


def _manifest_entries(
    payload: Any,
) -> tuple[list[dict[str, Any]], str | None, dict[str, Any]]:
    if isinstance(payload, list):
        entries = payload
        run_id = None
        metadata = {}
    elif isinstance(payload, dict) and isinstance(payload.get("files"), list):
        entries = payload["files"]
        run_id = str(payload.get("run_id") or "").strip() or None
        metadata = dict(payload)
    else:
        raise BrowserImportValidationError("manifest 必须是数组，或包含 files 数组的对象")

    if not all(isinstance(entry, dict) for entry in entries):
        raise BrowserImportValidationError("manifest.files 中每一项必须是对象")
    return entries, run_id, metadata


def _kind_for_entry(entry: dict[str, Any], path: Path) -> str:
    media_type = str(entry.get("media_type") or "").strip().lower()
    extension = path.suffix.lower().lstrip(".")
    for kind, config in TAB_CONFIGS.items():
        if media_type in config["media_types"] or extension in config["extensions"]:
            if extension not in config["extensions"]:
                raise BrowserImportValidationError(
                    f"文件扩展名与媒体类型不匹配: {path.name} ({media_type})"
                )
            return kind
    raise BrowserImportValidationError(f"不支持的侧边栏文件类型: {path.name} ({media_type})")


def _stable_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def _title_for_entry(entry: dict[str, Any], actual_name: str) -> str:
    title = str(entry.get("topic_title") or "").strip()
    if title:
        return title
    return _strip_chrome_duplicate_suffix(actual_name)


class BrowserSidebarImportService:
    """Validate sidebar manifests and produce canonical import records."""

    def __init__(
        self,
        downloads_root: str | os.PathLike[str] | None = None,
        data_root: str | os.PathLike[str] | None = None,
    ):
        self.downloads_root = _safe_resolve(
            Path(downloads_root or DEFAULT_DOWNLOADS_ROOT)
        )
        self.data_root = _safe_resolve(Path(data_root)) if data_root else None

    @property
    def inbox_root(self) -> Path:
        return self.downloads_root / "知识星球下载" / "inbox"

    @staticmethod
    def _history_identity(entry: dict[str, Any]) -> tuple[str, ...]:
        file_id = str(entry.get("file_id") or "").strip()
        if file_id:
            return ("file_id", file_id)
        filename = _strip_chrome_duplicate_suffix(
            str(entry.get("filename") or entry.get("name") or "").strip()
        )
        published_at = str(entry.get("published_at") or "").strip()
        source_url = str(entry.get("source_url") or "").strip()
        digest = str(entry.get("sha256") or "").strip().lower()
        if filename or published_at or source_url:
            return ("metadata", filename, published_at, source_url)
        if digest:
            return ("sha256", digest)
        return ("record", json.dumps(entry, ensure_ascii=False, sort_keys=True))

    @staticmethod
    def _history_entry_matches_kind(entry: dict[str, Any], kind: str) -> bool:
        filename = str(entry.get("filename") or entry.get("name") or "").strip()
        extension = Path(filename).suffix.lower().lstrip(".")
        media_type = str(entry.get("media_type") or "").strip().lower()
        config = TAB_CONFIGS[kind]
        if extension:
            return extension in config["extensions"]
        if media_type:
            return media_type in config["media_types"]
        return True

    def recent_download_history(
        self,
        kind: str,
        *,
        days: int = 3,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Return successful sidebar downloads from recent natural days.

        The backend owns this filesystem access so the Chrome sidebar never
        needs permission to enumerate or read the Downloads inbox directly.
        """
        kind = str(kind or "").strip().lower()
        if kind not in TAB_CONFIGS:
            raise BrowserImportValidationError(f"不支持的历史文件类型: {kind}")
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 7:
            raise BrowserImportValidationError("days 必须是 1 到 7 之间的整数")

        current = now or datetime.now().astimezone()
        source_paths: list[Path] = []
        for offset in reversed(range(days)):
            date_key = (current - timedelta(days=offset)).strftime("%Y%m%d")
            result_path = self.inbox_root / f"sidebar-result-{kind}-{date_key}.json"
            if result_path.is_file():
                source_paths.append(result_path)
            source_paths.extend(
                sorted(self.inbox_root.glob(f"sidebar-submit-{kind}-{date_key}*.json"))
                if self.inbox_root.is_dir()
                else []
            )

        unique_paths = list(dict.fromkeys(source_paths))
        errors: list[dict[str, Any]] = []
        records_seen = 0
        downloads: dict[tuple[str, ...], dict[str, Any]] = {}

        for path in unique_paths:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                entries, _run_id, metadata = _manifest_entries(payload)
                declared_kind = str(metadata.get("kind") or "").strip().lower()
                if declared_kind and declared_kind != kind:
                    continue
                observations = metadata.get("history_observations") or []
                if not isinstance(observations, list) or not all(
                    isinstance(item, dict) for item in observations
                ):
                    raise BrowserImportValidationError("history_observations 必须是对象数组")
            except (
                OSError,
                UnicodeDecodeError,
                json.JSONDecodeError,
                BrowserImportValidationError,
            ) as exc:
                errors.append(
                    {
                        "code": "history_file_invalid",
                        "path": str(path),
                        "error": str(exc),
                    }
                )
                continue

            history_entries = [
                *((entry, False) for entry in entries),
                *((entry, True) for entry in observations),
            ]
            for entry, is_observation in history_entries:
                records_seen += 1
                if entry.get("success") is False:
                    continue
                if not self._history_entry_matches_kind(entry, kind):
                    continue
                identity = self._history_identity(entry)
                count_at_download = entry.get(
                    "download_count_at_download", entry.get("download_count")
                )
                latest_count = entry.get(
                    "latest_download_count", entry.get("download_count")
                )
                existing = downloads.get(identity)
                if existing is None:
                    if is_observation:
                        # An observation can refresh metadata for a previously
                        # downloaded file, but can never prove a download by
                        # itself.
                        continue
                    filename = _strip_chrome_duplicate_suffix(
                        str(entry.get("filename") or entry.get("name") or "").strip()
                    )
                    downloads[identity] = {
                        "file_id": entry.get("file_id"),
                        "filename": filename,
                        "published_at": entry.get("published_at"),
                        "source_url": entry.get("source_url"),
                        "downloaded_at": entry.get("downloaded_at"),
                        "download_count_at_download": count_at_download,
                        "latest_download_count": latest_count,
                        "last_seen_at": entry.get("last_seen_at"),
                        "sha256": entry.get("sha256"),
                        "success": True,
                    }
                    continue
                if latest_count is not None:
                    existing["latest_download_count"] = latest_count
                if not existing.get("downloaded_at") and entry.get("downloaded_at"):
                    existing["downloaded_at"] = entry.get("downloaded_at")
                if not existing.get("sha256") and entry.get("sha256"):
                    existing["sha256"] = entry.get("sha256")
                if entry.get("last_seen_at"):
                    existing["last_seen_at"] = entry.get("last_seen_at")

        files = list(downloads.values())
        return {
            "kind": kind,
            "days": days,
            "complete": not errors,
            "files": files,
            "errors": errors,
            "summary": {
                "source_files": len(unique_paths),
                "records_seen": records_seen,
                "unique_downloads": len(files),
                "duplicates_collapsed": max(records_seen - len(files), 0),
            },
        }

    def canonical_manifest_payload(
        self, manifest: dict[str, Any]
    ) -> tuple[dict[str, Any], str]:
        """Validate an inline sidebar manifest and return its stable digest."""
        if not isinstance(manifest, dict):
            raise BrowserImportValidationError("manifest 必须是 JSON 对象")
        try:
            canonical_text = json.dumps(
                manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            canonical = json.loads(canonical_text)
        except (TypeError, ValueError) as exc:
            raise BrowserImportValidationError(f"manifest 无法序列化为 JSON: {exc}") from exc

        run_id = str(canonical.get("run_id") or "").strip()
        if not run_id or len(run_id) > 200:
            raise BrowserImportValidationError("内联 manifest 必须提供长度不超过 200 的 run_id")
        kind = str(canonical.get("kind") or "").strip().lower()
        if kind not in TAB_CONFIGS:
            raise BrowserImportValidationError("内联 manifest 的 kind 只允许 mp3 或 pdf")
        config = TAB_CONFIGS[kind]
        if str(canonical.get("group_id") or "").strip() != DEFAULT_GROUP_ID:
            raise BrowserImportValidationError("内联 manifest 的 group_id 与服务端配置不匹配")
        if str(canonical.get("tab_id") or "").strip() != config["tab_id"]:
            raise BrowserImportValidationError("内联 manifest 的 tab_id 与 kind 不匹配")
        if str(canonical.get("tab_label") or "").strip() != config["tab_label"]:
            raise BrowserImportValidationError("内联 manifest 的 tab_label 与 kind 不匹配")

        files = canonical.get("files")
        if not isinstance(files, list):
            raise BrowserImportValidationError("内联 manifest 必须包含 files 数组")
        if len(files) > 40:
            raise BrowserImportValidationError("内联 manifest 单次最多包含 40 个文件")
        for index, entry in enumerate(files):
            if not isinstance(entry, dict):
                raise BrowserImportValidationError(
                    "内联 manifest.files 中每一项必须是对象",
                    details=[{"index": index}],
                )
            if entry.get("success") is False:
                raise BrowserImportValidationError(
                    "内联 manifest 只能提交已成功下载的文件",
                    details=[{"index": index}],
                )
            if not self._history_entry_matches_kind(entry, kind):
                raise BrowserImportValidationError(
                    f"内联 manifest 包含错误文件类型，预期 {kind}",
                    details=[{"index": index, "filename": entry.get("filename")}],
                )
            count = entry.get("download_count")
            if isinstance(count, bool):
                raise BrowserImportValidationError(
                    "download_count 必须是整数", details=[{"index": index}]
                )
            try:
                numeric_count = int(count)
            except (TypeError, ValueError):
                raise BrowserImportValidationError(
                    "内联 manifest 的 download_count 必须是整数",
                    details=[{"index": index}],
                )
            if numeric_count <= config["download_count_gt"]:
                raise BrowserImportValidationError(
                    f"下载次数必须严格大于 {config['download_count_gt']}",
                    details=[
                        {
                            "index": index,
                            "download_count": numeric_count,
                            "threshold_gt": config["download_count_gt"],
                        }
                    ],
                )
            entry["download_count"] = numeric_count

        observations = canonical.get("history_observations", [])
        if not isinstance(observations, list):
            raise BrowserImportValidationError("history_observations 必须是对象数组")
        if len(observations) > 500:
            raise BrowserImportValidationError("history_observations 单次最多包含 500 项")
        for index, entry in enumerate(observations):
            if not isinstance(entry, dict):
                raise BrowserImportValidationError(
                    "history_observations 中每一项必须是对象",
                    details=[{"index": index}],
                )
            if not any(
                str(entry.get(field) or "").strip()
                for field in ("file_id", "filename", "name", "sha256")
            ):
                raise BrowserImportValidationError(
                    "history_observations 必须包含文件标识",
                    details=[{"index": index}],
                )
            if not self._history_entry_matches_kind(entry, kind):
                raise BrowserImportValidationError(
                    f"history_observations 包含错误文件类型，预期 {kind}",
                    details=[{"index": index, "filename": entry.get("filename")}],
                )
            count_field = (
                "latest_download_count"
                if "latest_download_count" in entry
                else "download_count"
            )
            count = entry.get(count_field)
            if isinstance(count, bool):
                raise BrowserImportValidationError(
                    f"history_observations.{count_field} 必须是非负整数",
                    details=[{"index": index}],
                )
            try:
                numeric_count = int(count)
            except (TypeError, ValueError):
                raise BrowserImportValidationError(
                    f"history_observations.{count_field} 必须是非负整数",
                    details=[{"index": index}],
                )
            if numeric_count < 0:
                raise BrowserImportValidationError(
                    f"history_observations.{count_field} 必须是非负整数",
                    details=[{"index": index}],
                )
            entry[count_field] = numeric_count

        canonical_text = json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return canonical, hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()

    @staticmethod
    def _inline_run_filename_part(run_id: str) -> str:
        value = re.sub(r"[^A-Za-z0-9._-]+", "_", run_id).strip("._-")
        return value[:120] or "run"

    @staticmethod
    def _inline_run_date_key(run_id: str) -> str:
        match = _RUN_DATE.search(run_id)
        if not match:
            raise BrowserImportValidationError(
                "内联 manifest 的 run_id 必须包含 YYYYMMDD 或 YYYY-MM-DD 日期"
            )
        date_key = "".join(match.groups())
        try:
            datetime.strptime(date_key, "%Y%m%d")
        except ValueError as exc:
            raise BrowserImportValidationError("run_id 中的日期无效") from exc
        return date_key

    def persist_inline_manifest(
        self,
        manifest: dict[str, Any],
        payload_sha256: str | None = None,
    ) -> Path:
        """Atomically persist one validated inline manifest in the server inbox."""
        canonical, actual_hash = self.canonical_manifest_payload(manifest)
        if payload_sha256 and payload_sha256 != actual_hash:
            raise BrowserImportValidationError("manifest payload_sha256 校验失败")
        payload_sha256 = actual_hash
        kind = canonical["kind"]
        date_key = self._inline_run_date_key(canonical["run_id"])
        run_part = self._inline_run_filename_part(canonical["run_id"])
        filename = (
            f"sidebar-submit-{kind}-{date_key}-{run_part}-{payload_sha256[:12]}.json"
        )
        self.inbox_root.mkdir(parents=True, exist_ok=True)
        target = self.inbox_root / filename

        existing = sorted(
            self.inbox_root.glob(f"sidebar-submit-{kind}-{date_key}-{run_part}-*.json")
        )
        for path in existing:
            if path == target:
                try:
                    prior = json.loads(path.read_text(encoding="utf-8"))
                    _canonical, prior_hash = self.canonical_manifest_payload(prior)
                except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise BrowserImportValidationError(
                        f"既有内联 manifest 无法读取: {path}: {exc}"
                    ) from exc
                if prior_hash != payload_sha256:
                    raise BrowserImportValidationError("同一 run_id 的既有 manifest 内容不一致")
                return target
            raise BrowserImportValidationError("同一 run_id 已提交不同的 manifest")

        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(
                    canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ),
                encoding="utf-8",
            )
            os.replace(temporary, target)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise BrowserImportValidationError(f"无法持久化内联 manifest: {exc}") from exc
        return target

    @staticmethod
    def _expected_idempotency_key(kind: str, run_id: str) -> str:
        return f"zsxq-sidebar:{kind}:{run_id}"

    @staticmethod
    def _canonical_dispatch_options(
        *,
        dispatch_clean: bool = False,
        cleaner: dict[str, Any] | None = None,
        auto_publish: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        options = {
            "dispatch_clean": bool(dispatch_clean),
            "cleaner": cleaner if isinstance(cleaner, dict) else {},
            "auto_publish": auto_publish if isinstance(auto_publish, dict) else {},
        }
        try:
            return json.loads(
                json.dumps(
                    options,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        except (TypeError, ValueError) as exc:
            raise BrowserImportValidationError(f"清洗/发布参数无法序列化为 JSON: {exc}") from exc

    @staticmethod
    def _submission_payload_sha256(
        manifest: dict[str, Any], dispatch_options: dict[str, Any]
    ) -> str:
        text = json.dumps(
            {"manifest": manifest, "dispatch_options": dispatch_options},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _submission_response(submission, *, replayed: bool) -> dict[str, Any]:
        result = submission.result if isinstance(submission.result, dict) else {}
        dispatch_options = result.get("dispatch_options")
        if not isinstance(dispatch_options, dict):
            dispatch_options = {
                "dispatch_clean": False,
                "cleaner": {},
                "auto_publish": {},
            }
        return {
            "submission_id": submission.id,
            "run_id": submission.run_id,
            "kind": submission.kind,
            "status": submission.status,
            "manifest_path": submission.manifest_path,
            "task_id": submission.celery_task_id,
            "payload_sha256": submission.payload_sha256,
            "manifest_payload_sha256": result.get("manifest_payload_sha256"),
            "dispatch_options": dispatch_options,
            "preview": result.get("preview"),
            "result": result.get("worker_result"),
            "error": submission.error_message,
            "replayed": bool(replayed),
            "should_dispatch": submission.status
            in {"received", "dispatch_failed", "failed"},
        }

    @staticmethod
    def _assert_submission_matches(
        submission,
        *,
        idempotency_key: str,
        kind: str,
        run_id: str,
        payload_sha256: str,
    ) -> None:
        if (
            submission.idempotency_key != idempotency_key
            or submission.kind != kind
            or submission.run_id != run_id
            or submission.payload_sha256 != payload_sha256
        ):
            raise IdempotencyConflictError("同一幂等键或 run_id 已用于不同的侧边栏提交")

    def prepare_inline_submission(
        self,
        manifest: dict[str, Any],
        idempotency_key: str,
        *,
        dispatch_clean: bool = False,
        cleaner: dict[str, Any] | None = None,
        auto_publish: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create or replay one durable inline-submission receipt."""
        from models import db
        from models.zsxq_sync import ZsxqBrowserSubmission
        from sqlalchemy.exc import IntegrityError

        canonical, manifest_payload_sha256 = self.canonical_manifest_payload(manifest)
        dispatch_options = self._canonical_dispatch_options(
            dispatch_clean=dispatch_clean,
            cleaner=cleaner,
            auto_publish=auto_publish,
        )
        payload_sha256 = self._submission_payload_sha256(canonical, dispatch_options)
        kind = canonical["kind"]
        run_id = canonical["run_id"]
        idempotency_key = str(idempotency_key or "").strip()
        expected_key = self._expected_idempotency_key(kind, run_id)
        if idempotency_key != expected_key:
            raise BrowserImportValidationError(f"Idempotency-Key 必须为 {expected_key}")

        existing = ZsxqBrowserSubmission.query.filter_by(
            idempotency_key=idempotency_key
        ).one_or_none()
        if existing is None:
            existing = ZsxqBrowserSubmission.query.filter_by(
                kind=kind, run_id=run_id
            ).one_or_none()
        if existing is not None:
            self._assert_submission_matches(
                existing,
                idempotency_key=idempotency_key,
                kind=kind,
                run_id=run_id,
                payload_sha256=payload_sha256,
            )
            return self._submission_response(existing, replayed=True)

        manifest_path = self.persist_inline_manifest(canonical, manifest_payload_sha256)
        preview = self.preview_manifests([str(manifest_path)])
        celery_task_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"zsxq-browser-import:{idempotency_key}")
        )
        submission = ZsxqBrowserSubmission(
            idempotency_key=idempotency_key,
            run_id=run_id,
            kind=kind,
            payload_sha256=payload_sha256,
            manifest_path=str(manifest_path),
            celery_task_id=celery_task_id,
            status="received",
            attempt=0,
            result={
                "preview": preview,
                "dispatch_options": dispatch_options,
                "manifest_payload_sha256": manifest_payload_sha256,
            },
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.session.add(submission)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            existing = ZsxqBrowserSubmission.query.filter_by(
                idempotency_key=idempotency_key
            ).one_or_none()
            if existing is None:
                existing = ZsxqBrowserSubmission.query.filter_by(
                    kind=kind, run_id=run_id
                ).one_or_none()
            if existing is None:
                raise
            self._assert_submission_matches(
                existing,
                idempotency_key=idempotency_key,
                kind=kind,
                run_id=run_id,
                payload_sha256=payload_sha256,
            )
            return self._submission_response(existing, replayed=True)
        return self._submission_response(submission, replayed=False)

    def mark_submission_queued(self, submission_id: str) -> dict[str, Any]:
        from models import db
        from models.zsxq_sync import ZsxqBrowserSubmission

        submission = ZsxqBrowserSubmission.query.filter_by(id=submission_id).one()
        if submission.status in {"received", "dispatch_failed", "failed"}:
            submission.status = "queued"
            submission.queued_at = datetime.utcnow()
            submission.updated_at = datetime.utcnow()
            submission.error_message = None
            db.session.commit()
        return self._submission_response(submission, replayed=False)

    def mark_submission_dispatch_failed(
        self, submission_id: str, error: str
    ) -> dict[str, Any]:
        from models import db
        from models.zsxq_sync import ZsxqBrowserSubmission

        submission = ZsxqBrowserSubmission.query.filter_by(id=submission_id).one()
        if submission.status not in {"processing", "completed"}:
            submission.status = "dispatch_failed"
            submission.error_message = str(error)[:4000]
            submission.updated_at = datetime.utcnow()
            db.session.commit()
        return self._submission_response(submission, replayed=False)

    def claim_submission(
        self, submission_id: str, celery_task_id: str | None = None
    ) -> dict[str, Any]:
        """Atomically allow only one worker to execute a logical submission."""
        from models import db
        from models.zsxq_sync import ZsxqBrowserSubmission

        submission = (
            ZsxqBrowserSubmission.query.filter_by(id=submission_id)
            .with_for_update()
            .one_or_none()
        )
        if submission is None:
            raise BrowserImportValidationError(f"侧边栏提交收据不存在: {submission_id}")
        if celery_task_id and submission.celery_task_id != celery_task_id:
            raise IdempotencyConflictError("Celery task ID 与侧边栏提交收据不匹配")
        if submission.status in {"processing", "completed"}:
            return {
                "claimed": False,
                **self._submission_response(submission, replayed=True),
            }
        if submission.status not in {
            "received",
            "queued",
            "dispatch_failed",
            "failed",
        }:
            raise BrowserImportValidationError(f"侧边栏提交状态无法执行: {submission.status}")
        submission.status = "processing"
        submission.attempt = int(submission.attempt or 0) + 1
        submission.error_message = None
        submission.updated_at = datetime.utcnow()
        db.session.commit()
        return {
            "claimed": True,
            **self._submission_response(submission, replayed=False),
        }

    def complete_submission(
        self, submission_id: str, worker_result: dict[str, Any]
    ) -> dict[str, Any]:
        from models import db
        from models.zsxq_sync import ZsxqBrowserSubmission

        submission = (
            ZsxqBrowserSubmission.query.filter_by(id=submission_id)
            .with_for_update()
            .one()
        )
        existing = submission.result if isinstance(submission.result, dict) else {}
        submission.result = {**existing, "worker_result": worker_result}
        submission.status = "completed"
        submission.completed_at = datetime.utcnow()
        submission.updated_at = datetime.utcnow()
        submission.error_message = None
        db.session.commit()
        return self._submission_response(submission, replayed=False)

    def fail_submission(self, submission_id: str, error: str) -> dict[str, Any]:
        from models import db
        from models.zsxq_sync import ZsxqBrowserSubmission

        submission = (
            ZsxqBrowserSubmission.query.filter_by(id=submission_id)
            .with_for_update()
            .one()
        )
        if submission.status != "completed":
            submission.status = "failed"
            submission.error_message = str(error)[:4000]
            submission.updated_at = datetime.utcnow()
            db.session.commit()
        return self._submission_response(submission, replayed=False)

    def _project_data_root(self) -> Path:
        if self.data_root:
            return self.data_root
        try:
            from flask import current_app, has_app_context

            if has_app_context():
                configured = current_app.config.get("ZSXQ_DATA_DIR")
                if configured:
                    return _safe_resolve(Path(configured))
        except (ImportError, RuntimeError):
            pass
        return _safe_resolve(Path(__file__).resolve().parents[3] / "data" / "zsxq")

    @staticmethod
    def _safe_path_part(value: str) -> str:
        value = re.sub(r'[\\/:*?"<>|\r\n\s]+', "_", str(value or "item"))
        return value.strip("_")[:120] or "item"

    @staticmethod
    def _safe_filename(value: str) -> str:
        value = re.sub(r'[\\/:*?"<>|\r\n]+', "_", str(value or "file"))
        return value[:180] or "file"

    def _allowed_file_path(self, path: Path) -> bool:
        return any(
            _is_within(path, root)
            for root in (self.downloads_root, self._project_data_root())
        )

    def _target_path(self, item: dict[str, Any]) -> Path:
        tab_dir = self._safe_path_part(f"{item['tab_label']}-{item['tab_id']}")
        target_dir = (
            self._project_data_root() / str(item["group_id"]) / tab_dir / "files"
        )
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir / self._safe_filename(item["filename"])

    @staticmethod
    def _unique_path(path: Path) -> Path:
        if not path.exists():
            return path
        for index in range(2, 1000):
            candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
            if not candidate.exists():
                return candidate
        raise BrowserImportValidationError(f"无法生成唯一项目文件路径: {path}")

    @staticmethod
    def _rollback_moves(moves: list[tuple[Path, Path]]) -> None:
        for source, target in reversed(moves):
            try:
                if target.exists() and not source.exists():
                    source.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(target), str(source))
            except OSError:
                # Preserve the original exception. The caller will report the
                # failed import, while a later reconciliation can inspect the
                # destination and source paths.
                continue

    def _relocate_item(
        self,
        item: dict[str, Any],
        *,
        moves: list[tuple[Path, Path]],
        pending_deletes: list[Path],
        seen_hashes: dict[str, Path],
    ) -> None:
        source = _safe_resolve(Path(item["absolute_path"]))
        if not self._allowed_file_path(source):
            raise BrowserImportValidationError(f"文件不在允许的 Downloads 或项目数据目录内: {source}")
        if not source.is_file() or source.stat().st_size <= 0:
            raise BrowserImportValidationError(f"文件不存在或为空: {source}")

        digest = str(item["sha256"])
        target = seen_hashes.get(digest)
        if target is None:
            target = self._target_path(item)
            if target.exists():
                if sha256_file(target) != digest:
                    target = self._unique_path(target)
            seen_hashes[digest] = target

        if source != target:
            if target.exists():
                if sha256_file(target) != digest:
                    raise BrowserImportValidationError(f"目标文件已存在但内容不一致: {target}")
                pending_deletes.append(source)
            else:
                shutil.move(str(source), str(target))
                if sha256_file(target) != digest:
                    self._rollback_moves(moves + [(source, target)])
                    raise BrowserImportValidationError(f"移动后文件校验失败: {target}")
                moves.append((source, target))

        item["absolute_path"] = str(target)
        item["stored_path"] = str(target)

    def _relocate_preview_files(self, preview: dict[str, Any]) -> dict[str, Any]:
        moves: list[tuple[Path, Path]] = []
        pending_deletes: list[Path] = []
        seen_hashes: dict[str, Path] = {}
        try:
            for item in preview["files"]:
                self._relocate_item(
                    item,
                    moves=moves,
                    pending_deletes=pending_deletes,
                    seen_hashes=seen_hashes,
                )
        except Exception:
            self._rollback_moves(moves)
            raise
        return {"moves": moves, "pending_deletes": pending_deletes}

    def _persist_manifest_storage_paths(self, preview: dict[str, Any]) -> None:
        """Record the canonical project path without losing the source path."""
        manifest_path = Path(preview["manifest_path"])
        mapping: dict[str, str] = {}
        for item in preview["files"]:
            entry = item.get("raw_manifest_entry") or {}
            original_path = entry.get("absolute_path")
            if original_path:
                mapping[str(_safe_resolve(Path(original_path)))] = item["absolute_path"]
        self._persist_manifest_path_mapping(manifest_path, mapping)

    def _persist_manifest_path_mapping(
        self, manifest_path: Path, mapping: dict[str, str]
    ) -> None:
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            entries, _run_id, _metadata = _manifest_entries(payload)
            for entry in entries:
                original_path = entry.get("absolute_path")
                key = str(_safe_resolve(Path(original_path))) if original_path else ""
                stored_path = mapping.get(key)
                if stored_path:
                    if original_path != stored_path:
                        entry.setdefault("source_absolute_path", original_path)
                    entry["stored_path"] = stored_path
                elif entry.get("stored_path"):
                    # Preserve an already-canonicalized entry on retries.
                    entry.setdefault("source_absolute_path", original_path)
            temporary = manifest_path.with_name(f".{manifest_path.name}.tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(temporary, manifest_path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, IndexError):
            # The import and DB transaction are already authoritative. A
            # missing convenience field must not turn a successful import into
            # a failure.
            return

    def load_manifest(
        self, manifest_path: str | os.PathLike[str]
    ) -> tuple[list[dict[str, Any]], str | None, dict[str, Any], Path]:
        path = _safe_resolve(Path(manifest_path))
        if not _is_within(path, self.downloads_root):
            raise BrowserImportValidationError(f"manifest 不在 Downloads 目录内: {path}")
        if not path.is_file():
            raise BrowserImportValidationError(f"manifest 不存在: {path}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BrowserImportValidationError(f"无法读取 manifest: {path}: {exc}") from exc
        entries, run_id, metadata = _manifest_entries(payload)
        return entries, run_id, metadata, path

    @staticmethod
    def _manifest_context(
        metadata: dict[str, Any], entries: list[dict[str, Any]]
    ) -> dict[str, str]:
        """Resolve the sync identity, including an empty-run manifest."""
        metadata = metadata if isinstance(metadata, dict) else {}
        kind = str(metadata.get("kind") or "").strip().lower()
        if kind and kind not in TAB_CONFIGS:
            raise BrowserImportValidationError(f"manifest 文件类型不支持: {kind}")

        if not kind and entries:
            # Non-empty legacy manifests derive the identity from their first
            # entry. Empty manifests must provide this metadata explicitly.
            kind = ""

        config = TAB_CONFIGS.get(kind) or {}
        tab_id = str(metadata.get("tab_id") or config.get("tab_id") or "").strip()
        tab_label = str(
            metadata.get("tab_label") or config.get("tab_label") or tab_id
        ).strip()
        group_id = str(metadata.get("group_id") or DEFAULT_GROUP_ID).strip()
        group_name = str(metadata.get("group_name") or "前沿信息收录").strip()
        return {
            "kind": kind,
            "group_id": group_id,
            "group_name": group_name,
            "tab_id": tab_id,
            "tab_label": tab_label,
        }

    def preview_manifest(self, manifest_path: str | os.PathLike[str]) -> dict[str, Any]:
        entries, supplied_run_id, metadata, manifest_path = self.load_manifest(
            manifest_path
        )
        batch_key = supplied_run_id or manifest_path.stem
        manifest_context = self._manifest_context(metadata, entries)
        if not entries and not manifest_context["kind"]:
            raise BrowserImportValidationError(
                "空 manifest 必须提供 kind、tab_id 和 tab_label"
            )
        files: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        seen_hashes: dict[str, str] = {}

        for index, entry in enumerate(entries):
            raw_path = str(
                entry.get("stored_path") or entry.get("absolute_path") or ""
            ).strip()
            if not raw_path:
                raise BrowserImportValidationError(
                    "manifest 项缺少 absolute_path", details=[{"index": index}]
                )
            path = _safe_resolve(Path(raw_path))
            if not self._allowed_file_path(path):
                raise BrowserImportValidationError(
                    f"文件不在允许的 Downloads 或项目数据目录内: {path}",
                    details=[{"index": index, "path": str(path)}],
                )
            if not path.is_file() or path.stat().st_size <= 0:
                raise BrowserImportValidationError(
                    f"文件不存在或为空: {path}", details=[{"index": index, "path": str(path)}]
                )
            if entry.get("success") is False:
                raise BrowserImportValidationError(
                    f"manifest 项标记为下载失败: {path.name}", details=[{"index": index}]
                )

            kind = _kind_for_entry(entry, path)
            config = TAB_CONFIGS[kind]
            digest = sha256_file(path)
            title = _title_for_entry(entry, path.name)
            published_at = str(entry.get("published_at") or "").strip() or None
            topic_seed = "|".join([config["tab_id"], title, published_at or "", digest])
            file_id = f"browser-file-{digest}"
            item = {
                "index": index,
                "kind": kind,
                "media_type": str(entry.get("media_type") or kind).strip().lower(),
                "file_ext": path.suffix.lower().lstrip("."),
                "filename": path.name,
                "absolute_path": str(path),
                "title": title,
                "download_count": entry.get("download_count"),
                "published_at": published_at,
                "downloaded_at": entry.get("downloaded_at"),
                "source_url": entry.get("source_url"),
                "group_id": DEFAULT_GROUP_ID,
                "tab_id": config["tab_id"],
                "tab_label": config["tab_label"],
                "topic_id": f"browser-topic-{_stable_digest(topic_seed)}",
                "file_id": file_id,
                "attachment_key": file_id,
                "sha256": digest,
                "success": True,
                "duplicate_of": None,
                "raw_manifest_entry": dict(entry),
            }
            if digest in seen_hashes:
                item["duplicate_of"] = seen_hashes[digest]
            else:
                seen_hashes[digest] = file_id

            count = entry.get("download_count")
            if count is None:
                warnings.append(
                    {
                        "code": "download_count_unknown",
                        "filename": path.name,
                        "message": "下载次数未知，保留为 null",
                    }
                )
            else:
                try:
                    numeric_count = int(count)
                except (TypeError, ValueError):
                    raise BrowserImportValidationError(
                        f"download_count 不是整数: {path.name}", details=[{"index": index}]
                    )
                if numeric_count <= config["download_count_gt"]:
                    warnings.append(
                        {
                            "code": "download_count_below_threshold",
                            "filename": path.name,
                            "download_count": numeric_count,
                            "threshold_gt": config["download_count_gt"],
                        }
                    )

            files.append(item)

        if files and not manifest_context["kind"]:
            first = files[0]
            manifest_context = {
                "kind": first["kind"],
                "group_id": first["group_id"],
                "group_name": "前沿信息收录",
                "tab_id": first["tab_id"],
                "tab_label": first["tab_label"],
            }

        duplicate_count = sum(1 for item in files if item["duplicate_of"])
        unknown_count = sum(1 for item in files if item["download_count"] is None)
        return {
            "manifest_path": str(manifest_path),
            "batch_key": batch_key,
            **manifest_context,
            "run_id": supplied_run_id,
            "scan_summary": metadata.get("scan_summary")
            if isinstance(metadata.get("scan_summary"), dict)
            else {},
            "files": files,
            "warnings": warnings,
            "summary": {
                "total": len(files),
                "valid": len(files),
                "duplicate_content": duplicate_count,
                "unknown_download_count": unknown_count,
                "warning_count": len(warnings),
            },
        }

    def preview_manifests(
        self, manifest_paths: Iterable[str | os.PathLike[str]]
    ) -> dict[str, Any]:
        manifests = [self.preview_manifest(path) for path in manifest_paths]
        return {
            "manifests": manifests,
            "summary": {
                "manifests": len(manifests),
                "files": sum(item["summary"]["total"] for item in manifests),
                "duplicate_content": sum(
                    item["summary"]["duplicate_content"] for item in manifests
                ),
                "unknown_download_count": sum(
                    item["summary"]["unknown_download_count"] for item in manifests
                ),
                "warnings": sum(item["summary"]["warning_count"] for item in manifests),
            },
        }

    @staticmethod
    def _parse_datetime(value: Any) -> datetime:
        if isinstance(value, datetime):
            parsed = value
        else:
            text = str(value or "").strip()
            if not text:
                return datetime.utcnow()
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return datetime.utcnow()
        if parsed.tzinfo:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed

    @staticmethod
    def _topic_payload(
        item: dict[str, Any], manifest_path: str, run_id: str
    ) -> dict[str, Any]:
        return {
            "source": "chrome_sidebar",
            "browser_import": {
                "run_id": run_id,
                "manifest_path": manifest_path,
                "sha256": item["sha256"],
                "file_id": item["file_id"],
                "source_url": item.get("source_url"),
                "original_manifest_entry": item.get("raw_manifest_entry") or {},
            },
            "talk": {
                "text": item["title"],
                "files": [
                    {
                        "file_id": item["file_id"],
                        "name": item["filename"],
                        "type": item["media_type"],
                        "url": item.get("source_url"),
                    }
                ],
            },
        }

    def _upsert_topic(self, item: dict[str, Any], *, run_id: str, manifest_path: str):
        from models import db
        from models.zsxq_sync import ZsxqTopic

        now = datetime.utcnow()
        topic = ZsxqTopic.query.filter_by(
            group_id=item["group_id"],
            tab_id=item["tab_id"],
            topic_id=item["topic_id"],
        ).one_or_none()
        created = topic is None
        payload = self._topic_payload(item, manifest_path, run_id)
        if created:
            topic = ZsxqTopic(
                group_id=item["group_id"],
                group_name="前沿信息收录",
                tab_id=item["tab_id"],
                tab_label=item["tab_label"],
                topic_id=item["topic_id"],
                topic_uid=item["topic_id"],
                title=item["title"],
                topic_type="talk",
                create_time=item.get("published_at") or item.get("downloaded_at"),
                raw_json_path=manifest_path,
                raw_payload=payload,
                first_sync_run_id=run_id,
                last_sync_run_id=run_id,
                first_synced_at=now,
                last_synced_at=now,
            )
            db.session.add(topic)
        else:
            topic.last_sync_run_id = run_id
            topic.last_synced_at = now
            if not topic.title:
                topic.title = item["title"]
            existing_payload = (
                topic.raw_payload if isinstance(topic.raw_payload, dict) else {}
            )
            topic.raw_payload = {**existing_payload, **payload}
        return topic, created

    def _upsert_attachment(
        self, item: dict[str, Any], *, run_id: str, manifest_path: str
    ):
        from models import db
        from models.zsxq_sync import ZsxqAttachment

        now = datetime.utcnow()
        attachment = (
            ZsxqAttachment.query.filter_by(
                group_id=item["group_id"],
                tab_id=item["tab_id"],
                file_id=item["file_id"],
            )
            .order_by(ZsxqAttachment.created_at.asc())
            .first()
        )
        created = attachment is None
        raw_payload = {
            "source": "chrome_sidebar",
            "sha256": item["sha256"],
            "browser_import": {
                "run_id": run_id,
                "manifest_path": manifest_path,
                "sha256": item["sha256"],
                "original_manifest_entry": item.get("raw_manifest_entry") or {},
            },
        }
        if created:
            attachment = ZsxqAttachment(
                group_id=item["group_id"],
                tab_id=item["tab_id"],
                topic_id=item["topic_id"],
                attachment_key=item["attachment_key"],
                attachment_type="file",
                name=item["filename"],
                file_id=item["file_id"],
                download_count=item.get("download_count"),
                local_path=item["absolute_path"],
                file_ext=item["file_ext"],
                download_status="downloaded",
                raw_payload=raw_payload,
                created_at=now,
                updated_at=now,
            )
            db.session.add(attachment)
        else:
            attachment.download_status = "downloaded"
            attachment.local_path = item["absolute_path"]
            if item.get("download_count") is not None:
                attachment.download_count = item["download_count"]
            existing_payload = (
                attachment.raw_payload
                if isinstance(attachment.raw_payload, dict)
                else {}
            )
            attachment.raw_payload = {**existing_payload, **raw_payload}
            attachment.updated_at = now
        return attachment, created

    def _import_manifest(self, preview: dict[str, Any]):
        from models import db
        from models.zsxq_sync import ZsxqSyncRun, ZsxqSyncRunDownload

        first = preview["files"][0] if preview["files"] else None
        context = {
            "group_id": first["group_id"] if first else preview.get("group_id"),
            "group_name": preview.get("group_name") or "前沿信息收录",
            "tab_id": first["tab_id"] if first else preview.get("tab_id"),
            "tab_label": first["tab_label"] if first else preview.get("tab_label"),
            "kind": first["kind"] if first else preview.get("kind"),
        }
        if not context["kind"] or not context["tab_id"]:
            raise BrowserImportValidationError("manifest 缺少同步编排所需的 kind 或 tab_id")
        now = datetime.utcnow()
        run = ZsxqSyncRun(
            group_id=context["group_id"],
            group_name=context["group_name"],
            tab_id=context["tab_id"],
            tab_label=context["tab_label"],
            mode="browser_sidebar",
            status="running",
            requested_tabs=[
                {
                    "id": context["tab_id"],
                    "label": context["tab_label"],
                    "file_type": context["kind"],
                }
            ],
            result={
                "source": "chrome_sidebar",
                "browser_import": {
                    "batch_key": preview["batch_key"],
                    "manifest_path": preview["manifest_path"],
                    "kind": context["kind"],
                    "scan_summary": preview.get("scan_summary") or {},
                    "no_new_files": not bool(preview["files"]),
                },
            },
            started_at=now,
            created_at=now,
            updated_at=now,
        )
        db.session.add(run)
        db.session.flush()

        created_topics = 0
        reused_topics = 0
        created_attachments = 0
        reused_attachments = 0
        ledger_rows = 0
        seen_attachment_ids: set[str] = set()
        for item in preview["files"]:
            topic, topic_created = self._upsert_topic(
                item,
                run_id=run.id,
                manifest_path=preview["manifest_path"],
            )
            if topic_created:
                created_topics += 1
            else:
                reused_topics += 1
            db.session.flush()

            attachment, attachment_created = self._upsert_attachment(
                item,
                run_id=run.id,
                manifest_path=preview["manifest_path"],
            )
            if attachment_created:
                created_attachments += 1
            else:
                reused_attachments += 1
            db.session.flush()

            if attachment.id not in seen_attachment_ids:
                ledger = ZsxqSyncRunDownload(
                    run_id=run.id,
                    attachment_id=attachment.id,
                    group_id=item["group_id"],
                    tab_id=attachment.tab_id,
                    topic_id=attachment.topic_id,
                    name=attachment.name,
                    local_path=attachment.local_path,
                    file_ext=item["file_ext"],
                    downloaded_at=self._parse_datetime(item.get("downloaded_at")),
                )
                db.session.add(ledger)
                seen_attachment_ids.add(attachment.id)
                ledger_rows += 1

        run.status = "completed"
        run.completed_at = datetime.utcnow()
        run.download_ledger_complete = True
        run.topics_fetched = len(preview["files"])
        run.topics_new = created_topics
        run.topics_updated = reused_topics
        run.topics_existing = reused_topics
        run.attachments_seen = len(preview["files"])
        run.attachments_downloaded = created_attachments
        run.attachments_skipped = reused_attachments
        run.result = {
            **(run.result if isinstance(run.result, dict) else {}),
            "topics_fetched": len(preview["files"]),
            "topics_new": created_topics,
            "topics_updated": reused_topics,
            "topics_existing": reused_topics,
            "attachments_seen": len(preview["files"]),
            "attachments_downloaded": created_attachments,
            "attachments_skipped": reused_attachments,
            "summary": {
                "files": len(preview["files"]),
                "created_topics": created_topics,
                "reused_topics": reused_topics,
                "created_attachments": created_attachments,
                "reused_attachments": reused_attachments,
                "ledger_rows": ledger_rows,
                "warnings": preview["warnings"],
                "scan_summary": preview.get("scan_summary") or {},
                "no_new_files": not bool(preview["files"]),
            },
        }
        run.sync_projection_fields()
        return run, run.result["summary"]

    def relocate_existing_browser_imports(self) -> dict[str, int]:
        """Move already-imported sidebar files into the canonical data tree."""
        from models import db
        from models.zsxq_sync import ZsxqAttachment, ZsxqSyncRunDownload

        tab_by_id = {config["tab_id"]: config for config in TAB_CONFIGS.values()}
        moves: list[tuple[Path, Path]] = []
        pending_deletes: list[Path] = []
        seen_hashes: dict[str, Path] = {}
        manifest_updates: dict[Path, dict[str, str]] = {}
        moved = 0
        already_in_project = 0
        missing = 0

        try:
            attachments = ZsxqAttachment.query.filter(
                ZsxqAttachment.file_id.like("browser-file-%")
            ).all()
            for attachment in attachments:
                payload = (
                    attachment.raw_payload
                    if isinstance(attachment.raw_payload, dict)
                    else {}
                )
                if payload.get("source") != "chrome_sidebar":
                    continue
                config = tab_by_id.get(str(attachment.tab_id))
                source = _safe_resolve(Path(str(attachment.local_path or "")))
                if not source.is_file() or source.stat().st_size <= 0:
                    missing += 1
                    continue
                digest = str(payload.get("sha256") or "").strip() or sha256_file(source)
                item = {
                    "absolute_path": str(source),
                    "sha256": digest,
                    "filename": attachment.name or source.name,
                    "group_id": attachment.group_id,
                    "tab_id": attachment.tab_id,
                    "tab_label": (config or {}).get("tab_label", attachment.tab_id),
                }
                browser_import = payload.get("browser_import")
                original_entry = (
                    browser_import.get("original_manifest_entry")
                    if isinstance(browser_import, dict)
                    else None
                )
                original_path = (
                    original_entry.get("absolute_path")
                    if isinstance(original_entry, dict)
                    else None
                )
                manifest_path = (
                    browser_import.get("manifest_path")
                    if isinstance(browser_import, dict)
                    else None
                )
                before = source
                self._relocate_item(
                    item,
                    moves=moves,
                    pending_deletes=pending_deletes,
                    seen_hashes=seen_hashes,
                )
                target = item["absolute_path"]
                if str(before) == target:
                    already_in_project += 1
                else:
                    moved += 1
                attachment.local_path = target
                attachment.updated_at = datetime.utcnow()
                attachment.raw_payload = {
                    **payload,
                    "browser_import": {
                        **(
                            payload.get("browser_import")
                            if isinstance(payload.get("browser_import"), dict)
                            else {}
                        ),
                        "stored_path": target,
                    },
                }
                if original_path and manifest_path:
                    manifest_updates.setdefault(
                        _safe_resolve(Path(str(manifest_path))), {}
                    )[str(_safe_resolve(Path(str(original_path))))] = target
                for ledger in ZsxqSyncRunDownload.query.filter_by(
                    attachment_id=attachment.id
                ).all():
                    ledger.local_path = target
        except Exception:
            db.session.rollback()
            self._rollback_moves(moves)
            raise

        db.session.commit()
        for path in pending_deletes:
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                continue
        for manifest_path, mapping in manifest_updates.items():
            self._persist_manifest_path_mapping(manifest_path, mapping)
        return {
            "moved": moved,
            "already_in_project": already_in_project,
            "missing": missing,
        }

    def import_manifests(
        self,
        manifest_paths: Iterable[str | os.PathLike[str]],
        *,
        dispatch_clean: bool = False,
    ) -> dict[str, Any]:
        """Persist validated manifests into the canonical ZSXQ tables.

        ``dispatch_clean`` is accepted for the task/API contract but is
        intentionally handled by the Celery task after the transaction.
        """
        from models import db

        previews = [self.preview_manifest(path) for path in manifest_paths]
        imported = []
        relocations: list[dict[str, Any]] = []
        try:
            for preview in previews:
                relocation = self._relocate_preview_files(preview)
                relocations.append(relocation)
                run, summary = self._import_manifest(preview)
                imported.append(
                    {
                        "manifest_path": preview["manifest_path"],
                        "run_id": run.id,
                        "kind": preview["kind"],
                        "group_id": preview["group_id"],
                        "group_name": preview["group_name"],
                        "tab_id": preview["tab_id"],
                        "tab_label": preview["tab_label"],
                        "status": run.status,
                        **summary,
                    }
                )
            db.session.commit()
        except Exception:
            db.session.rollback()
            for relocation in reversed(relocations):
                self._rollback_moves(relocation["moves"])
            raise

        for preview, relocation in zip(previews, relocations):
            for path in relocation["pending_deletes"]:
                try:
                    if path.exists():
                        path.unlink()
                except OSError:
                    continue
            self._persist_manifest_storage_paths(preview)

        return {
            "manifests": imported,
            "summary": {
                "manifests": len(imported),
                "created_topics": sum(item["created_topics"] for item in imported),
                "reused_topics": sum(item["reused_topics"] for item in imported),
                "created_attachments": sum(
                    item["created_attachments"] for item in imported
                ),
                "reused_attachments": sum(
                    item["reused_attachments"] for item in imported
                ),
                "ledger_rows": sum(item["ledger_rows"] for item in imported),
                "dispatch_clean_requested": bool(dispatch_clean),
            },
        }


zsxq_browser_import_service = BrowserSidebarImportService()


__all__ = [
    "BrowserImportValidationError",
    "BrowserSidebarImportService",
    "DEFAULT_GROUP_ID",
    "IdempotencyConflictError",
    "TAB_CONFIGS",
    "sha256_file",
    "zsxq_browser_import_service",
]
