import logging
import os
import sys
from datetime import datetime
from pathlib import Path

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from celery_config import celery_app
from models import db
from models.report_video import (
    ReportVideoAudio,
    ReportVideoOutput,
    ReportVideoReport,
    ReportVideoTask,
)
from services.report_video_ai_service import (
    generate_report_ai_content,
    generate_report_quick_card,
    generate_topic_clusters,
    generate_topic_video_script,
)
from services.report_video_render_service import render_report_video, render_task_video
from services.report_video_service import PROJECT_ROOT, parse_pdf_file, report_dir
from services.report_video_auto_service import (
    auto_scan_preview,
    create_task_for_source_pdf,
    load_auto_scan_config,
    load_auto_scan_config_from_dict,
)
from services.report_video_asset_service import create_zsxq_report_asset_batch


logger = logging.getLogger(__name__)


REPORT_VIDEO_TASK_FAILURE_STATUS = {
    "tasks.report_video.generate_ai_scripts_task": "ai_failed",
    "tasks.report_video.generate_topic_cards_task": "topic_cards_failed",
    "tasks.report_video.generate_topic_clusters_task": "topic_failed",
    "tasks.report_video.generate_topic_video_script_task": "topic_script_failed",
    "tasks.report_video.generate_videos_task": "video_failed",
}


class ReportVideoFailureTask(celery_app.Task):
    abstract = True

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        details = _mark_report_video_failure_safely(
            self.name,
            args=args or (),
            kwargs=kwargs or {},
            exc=exc,
        )
        _send_direct_report_video_failure_email(
            celery_task_name=self.name,
            celery_task_id=task_id,
            details=details,
            error=str(exc),
        )
        return super().on_failure(exc, task_id, args, kwargs, einfo)


def _arg(args, kwargs, key, index=0):
    if isinstance(kwargs, dict) and kwargs.get(key) is not None:
        return kwargs.get(key)
    if args and len(args) > index:
        return args[index]
    return None


def _mark_report_video_failure_safely(task_name, *, args=None, kwargs=None, exc=None):
    details = {
        "entity_type": None,
        "entity_id": None,
        "parent_task_id": None,
        "persisted": False,
        "persist_error": None,
    }
    args = args or ()
    kwargs = kwargs or {}
    message = str(exc or "研报视频任务异常")

    try:
        from app import create_app

        app = create_app()
        with app.app_context():
            if task_name == "tasks.report_video.parse_report_pdf_task":
                report_id = _arg(args, kwargs, "report_id", 0)
                details.update(_mark_report_failed(report_id, message, parent_status="parse_failed"))
            elif task_name == "tasks.report_video.auto_process_report_task":
                report_id = _arg(args, kwargs, "report_id", 0)
                details.update(_mark_report_failed(report_id, message, parent_status="failed"))
            elif task_name == "tasks.report_video.process_audio_timeline_task":
                audio_id = _arg(args, kwargs, "audio_id", 0)
                details.update(_mark_audio_timeline_failed(audio_id, message))
            elif task_name in REPORT_VIDEO_TASK_FAILURE_STATUS:
                task_id = _arg(args, kwargs, "task_id", 0)
                details.update(_mark_report_video_task_failed(
                    task_id,
                    message,
                    REPORT_VIDEO_TASK_FAILURE_STATUS[task_name],
                ))

            db.session.commit()
            details["persisted"] = bool(details.get("entity_id"))
    except Exception as persist_exc:
        details["persist_error"] = str(persist_exc)
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.exception(
            "[REPORT_VIDEO_GUARD] failed to persist task failure | task=%s entity=%s error=%s",
            task_name,
            details.get("entity_id") or "-",
            persist_exc,
        )

    return details


def _mark_report_failed(report_id, message, *, parent_status):
    if not report_id:
        return {"entity_type": "report", "entity_id": None}
    report = ReportVideoReport.query.get(str(report_id))
    if not report:
        return {"entity_type": "report", "entity_id": str(report_id)}

    report.error_message = message
    report.updated_at = datetime.utcnow()
    report.parse_status = "parse_failed"
    report.ai_status = "ai_blocked"
    if report.task:
        report.task.status = parent_status
        report.task.error_message = message
        report.task.updated_at = datetime.utcnow()
    return {
        "entity_type": "report",
        "entity_id": report.id,
        "parent_task_id": report.task_id,
    }


def _mark_audio_timeline_failed(audio_id, message):
    if not audio_id:
        return {"entity_type": "audio", "entity_id": None}
    audio = ReportVideoAudio.query.get(str(audio_id))
    if not audio:
        return {"entity_type": "audio", "entity_id": str(audio_id)}

    audio.match_status = "unmatched"
    audio.error_message = message
    audio.updated_at = datetime.utcnow()
    if audio.task:
        audio.task.status = "audio_failed"
        audio.task.error_message = message
        _update_audio_timeline_job(
            audio.task,
            status="failed",
            audio_id=audio.id,
            error_message=message,
            completed_at=datetime.utcnow().isoformat(),
        )
    return {
        "entity_type": "audio",
        "entity_id": audio.id,
        "parent_task_id": audio.task_id,
    }


