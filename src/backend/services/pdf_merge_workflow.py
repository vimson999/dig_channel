"""Pure contracts and filesystem boundaries for ordered PDF merge jobs."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import fitz  # type: ignore


FEATURE_NAME = "pdf_merge_workflow"
WORKFLOW_KEY = "pdf_merge_workflow"

DEFAULT_MAX_FILES = 30
DEFAULT_MAX_SINGLE_BYTES = 100 * 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 500 * 1024 * 1024

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_UPLOAD_ROOT = _PROJECT_ROOT / "data" / "uploads" / FEATURE_NAME
_DEFAULT_OUTPUT_ROOT = _PROJECT_ROOT / "runtime" / "content_pipeline" / "batches"
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]+")


class PdfMergeError(ValueError):
    """Raised when a merge source or output is unsafe or invalid."""


@dataclass(frozen=True)
class ValidatedPdfSource:
    path: Path
    filename: str
    sha256: str
    bytes: int
    page_count: int


@dataclass(frozen=True)
class PdfMergeResult:
    path: Path
    page_count: int
    bytes: int
    sha256: str


def max_pdf_merge_files() -> int:
    return _bounded_env(
        "PDF_MERGE_WORKFLOW_MAX_FILES",
        DEFAULT_MAX_FILES,
        minimum=2,
        maximum=200,
    )


def max_pdf_merge_single_bytes() -> int:
    return _bounded_env(
        "PDF_MERGE_WORKFLOW_MAX_SINGLE_BYTES",
        DEFAULT_MAX_SINGLE_BYTES,
        minimum=1024 * 1024,
        maximum=2 * 1024 * 1024 * 1024,
    )


def max_pdf_merge_total_bytes() -> int:
    return _bounded_env(
        "PDF_MERGE_WORKFLOW_MAX_TOTAL_BYTES",
        DEFAULT_MAX_TOTAL_BYTES,
        minimum=max_pdf_merge_single_bytes(),
        maximum=8 * 1024 * 1024 * 1024,
    )


def workflow_upload_root(task_id: str, *, create: bool = True) -> Path:
    """Return the only upload directory allowed for one merge task."""

    return _task_directory(
        os.getenv("PDF_MERGE_WORKFLOW_UPLOAD_ROOT"),
        _DEFAULT_UPLOAD_ROOT,
        task_id,
        create=create,
    )


def workflow_output_root(task_id: str, *, create: bool = True) -> Path:
    """Return the canonical-content output directory for one merge task."""

    configured = os.getenv("CONTENT_PIPELINE_OUTPUT_ROOT")
    base = Path(configured).expanduser() if configured else _DEFAULT_OUTPUT_ROOT
    return _task_directory(base, base, task_id, child="pdf_merge", create=create)


def workflow_output_path(task_id: str, filename: Any) -> Path:
    root = workflow_output_root(task_id)
    candidate = (root / normalize_output_filename(filename)).resolve()
    return safe_task_file(root, candidate)


def safe_task_file(root: Path | str, candidate: Path | str) -> Path:
    """Resolve ``candidate`` only if it is a file path inside ``root``."""

    root_path = Path(root).expanduser().resolve()
    candidate_path = Path(candidate).expanduser()
    if not candidate_path.is_absolute():
        candidate_path = root_path / candidate_path
    candidate_path = candidate_path.resolve()
    try:
        candidate_path.relative_to(root_path)
    except ValueError as exc:
        raise PdfMergeError("文件不在任务目录内") from exc
    return candidate_path


def normalize_output_filename(value: Any, *, now: datetime | None = None) -> str:
    """Return a stable, display-safe PDF filename without path components."""

    raw = Path(str(value or "").strip()).name
    raw = _CONTROL_CHARS_RE.sub("", raw).replace("/", "").replace("\\", "")
    if raw.lower().endswith(".pdf"):
        raw = raw[:-4]
    raw = raw.strip(" .")
    raw = re.sub(r"\s+", " ", raw)[:180]
    if not raw:
        timestamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
        raw = f"合并PDF-{timestamp}"
    return f"{raw}.pdf"


def fingerprint_file(path: Path | str) -> dict[str, Any]:
    """Return a content hash and display metadata for one local file."""

    candidate = Path(path).expanduser().resolve()
    if not candidate.exists() or not candidate.is_file():
        raise PdfMergeError("PDF 文件不存在")
    try:
        size = candidate.stat().st_size
    except OSError as exc:
        raise PdfMergeError("无法读取 PDF 文件") from exc
    if size <= 0:
        raise PdfMergeError("PDF 文件为空")
    return {
        "path": str(candidate),
        "filename": candidate.name,
        "bytes": size,
        "sha256": _sha256_file(candidate),
    }


def validate_source_collection(sources: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Validate cardinality, frozen sequence, and configured input byte limits."""

    if isinstance(sources, (str, bytes)) or not isinstance(sources, Sequence):
        raise PdfMergeError("PDF 列表格式不正确")
    values = [dict(source) for source in sources if isinstance(source, Mapping)]
    if len(values) != len(sources):
        raise PdfMergeError("PDF 列表格式不正确")
    if len(values) < 2:
        raise PdfMergeError("至少需要 2 个 PDF 才能合并")
    if len(values) > max_pdf_merge_files():
        raise PdfMergeError(f"一次最多合并 {max_pdf_merge_files()} 个 PDF")

    total_bytes = 0
    for position, source in enumerate(values, start=1):
        try:
            sequence = int(source.get("sequence"))
        except (TypeError, ValueError) as exc:
            raise PdfMergeError("PDF 序号不正确") from exc
        if sequence != position:
            raise PdfMergeError("PDF 序号必须从 1 开始连续递增")
        try:
            source_bytes = int(source.get("bytes"))
        except (TypeError, ValueError) as exc:
            raise PdfMergeError("PDF 文件大小不正确") from exc
        if source_bytes <= 0:
            raise PdfMergeError("PDF 文件为空")
        if source_bytes > max_pdf_merge_single_bytes():
            raise PdfMergeError("单个 PDF 超过大小限制")
        total_bytes += source_bytes
    if total_bytes > max_pdf_merge_total_bytes():
        raise PdfMergeError("PDF 总大小超过限制")
    return values


