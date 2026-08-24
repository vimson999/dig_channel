import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from flask import current_app, has_app_context
from sqlalchemy import or_

from models.task import PublicationTask

try:
    from models.zsxq_sync import ZsxqSyncRun
except Exception:  # pragma: no cover - keeps this utility importable during partial migrations.
    ZsxqSyncRun = None


PROJECT_ROOT = Path(__file__).resolve().parents[4]


def _default_pytest_dir(project_root: Path) -> Path:
    base_dir = project_root.parents[1] if len(project_root.parents) > 1 else project_root
    return base_dir / "pytest"


DEFAULT_PYTEST_DIR = _default_pytest_dir(PROJECT_ROOT)
LARGE_ATTACHMENT_SUFFIXES = {
    ".pdf",
    ".mp3",
    ".m4a",
    ".wav",
    ".aac",
    ".flac",
    ".ogg",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".zip",
}
PROCESSED_VIDEO_SUFFIXES = {".mp4"}
RETENTION_PROTECTED_TASK_FEATURES = (
    "content_pdf_clean",
    "content_audio_clean",
    "zsxq_clean_output_publish",
)


class RetentionCleanupError(RuntimeError):
    """Raised when retention cleanup cannot safely continue."""


@dataclass
class ContentRetentionPolicy:
    sync_raw_days: int = 3
    content_pipeline_batch_days: int = 3
    manual_publish_days: int = 3
    pytest_batch_days: int = 3
    processed_video_days: int = 3
    include_sync_raw: bool = True
    include_content_pipeline_batches: bool = True
    include_manual_publish: bool = True
    include_pytest_batches: bool = True
    include_processed_videos: bool = True
    max_candidates: int = 2000
    unfinished_reference_protect_days: int = 3

    @classmethod
    def from_payload(cls, payload: Optional[dict] = None):
        payload = payload if isinstance(payload, dict) else {}
        return cls(
            sync_raw_days=_positive_int(payload.get("sync_raw_days"), 3),
            content_pipeline_batch_days=_positive_int(payload.get("content_pipeline_batch_days"), 3),
            manual_publish_days=_positive_int(payload.get("manual_publish_days"), 3),
            pytest_batch_days=_positive_int(payload.get("pytest_batch_days"), 3),
            processed_video_days=_positive_int(payload.get("processed_video_days"), 3),
            include_sync_raw=_truthy(payload.get("include_sync_raw", True)),
            include_content_pipeline_batches=_truthy(payload.get("include_content_pipeline_batches", True)),
            include_manual_publish=_truthy(payload.get("include_manual_publish", True)),
            include_pytest_batches=_truthy(payload.get("include_pytest_batches", True)),
            include_processed_videos=_truthy(payload.get("include_processed_videos", True)),
            max_candidates=_positive_int(payload.get("max_candidates"), 2000),
            unfinished_reference_protect_days=_positive_int(payload.get("unfinished_reference_protect_days"), 3),
        )

    def to_dict(self):
        return {
            "sync_raw_days": self.sync_raw_days,
            "content_pipeline_batch_days": self.content_pipeline_batch_days,
            "manual_publish_days": self.manual_publish_days,
            "pytest_batch_days": self.pytest_batch_days,
            "processed_video_days": self.processed_video_days,
            "include_sync_raw": self.include_sync_raw,
            "include_content_pipeline_batches": self.include_content_pipeline_batches,
            "include_manual_publish": self.include_manual_publish,
            "include_pytest_batches": self.include_pytest_batches,
            "include_processed_videos": self.include_processed_videos,
            "max_candidates": self.max_candidates,
            "unfinished_reference_protect_days": self.unfinished_reference_protect_days,
        }


