import asyncio
import re
import time
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy.orm import load_only

from celery_config import celery_app
from models import db
from models.bilibili_publish import PublicationTaskBilibiliUpload
from models.platform_credential import PlatformCredentialAccount
from models.task import PublicationTask
from services.platform_publish_dispatch import dispatch_platform_publish


STATUS_MAP = {
    "pending": "queued",
    "processing": "running",
    "completed": "succeeded",
    "failed": "failed",
    "canceled": "canceled",
}

DEFAULT_BILIBILI_NEUTRAL_MARK = "个人观点，仅供参考"


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

    def _creator_seasons():
        seasons = []
        for item in uploader.creator_seasons():
            meta = item.get("season") if isinstance(item.get("season"), dict) else item
            season_id = meta.get("id") or meta.get("season_id")
            title = meta.get("title") or meta.get("name")
            if not season_id or not title:
                continue
            seasons.append({
                "id": str(season_id),
                "title": str(title),
                "name": str(meta.get("name") or title),
                "episode_count": int(meta.get("ep_num") or 0),
            })
        return seasons

    async def _public_seasons():
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

    try:
        return _creator_seasons()
    except Exception:
        # Compatibility fallback for accounts or older Bilibili deployments
        # where the creator-center endpoint is temporarily unavailable.
        return asyncio.run(_public_seasons())


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
    description = clean_upload_text(data.get("desc") or data.get("description"), max_length=2000)
    tid = bounded_int(data.get("tid"), 207, 1, 9999)
    copyright_value = bounded_int(data.get("copyright"), 1, 1, 2)
    no_reprint = bounded_int(data.get("no_reprint"), 1, 0, 1)
    dynamic = clean_upload_text(data.get("dynamic"), max_length=233)
    neutral_mark = clean_upload_text(data.get("neutral_mark"), max_length=80) or DEFAULT_BILIBILI_NEUTRAL_MARK
    is_only_self = bounded_int(
        data.get("is_only_self") or data.get("isOnlySelf") or data.get("only_self"),
        0,
        0,
        1,
    )
    space_hidden = _optional_int(data.get("space_hidden"))
    creation_statement_id = _optional_int(
        data.get("creation_statement_id") or data.get("creationStatementId")
    )
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
            "desc": description,
            "tags": tags,
            "tid": tid,
            "cover_path": cover_path,
            "cover_key": cover_key or "",
            "copyright": copyright_value,
            "no_reprint": no_reprint,
            "neutral_mark": neutral_mark,
            "is_only_self": is_only_self,
            "space_hidden": space_hidden,
            "creation_statement_id": creation_statement_id,
            "dynamic": dynamic,
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
    db.session.flush()
    projection = PublicationTaskBilibiliUpload(
        task_id=child.id,
        parent_task_id=parent_task.id,
        parent_feature=parent_feature,
        source_label=source_label,
        video_key=video_key,
        cover_key=cover_key or None,
        title=title,
        video_path=video_path,
        cover_path=cover_path,
        description=description or None,
        tags=tags,
        tid=tid,
        copyright=copyright_value,
        no_reprint=no_reprint,
        dynamic=dynamic or None,
        neutral_mark=neutral_mark,
        is_only_self=is_only_self,
        space_hidden=space_hidden,
        creation_statement_id=creation_statement_id,
        credential_id=credential_id or None,
        credential_name=credential_name,
        delay_time=delay_time,
        season_id=season_id,
        **_charging_projection_values(charging_pay),
    )
    db.session.add(projection)
    db.session.flush()
    response_payload = bilibili_upload_child_payload(child, upload=projection)
    child_snapshot = SimpleNamespace(
        id=str(child.id),
        celery_task_id=None,
        status="pending",
        current_step="queued",
        progress=0,
        error_message=None,
        created_at=child.created_at,
        completed_at=None,
        _bilibili_create_response_payload=response_payload,
    )
    db.session.commit()
    return child_snapshot