def validate_pdf_source(source: Mapping[str, Any]) -> ValidatedPdfSource:
    """Confirm a frozen source is still the exact readable, unencrypted PDF."""

    if not isinstance(source, Mapping):
        raise PdfMergeError("PDF 来源格式不正确")
    raw_path = str(source.get("path") or "").strip()
    if not raw_path:
        raise PdfMergeError("PDF 来源路径缺失")
    candidate = Path(raw_path).expanduser().resolve()
    if candidate.suffix.lower() != ".pdf":
        raise PdfMergeError("只支持 PDF 文件")

    fingerprint = fingerprint_file(candidate)
    expected_bytes = _optional_positive_int(source.get("bytes"), "PDF 文件大小不正确")
    if expected_bytes is not None and fingerprint["bytes"] != expected_bytes:
        raise PdfMergeError("PDF 文件内容已变化（大小不一致）")
    expected_sha256 = str(source.get("sha256") or "").strip().lower()
    if expected_sha256 and fingerprint["sha256"] != expected_sha256:
        raise PdfMergeError("PDF 文件内容已变化")

    try:
        document = fitz.open(str(candidate))
    except Exception as exc:
        raise PdfMergeError("PDF 无法打开") from exc
    try:
        if document.needs_pass:
            raise PdfMergeError("PDF 已加密，暂不支持合并")
        page_count = int(document.page_count or 0)
        if page_count <= 0:
            raise PdfMergeError("PDF 没有可合并页面")
    finally:
        document.close()
    return ValidatedPdfSource(
        path=candidate,
        filename=str(source.get("filename") or candidate.name),
        sha256=fingerprint["sha256"],
        bytes=fingerprint["bytes"],
        page_count=page_count,
    )


def merge_pdf_sources(
    sources: Sequence[Mapping[str, Any]],
    output_path: Path | str,
    progress_callback: Callable[[int, int, int], None] | None = None,
) -> PdfMergeResult:
    """Merge frozen sources in their submitted order and atomically verify output."""

    normalized_sources = validate_source_collection(sources)
    destination = Path(output_path).expanduser().resolve()
    if destination.suffix.lower() != ".pdf":
        raise PdfMergeError("合并输出必须是 PDF 文件")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".partial.pdf")
    _remove_if_exists(temporary)

    merged = fitz.open()
    total_pages = 0
    try:
        for position, source in enumerate(normalized_sources, start=1):
            validated = validate_pdf_source(source)
            try:
                with fitz.open(str(validated.path)) as document:
                    merged.insert_pdf(document)
            except PdfMergeError:
                raise
            except Exception as exc:
                raise PdfMergeError("PDF 页面合并失败") from exc
            total_pages += validated.page_count
            if progress_callback:
                progress_callback(position, len(normalized_sources), total_pages)

        merged.save(str(temporary))
    except Exception:
        _remove_if_exists(temporary)
        raise
    finally:
        merged.close()

    try:
        with fitz.open(str(temporary)) as verification:
            if verification.needs_pass or verification.page_count != total_pages:
                raise PdfMergeError("合并结果页数校验失败")
        os.replace(temporary, destination)
        fingerprint = fingerprint_file(destination)
    except Exception:
        _remove_if_exists(temporary)
        _remove_if_exists(destination)
        raise
    return PdfMergeResult(
        path=destination,
        page_count=total_pages,
        bytes=fingerprint["bytes"],
        sha256=fingerprint["sha256"],
    )


def _task_directory(
    configured_base: str | Path | None,
    default_base: Path,
    task_id: str,
    *,
    child: str | None = None,
    create: bool,
) -> Path:
    identifier = str(task_id or "").strip()
    if not _TASK_ID_RE.fullmatch(identifier):
        raise PdfMergeError("任务 ID 不合法")
    base = Path(configured_base).expanduser() if configured_base else default_base
    base = base.resolve()
    root = (base / child / identifier if child else base / identifier).resolve()
    try:
        root.relative_to(base)
    except ValueError as exc:
        raise PdfMergeError("任务目录不合法") from exc
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _optional_positive_int(value: Any, error_message: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise PdfMergeError(error_message) from exc
    if result <= 0:
        raise PdfMergeError(error_message)
    return result


def _bounded_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        raw = int(str(os.getenv(name, default)).strip())
    except (TypeError, ValueError):
        raw = default
    return max(minimum, min(raw, maximum))


def _remove_if_exists(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError:
        return


__all__ = [
    "DEFAULT_MAX_FILES",
    "DEFAULT_MAX_SINGLE_BYTES",
    "DEFAULT_MAX_TOTAL_BYTES",
    "FEATURE_NAME",
    "PdfMergeError",
    "PdfMergeResult",
    "ValidatedPdfSource",
    "WORKFLOW_KEY",
    "fingerprint_file",
    "max_pdf_merge_files",
    "max_pdf_merge_single_bytes",
    "max_pdf_merge_total_bytes",
    "merge_pdf_sources",
    "normalize_output_filename",
    "safe_task_file",
    "validate_pdf_source",
    "validate_source_collection",
    "workflow_output_path",
    "workflow_output_root",
    "workflow_upload_root",
]
