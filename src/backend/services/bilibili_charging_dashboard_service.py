"""Collect and aggregate public Bilibili charging counts for the home dashboard."""

import asyncio
import json
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Iterable, List
from zoneinfo import ZoneInfo

from bilibili_api import user
from sqlalchemy import and_, or_

from models import BilibiliChargingSnapshot, BilibiliCompetitor, SystemConfig, db
from services.bilibili_intelligence_service import _credential_from_auth
from services.platform_credential_service import (
    is_auth_error,
    mark_credential_error,
    mark_credential_success,
    resolve_bilibili_auth,
)
from services.runtime_guard import release_db_session_quietly


LOGGER = logging.getLogger("bilibili_charging_dashboard")
BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")
CONFIG_KEY = "bilibili_charging_dashboard.pricing"
SCHEDULE_CONFIG_KEY = "bilibili_charging_dashboard.schedule"
CONFIG_TYPE = "bilibili_charging_dashboard"
CONFIG_SCOPE = "global"
DEFAULT_PRICING = {
    "default": {
        "revenue_per_new": 30,
        "cash_rate": 0.4,
        "monthly_per_charger": 10,
    },
    "overrides": {
        "3546604889967195": {
            "revenue_per_new": 50,
            "cash_rate": 0.4,
            "monthly_per_charger": 20,
        },
    },
}
DEFAULT_SCHEDULE = {
    "enabled": True,
    "daily_at": "00:00",
    "timezone": "Asia/Shanghai",
    "notify_milestones": True,
}
GROWTH_RANKING_WINDOWS = {
    "day": 1,
    "week": 7,
    "month": 30,
}


class BilibiliChargingDashboardError(RuntimeError):
    pass


def business_date() -> date:
    return datetime.now(BUSINESS_TIMEZONE).date()


def _bounded_int(value, default, lower, upper):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(lower, min(upper, parsed))


def _decimal(value, default):
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(str(default))


def _money(value):
    rounded = Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int(rounded) if rounded == rounded.to_integral() else float(rounded)


def _normalized_rule(value, fallback):
    value = value if isinstance(value, dict) else {}
    fallback = fallback if isinstance(fallback, dict) else {}
    revenue_per_new = _decimal(
        value.get("revenue_per_new"),
        fallback.get("revenue_per_new", 30),
    )
    cash_rate = _decimal(value.get("cash_rate"), fallback.get("cash_rate", 0.4))
    monthly_per_charger = _decimal(
        value.get("monthly_per_charger"),
        fallback.get("monthly_per_charger", 10),
    )
    return {
        "revenue_per_new": float(max(Decimal("0"), revenue_per_new)),
        "cash_rate": float(max(Decimal("0"), min(Decimal("1"), cash_rate))),
        "monthly_per_charger": float(max(Decimal("0"), monthly_per_charger)),
    }


def load_pricing_config() -> Dict[str, Any]:
    row = (
        db.session.query(SystemConfig.value)
        .filter(
            SystemConfig.key == CONFIG_KEY,
            SystemConfig.scope == CONFIG_SCOPE,
            SystemConfig.status == 1,
        )
        .one_or_none()
    )
    payload = {}
    if row and row.value:
        try:
            payload = json.loads(row.value)
        except (TypeError, ValueError):
            LOGGER.warning("Invalid Bilibili charging pricing config; using defaults")
    default_rule = _normalized_rule(payload.get("default"), DEFAULT_PRICING["default"])
    raw_overrides = payload.get("overrides") if isinstance(payload.get("overrides"), dict) else {}
    overrides = {
        str(uid): _normalized_rule(rule, default_rule)
        for uid, rule in raw_overrides.items()
        if str(uid).isdigit()
    }
    for uid, rule in DEFAULT_PRICING["overrides"].items():
        overrides.setdefault(uid, _normalized_rule(rule, default_rule))
    return {"default": default_rule, "overrides": overrides}