def bilibili_upload_execution_context(task_id):
    """Load the complete upload command without opening task params/result."""
    row = (
        db.session.query(PublicationTask, PublicationTaskBilibiliUpload)
        .join(
            PublicationTaskBilibiliUpload,
            PublicationTaskBilibiliUpload.task_id == PublicationTask.id,
        )
        .options(
            load_only(
                PublicationTask.id,
                PublicationTask.celery_task_id,
                PublicationTask.status,
                PublicationTask.current_step,
                PublicationTask.progress,
            ),
            load_only(
                PublicationTaskBilibiliUpload.video_path,
                PublicationTaskBilibiliUpload.cover_path,
                PublicationTaskBilibiliUpload.title,
                PublicationTaskBilibiliUpload.description,
                PublicationTaskBilibiliUpload.tags,
                PublicationTaskBilibiliUpload.tid,
                PublicationTaskBilibiliUpload.copyright,
                PublicationTaskBilibiliUpload.no_reprint,
                PublicationTaskBilibiliUpload.dynamic,
                PublicationTaskBilibiliUpload.neutral_mark,
                PublicationTaskBilibiliUpload.is_only_self,
                PublicationTaskBilibiliUpload.space_hidden,
                PublicationTaskBilibiliUpload.creation_statement_id,
                PublicationTaskBilibiliUpload.credential_id,
                PublicationTaskBilibiliUpload.charging_enabled,
                PublicationTaskBilibiliUpload.preview_start_time,
                PublicationTaskBilibiliUpload.preview_end_time,
                PublicationTaskBilibiliUpload.upower_level_id,
                PublicationTaskBilibiliUpload.upower_unit_price,
                PublicationTaskBilibiliUpload.upower_mode,
                PublicationTaskBilibiliUpload.member_first_exp_time,
                PublicationTaskBilibiliUpload.delay_time,
                PublicationTaskBilibiliUpload.season_id,
            ),
        )
        .filter(PublicationTask.id == str(task_id))
        .one_or_none()
    )
    if row is None:
        return None
    task, upload = row
    return SimpleNamespace(
        task_id=str(task.id),
        celery_task_id=str(task.celery_task_id or ""),
        status=str(task.status or ""),
        current_step=str(task.current_step or ""),
        progress=int(task.progress or 0),
        video_path=upload.video_path,
        cover_path=upload.cover_path,
        title=upload.title,
        description=upload.description or "",
        tags=list(upload.tags or []),
        tid=int(upload.tid or 207),
        copyright=int(upload.copyright or 1),
        no_reprint=int(upload.no_reprint if upload.no_reprint is not None else 1),
        dynamic=upload.dynamic or "",
        neutral_mark=upload.neutral_mark or DEFAULT_BILIBILI_NEUTRAL_MARK,
        is_only_self=int(upload.is_only_self or 0),
        space_hidden=upload.space_hidden,
        creation_statement_id=upload.creation_statement_id,
        credential_id=upload.credential_id,
        charging_pay=_charging_pay_from_projection(upload),
        delay_time=upload.delay_time,
        season_id=upload.season_id,
    )


def enqueue_bilibili_upload_task(child):
    child_id = str(child.id)
    try:
        submission = dispatch_platform_publish("bilibili", child_id, retry=False)
    except Exception as exc:
        message = f"B站上传任务入队失败: {exc}"
        (
            PublicationTask.query
            .filter(PublicationTask.id == child_id)
            .update({
                PublicationTask.status: "failed",
                PublicationTask.current_step: "failed",
                PublicationTask.progress: 0,
                PublicationTask.error_message: message,
                PublicationTask.completed_at: datetime.utcnow(),
            }, synchronize_session=False)
        )
        db.session.commit()
        raise
    updated = (
        PublicationTask.query
        .filter(PublicationTask.id == child_id)
        .update(
            {PublicationTask.celery_task_id: submission["celery_id"]},
            synchronize_session=False,
        )
    )
    if not updated:
        db.session.rollback()
        raise ValueError(f"B站上传任务不存在: {child_id}")
    response_payload = dict(getattr(child, "_bilibili_create_response_payload", None) or {})
    response_payload["celery_id"] = submission["celery_id"]
    db.session.commit()
    child.celery_task_id = submission["celery_id"]
    child._bilibili_enqueue_response_payload = response_payload
    return child


