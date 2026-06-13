import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[4]


class PdfCleanerError(RuntimeError):
    """Raised when the local PDF cleaner service cannot complete a batch."""


@dataclass
class PdfCleanOptions:
    threshold: int = 245
    do_ocr: bool = False
    overwrite: bool = False
    prefix: str = ""
    poll_interval: float = 1.5
    timeout_seconds: int = 3600
    engine: str = "external"


class PdfCleanerAdapter:
    """Adapter for PDF cleaner engines used by the content pipeline.

    In external mode, this stages files into pytest/runtime/batches/<batch_id>,
    calls the pytest /api/batch-clean service, then copies cleaned PDFs back.
    In local mode, it runs the migrated PDFProcessor inside this project.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        cleaner_project_dir: Optional[str | Path] = None,
        cleaner_batch_root: Optional[str] = None,
        output_root: Optional[str | Path] = None,
    ):
        self.base_url = (base_url or os.getenv("PDF_CLEANER_BASE_URL") or "http://127.0.0.1:5005").rstrip("/")
        self.cleaner_project_dir = Path(
            cleaner_project_dir
            or os.getenv("PDF_CLEANER_PROJECT_DIR")
            or (PROJECT_ROOT.parents[1] / "pytest")
        ).expanduser().resolve()
        self.cleaner_batch_root = (cleaner_batch_root or os.getenv("PDF_CLEANER_BATCH_ROOT") or "runtime/batches").strip("/")
        self.output_root = Path(
            output_root
            or os.getenv("CONTENT_PIPELINE_OUTPUT_ROOT")
            or (PROJECT_ROOT / "runtime" / "content_pipeline" / "batches")
        ).expanduser().resolve()
        self.session = requests.Session()
        self.session.trust_env = False

    def check_service(self, timeout: float = 3.0, engine: Optional[str] = None) -> dict:
        """Check whether the configured PDF cleaner engine is reachable."""
        engine_name = str(engine or os.getenv("PDF_CLEANER_ENGINE") or "external").strip().lower()
        if engine_name == "local":
            try:
                from services.watermark.pdf_processor import PDFProcessor

                PDFProcessor(threshold=245)
                return {
                    "success": True,
                    "mode": "local",
                    "engine": "local",
                    "message": "当前系统本地 PDF 清洗引擎可用",
                }
            except Exception as exc:
                return {
                    "success": False,
                    "mode": "local",
                    "engine": "local",
                    "error": str(exc),
                }

        # External pytest mode. pytest may not expose /api/health, so we first
        # try it and fall back to /api/folder-tree, which its batch page uses.
        health_url = f"{self.base_url}/api/health"
        try:
            response = self.session.get(health_url, timeout=timeout)
            if response.status_code == 200:
                return {
                    "success": True,
                    "mode": "health",
                    "engine": "external",
                    "url": health_url,
                    "data": _safe_json(response),
                }
        except requests.RequestException:
            pass

        folder_tree_url = f"{self.base_url}/api/folder-tree"
        try:
            response = self.session.get(folder_tree_url, timeout=timeout)
            data = _safe_json(response)
            if response.ok and isinstance(data, dict) and data.get("success"):
                return {
                    "success": True,
                    "mode": "folder-tree",
                    "engine": "external",
                    "url": folder_tree_url,
                    "data": {
                        "success": data.get("success"),
                        "root": (data.get("tree") or {}).get("name"),
                    },
                }
            return {
                "success": False,
                "mode": "folder-tree",
                "engine": "external",
                "url": folder_tree_url,
                "status_code": response.status_code,
                "data": data,
            }
        except requests.RequestException as exc:
            return {
                "success": False,
                "mode": "unreachable",
                "engine": "external",
                "url": folder_tree_url,
                "error": str(exc),
            }

    def run_batch(
        self,
        *,
        batch_id: str,
        files: Iterable[str | Path],
        source_root: Optional[str | Path] = None,
        options: Optional[PdfCleanOptions] = None,
        progress_callback: Optional[Callable[[dict], None]] = None,
        reset_workspace: bool = True,
    ) -> dict:
        options = options or PdfCleanOptions()
        if str(options.engine or "").strip().lower() == "local":
            return self._run_local_batch(
                batch_id=batch_id,
                files=files,
                source_root=source_root,
                options=options,
                progress_callback=progress_callback,
                reset_workspace=reset_workspace,
            )

        safe_batch_id = _safe_batch_id(batch_id)
        input_files = _resolve_pdf_files(files)
        if not input_files:
            raise PdfCleanerError("没有可清洗的 PDF 文件")

        pytest_batch_dir = self._pytest_batch_dir(safe_batch_id)
        pytest_input_dir = pytest_batch_dir / "input"
        pytest_output_dir = pytest_batch_dir / "output"
        local_batch_dir = self.output_root / safe_batch_id
        local_output_dir = local_batch_dir / "pdf_cleaned"

        if reset_workspace and pytest_batch_dir.exists():
            _assert_inside(pytest_batch_dir, self.cleaner_project_dir)
            shutil.rmtree(pytest_batch_dir)

        pytest_input_dir.mkdir(parents=True, exist_ok=True)
        pytest_output_dir.mkdir(parents=True, exist_ok=True)
        local_output_dir.mkdir(parents=True, exist_ok=True)

        staged_files = self._stage_inputs(input_files, pytest_input_dir, source_root=source_root)
        manifest = {
            "batch_id": safe_batch_id,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "cleaner_base_url": self.base_url,
            "cleaner_project_dir": str(self.cleaner_project_dir),
            "pytest_input_dir": str(pytest_input_dir),
            "pytest_output_dir": str(pytest_output_dir),
            "local_output_dir": str(local_output_dir),
            "files": staged_files,
            "options": {
                "threshold": options.threshold,
                "do_ocr": options.do_ocr,
                "overwrite": options.overwrite,
                "prefix": options.prefix,
            },
        }
        _write_json(pytest_batch_dir / "manifest.json", manifest)
        _write_json(local_batch_dir / "manifest.json", manifest)

        job_id = self._start_clean_job(
            batch_id=safe_batch_id,
            input_dir=self._relative_to_cleaner_root(pytest_input_dir),
            output_dir=self._relative_to_cleaner_root(pytest_output_dir),
            options=options,
        )
        if progress_callback:
            progress_callback({"phase": "started", "job_id": job_id, "progress": 8})

        final_job = self._wait_for_job(job_id, options, progress_callback=progress_callback)
        copied_outputs = self._copy_outputs(pytest_output_dir, local_output_dir, final_job)

        result = {
            "batch_id": safe_batch_id,
            "job_id": job_id,
            "pytest_workspace": str(pytest_batch_dir),
            "local_workspace": str(local_batch_dir),
            "local_output_dir": str(local_output_dir),
            "job": final_job,
            "cleaned_files": copied_outputs,
            "success_count": len(copied_outputs),
            "failed_count": int(final_job.get("failed") or 0),
            "skipped_count": int(final_job.get("skipped") or 0),
        }
        manifest["result"] = result
        _write_json(pytest_batch_dir / "manifest.json", manifest)
        _write_json(local_batch_dir / "manifest.json", manifest)
        return result

    def _run_local_batch(
        self,
        *,
        batch_id: str,
        files: Iterable[str | Path],
        source_root: Optional[str | Path] = None,
        options: Optional[PdfCleanOptions] = None,
        progress_callback: Optional[Callable[[dict], None]] = None,
        reset_workspace: bool = True,
    ) -> dict:
        from services.watermark.pdf_processor import PDFProcessor

        options = options or PdfCleanOptions(engine="local")
        safe_batch_id = _safe_batch_id(batch_id)
        input_files = _resolve_pdf_files(files)
        if not input_files:
            raise PdfCleanerError("没有可清洗的 PDF 文件")

        local_batch_dir = self.output_root / safe_batch_id
        local_output_dir = local_batch_dir / "pdf_cleaned"
        if reset_workspace and local_batch_dir.exists():
            _assert_inside(local_batch_dir, self.output_root)
            shutil.rmtree(local_batch_dir)
        local_output_dir.mkdir(parents=True, exist_ok=True)

        source_base = Path(source_root).expanduser().resolve() if source_root else None
        processor = PDFProcessor(threshold=int(options.threshold))
        files_result = []
        cleaned_files = []
        failed_count = 0
        skipped_count = 0
        total = len(input_files)

        for index, source in enumerate(input_files, start=1):
            rel_path = _relative_source_path(source, source_base)
            output_rel = _prefixed_pdf_path(rel_path, options.prefix)
            target = (local_output_dir / output_rel).resolve()
            _assert_inside(target, local_output_dir)
            target.parent.mkdir(parents=True, exist_ok=True)

            if target.exists() and not options.overwrite:
                item = {
                    "source": rel_path.as_posix(),
                    "status": "SKIPPED",
                    "message": "输出文件已存在",
                    "duration": 0,
                    "output": output_rel.as_posix(),
                    "cleaner_output_path": str(target),
                    "path": str(target),
                    "relative_path": output_rel.as_posix(),
                }
                skipped_count += 1
                cleaned_files.append(item)
                files_result.append(item)
                continue

            if progress_callback:
                progress_callback({
                    "phase": "local_cleaning",
                    "progress": min(88, max(8, int((index - 1) / total * 80) + 8)),
                    "message": f"本地 PDF 清洗 {index}/{total}",
                })

            started = time.time()
            success, message = processor.remove_watermark(
                str(source),
                str(target),
                do_header_clean=True,
                do_ocr=bool(options.do_ocr),
            )
            duration = round(time.time() - started, 2)
            if success and target.exists():
                item = {
                    "source": rel_path.as_posix(),
                    "status": "SUCCESS",
                    "message": message,
                    "duration": duration,
                    "output": output_rel.as_posix(),
                    "cleaner_output_path": str(target),
                    "path": str(target),
                    "relative_path": output_rel.as_posix(),
                }
                cleaned_files.append(item)
                files_result.append(item)
            else:
                failed_count += 1
                files_result.append({
                    "source": rel_path.as_posix(),
                    "status": "FAILED",
                    "message": message,
                    "duration": duration,
                    "output": "",
                    "path": str(target),
                    "relative_path": output_rel.as_posix(),
                })

        job = {
            "state": "FINISHED",
            "total": total,
            "current": total,
            "success": sum(1 for item in files_result if item.get("status") == "SUCCESS"),
            "failed": failed_count,
            "skipped": skipped_count,
            "overall_percent": 100,
            "message": "批量清洗完成",
            "files": files_result,
        }
        result = {
            "batch_id": safe_batch_id,
            "engine": "local",
            "local_workspace": str(local_batch_dir),
            "local_output_dir": str(local_output_dir),
            "job": job,
            "files": files_result,
            "cleaned_files": cleaned_files,
            "success_count": len(cleaned_files),
            "failed_count": failed_count,
            "skipped_count": 0,
        }
        manifest = {
            "batch_id": safe_batch_id,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "engine": "local",
            "local_output_dir": str(local_output_dir),
            "files": [{"source_path": str(path)} for path in input_files],
            "options": {
                "threshold": options.threshold,
                "do_ocr": options.do_ocr,
                "overwrite": options.overwrite,
                "prefix": options.prefix,
                "engine": "local",
            },
            "result": result,
        }
        _write_json(local_batch_dir / "manifest.json", manifest)
        return result

    def _pytest_batch_dir(self, batch_id: str) -> Path:
        batch_root = (self.cleaner_project_dir / self.cleaner_batch_root).resolve()
        batch_dir = (batch_root / batch_id).resolve()
        _assert_inside(batch_dir, self.cleaner_project_dir)
        return batch_dir

    def _relative_to_cleaner_root(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.cleaner_project_dir).as_posix()
        except ValueError as exc:
            raise PdfCleanerError(f"路径不在 PDF 清洗项目目录内: {path}") from exc

    def _stage_inputs(self, files: list[Path], input_dir: Path, source_root: Optional[str | Path]) -> list[dict]:
        source_base = Path(source_root).expanduser().resolve() if source_root else None
        seen: set[str] = set()
        staged = []

        for index, source in enumerate(files, start=1):
            rel_path = _relative_source_path(source, source_base)
            rel_posix = rel_path.as_posix()
            if rel_posix in seen:
                rel_path = rel_path.with_name(f"{index:03d}_{rel_path.name}")
                rel_posix = rel_path.as_posix()
            seen.add(rel_posix)

            target = input_dir / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            staged.append(
                {
                    "source_path": str(source),
                    "staged_relative_path": rel_posix,
                    "staged_path": str(target),
                }
            )
        return staged

    def _start_clean_job(self, *, batch_id: str, input_dir: str, output_dir: str, options: PdfCleanOptions) -> str:
        payload = {
            "batch_id": batch_id,
            "input_dir": input_dir,
            "output_dir": output_dir,
            "threshold": int(options.threshold),
            "prefix": options.prefix or "",
            "do_ocr": bool(options.do_ocr),
            "overwrite": bool(options.overwrite),
        }
        try:
            response = self.session.post(
                f"{self.base_url}/api/batch-clean",
                json=payload,
                timeout=15,
            )
        except requests.RequestException as exc:
            raise PdfCleanerError(f"无法连接 PDF 清洗服务: {exc}") from exc

        data = _safe_json(response)
        if not response.ok or not isinstance(data, dict) or not data.get("success"):
            message = data.get("error") if isinstance(data, dict) else response.text
            raise PdfCleanerError(f"启动 PDF 清洗失败: {message}")
        job_id = data.get("job_id")
        if not job_id:
            raise PdfCleanerError("PDF 清洗服务未返回 job_id")
        return str(job_id)

    def _wait_for_job(
        self,
        job_id: str,
        options: PdfCleanOptions,
        progress_callback: Optional[Callable[[dict], None]] = None,
    ) -> dict:
        deadline = time.time() + max(30, int(options.timeout_seconds))
        last_job = None

        while time.time() < deadline:
            try:
                response = self.session.get(f"{self.base_url}/api/batch-clean/status/{job_id}", timeout=10)
            except requests.RequestException as exc:
                raise PdfCleanerError(f"查询 PDF 清洗状态失败: {exc}") from exc

            data = _safe_json(response)
            if not response.ok or not isinstance(data, dict) or not data.get("success"):
                message = data.get("error") if isinstance(data, dict) else response.text
                raise PdfCleanerError(f"PDF 清洗状态不可用: {message}")

            job = data.get("job") or {}
            last_job = job
            overall = float(job.get("overall_percent") or 0)
            if progress_callback:
                progress_callback(
                    {
                        "phase": "running",
                        "job_id": job_id,
                        "progress": min(88, max(10, int(overall * 0.8) + 10)),
                        "job": job,
                    }
                )

            state = str(job.get("state") or "").upper()
            if state == "FINISHED":
                return job
            if state in {"FAILED", "ERROR"}:
                raise PdfCleanerError(job.get("message") or f"PDF 清洗失败: {state}")

            time.sleep(max(0.5, float(options.poll_interval)))

        raise PdfCleanerError(f"PDF 清洗超时，最后状态: {last_job}")

    def _copy_outputs(self, pytest_output_dir: Path, local_output_dir: Path, job: dict) -> list[dict]:
        copied = []
        files = job.get("files") or []
        for item in files:
            status = str(item.get("status") or "").upper()
            output_rel = item.get("output")
            if status not in {"SUCCESS", "SKIPPED"} or not output_rel:
                continue
            source = (pytest_output_dir / output_rel).resolve()
            _assert_inside(source, pytest_output_dir)
            if not source.exists():
                continue
            target = (local_output_dir / output_rel).resolve()
            _assert_inside(target, local_output_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied.append(
                {
                    "source": item.get("name"),
                    "status": status,
                    "message": item.get("message") or "",
                    "duration": item.get("duration") or 0,
                    "cleaner_output_path": str(source),
                    "path": str(target),
                    "relative_path": output_rel,
                }
            )
        return copied


def _safe_batch_id(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    if not safe:
        raise PdfCleanerError("batch_id 不能为空")
    return safe[:120]


def _resolve_pdf_files(files: Iterable[str | Path]) -> list[Path]:
    resolved = []
    for item in files or []:
        path = Path(item).expanduser().resolve()
        if not path.exists() or not path.is_file():
            raise PdfCleanerError(f"PDF 文件不存在: {path}")
        if path.suffix.lower() != ".pdf":
            raise PdfCleanerError(f"只支持 PDF 文件: {path}")
        resolved.append(path)
    return resolved


def _relative_source_path(source: Path, source_base: Optional[Path]) -> Path:
    if source_base:
        try:
            return source.relative_to(source_base)
        except ValueError:
            pass
    return Path(source.name)


def _prefixed_pdf_path(rel_path: Path, prefix: str) -> Path:
    clean_prefix = str(prefix or "").strip()
    if not clean_prefix:
        return rel_path
    return rel_path.with_name(f"{clean_prefix}{rel_path.stem}.pdf")


def _assert_inside(path: Path, root: Path) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise PdfCleanerError(f"路径越界: {path}") from exc


def _safe_json(response) -> dict:
    try:
        return response.json()
    except ValueError:
        return {"raw": response.text}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
