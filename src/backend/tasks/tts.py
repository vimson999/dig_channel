import os
import time
import logging
from datetime import datetime

from app import create_app
from celery_config import celery_app
from models import db
from models.task import PublicationTask
from models.tts import TTSArticle, TTSSegment
from services.doubao_tts_ws import DoubaoTTSWebSocketClient, write_srt
from utils.volc_tts import VolcTTS
from sqlalchemy.orm import load_only
from sqlalchemy.orm.attributes import flag_modified

logger = logging.getLogger("TTS_TASK")

SUPPORTED_DOUBAO_FORMATS = {'mp3', 'ogg_opus', 'pcm'}

TASK_UPDATE_COLUMNS = (
    PublicationTask.id,
    PublicationTask.status,
    PublicationTask.current_step,
    PublicationTask.progress,
    PublicationTask.result,
    PublicationTask.error_message,
    PublicationTask.completed_at,
)

TASK_PAYLOAD_COLUMNS = (
    *TASK_UPDATE_COLUMNS,
    PublicationTask.params,
    PublicationTask.feature,
    PublicationTask.user_id,
    PublicationTask.created_at,
)


def _load_publication_task(task_id, *columns):
    attrs = columns or TASK_PAYLOAD_COLUMNS
    return PublicationTask.query.options(load_only(*attrs)).get(task_id)


def _update_publication_task(task_id, **fields):
    task = _load_publication_task(task_id, *TASK_UPDATE_COLUMNS)
    if not task:
        raise ValueError(f"Publication task not found: {task_id}")
    for key, value in fields.items():
        setattr(task, key, value)
    if "result" in fields:
        flag_modified(task, "result")
    db.session.commit()
    return task


def _safe_title(title):
    value = "".join([c for c in title if c.isalnum() or c in (' ', '_', '-')]).strip()
    return value[:80] or 'tts'


def _relative_to_project(path, project_root):
    return os.path.relpath(path, project_root).replace(os.sep, '/')


def _doubao_extension(audio_format):
    return 'opus' if audio_format == 'ogg_opus' else audio_format


def _process_with_doubao_ws(seg, article, config, base_save_dir, project_root):
    client = DoubaoTTSWebSocketClient(
        api_key=config.get('api_key') or None,
        resource_id=config.get('resource_id') or None,
    )
    audio_format = (config.get('format') or 'mp3').strip()
    if audio_format not in SUPPORTED_DOUBAO_FORMATS:
        audio_format = 'mp3'

    result = client.synthesize(
        text=seg.text,
        speaker=config.get('voice_type') or config.get('speaker') or 'zh_female_cancan_mars_bigtts',
        model=config.get('model') or 'seed-tts-2.0-standard',
        audio_format=audio_format,
        sample_rate=int(config.get('sample_rate') or 24000),
        bit_rate=int(config.get('bit_rate') or 128000),
        speed_ratio=float(config.get('speed') or 1.0),
        loudness_ratio=float(config.get('loudness') or 1.0),
        emotion=config.get('emotion') or '',
        emotion_scale=int(config.get('emotion_scale') or 4),
        enable_subtitle=bool(config.get('enable_subtitle', True)),
        uid=f"tts_article_{article.id}",
    )
    if not result.audio:
        raise RuntimeError('豆包 TTS 未返回音频数据')

    extension = 'opus' if audio_format == 'ogg_opus' else audio_format
    filename = f"{datetime.now().strftime('%Y%m%d')}_{_safe_title(article.title)}_段落{seg.sequence:02d}.{extension}"
    save_path = os.path.join(base_save_dir, filename)
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    with open(save_path, 'wb') as file:
        file.write(result.audio)

    if result.subtitles:
        write_srt(result.subtitles, os.path.splitext(save_path)[0] + '.srt')

    seg.audio_path = _relative_to_project(save_path, project_root)
    return True


@celery_app.task(bind=True, name='tts.process_doubao_publication')
def process_doubao_tts_task(self, task_id):
    app = create_app()
    with app.app_context():
        return _process_doubao_tts_task(task_id)


