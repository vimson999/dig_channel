#!/usr/bin/env python3
"""Stop workers from claiming new messages before a full-stack restart."""

import os
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "src" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault("BACKEND_PROCESS_BOOTSTRAP_ENABLED", "0")


def main() -> int:
    from celery_config import celery_app

    queues = sorted({str(queue.name) for queue in celery_app.conf.task_queues})
    if not queues:
        print("ERROR: no Celery queues are configured; refusing an unquiesced restart.", file=sys.stderr)
        return 2

    try:
        for queue in queues:
            celery_app.control.broadcast(
                "cancel_consumer",
                arguments={"queue": queue},
                reply=False,
            )
    except Exception as exc:
        print(f"ERROR: failed to quiesce Celery consumers: {exc}", file=sys.stderr)
        return 3

    settle_seconds = max(1.0, float(os.environ.get("CELERY_QUIESCE_SETTLE_SECONDS", "2")))
    time.sleep(settle_seconds)
    print(f"OK: broadcast cancel_consumer for {len(queues)} queue(s).")
    print("Queued messages remain in Redis and will be consumed after the new workers start.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
