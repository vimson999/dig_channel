import os
import sys
import logging
from datetime import datetime

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

PROJECT_ROOT = os.path.dirname(os.path.dirname(backend_dir))

from celery_config import celery_app
from models.task import PublicationTask
from models import db

logger = logging.getLogger(__name__)


@celery_app.task(bind=True)
def run_podcast_visualizer_task(self, task_id):
    from app import create_app
    from apis.v1.documents import (
        PODCAST_TEMPLATE_PATH,
        _parse_srt_content,
        _prepare_podcast_audio,
        _probe_media_duration,
        PODCAST_VISUALIZER_RENDERER_VERSION,
        _render_podcast_text_overlay,
        _retime_segments_for_audio,
        _segments_from_cached_asr,
        _write_podcast_lyrics_ass,
        _generate_podcast_visualizer_video,
    )

    app = create_app()
    with app.app_context():
        task = PublicationTask.query.get(task_id)
        if not task:
            logger.error("Podcast visualizer task not found: %s", task_id)
            return

        try:
            params = task.params or {}
            podcast = params.get('podcast_visualizer', {})
            task.status = 'processing'
            task.current_step = 'prepare'
            task.progress = 5
            db.session.commit()

            audio_path = podcast.get('audio_path')
            if not audio_path or not os.path.exists(audio_path):
                raise FileNotFoundError('音频文件不存在')

            orientation = podcast.get('orientation', 'landscape')
            width, height = (1080, 1920) if orientation == 'portrait' else (1920, 1080)
            fps = int(podcast.get('fps', 30))
            crf = int(podcast.get('crf', 20))
            title = podcast.get('title') or '播客标题'
            subtitle = podcast.get('subtitle') or ''
            headline_subtitle = podcast.get('headline_subtitle') or ''
            brand_text = podcast.get('brand_text') or '胶囊播客'
            caption_text = podcast.get('caption_text') or ''
            audio_start = podcast.get('audio_start')
            audio_end = podcast.get('audio_end')
            audio_speed = podcast.get('audio_speed') or 1
            srt_path = podcast.get('srt_path')
            auto_asr = bool(podcast.get('auto_asr'))
            asr_model = podcast.get('asr_model') or 'medium'

            work_dir = os.path.join(PROJECT_ROOT, 'data', 'processed', 'podcast_visualizer', task_id)
            os.makedirs(work_dir, exist_ok=True)
            processed_audio_path = os.path.join(work_dir, 'processed_audio.wav')
            overlay_path = os.path.join(work_dir, 'overlay.png')
            ass_path = os.path.join(work_dir, 'lyrics.ass')
            output_path = os.path.join(work_dir, f'{task_id}.mp4')

            audio_meta = _prepare_podcast_audio(
                input_path=audio_path,
                output_path=processed_audio_path,
                start_seconds=audio_start,
                end_seconds=audio_end,
                speed=audio_speed,
            )
            duration = audio_meta['processed_duration']

            task.current_step = 'lyrics'
            task.progress = 18
            db.session.commit()

            lyrics_segments = []
            if srt_path and os.path.exists(srt_path):
                with open(srt_path, 'r', encoding='utf-8-sig', errors='ignore') as file:
                    lyrics_segments = _retime_segments_for_audio(
                        _parse_srt_content(file.read()),
                        start_seconds=audio_meta['start_seconds'],
                        end_seconds=audio_meta['end_seconds'],
                        speed=audio_meta['speed'],
                    )
            elif auto_asr:
                lyrics_segments = _segments_from_cached_asr(
                    audio_path,
                    model_size=asr_model,
                    start_seconds=audio_meta['start_seconds'],
                    end_seconds=audio_meta['end_seconds'],
                    speed=audio_meta['speed'],
                    prepared_audio_path=processed_audio_path,
                )
            elif caption_text:
                lyrics_segments = [{'start': 0, 'end': min(duration, 6), 'text': caption_text}]

            if not lyrics_segments:
                if auto_asr:
                    raise RuntimeError('本地 ASR 没有提取到有效字幕，已停止生成视频')
                raise RuntimeError('没有可用字幕：请上传 SRT、开启本地 ASR，或填写手动字幕')

            _write_podcast_lyrics_ass(lyrics_segments, ass_path, width, height)

            task.current_step = 'render'
            task.progress = 38
            db.session.commit()

            _render_podcast_text_overlay(
                output_path=overlay_path,
                width=width,
                height=height,
                title=title,
                subtitle=subtitle,
                brand_text=brand_text,
                series_text=headline_subtitle,
                accent_color='#b9895e',
                caption_text='',
            )

            task.current_step = 'encode'
            task.progress = 58
            db.session.commit()

            _generate_podcast_visualizer_video(
                background_path=str(PODCAST_TEMPLATE_PATH),
                overlay_path=overlay_path,
                audio_path=processed_audio_path,
                output_path=output_path,
                width=width,
                height=height,
                duration=duration,
                fps=fps,
                crf=crf,
                brand_text=brand_text,
                lyrics_ass_path=ass_path,
            )

            task.status = 'completed'
            task.current_step = 'done'
            task.progress = 100
            task.completed_at = datetime.utcnow()
            task.result = {
                'video_path': output_path,
                'download_url': f'/api/v1/documents/podcast-visualizer/task/{task_id}/download',
                'duration': duration,
                'source_duration': audio_meta.get('source_duration'),
                'audio_start': audio_meta.get('start_seconds'),
                'audio_end': audio_meta.get('end_seconds'),
                'audio_speed': audio_meta.get('speed'),
                'lyrics_count': len(lyrics_segments),
                'renderer_version': PODCAST_VISUALIZER_RENDERER_VERSION,
            }
            db.session.commit()
            logger.info("Podcast visualizer task completed: %s", task_id)
            return {'status': 'completed', 'task_id': task_id}

        except Exception as exc:
            logger.error("Podcast visualizer task failed: %s", exc, exc_info=True)
            task.status = 'failed'
            task.current_step = 'failed'
            task.error_message = str(exc)
            task.updated_at = datetime.utcnow()
            db.session.commit()
            raise
