import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from collections import Counter
from pathlib import Path

import fitz  # type: ignore
from werkzeug.datastructures import FileStorage


logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
TASK_DATA_ROOT = PROJECT_ROOT / "data" / "tasks"
PDF_PARSE_CACHE_PREFIX = "report_video:pdf_parse"
PDF_PARSE_CACHE_VERSION = os.environ.get("REPORT_VIDEO_PDF_PARSE_CACHE_VERSION", "20260609_v1")
PDF_PARSE_CACHE_TTL = int(os.environ.get("REPORT_VIDEO_PDF_PARSE_CACHE_TTL", str(30 * 24 * 3600)))
_PDF_PARSE_REDIS_CLIENT = None


def sanitize_filename(value):
    name = (value or "").strip()
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    name = re.sub(r"\s+", "_", name)
    return name or "report.pdf"


def report_code(index):
    return f"{index:03d}"


def task_dir(task_id):
    return TASK_DATA_ROOT / str(task_id)


def report_dir(task_id, code):
    return task_dir(task_id) / "reports" / code


def rel_path(path):
    return str(Path(path).resolve().relative_to(PROJECT_ROOT))


def _pdf_parse_cache_enabled():
    return os.environ.get("REPORT_VIDEO_PDF_PARSE_CACHE_ENABLED", "true").lower() not in {"0", "false", "no", "off"}


def _pdf_parse_cache_client():
    if not _pdf_parse_cache_enabled():
        return None
    global _PDF_PARSE_REDIS_CLIENT
    if _PDF_PARSE_REDIS_CLIENT is not None:
        return _PDF_PARSE_REDIS_CLIENT
    try:
        import redis
        from config.base import BaseConfig

        redis_url = (
            os.environ.get("REPORT_VIDEO_PARSE_REDIS_URL")
            or os.environ.get("REDIS_URL")
            or os.environ.get("CELERY_BROKER_URL")
            or BaseConfig.REDIS_URL
        )
        _PDF_PARSE_REDIS_CLIENT = redis.Redis.from_url(redis_url, decode_responses=True)
        return _PDF_PARSE_REDIS_CLIENT
    except Exception as exc:
        logger.warning("[REPORT_VIDEO_PARSE_CACHE] redis client unavailable: %s", exc)
        return None


def _pdf_parse_cache_key(file_name):
    safe_name = sanitize_filename(file_name or "report.pdf")
    digest = hashlib.sha256(safe_name.encode("utf-8")).hexdigest()
    return f"{PDF_PARSE_CACHE_PREFIX}:{digest}", safe_name


def _read_pdf_parse_cache(file_name, file_size):
    client = _pdf_parse_cache_client()
    if client is None:
        return None

    cache_key, safe_name = _pdf_parse_cache_key(file_name)
    try:
        raw = client.get(cache_key)
        if not raw:
            return None
        payload = json.loads(raw)
        if payload.get("parser_version") != PDF_PARSE_CACHE_VERSION:
            return None
        if int(payload.get("file_size") or -1) != int(file_size or 0):
            return None
        if payload.get("file_name") != safe_name:
            return None
        result = payload.get("result") or {}
        raw_text = payload.get("raw_text")
        cleaned_text = payload.get("cleaned_text")
        if not isinstance(raw_text, str) or not isinstance(cleaned_text, str):
            return None
        logger.info("[REPORT_VIDEO_PARSE_CACHE] hit | key=%s file=%s", cache_key, safe_name)
        return {
            "cache_key": cache_key,
            "cached_at": payload.get("cached_at"),
            "result": result,
            "raw_text": raw_text,
            "cleaned_text": cleaned_text,
        }
    except Exception as exc:
        logger.warning("[REPORT_VIDEO_PARSE_CACHE] read failed | file=%s error=%s", safe_name, exc)
        return None


def _should_store_pdf_parse_cache(result):
    cleaned_chars = int(result.get("cleaned_text_chars") or 0)
    extraction_method = result.get("extraction_method") or "pdf_text"
    return cleaned_chars >= 500 and extraction_method != "pdf_text_low_quality"


def _write_pdf_parse_cache(file_name, file_size, result, raw_text, cleaned_text):
    if not _should_store_pdf_parse_cache(result):
        return None

    client = _pdf_parse_cache_client()
    if client is None:
        return None

    cache_key, safe_name = _pdf_parse_cache_key(file_name)
    payload = {
        "parser_version": PDF_PARSE_CACHE_VERSION,
        "file_name": safe_name,
        "file_size": int(file_size or 0),
        "cached_at": datetime.utcnow().isoformat(),
        "result": {
            "page_count": result.get("page_count"),
            "raw_text_chars": result.get("raw_text_chars"),
            "cleaned_text_chars": result.get("cleaned_text_chars"),
            "is_scanned": result.get("is_scanned"),
            "extraction_method": result.get("extraction_method"),
            "stats": result.get("stats") or {},
        },
        "raw_text": raw_text or "",
        "cleaned_text": cleaned_text or "",
    }
    try:
        client.setex(cache_key, PDF_PARSE_CACHE_TTL, json.dumps(payload, ensure_ascii=False))
        logger.info("[REPORT_VIDEO_PARSE_CACHE] stored | key=%s file=%s ttl=%s", cache_key, safe_name, PDF_PARSE_CACHE_TTL)
        return cache_key
    except Exception as exc:
        logger.warning("[REPORT_VIDEO_PARSE_CACHE] write failed | file=%s error=%s", safe_name, exc)
        return None