class ContentPipelineRetentionCleanupService:
    """Preview and delete short-lived sync/cleaning artifacts.

    The cleanup intentionally keeps database metadata and small JSON records.
    It targets large local files and batch workspaces that can be regenerated
    from sync/task records if needed.
    """

    def __init__(
        self,
        *,
        project_root: Optional[str | Path] = None,
        content_pipeline_root: Optional[str | Path] = None,
        zsxq_data_dir: Optional[str | Path] = None,
        processed_data_dir: Optional[str | Path] = None,
        pytest_project_dir: Optional[str | Path] = None,
    ):
        self.project_root = Path(project_root or PROJECT_ROOT).expanduser().resolve()
        self.content_pipeline_root = Path(
            content_pipeline_root
            or os.getenv("CONTENT_PIPELINE_RUNTIME_ROOT")
            or (self.project_root / "runtime" / "content_pipeline")
        ).expanduser().resolve()
        self.zsxq_data_dir = Path(
            zsxq_data_dir
            or _app_config("ZSXQ_DATA_DIR")
            or (self.project_root / "data" / "zsxq")
        ).expanduser().resolve()
        self.processed_data_dir = Path(
            processed_data_dir
            or os.getenv("PROCESSED_DATA_DIR")
            or _app_config("PROCESSED_DATA_DIR")
            or (self.project_root / "data" / "processed")
        ).expanduser().resolve()
        self.pytest_project_dir = Path(
            pytest_project_dir
            or os.getenv("PDF_CLEANER_PROJECT_DIR")
            or DEFAULT_PYTEST_DIR
        ).expanduser().resolve()
        self.pytest_batch_root = (
            os.getenv("PDF_CLEANER_BATCH_ROOT")
            or _app_config("PDF_CLEANER_BATCH_ROOT")
            or "runtime/batches"
        ).strip("/")

    def run(self, *, policy: Optional[ContentRetentionPolicy] = None, dry_run: bool = True) -> dict:
        policy = policy or ContentRetentionPolicy()
        now = time.time()
        protected_paths = self._protected_paths(policy=policy, now=now)
        candidates = self._collect_candidates(policy, now, protected_paths)
        candidates = candidates[: max(policy.max_candidates, 1)]

        deleted = []
        errors = []
        if not dry_run:
            for item in candidates:
                try:
                    self._delete_candidate(item)
                    deleted.append(item)
                except Exception as exc:
                    failed = dict(item)
                    failed["error"] = str(exc)
                    errors.append(failed)

        return {
            "dry_run": bool(dry_run),
            "policy": policy.to_dict(),
            "roots": {
                "content_pipeline_root": str(self.content_pipeline_root),
                "zsxq_data_dir": str(self.zsxq_data_dir),
                "processed_data_dir": str(self.processed_data_dir),
                "pytest_project_dir": str(self.pytest_project_dir),
                "pytest_batch_root": self.pytest_batch_root,
            },
            "candidate_count": len(candidates),
            "total_bytes": sum(int(item.get("bytes") or 0) for item in candidates),
            "total_human": _format_bytes(sum(int(item.get("bytes") or 0) for item in candidates)),
            "deleted_count": len(deleted),
            "deleted_bytes": sum(int(item.get("bytes") or 0) for item in deleted),
            "deleted_human": _format_bytes(sum(int(item.get("bytes") or 0) for item in deleted)),
            "errors": errors,
            "candidates": candidates,
        }

    def _collect_candidates(self, policy: ContentRetentionPolicy, now: float, protected_paths: set[Path]) -> list[dict]:
        candidates = []
        if policy.include_content_pipeline_batches:
            candidates.extend(
                self._collect_old_content_pipeline_files(
                    root=self.content_pipeline_root / "batches",
                    days=policy.content_pipeline_batch_days,
                    now=now,
                    protected_paths=protected_paths,
                )
            )
        if policy.include_manual_publish:
            candidates.extend(
                self._collect_old_directories(
                    root=self.content_pipeline_root / "manual_publish",
                    category="manual_publish",
                    days=policy.manual_publish_days,
                    now=now,
                    protected_paths=protected_paths,
                    reason="手动发布临时整理目录超过保留期",
                )
            )
        if policy.include_pytest_batches:
            candidates.extend(
                self._collect_old_directories(
                    root=(self.pytest_project_dir / self.pytest_batch_root),
                    category="pytest_batch",
                    days=policy.pytest_batch_days,
                    now=now,
                    protected_paths=protected_paths,
                    reason="pytest PDF 清洗中转批次超过保留期",
                    allowed_root=self.pytest_project_dir,
                )
            )
        if policy.include_sync_raw:
            candidates.extend(self._collect_old_sync_attachments(policy.sync_raw_days, now, protected_paths))
        if policy.include_processed_videos:
            candidates.extend(
                self._collect_old_processed_videos(
                    days=policy.processed_video_days,
                    now=now,
                    protected_paths=protected_paths,
                )
            )

        candidates.sort(key=lambda item: (item["category"], item["path"]))
        return candidates

    def _collect_old_content_pipeline_files(
        self,
        *,
        root: Path,
        days: int,
        now: float,
        protected_paths: set[Path],
    ) -> list[dict]:
        return self._collect_old_large_files(
            root=root,
            category="content_pipeline_batch_file",
            days=days,
            now=now,
            protected_paths=protected_paths,
            reason="content pipeline 批次内大文件超过保留期，保留 JSON/manifest 元数据",
            allowed_root=self.content_pipeline_root,
        )

    def _collect_old_directories(
        self,
        *,
        root: Path,
        category: str,
        days: int,
        now: float,
        protected_paths: set[Path],
        reason: str,
        allowed_root: Optional[Path] = None,
    ) -> list[dict]:
        if not root.exists() or not root.is_dir():
            return []
        allowed_root = (allowed_root or root).resolve()
        cutoff_seconds = days * 86400
        candidates = []
        for child in root.iterdir():
            if not child.is_dir() or child.is_symlink():
                continue
            child = child.resolve()
            if self._is_protected(child, protected_paths):
                continue
            _assert_inside(child, allowed_root)
            latest_mtime = _latest_mtime(child)
            age_seconds = now - latest_mtime
            if age_seconds < cutoff_seconds:
                continue
            size = _path_size(child)
            candidates.append(
                {
                    "category": category,
                    "kind": "directory",
                    "path": str(child),
                    "bytes": size,
                    "human_size": _format_bytes(size),
                    "age_days": round(age_seconds / 86400, 2),
                    "reason": reason,
                }
            )
        return candidates

    def _collect_old_sync_attachments(self, days: int, now: float, protected_paths: set[Path]) -> list[dict]:
        return self._collect_old_large_files(
            root=self.zsxq_data_dir,
            category="sync_raw_attachment",
            days=days,
            now=now,
            protected_paths=protected_paths,
            reason="知识星球同步附件超过保留期，仅删除大文件附件，保留数据库和 JSON 元数据",
            allowed_root=self.zsxq_data_dir,
            files_glob="*/*/files",
        )

    def _collect_old_processed_videos(
        self,
        *,
        days: int,
        now: float,
        protected_paths: set[Path],
    ) -> list[dict]:
        return self._collect_old_large_files(
            root=self.processed_data_dir,
            category="processed_video",
            days=days,
            now=now,
            protected_paths=protected_paths,
            reason="data/processed 下生成的 MP4 超过保留期",
            allowed_root=self.processed_data_dir,
            suffixes=PROCESSED_VIDEO_SUFFIXES,
        )

    def _collect_old_large_files(
        self,
        *,
        root: Path,
        category: str,
        days: int,
        now: float,
        protected_paths: set[Path],
        reason: str,
        allowed_root: Path,
        files_glob: Optional[str] = None,
        suffixes: Optional[set[str]] = None,
    ) -> list[dict]:
        if not root.exists() or not root.is_dir():
            return []
        cutoff_seconds = days * 86400
        candidates = []
        search_roots: Iterable[Path]
        if files_glob:
            search_roots = (path for path in root.glob(files_glob) if path.is_dir() and not path.is_symlink())
        else:
            search_roots = (root,)

        allowed_suffixes = LARGE_ATTACHMENT_SUFFIXES if suffixes is None else suffixes
        for search_root in search_roots:
            for path in search_root.rglob("*"):
                if not path.is_file() or path.is_symlink():
                    continue
                if path.suffix.lower() not in allowed_suffixes:
                    continue
                resolved = path.resolve()
                _assert_inside(resolved, allowed_root)
                if self._is_protected(resolved, protected_paths):
                    continue
                age_seconds = now - resolved.stat().st_mtime
                if age_seconds < cutoff_seconds:
                    continue
                size = resolved.stat().st_size
                candidates.append(
                    {
                        "category": category,
                        "kind": "file",
                        "path": str(resolved),
                        "bytes": size,
                        "human_size": _format_bytes(size),
                        "age_days": round(age_seconds / 86400, 2),
                        "reason": reason,
                    }
                )
        return candidates

    def _protected_paths(self, *, policy: ContentRetentionPolicy, now: float) -> set[Path]:
        protected = set()
        if policy.unfinished_reference_protect_days <= 0:
            return protected

        cutoff = datetime.utcfromtimestamp(now - policy.unfinished_reference_protect_days * 86400)
        task_rows = (
            PublicationTask.query.with_entities(
                PublicationTask.id,
                PublicationTask.feature,
                PublicationTask.content_clean_output_dir,
                PublicationTask.task_video_path,
                PublicationTask.publisher_vertical_video_path,
                PublicationTask.report_audio_video_path,
                PublicationTask.report_audio_output_path,
            )
            .filter(PublicationTask.status.in_(["pending", "processing", "failed"]))
            .filter(
                or_(
                    PublicationTask.feature.in_(RETENTION_PROTECTED_TASK_FEATURES),
                    PublicationTask.content_clean_output_dir.isnot(None),
                    PublicationTask.task_video_path.isnot(None),
                    PublicationTask.publisher_vertical_video_path.isnot(None),
                    PublicationTask.report_audio_video_path.isnot(None),
                    PublicationTask.report_audio_output_path.isnot(None),
                )
            )
            .filter(
                or_(
                    PublicationTask.updated_at.is_(None),
                    PublicationTask.updated_at >= cutoff,
                    PublicationTask.created_at >= cutoff,
                )
            )
            .all()
        )
        for row in task_rows:
            for value in (
                row.content_clean_output_dir,
                row.task_video_path,
                row.publisher_vertical_video_path,
                row.report_audio_video_path,
                row.report_audio_output_path,
            ):
                if value:
                    protected.add(Path(str(value)).expanduser())
            if row.feature not in RETENTION_PROTECTED_TASK_FEATURES:
                continue
            payload = (
                PublicationTask.query.with_entities(PublicationTask.params, PublicationTask.result)
                .filter(PublicationTask.id == row.id)
                .one_or_none()
            )
            if not payload:
                continue
            params, result = payload
            protected.update(_paths_from_nested(result))
            protected.update(_paths_from_nested(params))
        if ZsxqSyncRun is not None:
            sync_rows = (
                ZsxqSyncRun.query.with_entities(ZsxqSyncRun.result)
                .filter(ZsxqSyncRun.status.in_(["running", "pending", "processing"]))
                .filter(
                    or_(
                        ZsxqSyncRun.updated_at.is_(None),
                        ZsxqSyncRun.updated_at >= cutoff,
                        ZsxqSyncRun.created_at >= cutoff,
                    )
                )
                .all()
            )
            for (result,) in sync_rows:
                protected.update(_paths_from_nested(result))
        return {item.resolve() for item in protected if item.exists()}

    def _is_protected(self, path: Path, protected_paths: set[Path]) -> bool:
        for protected in protected_paths:
            try:
                path.relative_to(protected)
                return True
            except ValueError:
                pass
            try:
                protected.relative_to(path)
                return True
            except ValueError:
                pass
        return False

    def _delete_candidate(self, item: dict):
        path = Path(item["path"]).expanduser().resolve()
        category = item.get("category")
        if category in {"content_pipeline_batch_file", "manual_publish"}:
            _assert_inside(path, self.content_pipeline_root)
        elif category == "pytest_batch":
            _assert_inside(path, self.pytest_project_dir)
        elif category == "sync_raw_attachment":
            _assert_inside(path, self.zsxq_data_dir)
        elif category == "processed_video":
            _assert_inside(path, self.processed_data_dir)
        else:
            raise RetentionCleanupError(f"未知清理类别: {category}")

        if item.get("kind") == "directory":
            shutil.rmtree(path)
        elif item.get("kind") == "file":
            path.unlink()
            if category == "content_pipeline_batch_file":
                _prune_empty_parents(path.parent, stop_at=self.content_pipeline_root / "batches")
            elif category == "sync_raw_attachment":
                _prune_empty_parents(path.parent, stop_at=self.zsxq_data_dir)
            elif category == "processed_video":
                _prune_empty_parents(path.parent, stop_at=self.processed_data_dir)
        else:
            raise RetentionCleanupError(f"未知清理对象类型: {item.get('kind')}")


