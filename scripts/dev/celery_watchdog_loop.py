#!/usr/bin/env python3
import logging
import os
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _poll_seconds():
    try:
        return max(10, int(os.environ.get("CELERY_AUTO_HEAL_POLL_SECONDS", "60")))
    except ValueError:
        return 60


def main():
    from app import create_app
    from apis.v1.celery_monitor import run_watchdog_once
    from models import db

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    app = create_app()
    interval = _poll_seconds()
    print(f"Celery watchdog started. interval={interval}s", flush=True)

    while True:
        try:
            with app.app_context():
                try:
                    result = run_watchdog_once()
                    actions = [
                        f"{item.get('service_id')}:{item.get('action')}"
                        for item in result.get("results", [])
                        if item.get("action") not in {None, "none"}
                    ]
                    if actions:
                        print(f"watchdog actions: {', '.join(actions)}", flush=True)
                finally:
                    try:
                        db.session.rollback()
                    finally:
                        db.session.remove()
        except KeyboardInterrupt:
            print("Celery watchdog stopped.", flush=True)
            return 0
        except Exception as exc:
            print(f"Celery watchdog error: {exc}", flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
