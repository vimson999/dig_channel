import asyncio
import re
import time
from datetime import datetime

from sqlalchemy.orm import load_only
from sqlalchemy.orm.attributes import flag_modified

from celery_config import celery_app
from models import db
from models.platform_credential import PlatformCredentialAccount
from models.task import PublicationTask


STATUS_MAP = {
    "pending": "queued",
    "processing": "running",
    "completed": "succeeded",
    "failed": "failed",
    "canceled": "canceled",
}


def truthy(value, default=True):
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "y", "开启"}:
        return True
    if text in {"0", "false", "no", "off", "n", "关闭"}:
        return False
    return bool(default)


def bounded_int(value, default, minimum, maximum):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(default)
    return max(minimum, min(maximum, parsed))


def bounded_seconds(value, default, minimum, maximum):
    text = str(value or "").strip()
    if ":" in text:
        try:
            parts = [int(float(part)) for part in text.split(":")]
        except ValueError:
            return bounded_int(default, default, minimum, maximum)
        if len(parts) == 2:
            parsed = parts[0] * 60 + parts[1]
        elif len(parts) == 3:
            parsed = parts[0] * 3600 + parts[1] * 60 + parts[2]
        else:
            parsed = default
        return max(minimum, min(maximum, parsed))
    return bounded_int(value, default, minimum, maximum)


def clean_upload_text(value, max_length=500):
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text[:max_length]


def normalize_upload_tags(value, default=None):
    if isinstance(value, list):
        tags = [str(item).strip(" #") for item in value if str(item or "").strip(" #")]
    elif isinstance(value, str):
        tags = [item.strip(" #") for item in re.split(r"[,，、\n]+", value) if item.strip(" #")]
    else:
        tags = []
    seen = set()
    output = []
    for tag in tags:
        if tag in seen:
            continue
        seen.add(tag)
        output.append(tag[:20])
    return output[:10] or list(default or [])


def platform_credential_display_name(credential_id):
    account_id = str(credential_id or "").strip()
    if not account_id:
        return None
    account = (
        PlatformCredentialAccount.query.options(load_only(
            PlatformCredentialAccount.id,
            PlatformCredentialAccount.name,
            PlatformCredentialAccount.owner_label,
            PlatformCredentialAccount.masked_summary,
        ))
        .filter(PlatformCredentialAccount.id == account_id)
        .first()
    )
    if not account:
        return None
    return account.owner_label or account.name or account.masked_summary or account.id


def charging_options_payload(credential_id):
    account_id = clean_upload_text(credential_id, max_length=80)
    if not account_id:
        raise ValueError("请选择 B站账号")

    from services.bilibili.uploader import BiliUploader
    from services.platform_credential_service import resolve_bilibili_auth

    auth = resolve_bilibili_auth(account_id=account_id, required=True)
    uploader = BiliUploader(
        sessdata=auth.get("sessdata"),
        bili_jct=auth.get("bili_jct"),
        buvid3=auth.get("buvid3"),
        dedeuserid=auth.get("dedeuserid"),
    )
    return uploader.charging_pay_options()


def seasons_payload(credential_id):
    account_id = clean_upload_text(credential_id, max_length=80)
    if not account_id:
        raise ValueError("请选择 B站账号")

    from bilibili_api import user
    from services.bilibili.uploader import BiliUploader
    from services.platform_credential_service import resolve_bilibili_auth

    auth = resolve_bilibili_auth(account_id=account_id, required=True)
    uploader = BiliUploader(
        sessdata=auth.get("sessdata"),
        bili_jct=auth.get("bili_jct"),
        buvid3=auth.get("buvid3"),
        dedeuserid=auth.get("dedeuserid"),
    )

    async def _fetch():
        uid = int(auth.get("dedeuserid") or 0)
        if not uid:
            info = await user.get_self_info(uploader.credential)
            uid = int((info or {}).get("mid") or 0)
        if not uid:
            raise ValueError("B站账号缺少 DedeUserID，无法读取合集")
        channels = await user.User(credential=uploader.credential, uid=uid).get_channels()
        seasons = []
        for channel in channels or []:
            meta = getattr(channel, "meta", None) or {}
            season_id = meta.get("season_id") or meta.get("id")
            title = meta.get("title") or meta.get("name")
            if not season_id or not title:
                continue
            seasons.append({
                "id": str(season_id),
                "title": str(title),
                "name": str(meta.get("name") or title),
            })
        return seasons

    return asyncio.run(_fetch())