def _mark_report_video_task_failed(task_id, message, status):
    if not task_id:
        return {"entity_type": "task", "entity_id": None}
    task = ReportVideoTask.query.get(str(task_id))
    if not task:
        return {"entity_type": "task", "entity_id": str(task_id)}

    task.status = status
    task.error_message = message
    task.updated_at = datetime.utcnow()
    if status.startswith("topic"):
        try:
            _touch_topic_planning(task, status=status)
        except Exception:
            logger.exception("[REPORT_VIDEO_GUARD] failed to update topic planning | task=%s", task.id)
    return {
        "entity_type": "task",
        "entity_id": task.id,
        "parent_task_id": task.id,
    }


def _send_direct_report_video_failure_email(*, celery_task_name, celery_task_id=None, details=None, error=None):
    details = details or {}
    try:
        from services.workflow_notification_service import workflow_notification_service

        short_name = str(celery_task_name or "report_video").rsplit(".", 1)[-1]
        subject = f"[研报视频编排] 任务异常 · {short_name}"
        lines = [
            "事件：研报视频编排任务异常",
            f"Celery 任务：{celery_task_name or '-'}",
            f"Celery ID：{celery_task_id or '-'}",
            f"对象类型：{details.get('entity_type') or '-'}",
            f"对象 ID：{details.get('entity_id') or '-'}",
            f"父任务 ID：{details.get('parent_task_id') or '-'}",
            f"失败状态已写入 DB：{'是' if details.get('persisted') else '否'}",
        ]
        if details.get("persist_error"):
            lines.append(f"DB 写失败：{details.get('persist_error')}")
        lines.extend([
            f"错误：{error or '-'}",
            f"发送时间：{datetime.utcnow().isoformat()}Z",
        ])
        workflow_notification_service.send_email_message(subject, "\n".join(lines))
    except Exception:
        logger.exception("[REPORT_VIDEO_GUARD] direct failure email failed | task=%s", celery_task_name)


def _parse_result_status(parsed):
    cleaned_chars = int(parsed.get("cleaned_text_chars") or 0)
    extraction_method = parsed.get("extraction_method") or "pdf_text"
    if cleaned_chars >= 500 and extraction_method != "pdf_text_low_quality":
        return "parsed", None
    if extraction_method == "pdf_text_low_quality":
        ocr_meta = ((parsed.get("stats") or {}).get("ocr") or {})
        if ocr_meta.get("available") is False:
            return "parse_failed", "PDF 正文疑似图片/扫描版，当前环境 OCR 不可用"
        return "parse_failed", "PDF 可提取正文过少，OCR 未获得有效正文"
    return "parse_failed", "PDF 可提取正文过少，疑似扫描版或图片型 PDF"


def _refresh_task_parse_status(task):
    reports = list(task.reports)
    if not reports:
        task.status = "created"
        return
    if any(report.parse_status == "parsing" for report in reports):
        task.status = "parsing"
    elif all(report.parse_status == "parsed" for report in reports):
        task.status = "parsed"
    elif any(report.parse_status == "parsed" for report in reports):
        task.status = "parsed"
    else:
        task.status = "uploaded"


def _topic_planning_result(task):
    result = dict(task.result or {})
    planning = dict(result.get("topic_planning") or {})
    result["topic_planning"] = planning
    task.result = result
    return planning


def _touch_topic_planning(task, **updates):
    planning = _topic_planning_result(task)
    planning.update(updates)
    planning["updated_at"] = datetime.utcnow().isoformat()
    task.result = {**(task.result or {}), "topic_planning": planning}
    task.updated_at = datetime.utcnow()
    return planning


def _update_audio_timeline_job(task, **updates):
    result = dict(task.result or {})
    job = dict(result.get("audio_timeline_job") or {})
    job.update(updates)
    job["updated_at"] = datetime.utcnow().isoformat()
    result["audio_timeline_job"] = job
    task.result = result
    task.updated_at = datetime.utcnow()
    return job