def _process_doubao_tts_task(task_id):
    task = _load_publication_task(task_id, *TASK_PAYLOAD_COLUMNS)
    if not task:
        logger.error("未找到豆包TTS任务: %s", task_id)
        return False

    params = task.params or {}
    tts = params.get('tts') or {}
    config = tts.get('config') or {}
    title = tts.get('title') or '豆包语音合成'
    segments_text = [str(item).strip() for item in (tts.get('segments') or []) if str(item).strip()]
    total_count = len(segments_text)
    if not total_count:
        _update_publication_task(
            task_id,
            status='failed',
            current_step='failed',
            error_message='段落内容不能为空',
        )
        return False

    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    base_save_dir = os.path.join(project_root, 'data', 'processed', 'doubao_tts', task_id)
    os.makedirs(base_save_dir, exist_ok=True)

    current_progress = 3
    result_state = dict(task.result or {})
    existing_segments = result_state.get('segments') or []
    if len(existing_segments) != total_count:
        result_state = {'segments': [
            {
                'sequence': index + 1,
                'text': text,
                'status': 'waiting',
                'audio_path': None,
                'subtitle_path': None,
                'error_log': None,
            }
            for index, text in enumerate(segments_text)
        ]}
    _update_publication_task(
        task_id,
        status='processing',
        current_step='synthesis',
        progress=current_progress,
        result=result_state,
    )

    client = DoubaoTTSWebSocketClient(
        api_key=config.get('api_key') or None,
        resource_id=config.get('resource_id') or None,
    )
    success_count = 0
    result_segments = list(result_state.get('segments') or [])

    try:
        for index, text in enumerate(segments_text):
            sequence = index + 1
            result_segments[index]['status'] = 'processing'
            result_segments[index]['error_log'] = None
            current_progress = max(5, int(index / total_count * 92))
            _update_publication_task(
                task_id,
                progress=current_progress,
                result={'segments': result_segments},
            )

            try:
                audio_format = (config.get('format') or 'mp3').strip()
                if audio_format not in SUPPORTED_DOUBAO_FORMATS:
                    audio_format = 'mp3'

                response = client.synthesize(
                    text=text,
                    speaker=config.get('voice_type') or config.get('speaker') or 'zh_female_cancan_mars_bigtts',
                    model=config.get('model') or 'seed-tts-2.0-standard',
                    audio_format=audio_format,
                    sample_rate=int(config.get('sample_rate') or 24000),
                    bit_rate=int(config.get('bit_rate') or 128000),
                    speed_ratio=float(config.get('speed') or 1.0),
                    loudness_ratio=float(config.get('loudness') or 1.0),
                    emotion=config.get('emotion') or '',
                    emotion_scale=int(config.get('emotion_scale') or 4),
                    enable_subtitle=bool(config.get('enable_subtitle', True)),
                    uid=f"doubao_tts_{task_id}",
                )
                if not response.audio:
                    raise RuntimeError('豆包 TTS 未返回音频数据')

                extension = _doubao_extension(audio_format)
                filename = f"{sequence:02d}_{_safe_title(title)}.{extension}"
                audio_path = os.path.join(base_save_dir, filename)
                with open(audio_path, 'wb') as file:
                    file.write(response.audio)

                subtitle_path = None
                if response.subtitles:
                    candidate = os.path.splitext(audio_path)[0] + '.srt'
                    if write_srt(response.subtitles, candidate):
                        subtitle_path = candidate

                result_segments[index].update({
                    'status': 'success',
                    'audio_path': audio_path,
                    'subtitle_path': subtitle_path,
                    'error_log': None,
                })
                success_count += 1
            except Exception as exc:
                result_segments[index].update({
                    'status': 'failed',
                    'audio_path': None,
                    'subtitle_path': None,
                    'error_log': str(exc),
                })
                logger.error("豆包TTS段落 %s 失败: %s", sequence, exc)

            current_progress = min(96, int(sequence / total_count * 96))
            _update_publication_task(
                task_id,
                progress=current_progress,
                result={'segments': result_segments},
            )
            time.sleep(0.3)

        _update_publication_task(
            task_id,
            status='completed' if success_count == total_count else ('failed' if success_count == 0 else 'partial_failed'),
            current_step='done' if success_count else 'failed',
            progress=100 if success_count else current_progress,
            completed_at=datetime.utcnow(),
            result={'segments': result_segments, 'success_count': success_count, 'total_count': total_count},
        )
        logger.info("豆包TTS任务完成: %s %s/%s", task_id, success_count, total_count)
        return True
    except Exception as exc:
        _update_publication_task(
            task_id,
            status='failed',
            current_step='failed',
            completed_at=datetime.utcnow(),
            error_message=str(exc),
            result={'segments': result_segments, 'success_count': success_count, 'total_count': total_count},
        )
        logger.error("豆包TTS任务异常: %s", exc, exc_info=True)
        return False


