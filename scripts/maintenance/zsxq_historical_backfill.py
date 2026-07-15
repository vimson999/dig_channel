#!/usr/bin/env python3
"""Slow, resumable historical topic backfill for one ZSXQ group."""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import create_app  # noqa: E402
from models import db  # noqa: E402
from models.zsxq_sync import ZsxqSyncRun, ZsxqTopic  # noqa: E402
from services.platform_credential_service import resolve_zsxq_cookie  # noqa: E402
from services.zsxq_sync_service import zsxq_sync_service  # noqa: E402
from sqlalchemy import text  # noqa: E402


DEFAULT_GROUP_ID = "1824528822"
DEFAULT_GROUP_NAME = "生财有术"
DEFAULT_TARGET_START = "2024-01-01T00:00:00+08:00"
DEFAULT_TARGET_END = "2024-08-11T21:45:37.743+08:00"
DEFAULT_STATE_PATH = PROJECT_ROOT / "runtime" / "zsxq_shengcai_history_backfill.json"
TAB = {"id": "preset:all", "label": "最新", "scope": "all"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_topic_time(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    if len(raw) >= 5 and raw[-5] in {"+", "-"} and raw[-3] != ":":
        raw = raw[:-2] + ":" + raw[-2:]
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def topic_time(topic: Dict[str, Any]) -> str:
    talk = topic.get("talk") if isinstance(topic.get("talk"), dict) else {}
    return str(topic.get("create_time") or talk.get("create_time") or "")


def classify_topic_window(value: Any, target_start: datetime, target_end: datetime) -> str:
    parsed = parse_topic_time(value)
    if parsed is None:
        return "invalid"
    if parsed >= target_end:
        return "newer"
    if parsed < target_start:
        return "older"
    return "target"


def read_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_state(path: Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class RunLock:
    def __init__(self, state_path: Path, stale_seconds: int = 6 * 60 * 60):
        self.path = state_path.with_suffix(state_path.suffix + ".lock")
        self.stale_seconds = stale_seconds
        self.acquired = False

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and time.time() - self.path.stat().st_mtime > self.stale_seconds:
            self.path.unlink(missing_ok=True)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as error:
            raise RuntimeError("历史回填任务已经在运行，本轮跳过") from error
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"pid": os.getpid(), "started_at": now_iso()}))
        self.acquired = True
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        if self.acquired:
            self.path.unlink(missing_ok=True)