def bilibili_upload_children(parent_id, *, parent_feature=None, source_label=None, limit=20):
    query = (
        db.session.query(PublicationTask, PublicationTaskBilibiliUpload)
        .join(
            PublicationTaskBilibiliUpload,
            PublicationTaskBilibiliUpload.task_id == PublicationTask.id,
        )
        .options(
            load_only(
                PublicationTask.id,
                PublicationTask.celery_task_id,
                PublicationTask.status,
                PublicationTask.current_step,
                PublicationTask.progress,
                PublicationTask.error_message,
                PublicationTask.created_at,
                PublicationTask.completed_at,
            ),
            load_only(
                PublicationTaskBilibiliUpload.parent_task_id,
                PublicationTaskBilibiliUpload.parent_feature,
                PublicationTaskBilibiliUpload.source_label,
                PublicationTaskBilibiliUpload.video_key,
                PublicationTaskBilibiliUpload.cover_key,
                PublicationTaskBilibiliUpload.title,
                PublicationTaskBilibiliUpload.credential_id,
                PublicationTaskBilibiliUpload.credential_name,
                PublicationTaskBilibiliUpload.charging_enabled,
                PublicationTaskBilibiliUpload.preview_start_time,
                PublicationTaskBilibiliUpload.preview_end_time,
                PublicationTaskBilibiliUpload.upower_level_id,
                PublicationTaskBilibiliUpload.upower_unit_price,
                PublicationTaskBilibiliUpload.upower_mode,
                PublicationTaskBilibiliUpload.member_first_exp_time,
                PublicationTaskBilibiliUpload.delay_time,
                PublicationTaskBilibiliUpload.season_id,
                PublicationTaskBilibiliUpload.aid,
                PublicationTaskBilibiliUpload.bvid,
            ),
        )
        .filter(PublicationTaskBilibiliUpload.parent_task_id == parent_id)
    )
    if parent_feature:
        query = query.filter(PublicationTaskBilibiliUpload.parent_feature == parent_feature)
    if source_label:
        query = query.filter(PublicationTaskBilibiliUpload.source_label == source_label)
    rows = (
        query.order_by(PublicationTaskBilibiliUpload.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        bilibili_upload_child_payload(task, source_label=source_label, upload=upload)
        for task, upload in rows
    ]


def bilibili_upload_child_payload(task, *, source_label=None, upload=None):
    if upload is None:
        upload = PublicationTaskBilibiliUpload.query.filter_by(task_id=task.id).one_or_none()
    if upload is None:
        return None
    source = upload.source_label
    if source_label and source != source_label:
        return None
    if not source_label and not str(source or "").endswith(":bilibili_upload"):
        return None
    status = STATUS_MAP.get(task.status, task.status or "queued")
    upload_result = _upload_result_from_projection(upload)
    return {
        "task_id": task.id,
        "celery_id": task.celery_task_id,
        "status": status,
        "step": task.current_step,
        "progress": task.progress or 0,
        "error": task.error_message,
        "title": upload.title,
        "video_key": upload.video_key,
        "cover_key": upload.cover_key,
        "credential_id": upload.credential_id,
        "credential_name": upload.credential_name,
        "charging_pay": _charging_pay_from_projection(upload),
        "delay_time": upload.delay_time,
        "season_id": upload.season_id,
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
    upload = PublicationTaskBilibiliUpload.query.filter_by(task_id=task.id).one_or_none()
    if upload is None:
        return False, "B站上传任务结构化记录不存在"
    upload.canceled_at = now
    upload.cancel_reason = "user_canceled_queued_upload"
    task.status = "canceled"
    task.current_step = "canceled"
    task.progress = task.progress or 0
    task.error_message = None
    task.completed_at = now
    response_payload = bilibili_upload_child_payload(task, upload=upload)
    db.session.commit()
    task._bilibili_cancel_response_payload = response_payload
    return True, "已取消 B站上传排队任务"


def _charging_projection_values(charging_pay):
    source = charging_pay if isinstance(charging_pay, dict) else {}
    preview = source.get("preview") if isinstance(source.get("preview"), dict) else {}
    member_first = source.get("member_first") if isinstance(source.get("member_first"), dict) else {}
    return {
        "charging_enabled": bool(source.get("enabled")),
        "preview_start_time": _optional_int(
            source.get("preview_start_time", preview.get("start_time"))
        ),
        "preview_end_time": _optional_int(
            source.get("preview_end_time", preview.get("end_time"))
        ),
        "upower_level_id": clean_upload_text(source.get("upower_level_id"), 80) or None,
        "upower_unit_price": _optional_int(source.get("upower_unit_price")),
        "upower_mode": _optional_int(source.get("upower_mode")),
        "member_first_exp_time": _optional_int(member_first.get("exp_time")),
    }


def _charging_pay_from_projection(upload):
    if not upload.charging_enabled:
        return None
    payload = {"enabled": True}
    if upload.preview_start_time is not None or upload.preview_end_time is not None:
        preview = {
            "need_preview": 1,
            "start_time": int(upload.preview_start_time or 0),
            "end_time": int(upload.preview_end_time or 0),
        }
        payload["preview"] = preview
        payload["preview_end_time"] = preview["end_time"]
    if upload.upower_level_id:
        payload["upower_level_id"] = upload.upower_level_id
    if upload.upower_unit_price is not None:
        payload["upower_unit_price"] = upload.upower_unit_price
    if upload.upower_mode is not None:
        payload["upower_mode"] = upload.upower_mode
    if upload.member_first_exp_time is not None:
        payload["member_first"] = {
            "enabled": 1,
            "exp_time": upload.member_first_exp_time,
        }
    return payload


def _upload_result_from_projection(upload):
    payload = {}
    if upload.aid:
        payload["aid"] = upload.aid
    if upload.bvid:
        payload["bvid"] = upload.bvid
        payload["url"] = f"https://www.bilibili.com/video/{upload.bvid}"
    return payload or None


def _optional_int(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