def _process_with_legacy_volc(seg, article, config, base_save_dir, project_root):
    volc = VolcTTS()
    success, result = volc.synthesize(
        text=seg.text,
        voice_type=config.get('voice_type', 'bv001_streaming'),
        speed_ratio=config.get('speed', 1.0),
    )
    if not success:
        raise RuntimeError(result)

    filename = f"{datetime.now().strftime('%Y%m%d')}_{_safe_title(article.title)}_段落{seg.sequence:02d}.mp3"
    save_path = os.path.join(base_save_dir, filename)
    if not volc.save_to_file(result, save_path):
        raise RuntimeError("文件保存本地失败")
    seg.audio_path = _relative_to_project(save_path, project_root)
    return True


@celery_app.task(bind=True, name='tts.process_article')
def process_tts_article(self, article_id):
    """
    处理TTS文章合成任务 (串行处理所有段落)
    """
    app = create_app()
    with app.app_context():
        return _process_tts_article(article_id)


def _process_tts_article(article_id):
    article = TTSArticle.query.get(article_id)
    if not article:
        logger.error("未找到TTS文章任务: %s", article_id)
        return False

    article.status = 'processing'
    db.session.commit()

    segments = TTSSegment.query.filter_by(article_id=article_id).order_by(TTSSegment.sequence).all()
    success_count = 0
    total_count = len(segments)
    
    # 获取音频保存基路径 (根据项目规范使用 instance/downloads/tts)
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    base_save_dir = os.path.join(project_root, 'instance', 'downloads', 'tts', datetime.now().strftime('%Y%m%d'))
    os.makedirs(base_save_dir, exist_ok=True)

    logger.info(f"🎤 开始处理文章: {article.title}, 共 {total_count} 段")

    config = article.config or {}
    provider = config.get('provider') or 'legacy_volc'

    try:
        for seg in segments:
            seg.status = 'processing'
            db.session.commit()

            logger.info(f"🔄 正在处理段落 {seg.sequence}/{total_count}")
            
            try:
                if provider == 'doubao_ws_v3':
                    _process_with_doubao_ws(seg, article, config, base_save_dir, project_root)
                else:
                    _process_with_legacy_volc(seg, article, config, base_save_dir, project_root)

                seg.status = 'success'
                seg.error_log = None
                success_count += 1
            except Exception as exc:
                seg.status = 'failed'
                seg.error_log = str(exc)
                logger.error("段落 %s 失败: %s", seg.sequence, exc)

            seg.processed_at = datetime.utcnow()
            db.session.commit()

            # 串行限制：QPS=1
            time.sleep(0.4 if provider == 'doubao_ws_v3' else 1.2)

        # 更新文章总体状态
        if success_count == total_count:
            article.status = 'completed'
        elif success_count == 0:
            article.status = 'failed'
        else:
            article.status = 'partial_failed'
        
        db.session.commit()
        logger.info(f"✅ 文章 {article.title} 处理完成: {success_count}/{total_count} 成功")
        return True

    except Exception as e:
        logger.error(f"❌ 任务执行异常: {str(e)}")
        article.status = 'failed'
        db.session.commit()
        return False