class HistoricalBackfillRunner:
    def __init__(
        self,
        *,
        cookie: str,
        group_id: str,
        group_name: str,
        target_start: datetime,
        target_end: datetime,
        state_path: Path,
        pages: int,
        sleep_min: float,
        sleep_max: float,
    ):
        self.cookie = cookie
        self.group_id = str(group_id)
        self.group_name = group_name
        self.target_start = target_start
        self.target_end = target_end
        self.state_path = state_path
        self.pages = max(1, min(int(pages), 12))
        self.sleep_min = max(1.0, float(sleep_min))
        self.sleep_max = max(self.sleep_min, float(sleep_max))

    def initial_cursor(self) -> Optional[str]:
        value = db.session.execute(
            text(
                """
                SELECT min(create_time)
                FROM zsxq_topics
                WHERE group_id = :group_id AND tab_id = :tab_id AND create_time IS NOT NULL
                """
            ),
            {"group_id": self.group_id, "tab_id": TAB["id"]},
        ).scalar()
        return str(value) if value else None

    def run(self) -> Dict[str, Any]:
        state = read_state(self.state_path)
        if state.get("status") == "complete":
            return state

        cursor = state.get("cursor_end_time") or self.initial_cursor()
        totals = state.get("totals") if isinstance(state.get("totals"), dict) else {}
        for key in ("pages", "fetched", "stored", "new", "updated", "existing", "invalid"):
            totals[key] = int(totals.get(key) or 0)

        run = ZsxqSyncRun(
            group_id=self.group_id,
            group_name=self.group_name,
            tab_id=TAB["id"],
            tab_label=TAB["label"],
            mode="history_backfill",
            status="running",
            requested_tabs=[zsxq_sync_service._public_tab(TAB)],
            result={"running": True, "cursor_end_time": cursor, "started_at": now_iso()},
        )
        db.session.add(run)
        db.session.commit()

        batch = {"pages": 0, "fetched": 0, "stored": 0, "new": 0, "updated": 0, "existing": 0, "invalid": 0}
        phase = "seeking_target_window"
        try:
            for page in range(1, self.pages + 1):
                if page > 1:
                    time.sleep(random.uniform(self.sleep_min, self.sleep_max))
                response = zsxq_sync_service._get_topics_with_retry(
                    cookie=self.cookie,
                    group_id=self.group_id,
                    count=20,
                    scope="all",
                    end_time=cursor,
                    extra_params={},
                    tab_id=TAB["id"],
                    tab_label=TAB["label"],
                    page=page,
                )
                topics = zsxq_sync_service._extract_topics(response)
                batch["pages"] += 1
                batch["fetched"] += len(topics)
                totals["pages"] += 1
                totals["fetched"] += len(topics)
                if not topics:
                    phase = "upstream_exhausted"
                    break

                reached_before_start = False
                for topic in topics:
                    classification = classify_topic_window(topic_time(topic), self.target_start, self.target_end)
                    if classification == "invalid":
                        batch["invalid"] += 1
                        totals["invalid"] += 1
                        continue
                    if classification == "newer":
                        continue
                    if classification == "older":
                        reached_before_start = True
                        continue
                    phase = "backfilling_target_window"
                    self.upsert_topic(run, topic, batch, totals)

                db.session.commit()
                next_cursor = (response.get("resp_data") or {}).get("next_end_time") or zsxq_sync_service._oldest_topic_time(topics)
                if not next_cursor or next_cursor == cursor:
                    phase = "cursor_not_advanced"
                    break
                cursor = next_cursor
                state = self.build_state(phase, cursor, totals, batch, run.id)
                write_state(self.state_path, state)
                print(
                    f"[{now_iso()}] page={page}/{self.pages} fetched={len(topics)} stored={batch['stored']} "
                    f"cursor={cursor} phase={phase}",
                    flush=True,
                )
                if reached_before_start:
                    phase = "complete"
                    break

            state = self.build_state(phase, cursor, totals, batch, run.id)
            run.status = "completed"
            run.completed_at = datetime.utcnow()
            run.result = {**state, "running": False}
            db.session.commit()
            write_state(self.state_path, state)
            return state
        except BaseException as error:
            db.session.rollback()
            state = self.build_state("failed", cursor, totals, batch, run.id)
            state["last_error"] = str(error)
            run.status = "failed"
            run.completed_at = datetime.utcnow()
            run.error_message = str(error)
            run.result = {**state, "running": False}
            db.session.add(run)
            db.session.commit()
            write_state(self.state_path, state)
            raise

    def upsert_topic(self, run, topic, batch, totals) -> None:
        topic_id = zsxq_sync_service._topic_id(topic)
        if not topic_id:
            batch["invalid"] += 1
            totals["invalid"] += 1
            return
        existing = ZsxqTopic.query.filter_by(group_id=self.group_id, tab_id=TAB["id"], topic_id=topic_id).first()
        if existing:
            batch["existing"] += 1
            totals["existing"] += 1
            if zsxq_sync_service._update_existing_topic(existing, topic, TAB, run):
                batch["updated"] += 1
                totals["updated"] += 1
        else:
            db.session.add(zsxq_sync_service._create_topic_record(self.group_id, self.group_name, TAB, topic, run))
            batch["new"] += 1
            totals["new"] += 1
        batch["stored"] += 1
        totals["stored"] += 1

    def build_state(self, phase, cursor, totals, batch, run_id) -> Dict[str, Any]:
        return {
            "group_id": self.group_id,
            "group_name": self.group_name,
            "target_start": self.target_start.isoformat(),
            "target_end": self.target_end.isoformat(),
            "status": phase,
            "cursor_end_time": cursor,
            "totals": dict(totals),
            "last_run": {**batch, "run_id": str(run_id), "finished_at": now_iso()},
            "updated_at": now_iso(),
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--group-id", default=DEFAULT_GROUP_ID)
    parser.add_argument("--group-name", default=DEFAULT_GROUP_NAME)
    parser.add_argument("--target-start", default=DEFAULT_TARGET_START)
    parser.add_argument("--target-end", default=DEFAULT_TARGET_END)
    parser.add_argument("--state", default=str(DEFAULT_STATE_PATH))
    parser.add_argument("--pages", type=int, default=4)
    parser.add_argument("--sleep-min", type=float, default=6.0)
    parser.add_argument("--sleep-max", type=float, default=12.0)
    parser.add_argument("--credential-id", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    state_path = Path(args.state).expanduser().resolve()
    target_start = parse_topic_time(args.target_start)
    target_end = parse_topic_time(args.target_end)
    if not target_start or not target_end or target_start >= target_end:
        raise ValueError("目标时间范围无效")

    with RunLock(state_path):
        app = create_app()
        with app.app_context():
            credential = resolve_zsxq_cookie(
                account_id=args.credential_id or None,
                purpose="zsxq.sync",
                required=True,
            )
            runner = HistoricalBackfillRunner(
                cookie=credential["cookie"],
                group_id=args.group_id,
                group_name=args.group_name,
                target_start=target_start,
                target_end=target_end,
                state_path=state_path,
                pages=args.pages,
                sleep_min=args.sleep_min,
                sleep_max=args.sleep_max,
            )
            state = runner.run()
            print(json.dumps(state, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