@celery_app.task(bind=True, base=ReportVideoFailureTask, name="tasks.report_video.parse_report_pdf_task")
def parse_report_pdf_task(self, report_id):
    from app import create_app

    app = create_app()
    with app.app_context():
        report = ReportVideoReport.query.get(report_id)
        if not report:
            logger.error("[REPORT_VIDEO_PARSE] report not found: %s", report_id)
            return {"status": "missing", "report_id": report_id}

        try:
            logger.info("[REPORT_VIDEO_PARSE] start | report_id=%s file=%s", report.id, report.file_name)
            report.parse_status = "parsing"
            report.error_message = None
            report.updated_at = datetime.utcnow()
            db.session.commit()

            parsed = parse_pdf_file(
                PROJECT_ROOT / report.file_path,
                report_dir(report.task_id, report.report_code),
                cache_name=report.file_name,
            )
            parse_status, error_message = _parse_result_status(parsed)

            report.page_count = parsed["page_count"]
            report.raw_text_path = parsed["raw_text_path"]
            report.cleaned_text_path = parsed["cleaned_text_path"]
            report.raw_text_chars = parsed["raw_text_chars"]
            report.cleaned_text_chars = parsed["cleaned_text_chars"]
            report.is_scanned = parsed["is_scanned"]
            report.parse_status = parse_status
            report.ai_status = "ai_pending" if parse_status == "parsed" else "ai_blocked"
            report.error_message = error_message
            raw_meta = dict(report.raw_meta or {})
            raw_meta.update({
                "scan_stats": parsed.get("stats") or {},
                "extraction_method": parsed.get("extraction_method") or "pdf_text",
                "parse_cache": parsed.get("cache") or {},
                "parsed_at": datetime.utcnow().isoformat(),
                "async": True,
            })
            report.raw_meta = raw_meta
            report.updated_at = datetime.utcnow()
            _refresh_task_parse_status(report.task)
            report.task.updated_at = datetime.utcnow()
            db.session.commit()
            logger.info("[REPORT_VIDEO_PARSE] complete | report_id=%s status=%s", report.id, parse_status)
            return {"status": parse_status, "report_id": report.id}
        except Exception as exc:
            db.session.rollback()
            report = ReportVideoReport.query.get(report_id)
            if report:
                report.parse_status = "parse_failed"
                report.ai_status = "ai_blocked"
                report.error_message = str(exc)
                report.updated_at = datetime.utcnow()
                if report.task:
                    _refresh_task_parse_status(report.task)
                    report.task.updated_at = datetime.utcnow()
                db.session.commit()
            logger.exception("[REPORT_VIDEO_PARSE] failed | report_id=%s", report_id)
            raise


@celery_app.task(bind=True, base=ReportVideoFailureTask, name="tasks.report_video.generate_ai_scripts_task")
def generate_ai_scripts_task(self, task_id, segment_count=4):
    from app import create_app

    app = create_app()
    with app.app_context():
        task = ReportVideoTask.query.get(task_id)
        if not task:
            logger.error("[REPORT_VIDEO_AI] task not found: %s", task_id)
            return {"status": "missing", "task_id": task_id}

        generated = []
        failed = []
        try:
            task.status = "ai_generating"
            task.updated_at = datetime.utcnow()
            for report in task.reports:
                if report.parse_status == "parsed":
                    report.ai_status = "ai_generating"
                    report.error_message = None
            db.session.commit()

            parsed_reports = [report for report in task.reports if report.parse_status == "parsed"]
            for report in parsed_reports:
                try:
                    payload = generate_report_ai_content(report, int(segment_count or task.default_segment_count or 4))
                    generated.append({
                        "report_id": report.id,
                        "file_name": report.file_name,
                        "provider": payload.get("provider"),
                        "segment_count": len(payload.get("segments") or []),
                    })
                    db.session.commit()
                except Exception as exc:
                    db.session.rollback()
                    report = ReportVideoReport.query.get(report.id)
                    if report:
                        report.ai_status = "ai_blocked"
                        report.error_message = str(exc)
                        report.updated_at = datetime.utcnow()
                        failed.append({
                            "report_id": report.id,
                            "file_name": report.file_name,
                            "error": str(exc),
                        })
                        db.session.commit()

            task = ReportVideoTask.query.get(task_id)
            task.status = "script_ready" if generated else task.status
            task.updated_at = datetime.utcnow()
            db.session.commit()
            logger.info("[REPORT_VIDEO_AI] complete | task_id=%s generated=%s failed=%s", task_id, len(generated), len(failed))
            return {"status": task.status, "task_id": task_id, "generated": generated, "failed": failed}
        except Exception:
            db.session.rollback()
            task = ReportVideoTask.query.get(task_id)
            if task:
                task.status = "ai_failed"
                task.error_message = "AI 文案生成任务失败"
                task.updated_at = datetime.utcnow()
                db.session.commit()
            logger.exception("[REPORT_VIDEO_AI] failed | task_id=%s", task_id)
            raise