def load_schedule_config() -> Dict[str, Any]:
    row = (
        db.session.query(SystemConfig.value, SystemConfig.status)
        .filter(
            SystemConfig.key == SCHEDULE_CONFIG_KEY,
            SystemConfig.scope == CONFIG_SCOPE,
        )
        .one_or_none()
    )
    payload = {}
    if row and row.value:
        try:
            payload = json.loads(row.value)
        except (TypeError, ValueError):
            LOGGER.warning("Invalid Bilibili charging schedule config; using defaults")
    enabled_value = payload.get("enabled", DEFAULT_SCHEDULE["enabled"])
    if isinstance(enabled_value, str):
        enabled_value = enabled_value.strip().lower() in {"1", "true", "yes", "on"}
    notify_value = payload.get(
        "notify_milestones",
        DEFAULT_SCHEDULE["notify_milestones"],
    )
    if isinstance(notify_value, str):
        notify_value = notify_value.strip().lower() in {"1", "true", "yes", "on"}
    daily_at = str(payload.get("daily_at") or DEFAULT_SCHEDULE["daily_at"]).strip()
    try:
        datetime.strptime(daily_at, "%H:%M")
    except (TypeError, ValueError):
        LOGGER.warning(
            "Invalid Bilibili charging daily schedule time %r; using %s",
            daily_at,
            DEFAULT_SCHEDULE["daily_at"],
        )
        daily_at = DEFAULT_SCHEDULE["daily_at"]
    timezone_name = str(
        payload.get("timezone") or DEFAULT_SCHEDULE["timezone"]
    ).strip()
    try:
        ZoneInfo(timezone_name)
    except Exception:
        LOGGER.warning(
            "Invalid Bilibili charging schedule timezone %r; using %s",
            timezone_name,
            DEFAULT_SCHEDULE["timezone"],
        )
        timezone_name = DEFAULT_SCHEDULE["timezone"]
    return {
        "enabled": bool(enabled_value) and (row is None or int(row.status or 0) == 1),
        "daily_at": daily_at,
        "timezone": timezone_name,
        "notify_milestones": bool(notify_value),
    }


def _pricing_for_uid(config, uid):
    return dict((config.get("overrides") or {}).get(str(uid)) or config["default"])


async def _fetch_one(uid, credential):
    payload = await user.User(uid=int(uid), credential=credential).get_elec_user_monthly()
    if not isinstance(payload, dict):
        raise BilibiliChargingDashboardError("B站充电接口返回格式异常")
    raw_count = payload.get("total_count")
    if raw_count is None:
        raw_count = payload.get("total")
    try:
        count = int(raw_count)
    except (TypeError, ValueError) as exc:
        raise BilibiliChargingDashboardError("B站充电接口缺少 total_count") from exc
    if count < 0:
        raise BilibiliChargingDashboardError("B站充电人数不能为负数")
    return count


async def _fetch_many(accounts, credential):
    async def guarded(account):
        try:
            return {
                "competitor_id": account["id"],
                "uid": account["uid"],
                "name": account["name"],
                "charger_count": await _fetch_one(account["uid"], credential),
            }
        except Exception as exc:
            return {
                "competitor_id": account["id"],
                "uid": account["uid"],
                "name": account["name"],
                "error": str(exc)[:1000],
                "auth_error": is_auth_error(exc),
            }

    return await asyncio.gather(*(guarded(account) for account in accounts))


def collect_charging_snapshots(*, target_date=None, trigger="manual") -> Dict[str, Any]:
    target_date = target_date or business_date()
    accounts = [
        {"id": row.id, "uid": row.uid, "name": row.name}
        for row in (
            BilibiliCompetitor.query.with_entities(
                BilibiliCompetitor.id,
                BilibiliCompetitor.uid,
                BilibiliCompetitor.name,
            )
            .filter(BilibiliCompetitor.active.is_(True))
            .order_by(BilibiliCompetitor.created_at.asc())
            .all()
        )
    ]
    if not accounts:
        release_db_session_quietly()
        return {
            "snapshot_date": target_date.isoformat(),
            "trigger": trigger,
            "synced": 0,
            "failed": 0,
            "results": [],
            "errors": [],
        }

    auth = resolve_bilibili_auth(required=True)
    credential_id = auth.get("credential_id")
    credential = _credential_from_auth(auth)
    release_db_session_quietly()

    fetched = asyncio.run(_fetch_many(accounts, credential))
    successes = [item for item in fetched if "charger_count" in item]
    errors = [item for item in fetched if "error" in item]
    now = datetime.utcnow()
    competitor_ids = [item["competitor_id"] for item in successes]
    existing = {
        row.competitor_id: row
        for row in BilibiliChargingSnapshot.query.filter(
            BilibiliChargingSnapshot.snapshot_date == target_date,
            BilibiliChargingSnapshot.competitor_id.in_(competitor_ids),
        ).all()
    } if competitor_ids else {}

    for item in successes:
        snapshot = existing.get(item["competitor_id"])
        if snapshot is None:
            snapshot = BilibiliChargingSnapshot(
                competitor_id=item["competitor_id"],
                snapshot_date=target_date,
                charger_count=item["charger_count"],
                fetched_at=now,
            )
            db.session.add(snapshot)
        else:
            snapshot.charger_count = item["charger_count"]
            snapshot.fetched_at = now
            snapshot.source = "bilibili_elec_monthly"
    db.session.commit()

    if successes:
        mark_credential_success(credential_id)
    elif errors:
        first_error = BilibiliChargingDashboardError(errors[0]["error"])
        mark_credential_error(
            credential_id,
            first_error,
            expired=all(bool(item.get("auth_error")) for item in errors),
        )

    result = {
        "snapshot_date": target_date.isoformat(),
        "trigger": str(trigger or "manual")[:40],
        "synced": len(successes),
        "failed": len(errors),
        "results": successes,
        "errors": [
            {
                "competitor_id": item["competitor_id"],
                "uid": item["uid"],
                "name": item["name"],
                "error": item["error"],
            }
            for item in errors
        ],
        "fetched_at": now.isoformat() + "Z",
    }
    release_db_session_quietly()
    return result


