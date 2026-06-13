#!/usr/bin/env python3
"""Compare local and external pytest PDF cleaners on the same inputs."""

import argparse
import json
import sys
import uuid
from pathlib import Path

import fitz  # type: ignore
import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.content_pipeline.pdf_cleaner_adapter import PdfCleanOptions, PdfCleanerAdapter  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare migrated local PDF cleaner with external pytest service.")
    parser.add_argument("inputs", nargs="+", help="PDF files or directories containing PDFs.")
    parser.add_argument("--external-url", default="http://127.0.0.1:5005", help="External pytest cleaner base URL.")
    parser.add_argument("--output-root", default=str(PROJECT_ROOT / "runtime" / "content_pipeline" / "cleaner_compare"))
    parser.add_argument("--threshold", type=int, default=245)
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--pages", type=int, default=3, help="Pages per file to render for visual diff.")
    parser.add_argument("--dpi", type=int, default=72)
    parser.add_argument("--tolerance", type=float, default=1.0, help="Allowed mean absolute pixel difference.")
    parser.add_argument("--do-ocr", action="store_true")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON only.")
    args = parser.parse_args()

    files = collect_pdf_files(args.inputs)
    if not files:
        print("No PDF files found.", file=sys.stderr)
        return 2

    external_health = PdfCleanerAdapter(base_url=args.external_url).check_service(engine="external")
    if not external_health.get("success"):
        print(json.dumps({"success": False, "reason": "external_unavailable", "health": external_health}, ensure_ascii=False, indent=2))
        return 2

    output_root = Path(args.output_root).expanduser().resolve()
    local_result = run_cleaner("local", files, output_root / "local", args)
    external_result = run_cleaner("external", files, output_root / "external", args)
    comparisons = compare_results(local_result, external_result, max_pages=args.pages, dpi=args.dpi)

    failed = [
        item for item in comparisons
        if not item.get("success") or float(item.get("max_mean_abs_diff") or 0) > args.tolerance
    ]
    report = {
        "success": not failed,
        "inputs": [str(path) for path in files],
        "local": summarize_run(local_result),
        "external": summarize_run(external_result),
        "tolerance": args.tolerance,
        "comparisons": comparisons,
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_human_report(report)
    return 1 if failed else 0


def collect_pdf_files(inputs: list[str]) -> list[Path]:
    files = []
    seen = set()
    for raw in inputs:
        path = Path(raw).expanduser().resolve()
        candidates = sorted(path.rglob("*.pdf")) if path.is_dir() else [path]
        for candidate in candidates:
            if not candidate.exists() or not candidate.is_file() or candidate.suffix.lower() != ".pdf":
                continue
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            files.append(candidate)
    return files


def run_cleaner(engine: str, files: list[Path], output_root: Path, args) -> dict:
    adapter = PdfCleanerAdapter(base_url=args.external_url, output_root=output_root)
    return adapter.run_batch(
        batch_id=f"compare-{engine}-{uuid.uuid4().hex[:10]}",
        files=files,
        options=PdfCleanOptions(
            engine=engine,
            threshold=args.threshold,
            do_ocr=args.do_ocr,
            timeout_seconds=args.timeout,
        ),
        reset_workspace=True,
    )


def summarize_run(result: dict) -> dict:
    return {
        "engine": result.get("engine") or "external",
        "success_count": result.get("success_count"),
        "failed_count": result.get("failed_count"),
        "local_output_dir": result.get("local_output_dir"),
    }


def compare_results(local_result: dict, external_result: dict, max_pages: int, dpi: int) -> list[dict]:
    local_files = by_relative_path(local_result.get("cleaned_files") or [])
    external_files = by_relative_path(external_result.get("cleaned_files") or [])
    keys = sorted(set(local_files) | set(external_files))
    comparisons = []
    for key in keys:
        local_item = local_files.get(key)
        external_item = external_files.get(key)
        if not local_item or not external_item:
            comparisons.append({
                "relative_path": key,
                "success": False,
                "reason": "missing_output",
                "local_exists": bool(local_item),
                "external_exists": bool(external_item),
            })
            continue
        comparisons.append(compare_pdfs(Path(local_item["path"]), Path(external_item["path"]), key, max_pages, dpi))
    return comparisons


def by_relative_path(items: list[dict]) -> dict:
    return {str(item.get("relative_path") or item.get("source") or item.get("path")): item for item in items}


def compare_pdfs(local_path: Path, external_path: Path, relative_path: str, max_pages: int, dpi: int) -> dict:
    local_doc = fitz.open(local_path)
    external_doc = fitz.open(external_path)
    try:
        if len(local_doc) != len(external_doc):
            return {
                "relative_path": relative_path,
                "success": False,
                "reason": "page_count_mismatch",
                "local_pages": len(local_doc),
                "external_pages": len(external_doc),
            }

        page_count = min(len(local_doc), max_pages)
        page_diffs = []
        for index in range(page_count):
            local_img = render_page(local_doc[index], dpi)
            external_img = render_page(external_doc[index], dpi)
            if local_img.shape != external_img.shape:
                page_diffs.append({
                    "page": index + 1,
                    "success": False,
                    "reason": "shape_mismatch",
                    "local_shape": list(local_img.shape),
                    "external_shape": list(external_img.shape),
                })
                continue
            diff = np.abs(local_img.astype(np.int16) - external_img.astype(np.int16))
            page_diffs.append({
                "page": index + 1,
                "success": True,
                "mean_abs_diff": round(float(diff.mean()), 4),
                "max_abs_diff": int(diff.max()),
            })

        max_mean = max((float(item.get("mean_abs_diff") or 0) for item in page_diffs), default=0)
        return {
            "relative_path": relative_path,
            "success": all(item.get("success") for item in page_diffs),
            "checked_pages": page_count,
            "max_mean_abs_diff": round(max_mean, 4),
            "pages": page_diffs,
            "local_path": str(local_path),
            "external_path": str(external_path),
        }
    finally:
        local_doc.close()
        external_doc.close()


def render_page(page, dpi: int) -> np.ndarray:
    pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csRGB, alpha=False)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n).copy()


def print_human_report(report: dict) -> None:
    status = "PASS" if report.get("success") else "FAIL"
    print(f"PDF cleaner comparison: {status}")
    print(f"Local output:    {report['local'].get('local_output_dir')}")
    print(f"External output: {report['external'].get('local_output_dir')}")
    print(f"Tolerance:       mean_abs_diff <= {report['tolerance']}")
    for item in report.get("comparisons") or []:
        if not item.get("success"):
            print(f"- {item.get('relative_path')}: FAIL ({item.get('reason')})")
            continue
        print(
            f"- {item.get('relative_path')}: "
            f"pages={item.get('checked_pages')} max_mean_abs_diff={item.get('max_mean_abs_diff')}"
        )


if __name__ == "__main__":
    raise SystemExit(main())