@celery_app.task(bind=True, base=ReportVideoFailureTask, name="tasks.report_video.generate_topic_cards_task")
def generate_topic_cards_task(self, task_id):
    from app import create_app

    app = create_app()
    with app.app_context():
        task = ReportVideoTask.query.get(task_id)
        if not task:
            logger.error("[REPORT_VIDEO_TOPIC_CARDS] task not found: %s", task_id)
            return {"status": "missing", "task_id": task_id}

        candidate_reports = list(task.reports)
        if not candidate_reports:
            raise ValueError("当前任务没有可生成摘要卡的研报")

        generated = []
        failed = []
        try:
            task.status = "topic_cards_generating"
            _touch_topic_planning(task, status="cards_generating", report_cards=[], card_errors=[])
            db.session.commit()

            for report in candidate_reports:
                try:
                    card = generate_report_quick_card(report)
                    generated.append(card)
                    _touch_topic_planning(task, report_cards=generated, card_errors=failed, status="cards_generating")
                    db.session.commit()
                except Exception as exc:
                    db.session.rollback()
                    task = ReportVideoTask.query.get(task_id)
                    failed.append({
                        "report_id": report.id,
                        "file_name": report.file_name,
                        "error": str(exc),
                    })
                    _touch_topic_planning(task, report_cards=generated, card_errors=failed, status="cards_generating")
                    db.session.commit()

            task = ReportVideoTask.query.get(task_id)
            task.status = "topic_cards_ready" if generated else "topic_cards_failed"
            _touch_topic_planning(
                task,
                status="cards_ready" if generated else "cards_failed",
                report_cards=generated,
                card_errors=failed,
                cards_generated_at=datetime.utcnow().isoformat(),
            )
            db.session.commit()
            logger.info("[REPORT_VIDEO_TOPIC_CARDS] complete | task_id=%s cards=%s failed=%s", task_id, len(generated), len(failed))
            return {"status": task.status, "task_id": task_id, "cards": generated, "failed": failed}
        except Exception:
            db.session.rollback()
            task = ReportVideoTask.query.get(task_id)
            if task:
                task.status = "topic_cards_failed"
                task.error_message = "主题摘要卡生成任务失败"
                _touch_topic_planning(task, status="cards_failed")
                db.session.commit()
            logger.exception("[REPORT_VIDEO_TOPIC_CARDS] failed | task_id=%s", task_id)
            raise


@celery_app.task(bind=True, base=ReportVideoFailureTask, name="tasks.report_video.generate_topic_clusters_task")
def generate_topic_clusters_task(self, task_id):
    from app import create_app

    app = create_app()
    with app.app_context():
        task = ReportVideoTask.query.get(task_id)
        if not task:
            logger.error("[REPORT_VIDEO_TOPIC_CLUSTER] task not found: %s", task_id)
            return {"status": "missing", "task_id": task_id}

        try:
            planning = _topic_planning_result(task)
            report_cards = list(planning.get("report_cards") or [])
            existing_ids = {card.get("db_report_id") for card in report_cards if isinstance(card, dict)}
            for report in task.reports:
                if report.id not in existing_ids:
                    report_cards.append(generate_report_quick_card(report))
                    existing_ids.add(report.id)

            if not report_cards:
                raise ValueError("请先生成研报快速摘要卡")

            task.status = "topic_clustering"
            _touch_topic_planning(task, status="clustering", report_cards=report_cards)
            db.session.commit()

            clustered = generate_topic_clusters(task, report_cards)
            task = ReportVideoTask.query.get(task_id)
            task.status = "topic_ready"
            planning = _touch_topic_planning(
                task,
                status="topic_ready",
                report_cards=report_cards,
                topics=clustered.get("topics") or [],
                ranked_topics=clustered.get("ranked_topics") or [],
                external_heat=clustered.get("external_heat") or {},
                cluster_provider=clustered.get("provider"),
                cluster_provider_error=clustered.get("provider_error"),
                clustered_at=datetime.utcnow().isoformat(),
            )
            db.session.commit()
            logger.info("[REPORT_VIDEO_TOPIC_CLUSTER] complete | task_id=%s topics=%s", task_id, len(planning.get("topics") or []))
            return {
                "status": task.status,
                "task_id": task_id,
                "topics": planning.get("topics") or [],
                "ranked_topics": planning.get("ranked_topics") or [],
            }
        except Exception:
            db.session.rollback()
            task = ReportVideoTask.query.get(task_id)
            if task:
                task.status = "topic_failed"
                task.error_message = "主题聚类任务失败"
                _touch_topic_planning(task, status="topic_failed")
                db.session.commit()
            logger.exception("[REPORT_VIDEO_TOPIC_CLUSTER] failed | task_id=%s", task_id)
            raise


@celery_app.task(bind=True, base=ReportVideoFailureTask, name="tasks.report_video.generate_topic_video_script_task")
def generate_topic_video_script_task(self, task_id, topic_id):
    from app import create_app

    app = create_app()
    with app.app_context():
        task = ReportVideoTask.query.get(task_id)
        if not task:
            logger.error("[REPORT_VIDEO_TOPIC_SCRIPT] task not found: %s", task_id)
            return {"status": "missing", "task_id": task_id}

        try:
            planning = _topic_planning_result(task)
            topics = list(planning.get("topics") or [])
            topic = next((item for item in topics if item.get("topic_id") == topic_id), None)
            if not topic:
                raise ValueError("主题不存在，请先生成主题榜")
            report_cards = list(planning.get("report_cards") or [])
            if not report_cards:
                raise ValueError("主题脚本生成需要研报摘要卡")

            task.status = "topic_script_generating"
            _touch_topic_planning(task, status="topic_script_generating", active_topic_id=topic_id)
            db.session.commit()

            script = generate_topic_video_script(task, topic, report_cards)
            task = ReportVideoTask.query.get(task_id)
            scripts = dict((_topic_planning_result(task).get("topic_scripts") or {}))
            scripts[topic_id] = script
            task.status = "topic_script_ready"
            _touch_topic_planning(
                task,
                status="topic_script_ready",
                active_topic_id=topic_id,
                topic_scripts=scripts,
                topic_script_generated_at=datetime.utcnow().isoformat(),
            )
            db.session.commit()
            logger.info("[REPORT_VIDEO_TOPIC_SCRIPT] complete | task_id=%s topic_id=%s sections=%s", task_id, topic_id, len(script.get("sections") or []))
            return {"status": task.status, "task_id": task_id, "topic_id": topic_id, "script": script}
        except Exception:
            db.session.rollback()
            task = ReportVideoTask.query.get(task_id)
            if task:
                task.status = "topic_script_failed"
                task.error_message = "主题脚本生成任务失败"
                _touch_topic_planning(task, status="topic_script_failed", active_topic_id=topic_id)
                db.session.commit()
            logger.exception("[REPORT_VIDEO_TOPIC_SCRIPT] failed | task_id=%s topic_id=%s", task_id, topic_id)
            raise