def _write_parse_text_files(output_dir, raw_text, cleaned_text):
    raw_path = output_dir / "raw_text.txt"
    cleaned_path = output_dir / "cleaned_text.txt"
    raw_path.write_text(raw_text or "", encoding="utf-8")
    cleaned_path.write_text(cleaned_text or "", encoding="utf-8")
    return raw_path, cleaned_path


def clean_pdf_text(raw_text):
    text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    raw_lines = []
    for line in text.split("\n"):
        normalized = re.sub(r"\s+", " ", line).strip()
        if not normalized:
            raw_lines.append("")
            continue
        if re.fullmatch(r"[-—_ ]*\d+[-—_ ]*", normalized):
            continue
        raw_lines.append(normalized)

    line_counts = Counter(line for line in raw_lines if line)
    lines = []
    for line in raw_lines:
        if _is_noise_line(line, line_counts):
            continue
        lines.append(line)

    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _is_noise_line(line, line_counts):
    if not line:
        return False
    if line_counts.get(line, 0) <= 2:
        return False
    if "知识星球" in line or line.upper().startswith("VX:"):
        return True
    ascii_ratio = sum(1 for char in line if char.isascii() and char.isalnum()) / max(len(line), 1)
    return len(line) >= 32 and ascii_ratio >= 0.85


def scan_text_stats(sample_pages):
    cn_count = 0
    en_count = 0
    for text in sample_pages:
        cn_count += len(re.findall(r"[\u4e00-\u9fff]", text))
        en_count += len(re.findall(r"[A-Za-z0-9]", text))
    page_count = max(len(sample_pages), 1)
    return {
        "avg_cn_chars": cn_count / page_count,
        "avg_en_chars": en_count / page_count,
    }


def is_low_quality_extraction(text, page_count, stats):
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return True

    chars_per_page = len(compact) / max(page_count, 1)
    if page_count >= 2 and chars_per_page < 220:
        return True

    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if len(lines) >= 6:
        counts = Counter(lines)
        repeated_chars = sum(len(line) * count for line, count in counts.items() if count >= 3)
        repeated_ratio = repeated_chars / max(sum(len(line) for line in lines), 1)
        unique_ratio = len(counts) / len(lines)
        if repeated_ratio >= 0.45 or unique_ratio <= 0.35:
            return True

    if stats["avg_cn_chars"] < 50 and stats["avg_en_chars"] < 100:
        return True

    return False