def charging_pay_from_payload(data):
    source = data.get("charging_pay") if isinstance(data.get("charging_pay"), dict) else {}
    enabled_value = source.get("enabled")
    if enabled_value is None:
        enabled_value = data.get("charging_enabled")
    if enabled_value is None:
        enabled_value = data.get("charging_pay_enabled")
    if not truthy(enabled_value, default=False):
        return None

    preview = source.get("preview") if isinstance(source.get("preview"), dict) else {}
    preview_start = bounded_seconds(
        source.get("preview_start_time")
        if source.get("preview_start_time") is not None
        else preview.get("start_time", data.get("charging_preview_start_time")),
        0,
        0,
        24 * 60 * 60,
    )
    preview_end = bounded_seconds(
        source.get("preview_end_time")
        if source.get("preview_end_time") is not None
        else preview.get("end_time", data.get("charging_preview_end_time")),
        5,
        1,
        24 * 60 * 60,
    )
    if preview_end <= preview_start:
        raise ValueError("充电试看结束时间必须大于开始时间")

    payload = {
        "enabled": True,
        "preview": {
            "need_preview": 1,
            "start_time": preview_start,
            "end_time": preview_end,
        },
        "preview_end_time": preview_end,
    }
    level_id = clean_upload_text(source.get("upower_level_id") or data.get("upower_level_id"), max_length=80)
    if level_id:
        payload["upower_level_id"] = level_id

    unit_price = bounded_int(
        source.get("upower_unit_price") or data.get("upower_unit_price"),
        0,
        0,
        1_000_000,
    )
    if unit_price:
        payload["upower_unit_price"] = unit_price

    mode = bounded_int(source.get("upower_mode") or data.get("upower_mode"), 0, 0, 10)
    if mode:
        payload["upower_mode"] = mode

    member_first_exp = bounded_int(
        source.get("member_first_exp_time") or data.get("member_first_exp_time"),
        0,
        0,
        365 * 24 * 60 * 60,
    )
    if member_first_exp:
        payload["member_first"] = {"enabled": 1, "exp_time": member_first_exp}
    return payload


def upload_delay_time(data):
    enabled = (
        data.get("schedule_enabled")
        if data.get("schedule_enabled") is not None
        else data.get("delay_enabled")
    )
    raw = data.get("delay_time") or data.get("dtime") or data.get("schedule_time")
    if not truthy(enabled, default=False) and raw in (None, "", 0, "0", False):
        return None
    if raw in (None, "", 0, "0", False):
        raise ValueError("请选择 B站定时发布时间")
    if isinstance(raw, str) and re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", raw):
        try:
            timestamp = int(datetime.fromisoformat(raw).timestamp())
        except ValueError as exc:
            raise ValueError("B站定时发布时间格式无效") from exc
    else:
        try:
            timestamp = int(float(raw))
        except (TypeError, ValueError) as exc:
            raise ValueError("B站定时发布时间必须是秒级时间戳") from exc

    now_ts = int(time.time())
    min_ts = now_ts + 5 * 60
    max_ts = now_ts + 15 * 24 * 60 * 60
    if timestamp < min_ts or timestamp > max_ts:
        raise ValueError("B站定时发布时间需在 5 分钟后、15 天内")
    return timestamp