def _snapshot_rows(start_date, end_date) -> Iterable[Any]:
    previous = (
        db.session.query(
            BilibiliChargingSnapshot.competitor_id,
            db.func.max(BilibiliChargingSnapshot.snapshot_date).label("snapshot_date"),
        )
        .filter(BilibiliChargingSnapshot.snapshot_date < start_date)
        .group_by(BilibiliChargingSnapshot.competitor_id)
        .subquery()
    )
    previous_rows = (
        db.session.query(
            BilibiliChargingSnapshot.competitor_id,
            BilibiliChargingSnapshot.snapshot_date,
            BilibiliChargingSnapshot.charger_count,
            BilibiliChargingSnapshot.fetched_at,
        )
        .join(
            previous,
            and_(
                previous.c.competitor_id == BilibiliChargingSnapshot.competitor_id,
                previous.c.snapshot_date == BilibiliChargingSnapshot.snapshot_date,
            ),
        )
        .all()
    )
    current_rows = (
        db.session.query(
            BilibiliChargingSnapshot.competitor_id,
            BilibiliChargingSnapshot.snapshot_date,
            BilibiliChargingSnapshot.charger_count,
            BilibiliChargingSnapshot.fetched_at,
        )
        .filter(
            BilibiliChargingSnapshot.snapshot_date >= start_date,
            BilibiliChargingSnapshot.snapshot_date <= end_date,
        )
        .order_by(
            BilibiliChargingSnapshot.snapshot_date.asc(),
            BilibiliChargingSnapshot.competitor_id.asc(),
        )
        .all()
    )
    return [*previous_rows, *current_rows]


def _nearest_snapshot_rows(cutoffs):
    if not cutoffs:
        return {}
    nearest = (
        db.session.query(
            BilibiliChargingSnapshot.competitor_id,
            db.func.max(BilibiliChargingSnapshot.snapshot_date).label("snapshot_date"),
        )
        .filter(or_(*[
            and_(
                BilibiliChargingSnapshot.competitor_id == competitor_id,
                BilibiliChargingSnapshot.snapshot_date <= cutoff,
            )
            for competitor_id, cutoff in cutoffs.items()
        ]))
        .group_by(BilibiliChargingSnapshot.competitor_id)
        .subquery()
    )
    rows = (
        db.session.query(
            BilibiliChargingSnapshot.competitor_id,
            BilibiliChargingSnapshot.snapshot_date,
            BilibiliChargingSnapshot.charger_count,
            BilibiliChargingSnapshot.fetched_at,
        )
        .join(
            nearest,
            and_(
                nearest.c.competitor_id == BilibiliChargingSnapshot.competitor_id,
                nearest.c.snapshot_date == BilibiliChargingSnapshot.snapshot_date,
            ),
        )
        .all()
    )
    return {row.competitor_id: row for row in rows}