@celery_app.task(bind=True, base=ReportVideoFailureTask, name="tasks.report_video.process_audio_timeline_task")
def process_audio_timeline_task(self, audio_id, options=None):
    """Generate a full-audio timeline with ASR and match it to report-video segments."""
    from app import create_app

    app = create_app()
    with app.app_context():
        options = options or {}
        audio = ReportVideoAudio.query.get(audio_id)
        if not audio:
            logger.error("[REPORT_VIDEO_AUDIO_TIMELINE] audio not found: %s", audio_id)
            return {"status": "missing", "audio_id": audio_id}

        task = audio.task
        if not task:
            logger.error("[REPORT_VIDEO_AUDIO_TIMELINE] task missing | audio_id=%s", audio_id)
            return {"status": "missing_task", "audio_id": audio_id}

        try:
            calibration_model = str(options.get("calibration_model") or "medium").strip().lower() or "medium"
            try:
                audio_speed = float(options.get("audio_speed") or 1.0)
            except (TypeError, ValueError):
                audio_speed = 1.0
            audio.match_status = "asr_processing"
            audio.error_message = "正在使用本地 ASR 生成字幕时间轴"
            task.status = "audio_processing"
            task.error_message = None
            _update_audio_timeline_job(
                task,
                status="running",
                audio_id=audio.id,
                file_name=audio.file_name,
                mode="full_audio_asr",
                calibration_model=calibration_model,
                audio_speed=audio_speed,
                celery_task_id=self.request.id,
                started_at=datetime.utcnow().isoformat(),
            )
            db.session.commit()

            # Reuse the existing report-video audio timeline utilities. They live
            # beside the upload route today; import lazily to avoid Celery startup cycles.
            from apis.v1 import report_video as report_video_api

            audio_path = PROJECT_ROOT / audio.file_path if audio.file_path else None
            asr_initial_prompt = report_video_api._build_report_video_asr_prompt(task)
            timeline_entries, timeline_generation_meta = report_video_api._generate_timeline_entries_from_asr(
                task,
                audio_path if audio_path and audio_path.exists() else None,
                audio.duration_sec,
                model_size=calibration_model,
                initial_prompt=asr_initial_prompt,
            )

            timeline_source = "full_audio_asr"
            if not timeline_entries and options.get("fallback_to_script_estimate", False):
                timeline_entries, timeline_generation_meta = report_video_api._auto_generate_timeline_entries(task, audio.duration_sec)
                timeline_source = "script_estimate"

            if not timeline_entries:
                audio.match_status = "unmatched"
                audio.error_message = "ASR 没有生成有效字幕时间轴"
                task.status = "audio_failed"
                task.error_message = audio.error_message
                _update_audio_timeline_job(
                    task,
                    status="failed",
                    audio_id=audio.id,
                    timeline_source=timeline_source,
                    error_message=audio.error_message,
                    completed_at=datetime.utcnow().isoformat(),
                )
                db.session.commit()
                return {"status": "failed", "audio_id": audio.id, "reason": audio.error_message}

            suffix = "asr_generated_timeline" if timeline_source == "full_audio_asr" else "auto_generated_timeline"
            timeline_rel_path = report_video_api._write_timeline_source_text(task, timeline_entries, suffix=suffix)

            if timeline_source == "full_audio_asr":
                retime_meta = {
                    "calibration_mode": "full_audio_asr",
                    "calibration_method": "full_audio_asr",
                    "strategy": "full_audio_asr",
                    "status": (timeline_generation_meta or {}).get("status") or "calibrated",
                    "message": "已通过整片 ASR 生成字幕时间轴。",
                    "audio_duration_sec": round(float(audio.duration_sec or 0), 3),
                    "target_duration_sec": round(float(audio.duration_sec or 0), 3),
                    "sample_count": 1,
                    "asr_total_duration_sec": round(float(audio.duration_sec or 0), 3),
                    "anchor_count": 0,
                    "confidence_avg": 0,
                    "average_offset_sec": 0,
                    "max_offset_sec": 0,
                    "subtitle_source": "full_audio_asr",
                    "timeline_generation": timeline_generation_meta or {},
                    "audio_speed": {
                        "speed": audio_speed,
                        "applied": abs(audio_speed - 1.0) >= 0.001,
                        "strategy": "ffmpeg_atempo_before_asr",
                    },
                }
            else:
                retime_meta = {
                    "calibration_mode": "script_estimate",
                    "calibration_method": "script_duration_estimate",
                    "strategy": "script_duration_estimate",
                    "status": "fallback",
                    "message": "ASR 未生成有效结果，已退回口播稿时长估算。",
                    "audio_duration_sec": round(float(audio.duration_sec or 0), 3),
                    "target_duration_sec": round(float(audio.duration_sec or 0), 3),
                    "subtitle_source": "script_estimate",
                    "timeline_generation": timeline_generation_meta or {},
                    "audio_speed": {
                        "speed": audio_speed,
                        "applied": abs(audio_speed - 1.0) >= 0.001,
                        "strategy": "ffmpeg_atempo_before_asr",
                    },
                }

            subtitle_rel_path = report_video_api._write_timeline_srt(task, timeline_entries)
            timeline_result = report_video_api._apply_full_audio_timeline(
                task,
                audio,
                timeline_entries,
                timeline_rel_path,
                subtitle_rel_path,
                retime_meta,
            )
            timeline_result["auto_generated"] = True
            timeline_result["timeline_source"] = timeline_source

            task.status = "waiting_video" if audio.match_status == "timeline_matched" else "audio_failed"
            if audio.match_status == "timeline_matched":
                audio.error_message = None
                task.error_message = None
            else:
                task.error_message = audio.error_message or "整片音频时间轴没有匹配到 segment"
            _update_audio_timeline_job(
                task,
                status="completed" if audio.match_status == "timeline_matched" else "failed",
                audio_id=audio.id,
                timeline_source=timeline_source,
                timeline_entry_count=len(timeline_entries),
                matched_segment_count=timeline_result.get("matched", 0),
                asr_cache=((timeline_generation_meta or {}).get("asr_cache") or {}),
                completed_at=datetime.utcnow().isoformat(),
            )
            db.session.commit()
            logger.info(
                "[REPORT_VIDEO_AUDIO_TIMELINE] complete | task_id=%s audio_id=%s source=%s matched=%s",
                task.id,
                audio.id,
                timeline_source,
                timeline_result.get("matched", 0),
            )
            return {
                "status": task.status,
                "audio_id": audio.id,
                "timeline_source": timeline_source,
                "matched": timeline_result.get("matched", 0),
            }
        except Exception as exc:
            db.session.rollback()
            audio = ReportVideoAudio.query.get(audio_id)
            if audio:
                task = audio.task
                audio.match_status = "unmatched"
                audio.error_message = str(exc)
                audio.updated_at = datetime.utcnow()
                if task:
                    task.status = "audio_failed"
                    task.error_message = str(exc)
                    _update_audio_timeline_job(
                        task,
                        status="failed",
                        audio_id=audio.id,
                        error_message=str(exc),
                        completed_at=datetime.utcnow().isoformat(),
                    )
                db.session.commit()
            logger.exception("[REPORT_VIDEO_AUDIO_TIMELINE] failed | audio_id=%s", audio_id)
            raise


