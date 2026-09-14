from datetime import datetime
import uuid
from sqlalchemy import event, inspect as sqlalchemy_inspect

from . import db

_UNLOADED = object()
_SOURCE_TASK_ID_MAX_LENGTH = 36


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


def _gemini_response_chars_from_result(result):
    response = result.get('final_response_text') or result.get('response_text')
    return len("".join(str(response or "").split()))


def _non_negative_int(value):
    try:
        return max(0, int(float(value or 0)))
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
    # Report visual workflow strategy.  This is a hot routing key used by list,
    # polling and retry paths; keep it out of large params/result JSON reads.
    report_visual_scheme_key = db.Column(db.String(64), nullable=True)

    # Content-clean terminal projections. Workflow polling and clean->publish
    # handoff only need these bounded fields; the full file audit remains in
    # result for detail pages and troubleshooting.
    content_clean_output_dir = db.Column(db.String(600), nullable=True)
    content_clean_files_count = db.Column(db.Integer, nullable=True)
    content_clean_success_count = db.Column(db.Integer, nullable=True)
    content_clean_failed_count = db.Column(db.Integer, nullable=True)
    content_clean_message = db.Column(db.Text, nullable=True)
    content_submission_channel = db.Column(db.String(40), nullable=True)
    content_web_submission_id = db.Column(db.String(36), nullable=True)
    content_submission_input_mode = db.Column(db.String(16), nullable=True)

    # Publisher vertical-video and Doubao TTS child projections. These fields
    # keep child polling, cache matching, and artifact lookup off params/result.
    publisher_vertical_enabled = db.Column(db.Boolean, nullable=True)
    publisher_landscape_enabled = db.Column(db.Boolean, nullable=True)
    publisher_render_mode = db.Column(db.String(32), nullable=True)
    publisher_vertical_scheme = db.Column(db.String(32), nullable=True)
    publisher_preview_seconds = db.Column(db.Integer, nullable=False, default=0)
    publisher_vertical_video_path = db.Column(db.String(600), nullable=True)
    publisher_vertical_cover_path = db.Column(db.String(600), nullable=True)
    publisher_podcast_cover_path = db.Column(db.String(600), nullable=True)
    publisher_bilibili_cover_path = db.Column(db.String(600), nullable=True)
    publisher_child_role = db.Column(db.String(80), nullable=True)
    publisher_aspect_ratio = db.Column(db.String(16), nullable=True)
    publisher_generate_subtitles = db.Column(db.Boolean, nullable=True)
    publisher_audio_signature = db.Column(db.String(64), nullable=True)
    doubao_tts_text_hash = db.Column(db.String(64), nullable=True)
    doubao_tts_chunk_index = db.Column(db.Integer, nullable=False, default=0)
    doubao_tts_chunk_attempt = db.Column(db.Integer, nullable=False, default=0)
    doubao_tts_wav_path = db.Column(db.String(600), nullable=True)
    gemini_response_chars = db.Column(db.Integer, nullable=False, default=0)

    # MP3 workflow list projection. Detail and audit payloads remain in JSON,
    # while list pages can render the complete audio summary from normal columns.
    mp3_audio_filename = db.Column(db.String(500), nullable=True)
    mp3_audio_topic_title = db.Column(db.String(500), nullable=True)
    mp3_audio_tab_label = db.Column(db.String(160), nullable=True)
    mp3_audio_path = db.Column(db.String(600), nullable=True)
    mp3_audio_download_count = db.Column(db.Integer, nullable=True)
    mp3_audio_duration_seconds = db.Column(db.Float, nullable=True)

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
    report_audio_landscape_keyframe_path = db.Column(db.String(600), nullable=True)
    report_audio_vertical_cover_path = db.Column(db.String(600), nullable=True)
    report_audio_podcast_cover_path = db.Column(db.String(600), nullable=True)
    report_audio_bilibili_cover_path = db.Column(db.String(600), nullable=True)
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
            self.source_feature = _compact_text(params.get('source_feature'), 80)
            self.report_visual_scheme_key = _compact_text(
                params.get('visual_scheme_key')
                or _nested(params, 'report_stick_figure_video_workflow', 'visual_scheme_key')
                or _nested(params, 'report_visual_plan', 'visual_scheme_key'),
                64,
            )

            zsxq_publish = params.get('zsxq_publish') if isinstance(params.get('zsxq_publish'), dict) else {}
            mp3_workflow = params.get('mp3_video_workflow') if isinstance(params.get('mp3_video_workflow'), dict) else {}
            mp3_audio = mp3_workflow.get('audio') if isinstance(mp3_workflow.get('audio'), dict) else {}
            projected_source_task_id = _source_task_id_projection(
                self,
                params.get('source_task_id')
                or zsxq_publish.get('source_task_id')
                or mp3_workflow.get('audio_asset_id')
                or mp3_audio.get('attachment_id')
                or mp3_audio.get('audio_asset_id')
                or (result.get('source_task_id') if result_loaded else None),
            )
            if projected_source_task_id is not _UNLOADED:
                self.source_task_id = projected_source_task_id
        elif result_loaded and result.get('source_task_id'):
            projected_source_task_id = _source_task_id_projection(
                self,
                result.get('source_task_id'),
            )
            if projected_source_task_id is not _UNLOADED:
                self.source_task_id = projected_source_task_id

        if result_loaded:
            self.task_video_path = _compact_text(result.get('video_path'), 600)
            self.gemini_response_chars = _gemini_response_chars_from_result(result)

        title = self._derive_task_title(params if params_loaded else {}, result if result_loaded else {})
        if title or (params_loaded and result_loaded):
            self.task_title = title

        self._sync_report_audio_projection(
            params if params_loaded else None,
            result if result_loaded else None,
        )
        self._sync_child_hot_projection(
            params if params_loaded else None,
            result if result_loaded else None,
        )
        self._sync_mp3_hot_projection(
            params if params_loaded else None,
            result if result_loaded else None,
        )
        if result_loaded:
            self._sync_content_clean_projection(result)
        if params_loaded:
            self._sync_content_submission_projection(params)
        if result_loaded:
            self._sync_workflow_state_projection(result)
        return self

    def _derive_task_title(self, params, result):
        candidates = [
            _nested(params, 'audio_transcription', 'original_filename'),
            _nested(params, 'bilibili', 'title'),
            _nested(params, 'thumbnail', 'main_title'),
            _nested(params, 'content_pipeline', 'title'),
            _nested(params, 'pdf_merge_workflow', 'title'),
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
        if not workflow:
            workflow = (
                params.get('report_stick_figure_video_workflow')
                if isinstance(params.get('report_stick_figure_video_workflow'), dict)
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
            keyframes = video.get('keyframes') if isinstance(video.get('keyframes'), dict) else {}
            landscape_cover = keyframes.get('landscape_cover') if isinstance(keyframes.get('landscape_cover'), dict) else {}
            vertical_cover = keyframes.get('vertical_cover') if isinstance(keyframes.get('vertical_cover'), dict) else {}
            podcast_cover = keyframes.get('podcast_cover') if isinstance(keyframes.get('podcast_cover'), dict) else {}
            bilibili_cover = keyframes.get('bilibili_cover') if isinstance(keyframes.get('bilibili_cover'), dict) else {}

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
            self.report_audio_landscape_keyframe_path = _compact_text(
                landscape_cover.get('path'),
                600,
            )
            self.report_audio_vertical_cover_path = _compact_text(
                video.get('vertical_cover_path') or vertical_cover.get('path'),
                600,
            )
            self.report_audio_podcast_cover_path = _compact_text(
                video.get('podcast_cover_path') or podcast_cover.get('path'),
                600,
            )
            self.report_audio_bilibili_cover_path = _compact_text(
                video.get('bilibili_cover_path') or bilibili_cover.get('path'),
                600,
            )
            self.final_script_chars = _script_chars_from_result(result)

    def _sync_child_hot_projection(self, params=None, result=None):
        params_loaded = params is not None
        result_loaded = result is not None
        params = params if isinstance(params, dict) else {}
        result = result if isinstance(result, dict) else {}
        is_publisher = _loaded_model_attr(self, 'feature') == 'publisher'

        if params_loaded:
            if is_publisher:
                landscape = params.get('video') if isinstance(params.get('video'), dict) else {}
                vertical = params.get('vertical_video') if isinstance(params.get('vertical_video'), dict) else {}
                metadata = params.get('metadata') if isinstance(params.get('metadata'), dict) else {}
                self.publisher_landscape_enabled = _boolish(landscape.get('enabled'))
                self.publisher_render_mode = _compact_text(
                    metadata.get('render_mode') or params.get('render_mode') or 'full_video',
                    32,
                )
                self.publisher_vertical_enabled = _boolish(vertical.get('enabled'))
                self.publisher_vertical_scheme = _compact_text(vertical.get('scheme'), 32)
                self.publisher_preview_seconds = _non_negative_int(vertical.get('preview_seconds'))
                self.publisher_child_role = _compact_text(metadata.get('role'), 80)
                self.publisher_aspect_ratio = _compact_text(params.get('aspect_ratio'), 16)
                generate_subtitles = params.get('generate_subtitles')
                if generate_subtitles is None:
                    generate_subtitles = metadata.get('generate_subtitles')
                if generate_subtitles is None:
                    generate_subtitles = landscape.get('sub_burn_enabled')
                self.publisher_generate_subtitles = _boolish(generate_subtitles)
                self.publisher_audio_signature = _compact_text(metadata.get('audio_signature'), 64)

            doubao = params.get('doubao_browser_tts') if isinstance(params.get('doubao_browser_tts'), dict) else {}
            doubao_metadata = doubao.get('metadata') if isinstance(doubao.get('metadata'), dict) else {}
            self.doubao_tts_text_hash = _compact_text(doubao_metadata.get('text_hash'), 64)
            self.doubao_tts_chunk_index = _non_negative_int(doubao_metadata.get('chunk_index'))
            self.doubao_tts_chunk_attempt = _non_negative_int(doubao_metadata.get('chunk_attempt'))

        if result_loaded:
            metadata = result.get('metadata') if isinstance(result.get('metadata'), dict) else {}
            audio = result.get('audio') if isinstance(result.get('audio'), dict) else {}
            if is_publisher and (metadata.get('render_mode') or result.get('render_mode')):
                self.publisher_render_mode = _compact_text(
                    metadata.get('render_mode') or result.get('render_mode'),
                    32,
                )
            if is_publisher:
                if metadata.get('role'):
                    self.publisher_child_role = _compact_text(metadata.get('role'), 80)
                if result.get('aspect_ratio'):
                    self.publisher_aspect_ratio = _compact_text(result.get('aspect_ratio'), 16)
                if result.get('generate_subtitles') is not None:
                    self.publisher_generate_subtitles = _boolish(result.get('generate_subtitles'))
                if metadata.get('audio_signature') or result.get('audio_signature'):
                    self.publisher_audio_signature = _compact_text(
                        metadata.get('audio_signature') or result.get('audio_signature'),
                        64,
                    )
            vertical_enabled = _loaded_model_attr(self, 'publisher_vertical_enabled')
            if metadata.get('vertical_video_scheme'):
                self.publisher_vertical_scheme = _compact_text(metadata.get('vertical_video_scheme'), 32)
            if metadata.get('preview_seconds') is not None:
                self.publisher_preview_seconds = _non_negative_int(metadata.get('preview_seconds'))
            self.publisher_vertical_video_path = _compact_text(
                result.get('vertical_video_path')
                or (result.get('video_path') if vertical_enabled else None),
                600,
            )
            self.publisher_vertical_cover_path = _compact_text(result.get('vertical_cover_path'), 600)
            self.publisher_podcast_cover_path = _compact_text(result.get('podcast_cover_path'), 600)
            self.publisher_bilibili_cover_path = _compact_text(result.get('bilibili_cover_path'), 600)

            if metadata.get('text_hash'):
                self.doubao_tts_text_hash = _compact_text(metadata.get('text_hash'), 64)
            if metadata.get('chunk_index') is not None:
                self.doubao_tts_chunk_index = _non_negative_int(metadata.get('chunk_index'))
            if metadata.get('chunk_attempt') is not None:
                self.doubao_tts_chunk_attempt = _non_negative_int(metadata.get('chunk_attempt'))
            self.doubao_tts_wav_path = _compact_text(audio.get('wav_path') or result.get('wav_path'), 600)

    def _sync_mp3_hot_projection(self, params=None, result=None):
        params = params if isinstance(params, dict) else {}
        result = result if isinstance(result, dict) else {}
        if _loaded_model_attr(self, 'feature') != 'mp3_video_workflow':
            return

        workflow = params.get('mp3_video_workflow') if isinstance(params.get('mp3_video_workflow'), dict) else {}
        request_audio = workflow.get('audio') if isinstance(workflow.get('audio'), dict) else {}
        result_audio = result.get('audio') if isinstance(result.get('audio'), dict) else {}
        audio = {**request_audio, **result_audio}
        if not audio:
            return

        self.mp3_audio_filename = _compact_text(audio.get('filename') or audio.get('name'), 500)
        self.mp3_audio_topic_title = _compact_text(audio.get('topic_title') or audio.get('title'), 500)
        self.mp3_audio_tab_label = _compact_text(audio.get('tab_label') or audio.get('tab'), 160)
        self.mp3_audio_path = _compact_text(audio.get('path'), 600)
        download_count = audio.get('download_count')
        self.mp3_audio_download_count = _non_negative_int(download_count) if download_count is not None else None
        try:
            duration = float(audio.get('duration_seconds')) if audio.get('duration_seconds') is not None else None
        except (TypeError, ValueError):
            duration = None
        self.mp3_audio_duration_seconds = max(0.0, duration) if duration is not None else None

    def _sync_content_clean_projection(self, result):
        if _loaded_model_attr(self, 'feature') not in {'content_pdf_clean', 'content_audio_clean'}:
            return
        result = result if isinstance(result, dict) else {}
        cleaned_files = result.get('cleaned_files') if isinstance(result.get('cleaned_files'), list) else []
        files = result.get('files') if isinstance(result.get('files'), list) else []
        files_count = result.get('files_count')
        success_count = result.get('success_count')
        failed_count = result.get('failed_count')
        self.content_clean_output_dir = _compact_text(result.get('local_output_dir'), 600)
        self.content_clean_files_count = _non_negative_int(
            files_count if files_count is not None else len(cleaned_files or files)
        )
        self.content_clean_success_count = _non_negative_int(
            success_count if success_count is not None else len(cleaned_files)
        )
        self.content_clean_failed_count = _non_negative_int(failed_count)
        self.content_clean_message = _compact_text(result.get('message'))

    def _sync_content_submission_projection(self, params):
        if _loaded_model_attr(self, 'feature') not in {'content_pdf_clean', 'content_audio_clean'}:
            return
        pipeline = params.get('content_pipeline') if isinstance(params.get('content_pipeline'), dict) else {}
        metadata = pipeline.get('metadata') if isinstance(pipeline.get('metadata'), dict) else {}
        self.content_submission_channel = _compact_text(
            metadata.get('submission_channel'),
            40,
        )
        self.content_web_submission_id = _compact_text(
            metadata.get('web_submission_id'),
            36,
        )
        self.content_submission_input_mode = _compact_text(
            metadata.get('input_mode'),
            16,
        )

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


def _source_task_id_projection(record, value):
    """Project an identity without ever creating a new truncated key.

    Eleven completed MP3 rows predate this contract and already contain the
    exact 36-character prefix of a typed ``pipeline:<uuid>`` reference.  They
    remain updateable, but the exception is deliberately restricted to that
    persisted legacy fingerprint.  Every new or changed overlong identity
    fails before PostgreSQL can reject it or the application can corrupt it.
    """
    text = _compact_text(value)
    if text is None or len(text) <= _SOURCE_TASK_ID_MAX_LENGTH:
        return text

    try:
        state = sqlalchemy_inspect(record)
    except Exception:
        state = None
    current = _loaded_model_attr(record, 'source_task_id', _UNLOADED)
    feature = _loaded_model_attr(record, 'feature', _UNLOADED)
    source_feature = _loaded_model_attr(record, 'source_feature', _UNLOADED)
    if (
        state is not None
        and state.persistent
        and current is not _UNLOADED
        and feature == 'mp3_video_workflow'
        and source_feature == 'zsxq_attachment'
        and text.startswith('pipeline:')
        and str(current or '').strip() == text[:_SOURCE_TASK_ID_MAX_LENGTH]
    ):
        return str(current).strip()

    raise ValueError(
        'source_task_id exceeds 36 characters; store the owner in '
        'source_feature and a canonical ID in source_task_id'
    )


@event.listens_for(PublicationTask, "before_insert")
@event.listens_for(PublicationTask, "before_update")
def _sync_publication_task_projection(_mapper, _connection, target):
    target.sync_projection_fields()
