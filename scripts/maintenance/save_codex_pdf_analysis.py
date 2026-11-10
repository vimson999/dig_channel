#!/usr/bin/env python3
"""Persist one Codex PDF analysis submission through Shioa's local DB config."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "src" / "backend"
MAX_PAYLOAD_BYTES = 5 * 1024 * 1024


def _write_result(result: dict, *, stream=sys.stdout):
    stream.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")


def _load_submission():
    payload_bytes = sys.stdin.buffer.read(MAX_PAYLOAD_BYTES + 1)
    if not payload_bytes:
        raise ValueError("stdin 中没有提交内容")
    if len(payload_bytes) > MAX_PAYLOAD_BYTES:
        raise ValueError("提交内容不能超过 5 MiB")
    try:
        payload_text = payload_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("提交内容必须使用 UTF-8 编码") from exc
    try:
        return json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"提交内容不是有效 JSON（第 {exc.lineno} 行第 {exc.colno} 列）") from exc


def _make_app():
    # Reuse Shioa's local .env and config. No interactive password, API token,
    # or separately maintained database credential is requested from Codex.
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
    sys.path.insert(0, str(BACKEND_ROOT))

    from flask import Flask
    from config import Config
    from models import db

    app = Flask("codex_pdf_analysis_writer")
    app.config.from_object(Config)
    db.init_app(app)
    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--user-id",
        type=int,
        help="现有 Shioa 管理员用户 ID；单一 active admin 时可省略",
    )
    args = parser.parse_args(argv)

    try:
        submission = _load_submission()
    except ValueError as exc:
        _write_result({"status": "error", "message": str(exc)}, stream=sys.stderr)
        return 2
    try:
        app = _make_app()
    except Exception as exc:
        _write_result(
            {"status": "error", "message": f"无法加载 Shioa 配置（{type(exc).__name__}）"},
            stream=sys.stderr,
        )
        return 1

    from models import db
    from services.codex_pdf_analysis_service import (
        CodexPdfAnalysisDuplicateError,
        CodexPdfAnalysisValidationError,
        resolve_active_admin_id,
        save_codex_pdf_analysis,
    )

    try:
        with app.app_context():
            user_id = resolve_active_admin_id(args.user_id)
            run = save_codex_pdf_analysis(user_id, submission)
            _write_result(
                {
                    "status": "saved",
                    "run_id": run.id,
                    "viewer_path": f"/codex-pdf-analyses.html?run_id={run.id}",
                    "contract_version": run.contract_version,
                    "analysis_version": run.analysis_version,
                }
            )
            return 0
    except CodexPdfAnalysisDuplicateError as exc:
        _write_result(
            {
                "status": "already_exists",
                "run_id": exc.existing_run_id,
                "viewer_path": f"/codex-pdf-analyses.html?run_id={exc.existing_run_id}",
                "message": str(exc),
            }
        )
        return 3
    except CodexPdfAnalysisValidationError as exc:
        _write_result({"status": "error", "message": str(exc)}, stream=sys.stderr)
        return 2
    except Exception as exc:  # Do not emit DB URLs or connection secrets in CLI output.
        _write_result(
            {"status": "error", "message": f"数据库写入失败（{type(exc).__name__}）"},
            stream=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