def upload_season_id(data):
    raw = data.get("season_id") or data.get("bili_season_id")
    if raw in (None, "", 0, "0", False):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("B站合集 ID 必须是整数") from exc


def create_bilibili_upload_task(
    parent_task,
    *,
    current_user=None,
    parent_feature,
    source_label,
    video_path,
    video_key,
    title,
    data,
    cover_path=None,
    cover_key="",
    default_tags=None,
    metadata_extra=None,
    result_extra=None,
):
    if not video_path:
        raise ValueError("当前任务还没有可上传的视频文件")
    title = clean_upload_text(title, max_length=120)
    if not title:
        raise ValueError("B站标题不能为空")

    credential_id = clean_upload_text(
        data.get("credential_id") or data.get("platform_credential_id"),
        max_length=80,
    )
    credential_name = platform_credential_display_name(credential_id)
    charging_pay = charging_pay_from_payload(data)
    delay_time = upload_delay_time(data)
    season_id = upload_season_id(data)
    tags = normalize_upload_tags(data.get("tags"), default=default_tags or ["财经", "音频"])
    user_id = str(getattr(current_user, "id", None) or parent_task.user_id or "system")
    metadata = {
        "source": source_label,
        "parent_task_id": parent_task.id,
        "video_key": video_key,
        "cover_key": cover_key or "",
        **(metadata_extra or {}),
    }
    params = {
        "feature": "publisher",
        "user_id": user_id,
        "parent_feature": parent_feature,
        "parent_task_id": parent_task.id,
        "source_feature": parent_feature,
        "source_task_id": parent_task.id,
        "upload_video_path": video_path,
        "video": {
            "enabled": False,
            "video_path": video_path,
            "media_type": "existing_video",
        },
        "thumbnail": {},
        "bilibili": {
            "enabled": True,
            "credential_id": credential_id or None,
            "credential_name": credential_name,
            "title": title,
            "desc": clean_upload_text(data.get("desc") or data.get("description"), max_length=2000),
            "tags": tags,
            "tid": bounded_int(data.get("tid"), 207, 1, 9999),
            "cover_path": cover_path,
            "cover_key": cover_key or "",
            "copyright": bounded_int(data.get("copyright"), 1, 1, 2),
            "no_reprint": bounded_int(data.get("no_reprint"), 1, 0, 1),
            "neutral_mark": clean_upload_text(data.get("neutral_mark"), max_length=80) or "个人观点，仅供参考",
            "is_only_self": bounded_int(data.get("is_only_self") or data.get("only_self"), 0, 0, 1),
            "dynamic": clean_upload_text(data.get("dynamic"), max_length=233),
            "charging_pay": charging_pay,
            "delay_time": delay_time,
            "season_id": season_id,
        },
        "youtube": {"enabled": False},
        "metadata": metadata,
    }
    child = PublicationTask(
        status="pending",
        current_step="queued",
        progress=0,
        params=params,
        result={
            "metadata": metadata,
            "source_task_id": parent_task.id,
            "video_path": video_path,
            "cover_path": cover_path,
            "bilibili_credential_id": credential_id or None,
            "bilibili_credential_name": credential_name,
            "bilibili_charging_pay": charging_pay,
            "bilibili_delay_time": delay_time,
            "bilibili_season_id": season_id,
            **(result_extra or {}),
        },
    )
    db.session.add(child)
    db.session.commit()
    return child


def enqueue_bilibili_upload_task(child):
    from tasks.publisher import PLATFORM_UPLOAD_QUEUE, run_bilibili_upload_only

    try:
        celery_task = run_bilibili_upload_only.apply_async(args=[child.id], queue=PLATFORM_UPLOAD_QUEUE, retry=False)
    except Exception as exc:
        child.status = "failed"
        child.current_step = "failed"
        child.progress = 0
        child.error_message = f"B站上传任务入队失败: {exc}"
        child.completed_at = datetime.utcnow()
        db.session.commit()
        raise
    child.celery_task_id = celery_task.id
    db.session.commit()
    return child