def _growth_ranking_item(account, latest, baseline):
    growth = (
        latest.charger_count - baseline.charger_count
        if latest is not None and baseline is not None
        else None
    )
    return {
        "rank": None,
        "competitor_id": account["id"],
        "uid": account["uid"],
        "name": account["name"],
        "source_url": account["source_url"],
        "growth": growth,
        "latest_count": latest.charger_count if latest is not None else None,
        "latest_date": latest.snapshot_date.isoformat() if latest is not None else None,
        "baseline_count": baseline.charger_count if baseline is not None else None,
        "baseline_date": baseline.snapshot_date.isoformat() if baseline is not None else None,
    }


def _growth_rankings(accounts, end_date):
    latest_by_account = _nearest_snapshot_rows({
        account["id"]: end_date
        for account in accounts
    })
    rankings = {}
    for period, window_days in GROWTH_RANKING_WINDOWS.items():
        baseline_by_account = _nearest_snapshot_rows({
            competitor_id: latest.snapshot_date - timedelta(days=window_days)
            for competitor_id, latest in latest_by_account.items()
        })
        items = [
            _growth_ranking_item(
                account,
                latest_by_account.get(account["id"]),
                baseline_by_account.get(account["id"]),
            )
            for account in accounts
        ]
        items.sort(
            key=lambda item: (
                item["growth"] is None,
                -(item["growth"] or 0),
                -(item["latest_count"] or 0),
                item["name"],
                item["uid"],
            )
        )
        next_rank = 1
        for item in items:
            if item["growth"] is not None:
                item["rank"] = next_rank
                next_rank += 1
        rankings[period] = {
            "window_days": window_days,
            "items": items,
        }
    return rankings


def charging_dashboard_payload(*, days=31) -> Dict[str, Any]:
    days = _bounded_int(days, 31, 2, 366)
    end_date = business_date()
    start_date = end_date - timedelta(days=days - 1)
    accounts = [
        {
            "id": row.id,
            "uid": row.uid,
            "name": row.name,
            "source_url": row.source_url,
        }
        for row in (
            BilibiliCompetitor.query.with_entities(
                BilibiliCompetitor.id,
                BilibiliCompetitor.uid,
                BilibiliCompetitor.name,
                BilibiliCompetitor.source_url,
            )
            .filter(BilibiliCompetitor.active.is_(True))
            .order_by(BilibiliCompetitor.created_at.asc())
            .all()
        )
    ]
    pricing = load_pricing_config()
    rows = _snapshot_rows(start_date, end_date)
    by_account: Dict[str, List[Any]] = {account["id"]: [] for account in accounts}
    for row in rows:
        if row.competitor_id in by_account:
            by_account[row.competitor_id].append(row)

    all_dates = sorted({
        row.snapshot_date.isoformat()
        for row in rows
        if row.snapshot_date >= start_date
    })
    output_accounts = []
    for account in accounts:
        ordered = sorted(
            by_account.get(account["id"], []),
            key=lambda item: item.snapshot_date,
        )
        rule = _pricing_for_uid(pricing, account["uid"])
        previous_count = None
        points = []
        latest_fetched_at = None
        for row in ordered:
            delta = 0 if previous_count is None else row.charger_count - previous_count
            previous_count = row.charger_count
            if row.snapshot_date < start_date:
                continue
            daily_revenue = Decimal(delta) * _decimal(rule["revenue_per_new"], 30)
            daily_cash = daily_revenue * _decimal(rule["cash_rate"], 0.4)
            monthly_income = Decimal(row.charger_count) * _decimal(
                rule["monthly_per_charger"],
                10,
            )
            latest_fetched_at = row.fetched_at
            points.append({
                "date": row.snapshot_date.isoformat(),
                "charger_count": row.charger_count,
                "daily_growth": delta,
                "daily_revenue": _money(daily_revenue),
                "daily_cash": _money(daily_cash),
                "monthly_income": _money(monthly_income),
            })
        latest = points[-1] if points else None
        output_accounts.append({
            **account,
            "pricing": rule,
            "latest": latest,
            "points": points,
            "last_fetched_at": (
                latest_fetched_at.isoformat() + "Z"
                if latest_fetched_at else None
            ),
        })

    latest_times = [
        item["last_fetched_at"]
        for item in output_accounts
        if item.get("last_fetched_at")
    ]
    payload = {
        "range": {
            "days": days,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        },
        "dates": all_dates,
        "accounts": output_accounts,
        "growth_rankings": _growth_rankings(accounts, end_date),
        "last_fetched_at": max(latest_times) if latest_times else None,
        "has_data": any(item.get("points") for item in output_accounts),
    }
    release_db_session_quietly()
    return payload