@celery_app.task(bind=True, base=ReportVideoFailureTask, name="tasks.report_video.generate_videos_task")
def generate_videos_task(self, task_id, ratios=None, report_id=None):
    from app import create_app

    app = create_app()
    with app.app_context():
        task = ReportVideoTask.query.get(task_id)
        if not task:
            logger.error("[REPORT_VIDEO_RENDER] task not found: %s", task_id)
            return {"status": "missing", "task_id": task_id}

        ratios = ratios or task.output_ratios or ["16:9"]
        generated = []
        failed = []
        try:
            task.status = "video_generating"
            task.error_message = None
            task.updated_at = datetime.utcnow()
            db.session.commit()

            if (task.result or {}).get("audio_mode") == "full_audio_srt":
                subtitle_calibration = (task.result or {}).get("subtitle_calibration") or ((task.result or {}).get("full_audio") or {}).get("subtitle_calibration")
                if subtitle_calibration:
                    logger.info(
                        "[REPORT_VIDEO_RENDER] subtitle_calibration | task_id=%s report=%s",
                        task_id,
                        subtitle_calibration,
                    )
                for ratio in ratios:
                    try:
                        result = render_task_video(task, ratio)
                        output = ReportVideoOutput(
                            task_id=task.id,
                            report_id=None,
                            ratio=result["ratio"],
                            video_type=result["video_type"],
                            duration_sec=result["duration_sec"],
                            file_path=result["file_path"],
                            status="completed",
                        )
                        db.session.add(output)
                        db.session.flush()
                        generated.append(output.to_dict())
                        db.session.commit()
                    except Exception as exc:
                        db.session.rollback()
                        failed.append({
                            "report_id": None,
                            "file_name": task.task_name,
                            "ratio": ratio,
                            "error": str(exc),
                        })
            else:
                reports = [report for report in task.reports if report.analysis and report.segments]
                if report_id:
                    reports = [report for report in reports if report.id == report_id]
                for report in reports:
                    for ratio in ratios:
                        try:
                            result = render_report_video(report, ratio)
                            output = ReportVideoOutput(
                                task_id=task.id,
                                report_id=report.id,
                                ratio=result["ratio"],
                                video_type=result["video_type"],
                                duration_sec=result["duration_sec"],
                                file_path=result["file_path"],
                                status="completed",
                            )
                            db.session.add(output)
                            db.session.flush()
                            generated.append(output.to_dict())
                            db.session.commit()
                        except Exception as exc:
                            db.session.rollback()
                            failed.append({
                                "report_id": report.id,
                                "file_name": report.file_name,
                                "ratio": ratio,
                                "error": str(exc),
                            })

            task = ReportVideoTask.query.get(task_id)
            task.status = "completed" if generated and not failed else ("video_failed" if failed and not generated else "video_partial")
            task.error_message = None if generated else "; ".join(item["error"] for item in failed[:3])
            task.updated_at = datetime.utcnow()
            db.session.commit()
            logger.info("[REPORT_VIDEO_RENDER] complete | task_id=%s generated=%s failed=%s", task_id, len(generated), len(failed))
            return {
                "status": task.status,
                "task_id": task_id,
                "generated": generated,
                "failed": failed,
                "subtitle_calibration": (task.result or {}).get("subtitle_calibration"),
            }
        except Exception:
            db.session.rollback()
            task = ReportVideoTask.query.get(task_id)
            if task:
                task.status = "video_failed"
                task.error_message = "视频生成任务失败"
                task.updated_at = datetime.utcnow()
                db.session.commit()
            logger.exception("[REPORT_VIDEO_RENDER] failed | task_id=%s", task_id)
            raise


