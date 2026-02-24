import os
import logging
import yt_dlp
from datetime import datetime
from models import db, DownloadTask

logger = logging.getLogger(__name__)

class DownloadService:
    def __init__(self):
        # Determine base directory
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        self.download_dir = os.path.join(base_dir, 'instance', 'downloads')
        if not os.path.exists(self.download_dir):
            os.makedirs(self.download_dir, exist_ok=True)

    def download_video(self, task_id, url):
        """
        Download video using yt-dlp.
        This should be called within a Celery task.
        """
        task = DownloadTask.query.get(task_id)
        if not task:
            logger.error(f"Task {task_id} not found")
            return

        def progress_hook(d):
            if d['status'] == 'downloading':
                try:
                    p = d.get('_percent_str', '0%').replace('%', '').strip()
                    task.progress = float(p)
                    task.status = 'downloading'
                    db.session.commit()
                except Exception as e:
                    logger.error(f"Progress hook error: {e}")
            elif d['status'] == 'finished':
                task.progress = 100.0
                task.status = 'processing'
                db.session.commit()

        ydl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'outtmpl': os.path.join(self.download_dir, '%(title)s.%(ext)s'),
            'progress_hooks': [progress_hook],
            'logger': logger,
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # Extract info first
                info = ydl.extract_info(url, download=False)
                task.title = info.get('title')
                task.thumbnail = info.get('thumbnail')
                task.duration = info.get('duration')
                task.status = 'downloading'
                db.session.commit()

                # Perform download
                ydl.download([url])
                
                # Update task with final file info
                info = ydl.extract_info(url, download=False)
                filename = ydl.prepare_filename(info)
                task.filename = os.path.basename(filename)
                task.filepath = filename
                task.file_size = os.path.getsize(filename) if os.path.exists(filename) else 0
                task.status = 'completed'
                task.completed_at = datetime.utcnow()
                db.session.commit()
                
                return task.to_dict()

        except Exception as e:
            logger.error(f"Download error: {e}")
            task.status = 'failed'
            task.error_message = str(e)
            db.session.commit()
            raise e

download_service = DownloadService()