def bilibili_upload_children(parent_id, *, parent_feature=None, source_label=None, limit=20):
    query = (
        db.session.query(PublicationTask.id)
        .filter(PublicationTask.parent_task_id == parent_id)
        .filter(PublicationTask.feature == "publisher")
    )
    if parent_feature:
        query = query.filter(PublicationTask.parent_feature == parent_feature)
    candidate_ids = [
        row.id
        for row in query.order_by(PublicationTask.created_at.desc()).limit(limit).all()
    ]
    items = []
    for candidate_id in candidate_ids:
        row = (
            PublicationTask.query.options(load_only(
                PublicationTask.id,
                PublicationTask.celery_task_id,
                PublicationTask.status,
                PublicationTask.current_step,
                PublicationTask.progress,
                PublicationTask.error_message,
                PublicationTask.params,
                PublicationTask.result,
                PublicationTask.created_at,
                PublicationTask.completed_at,
            ))
            .filter(PublicationTask.id == candidate_id)
            .one_or_none()
        )
        if not row:
            continue
        item = bilibili_upload_child_payload(row, source_label=source_label)
        if item:
            items.append(item)
    return items


def bilibili_upload_child_payload(task, *, source_label=None):
    params = task.params if isinstance(task.params, dict) else {}
    metadata = params.get("metadata") if isinstance(params.get("metadata"), dict) else {}
    source = metadata.get("source")
    if source_label and source != source_label:
        return None
    if not source_label and not str(source or "").endswith(":bilibili_upload"):
        return None
    bili = params.get("bilibili") if isinstance(params.get("bilibili"), dict) else {}
    result = task.result if isinstance(task.result, dict) else {}
    status = STATUS_MAP.get(task.status, task.status or "queued")
    upload_result = result.get("bilibili_result")
    return {
        "task_id": task.id,
        "celery_id": task.celery_task_id,
        "status": status,
        "step": task.current_step,
        "progress": task.progress or 0,
        "error": task.error_message or result.get("bilibili_error"),
        "title": bili.get("title") or result.get("bilibili_title"),
        "video_key": metadata.get("video_key"),
        "cover_key": metadata.get("cover_key") or bili.get("cover_key"),
        "credential_id": bili.get("credential_id") or result.get("bilibili_credential_id"),
        "credential_name": (
            result.get("bilibili_credential_name")
            or bili.get("credential_name")
            or platform_credential_display_name(bili.get("credential_id") or result.get("bilibili_credential_id"))
        ),
        "charging_pay": bili.get("charging_pay") or result.get("bilibili_charging_pay"),
        "delay_time": bili.get("delay_time") or result.get("bilibili_delay_time"),
        "season_id": bili.get("season_id") or result.get("bilibili_season_id"),
        "created_at": task.created_at.isoformat() + "Z" if task.created_at else None,
        "completed_at": task.completed_at.isoformat() + "Z" if task.completed_at else None,
        "result": upload_result,
    }


def cancel_bilibili_upload_child(task):
    status = str(task.status or "").lower()
    if status in {"completed", "failed", "canceled"}:
        return True, "上传任务已结束"
    if status not in {"pending", "queued"}:
        return False, "上传任务已开始运行，暂不支持从这里取消"

    if task.celery_task_id:
        celery_app.control.revoke(task.celery_task_id, terminate=False)

    now = datetime.utcnow()
    result = dict(task.result or {})
    result["bilibili_upload_canceled_at"] = now.isoformat() + "Z"
    result["bilibili_upload_cancel_reason"] = "user_canceled_queued_upload"
    task.status = "canceled"
    task.current_step = "canceled"
    task.progress = task.progress or 0
    task.error_message = None
    task.completed_at = now
    task.result = result
    flag_modified(task, "result")
    db.session.commit()
    return True, "已取消 B站上传排队任务"