@celery_app.task(bind=True, base=ReportVideoFailureTask, name="tasks.report_video.auto_process_report_task")
def auto_process_report_task(self, report_id):
    from app import create_app

    app = create_app()
    with app.app_context():
        report = ReportVideoReport.query.get(report_id)
        if not report:
            logger.error("[REPORT_VIDEO_AUTO_PROCESS] report not found: %s", report_id)
            return {"status": "missing", "report_id": report_id}

        task = report.task
        generated = []
        failed = []
        try:
            logger.info("[REPORT_VIDEO_AUTO_PROCESS] start | report_id=%s file=%s", report.id, report.file_name)
            report.parse_status = "parsing"
            report.error_message = None
            task.status = "parsing"
            task.updated_at = datetime.utcnow()
            db.session.commit()

            parsed = parse_pdf_file(
                PROJECT_ROOT / report.file_path,
                report_dir(report.task_id, report.report_code),
                cache_name=report.file_name,
            )
            parse_status, error_message = _parse_result_status(parsed)
            report.page_count = parsed["page_count"]
            report.raw_text_path = parsed["raw_text_path"]
            report.cleaned_text_path = parsed["cleaned_text_path"]
            report.raw_text_chars = parsed["raw_text_chars"]
            report.cleaned_text_chars = parsed["cleaned_text_chars"]
            report.is_scanned = parsed["is_scanned"]
            report.parse_status = parse_status
            report.ai_status = "ai_pending" if parse_status == "parsed" else "ai_blocked"
            report.error_message = error_message
            raw_meta = dict(report.raw_meta or {})
            raw_meta.update({
                "scan_stats": parsed.get("stats") or {},
                "extraction_method": parsed.get("extraction_method") or "pdf_text",
                "parse_cache": parsed.get("cache") or {},
                "parsed_at": datetime.utcnow().isoformat(),
                "async": True,
            })
            report.raw_meta = raw_meta
            _refresh_task_parse_status(task)
            task.updated_at = datetime.utcnow()
            db.session.commit()

            if parse_status != "parsed":
                task = ReportVideoTask.query.get(task.id)
                task.status = "parse_failed"
                task.error_message = error_message or "PDF 未解析出有效正文，自动制作已停止"
                task.updated_at = datetime.utcnow()
                db.session.commit()
                return {
                    "status": "parse_failed",
                    "task_id": task.id,
                    "report_id": report.id,
                    "error": task.error_message,
                }

            task = ReportVideoTask.query.get(task.id)
            task.status = "ai_generating"
            report = ReportVideoReport.query.get(report.id)
            report.ai_status = "ai_generating"
            db.session.commit()

            payload = generate_report_ai_content(report, int(task.default_segment_count or 4))
            db.session.commit()

            task = ReportVideoTask.query.get(task.id)
            task.status = "video_generating"
            task.updated_at = datetime.utcnow()
            db.session.commit()

            ratios = task.output_ratios or ["16:9"]
            report = ReportVideoReport.query.get(report.id)
            for ratio in ratios:
                try:
                    result = render_report_video(report, ratio)
                    output = ReportVideoOutput(
                        task_id=task.id,
                        report_id=report.id,
                        ratio=result["ratio"],
                        video_type=result["video_type"],
                        duration_sec=result["duration_sec"],
                        file_path=result["file_path"],
                        status="completed",
                    )
                    db.session.add(output)
                    db.session.flush()
                    generated.append(output.to_dict())
                    db.session.commit()
                except Exception as exc:
                    db.session.rollback()
                    failed.append({
                        "report_id": report.id,
                        "file_name": report.file_name,
                        "ratio": ratio,
                        "error": str(exc),
                    })

            task = ReportVideoTask.query.get(task.id)
            task.status = "completed" if generated and not failed else ("video_failed" if failed and not generated else "video_partial")
            task.error_message = None if generated else "; ".join(item["error"] for item in failed[:3])
            result = dict(task.result or {})
            auto = dict(result.get("auto_scan") or {})
            auto.update({
                "processed_at": datetime.utcnow().isoformat(),
                "generated_count": len(generated),
                "failed_count": len(failed),
                "ai_provider": payload.get("provider"),
            })
            result["auto_scan"] = auto
            task.result = result
            task.updated_at = datetime.utcnow()
            db.session.commit()
            logger.info("[REPORT_VIDEO_AUTO_PROCESS] complete | task_id=%s generated=%s failed=%s", task.id, len(generated), len(failed))
            return {"status": task.status, "task_id": task.id, "report_id": report.id, "generated": generated, "failed": failed}
        except Exception as exc:
            db.session.rollback()
            report = ReportVideoReport.query.get(report_id)
            if report:
                report.error_message = str(exc)
                report.updated_at = datetime.utcnow()
                if report.task:
                    report.task.status = "failed"
                    report.task.error_message = str(exc)
                    report.task.updated_at = datetime.utcnow()
                db.session.commit()
            logger.exception("[REPORT_VIDEO_AUTO_PROCESS] failed | report_id=%s", report_id)
            raise