def tesseract_languages():
    try:
        result = subprocess.run(
            ["tesseract", "--list-langs"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None

    langs = set(result.stdout.splitlines())
    selected = []
    if "chi_sim" in langs:
        selected.append("chi_sim")
    if "eng" in langs:
        selected.append("eng")
    return "+".join(selected) if selected else None


def ocr_pdf_file(pdf_path, page_count, dpi=170):
    languages = tesseract_languages()
    if not languages:
        return "", {"available": False, "error": "tesseract 或语言包不可用"}

    texts = []
    errors = []
    scale = dpi / 72
    with tempfile.TemporaryDirectory(prefix="report_video_ocr_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        with fitz.open(str(pdf_path)) as doc:
            for index, page in enumerate(doc):
                image_path = tmp_path / f"page_{index + 1:04d}.png"
                pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                pix.save(str(image_path))
                try:
                    result = subprocess.run(
                        [
                            "tesseract",
                            str(image_path),
                            "stdout",
                            "-l",
                            languages,
                            "--psm",
                            "6",
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                        timeout=120,
                    )
                    page_text = result.stdout.strip()
                    texts.append(page_text)
                except subprocess.SubprocessError as exc:
                    errors.append({"page": index + 1, "error": str(exc)})
                    texts.append("")

    return "\n\n".join(texts).strip(), {
        "available": True,
        "languages": languages,
        "dpi": dpi,
        "page_count": page_count,
        "errors": errors,
    }


def parse_pdf_file(pdf_path, output_dir, cache_name=None):
    pdf_path = Path(pdf_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    file_size = pdf_path.stat().st_size if pdf_path.exists() else 0
    cache_name = cache_name or pdf_path.name

    cached = _read_pdf_parse_cache(cache_name, file_size)
    if cached:
        raw_text = cached["raw_text"]
        cleaned_text = cached["cleaned_text"]
        raw_path, cleaned_path = _write_parse_text_files(output_dir, raw_text, cleaned_text)
        result = dict(cached["result"] or {})
        result.update({
            "raw_text_path": rel_path(raw_path),
            "cleaned_text_path": rel_path(cleaned_path),
            "raw_text_chars": len(raw_text),
            "cleaned_text_chars": len(cleaned_text),
            "cache": {
                "hit": True,
                "key": cached["cache_key"],
                "parser_version": PDF_PARSE_CACHE_VERSION,
                "cached_at": cached.get("cached_at"),
            },
        })
        return result

    with fitz.open(str(pdf_path)) as doc:
        page_texts = []
        sample_pages = []
        for idx, page in enumerate(doc):
            text = page.get_text("text") or ""
            page_texts.append(text)
            if idx < 3:
                sample_pages.append(text)
        page_count = doc.page_count

    direct_text = "\n\n".join(page_texts).strip()
    stats = scan_text_stats(sample_pages)
    extraction_method = "pdf_text"
    ocr_meta = None
    raw_text = direct_text

    if is_low_quality_extraction(direct_text, page_count, stats):
        ocr_text, ocr_meta = ocr_pdf_file(pdf_path, page_count)
        if len(re.sub(r"\s+", "", ocr_text)) > len(re.sub(r"\s+", "", direct_text)):
            raw_text = ocr_text
            extraction_method = "ocr"
        else:
            extraction_method = "pdf_text_low_quality"

    cleaned_text = clean_pdf_text(raw_text)
    cleaned_stats = scan_text_stats([cleaned_text])
    is_scanned = extraction_method in {"ocr", "pdf_text_low_quality"} or stats["avg_cn_chars"] < 50 and stats["avg_en_chars"] < 100

    raw_path, cleaned_path = _write_parse_text_files(output_dir, raw_text, cleaned_text)

    result = {
        "page_count": page_count,
        "raw_text_path": rel_path(raw_path),
        "cleaned_text_path": rel_path(cleaned_path),
        "raw_text_chars": len(raw_text),
        "cleaned_text_chars": len(cleaned_text),
        "is_scanned": is_scanned,
        "extraction_method": extraction_method,
        "stats": {
            "direct": stats,
            "cleaned": cleaned_stats,
            "ocr": ocr_meta,
        },
        "cache": {
            "hit": False,
            "parser_version": PDF_PARSE_CACHE_VERSION,
        },
    }
    cache_key = _write_pdf_parse_cache(cache_name, file_size, result, raw_text, cleaned_text)
    if cache_key:
        result["cache"]["stored"] = True
        result["cache"]["key"] = cache_key
    return result


def save_report_pdf(task_id, code, file_storage: FileStorage):
    target_dir = report_dir(task_id, code)
    target_dir.mkdir(parents=True, exist_ok=True)

    safe_name = sanitize_filename(file_storage.filename)
    if not safe_name.lower().endswith(".pdf"):
        safe_name = f"{safe_name}.pdf"

    pdf_path = target_dir / "original.pdf"
    file_storage.save(str(pdf_path))
    original_name_path = target_dir / safe_name
    if original_name_path != pdf_path:
        try:
            if original_name_path.exists():
                original_name_path.unlink()
            shutil.copyfile(pdf_path, original_name_path)
        except OSError:
            pass

    parse_result = parse_pdf_file(pdf_path, target_dir, cache_name=file_storage.filename or safe_name)
    parse_result.update({
        "file_name": file_storage.filename or safe_name,
        "file_path": rel_path(pdf_path),
    })
    return parse_result


def save_report_pdf_file(task_id, code, file_storage: FileStorage):
    """Save a PDF for async parsing without doing expensive extraction inline."""
    target_dir = report_dir(task_id, code)
    target_dir.mkdir(parents=True, exist_ok=True)

    safe_name = sanitize_filename(file_storage.filename)
    if not safe_name.lower().endswith(".pdf"):
        safe_name = f"{safe_name}.pdf"

    pdf_path = target_dir / "original.pdf"
    file_storage.save(str(pdf_path))
    original_name_path = target_dir / safe_name
    if original_name_path != pdf_path:
        try:
            if original_name_path.exists():
                original_name_path.unlink()
            shutil.copyfile(pdf_path, original_name_path)
        except OSError:
            pass

    return {
        "file_name": file_storage.filename or safe_name,
        "file_path": rel_path(pdf_path),
    }


def read_task_text_file(path):
    if not path:
        return ""
    abs_path = (PROJECT_ROOT / path).resolve()
    if not str(abs_path).startswith(str(PROJECT_ROOT)):
        raise ValueError("Access denied")
    if not abs_path.exists():
        return ""
    return abs_path.read_text(encoding="utf-8", errors="replace")
