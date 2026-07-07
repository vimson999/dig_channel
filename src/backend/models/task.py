from datetime import datetime
import uuid
from sqlalchemy import event, inspect as sqlalchemy_inspect

from . import db

_UNLOADED = object()


def _compact_text(value, max_length=None):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if max_length and len(text) > max_length:
        return text[:max_length]
    return text


def _is_generic_report_title(value):
    text = str(value or "").strip()
    if not text:
        return False
    normalized = "".join(text.split()).lstrip("#")
    return normalized in {"外资研报", "研报", "PDF", "pdf"}


def _nested(mapping, *keys):
    value = mapping if isinstance(mapping, dict) else {}
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _boolish(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if not text:
        return None
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return None


def _script_chars_from_result(result):
    structured = result.get('structured_result') if isinstance(result.get('structured_result'), dict) else {}
    script = (
        structured.get('final_script_for_tts')
        or result.get('final_script_for_tts')
        or _nested(result, 'script_material', 'final_script_for_tts')
    )
    text = str(script or "").strip()
    if text:
        return len(text)
    try:
        return max(0, int(_nested(result, 'script_material', 'chars') or 0))
    except (TypeError, ValueError):
        return 0


class PublicationTask(db.Model):
    __tablename__ = 'publication_tasks'

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    celery_task_id = db.Column(db.String(50), nullable=True, index=True)
    
    # 任务状态: pending, processing, completed, failed
    status = db.Column(db.String(20), default='pending', index=True)
    
    # 当前步骤: init, synthesis, thumbnail, upload, done
    current_step = db.Column(db.String(80), default='init')
    
    # 进度百分比 (0-100)
    progress = db.Column(db.Integer, default=0)
    
    # 用户提交的原始参数 (视频配置、封面配置、B站配置)
    # 使用 JSON 类型以兼容 SQLite (开发环境) 和 PostgreSQL
    params = db.Column(db.JSON, nullable=False)
    
    # 执行结果 (生成的文件路径、B站链接等)
    result = db.Column(db.JSON, default=dict)
    
    # 错误信息
    error_message = db.Column(db.Text, nullable=True)

    # 高频查询投影列。完整载荷仍保留在 params/result；这些列只承接列表、关联、
    # 轮询等热路径，避免每次从 JSON 大字段中过滤和投影。
    feature = db.Column(db.String(80), nullable=True, index=True)
    user_id = db.Column(db.String(64), nullable=True, index=True)
    parent_task_id = db.Column(db.String(36), nullable=True, index=True)
    source_task_id = db.Column(db.String(36), nullable=True, index=True)
    parent_feature = db.Column(db.String(80), nullable=True)
    source_feature = db.Column(db.String(80), nullable=True)
    task_title = db.Column(db.String(500), nullable=True)
    task_video_path = db.Column(db.String(600), nullable=True)

    # report_pdf_audio_workflow 列表页投影列。
    report_audio_pdf_attachment_id = db.Column(db.String(36), nullable=True, index=True)
    report_audio_pdf_filename = db.Column(db.String(500), nullable=True)
    report_audio_pdf_bytes = db.Column(db.String(40), nullable=True)
    report_audio_pdf_topic_title = db.Column(db.String(500), nullable=True)
    report_audio_result_pdf_filename = db.Column(db.String(500), nullable=True)
    report_audio_result_pdf_bytes = db.Column(db.String(40), nullable=True)
    report_audio_result_pdf_topic_title = db.Column(db.String(500), nullable=True)
    report_audio_metadata_title = db.Column(db.String(500), nullable=True)
    report_audio_video_title = db.Column(db.String(500), nullable=True)
    report_audio_cover_title = db.Column(db.String(500), nullable=True)
    report_audio_cover_subtitle = db.Column(db.String(220), nullable=True)
    report_audio_structured_title = db.Column(db.String(500), nullable=True)
    report_audio_started_at = db.Column(db.String(64), nullable=True)
    report_audio_completed_at = db.Column(db.String(64), nullable=True)
    report_audio_error = db.Column(db.Text, nullable=True)
    report_audio_output_path = db.Column(db.String(600), nullable=True)
    report_audio_wav_path = db.Column(db.String(600), nullable=True)
    report_audio_seconds = db.Column(db.String(40), nullable=True)
    report_audio_audio_bytes = db.Column(db.String(40), nullable=True)
    report_audio_video_enabled = db.Column(db.Boolean, nullable=True)
    report_audio_video_status = db.Column(db.String(80), nullable=True)
    report_audio_video_publisher_task_id = db.Column(db.String(36), nullable=True)
    report_audio_video_message = db.Column(db.Text, nullable=True)
    report_audio_video_path = db.Column(db.String(600), nullable=True)
    report_audio_cover_path = db.Column(db.String(600), nullable=True)
    final_script_chars = db.Column(db.Integer, nullable=False, default=0)

    # Parent workflow hot-state projection. The full retry/children payload stays
    # in result for audit/debug, while monitor and polling paths can read these
    # columns without touching the large JSON document.
    waiting_child_label = db.Column(db.String(80), nullable=True, index=True)
    waiting_child_id = db.Column(db.String(36), nullable=True, index=True)
    waiting_child_status = db.Column(db.String(40), nullable=True)
    waiting_child_step = db.Column(db.String(80), nullable=True)
    waiting_child_updated_at = db.Column(db.String(64), nullable=True)
    retry_state_status = db.Column(db.String(40), nullable=True)
    
    # 时间戳
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.sync_projection_fields()

    def sync_projection_fields(self):
        params_value = _loaded_model_attr(self, 'params', _UNLOADED)
        result_value = _loaded_model_attr(self, 'result', _UNLOADED)
        params_loaded = params_value is not _UNLOADED
        result_loaded = result_value is not _UNLOADED
        if not params_loaded and not result_loaded:
            return self

        params = params_value if isinstance(params_value, dict) else {}
        result = result_value if isinstance(result_value, dict) else {}

        if params_loaded:
            feature = _compact_text(params.get('feature'), 80)
            self.feature = feature or 'publisher'
            self.user_id = _compact_text(params.get('user_id'), 64)
            self.parent_task_id = _compact_text(params.get('parent_task_id'), 36)
            self.parent_feature = _compact_text(params.get('parent_feature'), 80)

            zsxq_publish = params.get('zsxq_publish') if isinstance(params.get('zsxq_publish'), dict) else {}
            self.source_task_id = _compact_text(
                params.get('source_task_id')
                or zsxq_publish.get('source_task_id')
                or (result.get('source_task_id') if result_loaded else None),
                36,
            )
            self.source_feature = _compact_text(params.get('source_feature'), 80)
        elif result_loaded and result.get('source_task_id'):
            self.source_task_id = _compact_text(result.get('source_task_id'), 36)

        if result_loaded:
            self.task_video_path = _compact_text(result.get('video_path'), 600)

        title = self._derive_task_title(params if params_loaded else {}, result if result_loaded else {})
        if title or (params_loaded and result_loaded):
            self.task_title = title

        self._sync_report_audio_projection(
            params if params_loaded else None,
            result if result_loaded else None,
        )
        if result_loaded:
            self._sync_workflow_state_projection(result)
        return self

    def _derive_task_title(self, params, result):
        candidates = [
            _nested(params, 'bilibili', 'title'),
            _nested(params, 'thumbnail', 'main_title'),
            _nested(params, 'content_pipeline', 'title'),
            _nested(params, 'zsxq_publish', 'title'),
            _nested(params, 'report_audio_workflow', 'metadata', 'title'),
            _nested(params, 'report_audio_workflow', 'video', 'title'),
            _nested(params, 'report_script_material_workflow', 'metadata', 'title'),
            _nested(result, 'editor_result', 'program_title'),
            _nested(result, 'editor_result', 'cover_title'),
            _nested(params, 'report_top10_workflow', 'program_name'),
            _nested(result, 'plan', 'title'),
            _nested(params, 'mp3_video_workflow', 'metadata', 'title'),
            _nested(params, 'mp3_video_workflow', 'audio', 'topic_title'),
            _nested(params, 'mp3_video_workflow', 'audio', 'filename'),
            _nested(result, 'structured_result', 'cover_title'),
            _nested(result, 'structured_result', 'title'),
            _nested(result, 'pdf', 'topic_title'),
            _nested(params, 'report_audio_workflow', 'pdf', 'filename'),
            _nested(params, 'report_audio_workflow', 'pdf', 'topic_title'),
            _nested(params, 'report_script_material_workflow', 'pdf', 'filename'),
            _nested(params, 'report_script_material_workflow', 'pdf', 'topic_title'),
        ]
        generic_fallback = None
        for value in candidates:
            text = _compact_text(value, 500)
            if text:
                if _is_generic_report_title(text):
                    generic_fallback = generic_fallback or text
                    continue
                return text
        return generic_fallback

    def _sync_report_audio_projection(self, params=None, result=None):
        params_loaded = params is not None
        result_loaded = result is not None
        params = params if isinstance(params, dict) else {}
        result = result if isinstance(result, dict) else {}
        workflow = params.get('report_audio_workflow') if isinstance(params.get('report_audio_workflow'), dict) else {}
        if not workflow:
            workflow = (
                params.get('report_script_material_workflow')
                if isinstance(params.get('report_script_material_workflow'), dict)
                else {}
            )
        if params_loaded:
            request_pdf = workflow.get('pdf') if isinstance(workflow.get('pdf'), dict) else {}
            self.report_audio_pdf_attachment_id = _compact_text(request_pdf.get('attachment_id'), 36)
            self.report_audio_pdf_filename = _compact_text(request_pdf.get('filename'), 500)
            self.report_audio_pdf_bytes = _compact_text(request_pdf.get('bytes'), 40)
            self.report_audio_pdf_topic_title = _compact_text(request_pdf.get('topic_title'), 500)
            self.report_audio_metadata_title = _compact_text(_nested(workflow, 'metadata', 'title'), 500)
            self.report_audio_video_title = _compact_text(_nested(workflow, 'video', 'title'), 500)

        if result_loaded:
            result_pdf = result.get('pdf') if isinstance(result.get('pdf'), dict) else {}
            structured = result.get('structured_result') if isinstance(result.get('structured_result'), dict) else {}
            audio = result.get('audio') if isinstance(result.get('audio'), dict) else {}
            video = result.get('video') if isinstance(result.get('video'), dict) else {}

            self.report_audio_result_pdf_filename = _compact_text(result_pdf.get('filename'), 500)
            self.report_audio_result_pdf_bytes = _compact_text(result_pdf.get('bytes'), 40)
            self.report_audio_result_pdf_topic_title = _compact_text(result_pdf.get('topic_title'), 500)
            self.report_audio_cover_title = _compact_text(structured.get('cover_title'), 500)
            self.report_audio_cover_subtitle = _compact_text(
                structured.get('cover_subtitle') or video.get('cover_subtitle'),
                220,
            )
            self.report_audio_structured_title = _compact_text(structured.get('title'), 500)
            self.report_audio_started_at = _compact_text(result.get('started_at'), 64)
            self.report_audio_completed_at = _compact_text(result.get('completed_at'), 64)
            self.report_audio_error = _compact_text(result.get('error'))
            self.report_audio_output_path = _compact_text(result.get('output_path'), 600)
            self.report_audio_wav_path = _compact_text(audio.get('wav_path'), 600)
            self.report_audio_seconds = _compact_text(audio.get('seconds'), 40)
            self.report_audio_audio_bytes = _compact_text(audio.get('bytes'), 40)
            self.report_audio_video_enabled = _boolish(video.get('enabled'))
            self.report_audio_video_status = _compact_text(video.get('status'), 80)
            self.report_audio_video_publisher_task_id = _compact_text(video.get('publisher_task_id'), 36)
            self.report_audio_video_message = _compact_text(video.get('message'))
            self.report_audio_video_path = _compact_text(video.get('video_path'), 600)
            self.report_audio_cover_path = _compact_text(video.get('cover_path'), 600)
            self.final_script_chars = _script_chars_from_result(result)

    def _sync_workflow_state_projection(self, result):
        waiting_child = result.get('waiting_child') if isinstance(result.get('waiting_child'), dict) else {}
        retry_state = result.get('retry_state') if isinstance(result.get('retry_state'), dict) else {}
        latest_retry = None
        for value in retry_state.values():
            if isinstance(value, dict):
                latest_retry = value
        self.waiting_child_label = _compact_text(waiting_child.get('label'), 80)
        self.waiting_child_id = _compact_text(
            waiting_child.get('child_id') or waiting_child.get('task_id') or waiting_child.get('id'),
            36,
        )
        self.waiting_child_status = _compact_text(waiting_child.get('status'), 40)
        self.waiting_child_step = _compact_text(waiting_child.get('step'), 80)
        self.waiting_child_updated_at = _compact_text(waiting_child.get('updated_at'), 64)
        self.retry_state_status = _compact_text(
            latest_retry.get('status') if isinstance(latest_retry, dict) else None,
            40,
        )

    def to_dict(self):
        # Extract a human-readable title
        title = "未命名任务"
        if self.params:
            if isinstance(self.params, dict):
                title = self.params.get('bilibili', {}).get('title')
                if not title:
                    main_title = self.params.get('thumbnail', {}).get('main_title', '')
                    sub_title = self.params.get('thumbnail', {}).get('sub_title', '')
                    
                    parts = [l.strip() for l in main_title.split('\n') if l.strip()]
                    if sub_title.strip():
                        parts.append(sub_title.strip())
                    
                    title = " | ".join(parts) if parts else "未命名任务"
                if title == "未命名任务" and self.params.get('feature') == 'content_pdf_clean':
                    title = self.params.get('content_pipeline', {}).get('title') or "PDF 清洗任务"
                if title == "未命名任务" and self.params.get('feature') == 'content_audio_clean':
                    title = self.params.get('content_pipeline', {}).get('title') or "音频清洗任务"
                if title == "未命名任务" and self.params.get('feature') == 'zsxq_clean_output_publish':
                    title = self.params.get('zsxq_publish', {}).get('title') or "知识星球发布任务"
                if title == "未命名任务" and self.params.get('feature') == 'mp3_video_workflow':
                    workflow = self.params.get('mp3_video_workflow', {})
                    audio = workflow.get('audio', {}) if isinstance(workflow, dict) else {}
                    title = audio.get('topic_title') or audio.get('filename') or "MP3 视频编排任务"
                if title == "未命名任务" and self.params.get('feature') == 'report_top10_workflow':
                    workflow = self.params.get('report_top10_workflow', {})
                    title = workflow.get('program_name') or "今日研报 Top10 编排"
                if title == "未命名任务" and self.params.get('feature') == 'report_script_material_workflow':
                    workflow = self.params.get('report_script_material_workflow', {})
                    pdf = workflow.get('pdf', {}) if isinstance(workflow, dict) else {}
                    title = (
                        workflow.get('metadata', {}).get('title')
                        if isinstance(workflow.get('metadata'), dict)
                        else None
                    ) or pdf.get('topic_title') or pdf.get('filename') or "研报基础文案物料"
        
        return {
            'id': self.id,
            'title': title, # Added title
            'celery_task_id': self.celery_task_id,
            'status': self.status,
            'current_step': self.current_step,
            'progress': self.progress,
            'params': self.params,
            'result': self.result,
            'error_message': self.error_message,
            'created_at': (self.created_at.isoformat() + "Z") if self.created_at else None,
            'updated_at': (self.updated_at.isoformat() + "Z") if self.updated_at else None,
            'completed_at': (self.completed_at.isoformat() + "Z") if self.completed_at else None
        }


def _loaded_model_attr(record, name, default=None):
    try:
        state = sqlalchemy_inspect(record)
        if name in state.unloaded:
            return default
    except Exception:
        pass
    try:
        return getattr(record, name, default)
    except Exception:
        return default


@event.listens_for(PublicationTask, "before_insert")
@event.listens_for(PublicationTask, "before_update")
def _sync_publication_task_projection(_mapper, _connection, target):
    target.sync_projection_fields()