@celery_app.task(bind=True, base=ReportVideoFailureTask, name="tasks.report_video.scan_report_video_folder_task")
def scan_report_video_folder_task(self, config_override=None, dry_run=False, trigger="manual"):
    from app import create_app

    app = create_app()
    with app.app_context():
        config = load_auto_scan_config()
        if isinstance(config_override, dict):
            config.update({key: value for key, value in config_override.items() if value is not None})
            config = load_auto_scan_config_from_dict(config)
        preview = auto_scan_preview(config)
        max_files = int(config.get("max_files_per_run") or 1)
        new_items = [item for item in preview["items"] if item["status"] == "new"][:max_files]

        if dry_run:
            return {"status": "dry_run", "preview": preview, "created": [], "queued": []}

        created = []
        queued = []
        for item in new_items:
            try:
                task, report = create_task_for_source_pdf(
                    item["path"],
                    item["source_sha256"],
                    config=config,
                    trigger=trigger,
                )
                db.session.commit()
                celery_task = auto_process_report_task.apply_async(args=[report.id], queue="report_video")
                created.append({"task_id": task.id, "report_id": report.id, "file_name": report.file_name})
                queued.append({"task_id": task.id, "report_id": report.id, "celery_id": celery_task.id})
            except Exception as exc:
                db.session.rollback()
                logger.exception("[REPORT_VIDEO_AUTO_SCAN] create failed | file=%s", item.get("path"))
                created.append({"file_name": item.get("file_name"), "error": str(exc)})

        return {
            "status": "queued",
            "config": config,
            "scanned": preview,
            "created": created,
            "queued": queued,
        }


@celery_app.task(bind=True, base=ReportVideoFailureTask, name="tasks.report_video.scan_zsxq_report_assets_task")
def scan_zsxq_report_assets_task(self, payload=None, dry_run=False):
    from app import create_app

    app = create_app()
    with app.app_context():
        payload = payload or {}
        if dry_run:
            from services.report_video_asset_service import preview_zsxq_report_assets

            return {"status": "dry_run", "preview": preview_zsxq_report_assets(payload), "queued": []}

        result = create_zsxq_report_asset_batch(payload)
        queued = []
        if result.get("parse_pdfs"):
            for report_id in result.get("report_ids") or []:
                celery_task = parse_report_pdf_task.apply_async(args=[report_id], queue="report_video")
                queued.append({"report_id": report_id, "celery_id": celery_task.id})
        result["queued_parse"] = queued
        result["status"] = "queued" if result.get("created_count", 0) > 0 else "no_new_assets"
        logger.info(
            "[REPORT_VIDEO_ASSET_SCAN] complete | source=%s run_id=%s target_date=%s candidates=%s created=%s queued_parse=%s",
            result.get("source"),
            result.get("run_id") or "-",
            result.get("target_date") or "-",
            result.get("candidate_count"),
            result.get("created_count"),
            len(queued),
        )
        return result
