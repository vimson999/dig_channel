#!/usr/bin/env python3
"""Scan, fetch, and enrich external documents linked from cached ZSXQ topics."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import create_app  # noqa: E402
from models import db  # noqa: E402
from models.external_document import ExternalDocument, ZsxqTopicExternalDocument  # noqa: E402
from services.external_document_service import canonicalize_url, external_document_service, url_hash  # noqa: E402
from services.platform_credential_service import save_credential, validate_payload_structure  # noqa: E402


def positive_int(value: Optional[str]) -> Optional[int]:
    if value is None or value == "":
        return None
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def import_feishu_cookie(path: Path, *, name: str, priority: int) -> Dict[str, Any]:
    cookie = path.read_text(encoding="utf-8").strip()
    ok, message = validate_payload_structure("feishu", "cookie_header", cookie)
    account = save_credential({
        "platform": "feishu",
        "credential_type": "cookie_header",
        "name": name,
        "purposes": ["feishu.fetch"],
        "status": "active",
        "priority": priority,
        "owner_label": "文档抓取",
        "payload": cookie,
        "notes": f"Imported from {path.name} by zsxq_external_documents_backfill.py",
    })
    return {
        "id": account.id,
        "name": account.name,
        "masked_summary": account.masked_summary,
        "valid": ok,
        "validation_message": message,
    }


def collect_counts(group_id: Optional[str] = None) -> Dict[str, Any]:
    doc_query = ExternalDocument.query
    relation_query = ZsxqTopicExternalDocument.query
    if group_id:
        relation_query = relation_query.filter(ZsxqTopicExternalDocument.group_id == str(group_id))
        document_ids = relation_query.with_entities(ZsxqTopicExternalDocument.external_document_id).distinct()
        doc_query = doc_query.filter(ExternalDocument.id.in_(document_ids))

    fetch_counts = {}
    summary_counts = {}
    tts_counts = {}
    for row in doc_query.with_entities(ExternalDocument.fetch_status, db.func.count(ExternalDocument.id)).group_by(ExternalDocument.fetch_status):
        fetch_counts[row[0] or "unknown"] = int(row[1])
    for row in doc_query.with_entities(ExternalDocument.summary_status, db.func.count(ExternalDocument.id)).group_by(ExternalDocument.summary_status):
        summary_counts[row[0] or "unknown"] = int(row[1])
    for row in doc_query.with_entities(ExternalDocument.tts_status, db.func.count(ExternalDocument.id)).group_by(ExternalDocument.tts_status):
        tts_counts[row[0] or "unknown"] = int(row[1])

    return {
        "documents": int(doc_query.count()),
        "relations": int(relation_query.count()),
        "fetch_status": fetch_counts,
        "summary_status": summary_counts,
        "tts_status": tts_counts,
    }


def normalize_existing_urls() -> Dict[str, int]:
    documents = ExternalDocument.query.filter(ExternalDocument.canonical_url.contains("%5C")).all()
    updated = 0
    merged = 0
    deleted_relations = 0
    reset_failed = 0
    for document in documents:
        canonical = canonicalize_url(document.canonical_url or document.source_url)
        if not canonical or canonical == document.canonical_url:
            continue
        next_hash = url_hash(canonical)
        with db.session.no_autoflush:
            existing = ExternalDocument.query.filter(
                ExternalDocument.url_hash == next_hash,
                ExternalDocument.id != document.id,
            ).first()
        relations = ZsxqTopicExternalDocument.query.filter_by(external_document_id=document.id).all()
        if existing and existing.id:
            for relation in relations:
                with db.session.no_autoflush:
                    duplicate = ZsxqTopicExternalDocument.query.filter_by(
                        group_id=relation.group_id,
                        tab_id=relation.tab_id,
                        topic_id=relation.topic_id,
                        url_hash=next_hash,
                    ).first()
                if duplicate:
                    db.session.delete(relation)
                    deleted_relations += 1
                else:
                    relation.external_document_id = existing.id
                    relation.canonical_url = canonical
                    relation.url_hash = next_hash
            db.session.flush()
            ExternalDocument.query.filter_by(id=document.id).delete(synchronize_session=False)
            merged += 1
            continue

        document.canonical_url = canonical
        document.source_url = canonical
        document.url_hash = next_hash
        if document.fetch_status == "failed":
            document.fetch_status = "pending"
            document.fetch_error = None
            reset_failed += 1
        for relation in relations:
            relation.canonical_url = canonical
            relation.source_url = canonical
            relation.url_hash = next_hash
        updated += 1
    db.session.commit()

    invalid_cookie_failures = ExternalDocument.query.filter(
        ExternalDocument.fetch_status == "failed",
        ExternalDocument.fetch_error.ilike("%Invalid leading whitespace%"),
    ).all()
    for document in invalid_cookie_failures:
        document.fetch_status = "pending"
        document.fetch_error = None
        reset_failed += 1
    if invalid_cookie_failures:
        db.session.commit()
    return {
        "updated": updated,
        "merged": merged,
        "deleted_relations": deleted_relations,
        "reset_failed": reset_failed,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group-id", help="Only scan/backfill one ZSXQ group.")
    parser.add_argument("--scan-limit", type=positive_int, help="Limit topics scanned from the cached topic table.")
    parser.add_argument("--document-limit", type=positive_int, help="Limit external documents processed for fetch/summary.")
    parser.add_argument("--skip-scan", action="store_true", help="Skip topic scanning and only process existing external document rows.")
    parser.add_argument("--fetch", action="store_true", help="Fetch pending/failed Feishu documents after scanning.")
    parser.add_argument("--summarize", action="store_true", help="Generate DS v4 flash summaries for fetched documents.")
    parser.add_argument("--normalize-urls", action="store_true", help="Normalize already scanned external document URLs before processing.")
    parser.add_argument("--credential-id", help="Feishu credential id to use for fetching.")
    parser.add_argument("--import-feishu-cookie-file", type=Path, help="Import a Feishu cookie header/json file before backfill.")
    parser.add_argument("--credential-name", default="飞书文档 Cookie", help="Name for imported Feishu credential.")
    parser.add_argument("--credential-priority", type=int, default=90, help="Priority for imported Feishu credential.")
    parser.add_argument("--report-file", type=Path, help="Write the final JSON report to this file.")
    parser.add_argument("--quiet", action="store_true", help="Suppress application logs while running.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.quiet:
        logging.disable(logging.CRITICAL)
    app = create_app()

    with app.app_context():
        imported = None
        credential_id = args.credential_id
        if args.import_feishu_cookie_file:
            imported = import_feishu_cookie(
                args.import_feishu_cookie_file,
                name=args.credential_name,
                priority=args.credential_priority,
            )
            credential_id = imported["id"]
        normalization = normalize_existing_urls() if args.normalize_urls else None

        result = external_document_service.backfill(
            group_id=args.group_id,
            scan_limit=args.scan_limit,
            scan=not args.skip_scan,
            fetch=args.fetch,
            summarize=args.summarize,
            document_limit=args.document_limit,
            credential_id=credential_id,
        )
        report = {
            "success": True,
            "imported_credential": imported,
            "normalization": normalization,
            "backfill": result,
            "counts": collect_counts(args.group_id),
        }
        report_text = json.dumps(report, ensure_ascii=False, indent=2)
        if args.report_file:
            args.report_file.write_text(report_text, encoding="utf-8")
            print(json.dumps({"success": True, "report_file": str(args.report_file)}, ensure_ascii=False))
        else:
            print(report_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
