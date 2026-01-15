import json
from pathlib import Path
from typing import List

QUEUE_FILE = Path("data/queue.json")

def load_queue_order() -> List[str]:
    if not QUEUE_FILE.exists():
        return []
    try:
        with open(QUEUE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []

def save_queue_order(order: List[str]):
    with open(QUEUE_FILE, "w", encoding="utf-8") as f:
        json.dump(order, f, ensure_ascii=False, indent=2)
