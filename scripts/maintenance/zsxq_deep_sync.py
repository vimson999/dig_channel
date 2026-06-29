#!/usr/bin/env python3
"""Deep ZSXQ metadata sync for one-off backfills.

This intentionally bypasses the normal sync_tabs target-topic cap so a caller can
sync a fixed number of pages per tab, while still writing into the normal DB
tables and run history.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import create_app  # noqa: E402
from models import db  # noqa: E402
from models.zsxq_sync import ZsxqSyncRun, ZsxqTopic  # noqa: E402
from services.platform_credential_service import resolve_zsxq_cookie  # noqa: E402
from services.zsxq_service import zsxq_service  # noqa: E402
from services.zsxq_sync_service import zsxq_sync_service  # noqa: E402
from sqlalchemy.orm import load_only  # noqa: E402


DEFAULT_GROUPS = [
    {
        "group_id": "1824528822",
        "group_name": "生财有术",
        "mode": "deep_10p",
        "strategy": "all_tabs_fixed_pages",
        "pages_per_tab": 10,
    },
    {
        "group_id": "88885882121542",
        "group_name": "洪灝的宏观策略",
        "mode": "deep_2026",
        "strategy": "latest_until_year",
        "year": 2026,
        "max_pages": 500,
    },
]


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def clean_menu_title(value: Any) -> str:
    text = str(value or "").strip()
    return text.strip("#").strip() or "未命名"


def menu_hashtag_id(menu: Dict[str, Any]) -> Optional[str]:
    hashtag = menu.get("hashtag") if isinstance(menu.get("hashtag"), dict) else {}
    value = (
        hashtag.get("hashtag_id")
        or menu.get("hashtag_id")
        or ((menu.get("target") or {}).get("hashtag_id") if isinstance(menu.get("target"), dict) else None)
        or ((menu.get("target") or {}).get("id") if isinstance(menu.get("target"), dict) else None)
        or (menu.get("menu_id") if menu.get("type") == "hashtag" else None)
    )
    return str(value).strip() if value else None


def menu_to_tab(menu: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    label = clean_menu_title(menu.get("title") or menu.get("name") or menu.get("menu_id"))
    hashtag_id = menu_hashtag_id(menu)
    if hashtag_id:
        return {
            "id": f"hashtag:{hashtag_id}",
            "label": label,
            "scope": "all",
            "hashtagId": hashtag_id,
        }

    if label == "最新":
        return {"id": "preset:all", "label": label, "scope": "all"}
    if label == "精华":
        return {"id": "preset:digests", "label": label, "scope": "digests"}

    preset = menu.get("preset_type") or menu.get("preset") or menu.get("type")
    if preset:
        preset = str(preset)
        scope = {"all": "all", "with_files": "with_files", "with_images": "with_images", "digests": "digests"}.get(
            preset,
            preset,
        )
        return {"id": f"preset:{preset}", "label": label, "scope": scope}
    return None


def dedupe_tabs(tabs: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    output = []
    for tab in tabs:
        tab_id = str(tab.get("id") or "").strip()
        if not tab_id or tab_id in seen:
            continue
        seen.add(tab_id)
        output.append(tab)
    return output


def topic_time(topic: Dict[str, Any]) -> str:
    talk = topic.get("talk") if isinstance(topic.get("talk"), dict) else {}
    return str(topic.get("create_time") or talk.get("create_time") or "")


def topic_year(topic: Dict[str, Any]) -> Optional[int]:
    value = topic_time(topic)
    if len(value) >= 4 and value[:4].isdigit():
        return int(value[:4])
    return None


def page_size_for_tab(tab: Dict[str, Any]) -> int:
    return 20


def merge_attachment_stats(target: Dict[str, Any], stats: Dict[str, Any]) -> None:
    zsxq_sync_service._merge_attachment_stats(target, stats)


def requested_tabs_payload(tabs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [zsxq_sync_service._public_tab(tab) for tab in tabs]


class DeepSyncRunner:
    def __init__(self, *, cookie: str, attempts: int = 6, base_delay: float = 8.0, dry_run: bool = False):
        self.cookie = cookie
        self.attempts = max(1, min(int(attempts), 8))
        self.base_delay = max(float(base_delay), 0.1)
        self.dry_run = dry_run
        self.errors: List[Dict[str, Any]] = []

    def get_menus_with_retry(self, group_id: str) -> List[Dict[str, Any]]:
        for attempt in range(1, self.attempts + 1):
            result = zsxq_service.get_menus(self.cookie, group_id)
            try:
                zsxq_sync_service._assert_upstream_success(result)
                data = result.get("resp_data") or {}
                return data.get("menus") or data.get("menu") or []
            except Exception as exc:
                if attempt >= self.attempts:
                    raise
                delay = min(self.base_delay * (2 ** (attempt - 1)), 60.0) + random.uniform(0, 1.5)
                print(
                    f"[{now_iso()}] menu retry group={group_id} attempt={attempt}/{self.attempts} "
                    f"delay={delay:.1f}s error={exc}",
                    flush=True,
                )
                time.sleep(delay)
        return []

    def tabs_for_group(self, group_id: str, *, require_menus: bool) -> List[Dict[str, Any]]:
        try:
            menus = self.get_menus_with_retry(group_id)
        except Exception as exc:
            if require_menus:
                raise
            self.errors.append({"group_id": group_id, "stage": "menus", "error": str(exc)})
            menus = []

        tabs = dedupe_tabs(tab for tab in (menu_to_tab(menu) for menu in menus) if tab)
        if tabs:
            return tabs
        return [{"id": "preset:all", "label": "最新", "scope": "all"}]

    def run_group(self, config: Dict[str, Any]) -> Dict[str, Any]:
        group_id = str(config["group_id"])
        group_name = str(config.get("group_name") or group_id)
        strategy = str(config["strategy"])
        tabs = (
            self.tabs_for_group(group_id, require_menus=True)
            if strategy == "all_tabs_fixed_pages"
            else [{"id": "preset:all", "label": "最新", "scope": "all"}]
        )

        print(
            f"[{now_iso()}] start group={group_id} name={group_name} strategy={strategy} tabs={len(tabs)}",
            flush=True,
        )
        for index, tab in enumerate(tabs, start=1):
            print(f"  tab {index}/{len(tabs)} id={tab['id']} label={tab['label']}", flush=True)

        run = ZsxqSyncRun(
            group_id=group_id,
            group_name=group_name,
            tab_id=tabs[0]["id"] if len(tabs) == 1 else None,
            tab_label=tabs[0]["label"] if len(tabs) == 1 else None,
            mode=str(config.get("mode") or "deep")[:20],
            status="running",
            requested_tabs=requested_tabs_payload(tabs),
            result={
                "running": True,
                "strategy": strategy,
                "started_at": now_iso(),
                "tabs_total": len(tabs),
            },
        )
        db.session.add(run)
        db.session.commit()

        result = self.initial_result(group_id, group_name, strategy, tabs, config)
        try:
            for index, tab in enumerate(tabs, start=1):
                if index > 1:
                    zsxq_sync_service._human_pause()
                tab_result = self.run_tab(group_id, group_name, tab, run, config)
                result["tabs"].append(tab_result)
                self.merge_tab_result(result, tab_result)
                self.persist_run_progress(run, result, tab, tab_result)

            result["running"] = False
            result["completed_at"] = now_iso()
            result["errors"] = self.errors
            run.status = "completed"
            run.completed_at = datetime.utcnow()
            run.error_message = None
            run.result = result
            db.session.commit()
            print(
                f"[{now_iso()}] completed group={group_id} run={run.id} "
                f"topics={result['topics_fetched']} new={result['topics_new']} "
                f"updated={result['topics_updated']} existing={result['topics_existing']} errors={len(self.errors)}",
                flush=True,
            )
            return {"run_id": run.id, "status": run.status, "result": result}
        except BaseException as exc:
            db.session.rollback()
            result["running"] = False
            result["failed_at"] = now_iso()
            result["errors"] = [*self.errors, {"group_id": group_id, "stage": "group", "error": str(exc)}]
            run.status = "failed"
            run.completed_at = datetime.utcnow()
            run.error_message = str(exc)
            run.result = result
            db.session.add(run)
            db.session.commit()
            print(f"[{now_iso()}] failed group={group_id} run={run.id} error={exc}", flush=True)
            if isinstance(exc, KeyboardInterrupt):
                raise
            return {"run_id": run.id, "status": run.status, "result": result}

    def initial_result(
        self,
        group_id: str,
        group_name: str,
        strategy: str,
        tabs: List[Dict[str, Any]],
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "group_id": group_id,
            "group_name": group_name,
            "strategy": strategy,
            "started_at": now_iso(),
            "tabs_total": len(tabs),
            "requested_tabs": requested_tabs_payload(tabs),
            "planned_pages_total": self.planned_pages_total(tabs, config),
            "topics_fetched": 0,
            "topics_new": 0,
            "topics_updated": 0,
            "topics_existing": 0,
            "attachments_seen": 0,
            "attachments_downloaded": 0,
            "attachments_failed": 0,
            "attachments_skipped": 0,
            "download_limit_reached": False,
            "tabs": [],
        }

    def planned_pages_total(self, tabs: List[Dict[str, Any]], config: Dict[str, Any]) -> Optional[int]:
        if config["strategy"] == "all_tabs_fixed_pages":
            return int(config.get("pages_per_tab") or 0) * len(tabs)
        return None

    def run_tab(
        self,
        group_id: str,
        group_name: str,
        tab: Dict[str, Any],
        run: ZsxqSyncRun,
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        strategy = str(config["strategy"])
        max_pages = int(config.get("pages_per_tab") or config.get("max_pages") or 1)
        year = int(config["year"]) if config.get("year") else None
        tab_result = {
            "tab_id": tab["id"],
            "tab_label": tab["label"],
            "strategy": strategy,
            "page_size": page_size_for_tab(tab),
            "planned_pages": max_pages,
            "pages": 0,
            "topics_fetched": 0,
            "topics_new": 0,
            "topics_updated": 0,
            "topics_existing": 0,
            "attachments_seen": 0,
            "attachments_downloaded": 0,
            "attachments_failed": 0,
            "attachments_skipped": 0,
            "last_end_time": None,
            "stopped_reason": None,
            "errors": [],
        }
        end_time = None
        newest_topic = None
        seen_ids = set()
        last_end_time = None

        for page in range(1, max_pages + 1):
            if page > 1:
                zsxq_sync_service._human_pause()
            try:
                response = zsxq_sync_service._get_topics_with_retry(
                    cookie=self.cookie,
                    group_id=group_id,
                    count=tab_result["page_size"],
                    scope=tab.get("scope") or "all",
                    end_time=end_time,
                    extra_params=zsxq_sync_service._tab_extra_params(tab),
                    tab_id=tab["id"],
                    tab_label=tab["label"],
                    page=page,
                )
            except Exception as exc:
                error = {"page": page, "error": str(exc)}
                tab_result["errors"].append(error)
                self.errors.append({"group_id": group_id, "tab_id": tab["id"], **error})
                tab_result["stopped_reason"] = "page_error"
                print(
                    f"[{now_iso()}] page failed group={group_id} tab={tab['label']} "
                    f"page={page} error={exc}",
                    flush=True,
                )
                break

            raw_topics = zsxq_sync_service._extract_topics(response)
            tab_result["pages"] += 1
            if not raw_topics:
                tab_result["stopped_reason"] = "empty_page"
                break

            selected_topics = []
            stop_after_page = False
            for topic in raw_topics:
                topic_id = zsxq_sync_service._topic_id(topic)
                if not topic_id or topic_id in seen_ids:
                    continue
                seen_ids.add(topic_id)
                if year:
                    current_year = topic_year(topic)
                    if current_year == year:
                        selected_topics.append(topic)
                    elif current_year is not None and current_year < year:
                        stop_after_page = True
                else:
                    selected_topics.append(topic)

            if selected_topics and newest_topic is None:
                newest_topic = selected_topics[0]
            for topic in selected_topics:
                self.upsert_topic(group_id, group_name, tab, run, topic, tab_result)

            db.session.commit()
            next_end_time = (response.get("resp_data") or {}).get("next_end_time") or zsxq_sync_service._oldest_topic_time(raw_topics)
            tab_result["last_end_time"] = next_end_time
            self.persist_run_progress(run, None, tab, tab_result)
            print(
                f"[{now_iso()}] page ok group={group_id} tab={tab['label']} page={page}/{max_pages} "
                f"raw={len(raw_topics)} kept={len(selected_topics)} total={tab_result['topics_fetched']} "
                f"new={tab_result['topics_new']} updated={tab_result['topics_updated']} "
                f"existing={tab_result['topics_existing']} end={next_end_time}",
                flush=True,
            )

            if stop_after_page:
                tab_result["stopped_reason"] = f"before_{year}"
                break
            if not next_end_time:
                tab_result["stopped_reason"] = "no_next_end_time"
                break
            if next_end_time == last_end_time or next_end_time == end_time:
                tab_result["stopped_reason"] = "cursor_not_advanced"
                break
            last_end_time = next_end_time
            end_time = next_end_time

        if not tab_result["stopped_reason"]:
            tab_result["stopped_reason"] = "planned_pages_done"
        if newest_topic:
            zsxq_sync_service._upsert_cursor(group_id, tab, newest_topic, tab_result)
            db.session.commit()
        return tab_result

    def upsert_topic(
        self,
        group_id: str,
        group_name: str,
        tab: Dict[str, Any],
        run: ZsxqSyncRun,
        topic: Dict[str, Any],
        tab_result: Dict[str, Any],
    ) -> None:
        topic_id = zsxq_sync_service._topic_id(topic)
        existing = ZsxqTopic.query.filter_by(group_id=group_id, tab_id=tab["id"], topic_id=topic_id).options(
            load_only(
                ZsxqTopic.id,
                ZsxqTopic.group_id,
                ZsxqTopic.tab_id,
                ZsxqTopic.tab_label,
                ZsxqTopic.topic_id,
                ZsxqTopic.title,
                ZsxqTopic.author_name,
                ZsxqTopic.content,
                ZsxqTopic.create_time,
                ZsxqTopic.raw_payload,
                ZsxqTopic.last_sync_run_id,
            )
        ).first()
        if existing:
            tab_result["topics_existing"] += 1
            changed = zsxq_sync_service._update_existing_topic(existing, topic, tab, run)
            if changed:
                tab_result["topics_updated"] += 1
        else:
            record = zsxq_sync_service._create_topic_record(group_id, group_name, tab, topic, run)
            db.session.add(record)
            db.session.flush()
            tab_result["topics_new"] += 1

        attachment_stats = zsxq_sync_service._upsert_attachments(group_id, tab, topic)
        merge_attachment_stats(tab_result, attachment_stats)
        tab_result["topics_fetched"] += 1

    def merge_tab_result(self, result: Dict[str, Any], tab_result: Dict[str, Any]) -> None:
        for key in (
            "pages",
            "topics_fetched",
            "topics_new",
            "topics_updated",
            "topics_existing",
            "attachments_seen",
            "attachments_downloaded",
            "attachments_failed",
            "attachments_skipped",
        ):
            result[key] = int(result.get(key) or 0) + int(tab_result.get(key) or 0)

    def persist_run_progress(
        self,
        run: ZsxqSyncRun,
        result: Optional[Dict[str, Any]],
        tab: Dict[str, Any],
        tab_result: Dict[str, Any],
    ) -> None:
        if result is None:
            previous = run.result if isinstance(run.result, dict) else {}
            result = {
                **{key: previous.get(key) for key in ("strategy", "started_at", "tabs_total") if key in previous},
                "running": True,
                "current_tab": zsxq_sync_service._public_tab(tab),
                "partial": dict(tab_result),
            }
        else:
            result = {
                **result,
                "running": True,
                "current_tab": zsxq_sync_service._public_tab(tab),
                "partial": dict(tab_result),
            }
        run.result = result
        run.updated_at = datetime.utcnow()
        db.session.commit()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--credential-id", default="", help="PlatformCredentialAccount id. Defaults to active zsxq.sync.")
    parser.add_argument("--attempts", type=int, default=6)
    parser.add_argument("--base-delay", type=float, default=8.0)
    parser.add_argument("--output", default="")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    runtime_dir = PROJECT_ROOT / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    output_path = Path(args.output) if args.output else runtime_dir / f"zsxq_deep_sync_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"

    app = create_app()
    app.config["ZSXQ_SYNC_RETRY_ATTEMPTS"] = args.attempts
    app.config["ZSXQ_SYNC_RETRY_BASE_DELAY_SECONDS"] = args.base_delay
    with app.app_context():
        credential = resolve_zsxq_cookie(
            account_id=args.credential_id or None,
            purpose="zsxq.sync",
            required=True,
        )
        print(
            f"[{now_iso()}] using credential={credential.get('credential_id')} "
            f"account={credential.get('account_name') or '-'}",
            flush=True,
        )
        runner = DeepSyncRunner(
            cookie=credential["cookie"],
            attempts=args.attempts,
            base_delay=args.base_delay,
            dry_run=args.dry_run,
        )
        report = {
            "started_at": now_iso(),
            "credential_id": credential.get("credential_id"),
            "groups": [],
        }
        for config in DEFAULT_GROUPS:
            report["groups"].append(runner.run_group(config))
        report["completed_at"] = now_iso()
        report["errors"] = runner.errors
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[{now_iso()}] report written {output_path}", flush=True)
        return 0 if not any(group["status"] == "failed" for group in report["groups"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
