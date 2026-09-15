import glob
import logging
import os
import re
from datetime import datetime

import yt_dlp

from models import DownloadTask, db

logger = logging.getLogger(__name__)


class DownloadCancelled(Exception):
    pass


class DownloadService:
    def __init__(self):
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        self.download_dir = os.path.join(base_dir, 'instance', 'downloads')
        os.makedirs(self.download_dir, exist_ok=True)

    @staticmethod
    def srt_to_text(source_path, target_path):
        """Convert SRT cues to readable text and collapse adjacent duplicates."""
        with open(source_path, encoding='utf-8-sig') as source:
            lines = source.readlines()
        output = []
        previous = None
        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.isdigit() or '-->' in line:
                continue
            line = re.sub(r'<[^>]+>', '', line).strip()
            if line and line != previous:
                output.append(line)
                previous = line
        with open(target_path, 'w', encoding='utf-8') as target:
            target.write('\n'.join(output) + ('\n' if output else ''))

    def _options(self, download_type, progress_hook):
        options = {
            'outtmpl': os.path.join(self.download_dir, '%(title)s.%(ext)s'),
            'progress_hooks': [progress_hook],
            'logger': logger,
            'noplaylist': True,
        }
        if download_type == 'video':
            options['format'] = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
        elif download_type == 'audio':
            options.update({
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
            })
        else:
            options.update({
                'skip_download': True,
                'writesubtitles': True,
                'writeautomaticsub': True,
                'subtitleslangs': ['zh-Hans', 'zh-Hant', 'zh.*', 'en.*'],
                'subtitlesformat': 'srt/best',
                'convertsubtitles': 'srt',
            })
        return options

    def _result_path(self, ydl, info, download_type):
        prepared = ydl.prepare_filename(info)
        base, _ = os.path.splitext(prepared)
        if download_type == 'audio':
            return base + '.mp3'
        if download_type in {'srt', 'text'}:
            matches = sorted(glob.glob(base + '*.srt'), key=os.path.getmtime, reverse=True)
            if not matches:
                raise RuntimeError('该视频没有可用字幕或自动字幕')
            if download_type == 'text':
                target = base + '.txt'
                self.srt_to_text(matches[0], target)
                return target
            return matches[0]
        requested = info.get('requested_downloads') or []
        if requested and requested[0].get('filepath'):
            return requested[0]['filepath']
        return prepared

    def download(self, task_id, url, download_type='video'):
        task = DownloadTask.query.get(task_id)
        if not task:
            logger.error('Task %s not found', task_id)
            return None

        active_files = set()

        def ensure_not_cancelled():
            db.session.refresh(task)
            if task.status == 'cancelled':
                raise DownloadCancelled('下载已由用户停止')

        def cleanup_partial_files():
            download_root = os.path.realpath(self.download_dir) + os.sep
            for candidate in active_files:
                if not candidate:
                    continue
                for path in {candidate, candidate + '.part', *glob.glob(candidate + '.part-Frag*')}:
                    real_path = os.path.realpath(path)
                    if real_path.startswith(download_root) and os.path.isfile(real_path):
                        try:
                            os.remove(real_path)
                        except OSError as exc:
                            logger.warning('Failed to clean cancelled download %s: %s', real_path, exc)

        def progress_hook(data):
            filename = data.get('filename') or data.get('tmpfilename')
            if filename:
                active_files.add(filename)
            ensure_not_cancelled()
            if data['status'] == 'downloading':
                try:
                    percent = data.get('_percent_str', '0%').replace('%', '').strip()
                    task.progress = float(percent)
                    task.status = 'downloading'
                    db.session.commit()
                except Exception as exc:
                    logger.error('Progress hook error: %s', exc)
            elif data['status'] == 'finished':
                task.progress = 100.0
                task.status = 'processing'
                db.session.commit()

        try:
            ensure_not_cancelled()
            with yt_dlp.YoutubeDL(self._options(download_type, progress_hook)) as ydl:
                task.status = 'downloading'
                info = ydl.extract_info(url, download=True)
                task.title = info.get('title')
                task.thumbnail = info.get('thumbnail')
                task.duration = info.get('duration')
                filepath = self._result_path(ydl, info, download_type)
                if not os.path.exists(filepath):
                    raise RuntimeError('下载完成但未找到输出文件')
                task.filename = os.path.basename(filepath)
                task.filepath = filepath
                task.file_size = os.path.getsize(filepath)
                task.progress = 100.0
                task.status = 'completed'
                task.completed_at = datetime.utcnow()
                db.session.commit()
                return task.to_dict()
        except DownloadCancelled:
            db.session.rollback()
            cleanup_partial_files()
            task = DownloadTask.query.get(task_id)
            if task:
                task.status = 'cancelled'
                task.error_message = None
                db.session.commit()
            return None
        except Exception as exc:
            logger.error('Download error: %s', exc)
            task.status = 'failed'
            task.error_message = str(exc)
            db.session.commit()
            raise

    def download_video(self, task_id, url):
        """Compatibility entry point for existing video-only callers."""
        return self.download(task_id, url, 'video')


download_service = DownloadService()
