"""Fetch all UPower archive-stat pages and persist raw JSON plus projections.

Run from the repository root: venv/bin/python scripts/bilibili/import_upower_archive.py
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src" / "backend"))

from app import create_app  # noqa: E402
from models import db  # noqa: E402
from models.bilibili_upower_archive import BilibiliUpowerArchiveStat  # noqa: E402
from services.platform_credential_service import resolve_bilibili_auth  # noqa: E402

URL = "https://api.bilibili.com/x/upower/dashboard/archive/stats"
PERIOD = 90
PAGE_SIZE = 10
PAGE_COUNT = 26


def fetch_pages():
    auth = resolve_bilibili_auth()
    uid = str(auth.get("dedeuserid") or "").strip()
    if not uid.isdigit():
        raise RuntimeError("B站凭据缺少有效的 DedeUserID")
    session = requests.Session()
    session.cookies.update({k: v for k, v in {
        "SESSDATA": auth.get("sessdata"),
        "bili_jct": auth.get("bili_jct"),
        "buvid3": auth.get("buvid3"),
        "DedeUserID": uid,
    }.items() if v})
    session.headers.update({
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://member.bilibili.com/",
        "Origin": "https://member.bilibili.com",
    })
    pages = []
    for pn in range(1, PAGE_COUNT + 1):
        for attempt in range(3):
            try:
                response = session.get(URL, params={
                    "keyword": "", "period": PERIOD, "pn": pn,
                    "ps": PAGE_SIZE, "t": int(time.time() * 1000),
                }, timeout=25)
                response.raise_for_status()
                payload = response.json()
                if payload.get("code") != 0:
                    raise RuntimeError(f"B站第 {pn} 页返回错误码 {payload.get('code')}: {payload.get('message')}")
                data = payload.get("data") or {}
                if int(data.get("pn", -1)) != pn or not isinstance(data.get("list"), list):
                    raise RuntimeError(f"B站第 {pn} 页结构异常")
                pages.append(payload)
                print(f"page {pn}/{PAGE_COUNT}: {len(data['list'])} items", flush=True)
                break
            except (requests.RequestException, ValueError) as exc:
                if attempt == 2:
                    raise RuntimeError(f"第 {pn} 页请求失败") from exc
                time.sleep(attempt + 1)
        time.sleep(0.2)
    return uid, pages


def save(uid, pages):
    items = [item for page in pages for item in page["data"]["list"]]
    expected = int(pages[0]["data"]["total"])
    aids = [int(item["aid"]) for item in items]
    if len(items) != expected or len(set(aids)) != expected:
        raise RuntimeError(f"数据不完整：获取 {len(items)} 条、去重 {len(set(aids))} 条，接口 total={expected}")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder = ROOT / "data" / "bilibili_upower_archive" / f"{stamp}_uid_{uid}_period_{PERIOD}"
    folder.mkdir(parents=True, exist_ok=False)
    for pn, payload in enumerate(pages, 1):
        (folder / f"page_{pn:02d}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (folder / "all.json").write_text(json.dumps({
        "account_uid": uid, "period_days": PERIOD, "total": expected,
        "fetched_at": datetime.utcnow().isoformat() + "Z", "items": items,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    now = datetime.utcnow()
    for item in items:
        aid = int(item["aid"])
        row = BilibiliUpowerArchiveStat.query.filter_by(account_uid=uid, period_days=PERIOD, aid=aid).first()
        if row is None:
            row = BilibiliUpowerArchiveStat(account_uid=uid, period_days=PERIOD, aid=aid)
            db.session.add(row)
        row.title = str(item.get("title") or "")
        row.cover_url = item.get("cover")
        row.video_url = item.get("link")
        row.pub_time = datetime.utcfromtimestamp(int(item["pub_time"])) if item.get("pub_time") else None
        row.duration_seconds = item.get("duration")
        row.play_count = item.get("play_count")
        row.income = item.get("income")
        row.level_price = item.get("level_price")
        row.level_name = item.get("level_name")
        row.badge_text = item.get("badge_text")
        row.raw_payload = item
        row.fetched_at = now
    db.session.commit()
    print(f"saved {expected} rows and 26 page JSON files: {folder}", flush=True)


if __name__ == "__main__":
    with create_app(register_apis=False).app_context():
        save(*fetch_pages())
