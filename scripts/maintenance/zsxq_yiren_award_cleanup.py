#!/usr/bin/env python3
"""Backfill local favorites for explicit winning replies by Yiren."""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import create_app  # noqa: E402
from services.zsxq_yiren_award_cleanup_service import cleanup_yiren_award_favorites  # noqa: E402


def parse_args():
    parser = argparse.ArgumentParser(description="清洗亦仁明确中标回复并补齐本地收藏")
    parser.add_argument("--group-id", default="1824528822")
    parser.add_argument("--since", help="只扫描此 ISO 时间之后的回复")
    parser.add_argument("--dry-run", action="store_true", help="只审计，不写入收藏")
    parser.add_argument("--output", type=Path, help="审计报告 JSON 路径")
    return parser.parse_args()


def write_report(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def main():
    args = parse_args()
    app = create_app()
    with app.app_context():
        report = cleanup_yiren_award_favorites(
            group_id=args.group_id,
            since=args.since,
            dry_run=args.dry_run,
        )
    output = args.output or (
        PROJECT_ROOT
        / "runtime"
        / f"zsxq_yiren_award_cleanup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    write_report(output, report)
    report["report_path"] = str(output)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