def _paths_from_nested(value) -> set[Path]:
    paths = set()
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered.endswith("_path") or lowered.endswith("_dir") or lowered in {"local_workspace", "pytest_workspace", "local_output_dir"}:
                if isinstance(item, str) and item:
                    paths.add(Path(item).expanduser())
            paths.update(_paths_from_nested(item))
    elif isinstance(value, list):
        for item in value:
            paths.update(_paths_from_nested(item))
    elif isinstance(value, str) and value.startswith("/"):
        paths.add(Path(value).expanduser())
    return paths


def _record_is_within_protection_window(record, now: float, days: int) -> bool:
    if days <= 0:
        return False
    timestamp = _record_timestamp(record)
    if timestamp is None:
        return True
    return now - timestamp < days * 86400


def _record_timestamp(record) -> Optional[float]:
    for attr in ("updated_at", "completed_at", "started_at", "created_at"):
        value = getattr(record, attr, None)
        if isinstance(value, datetime):
            return value.timestamp()
    return None


def _latest_mtime(path: Path) -> float:
    latest = path.stat().st_mtime
    for child in path.rglob("*"):
        try:
            latest = max(latest, child.stat().st_mtime)
        except OSError:
            continue
    return latest


def _path_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for child in path.rglob("*"):
        try:
            if child.is_file() and not child.is_symlink():
                total += child.stat().st_size
        except OSError:
            continue
    return total


def _assert_inside(path: Path, root: Path):
    path = path.resolve()
    root = root.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise RetentionCleanupError(f"拒绝清理根目录之外的路径: {path}") from exc


def _prune_empty_parents(path: Path, *, stop_at: Path):
    stop_at = stop_at.resolve()
    current = path.resolve()
    while current != stop_at:
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def _format_bytes(value: int) -> str:
    size = float(value or 0)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _positive_int(value, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(parsed, 0)


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off", ""}


def _app_config(key: str):
    if has_app_context():
        return current_app.config.get(key)
    return None
