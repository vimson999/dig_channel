"""持久化音频转写消费者；文件处理与 ASR 始终在数据库事务之外执行。"""
import logging
from datetime import datetime
import fcntl

from celery_config import celery_app
from models import db
from models.task import PublicationTask
from services import audio_transcription as service
from services.runtime_guard import guard_publication_task_start, is_retryable_db_error, release_db_session_quietly

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name='tasks.audio_transcription.process_job', max_retries=None)
def process_job(self, task_id, db_retry_count=0):
    from app import create_app
    app = create_app(register_apis=False)
    with app.app_context():
        try:
            delivery_id = str(self.request.id or '')
            row = db.session.query(PublicationTask.status, PublicationTask.celery_task_id).filter(
                PublicationTask.id == task_id, PublicationTask.feature == service.FEATURE_NAME).first()
            valid = bool(row and row.status in ('pending', 'processing') and row.celery_task_id == delivery_id)
            release_db_session_quietly()
            if not valid:
                return False
            if db_retry_count >= 5:
                # 业务执行重试有界；数据库恢复前保留消息，仅重试终态落库。
                update(task_id, delivery_id, status='failed', current_step='failed',
                       error_message='数据库连接多次失败，请稍后从历史记录重试。',
                       completed_at=datetime.utcnow())
                return False
            guard_publication_task_start(self, task_id, feature=service.FEATURE_NAME,
                                         queue=service.QUEUE_NAME)
            return execute(task_id, delivery_id)
        except Exception as exc:
            release_db_session_quietly()
            if not is_retryable_db_error(exc):
                raise
            delay = min(300, 15 * 2 ** min(db_retry_count, 5))
            logger.warning('音频转写等待数据库恢复 task=%s retry=%s delay=%ss',
                           task_id, db_retry_count, delay)
            raise self.retry(exc=exc, args=[task_id],
                             kwargs={'db_retry_count': min(db_retry_count + 1, 5)},
                             countdown=delay, max_retries=None)
        finally:
            release_db_session_quietly()


def execute(task_id, delivery_id):
    """通过投递 ID 与文件锁阻止过期消息及重复并发，允许丢失 worker 后重投。"""
    lock = None
    try:
        release_db_session_quietly()
        root = service.task_root(task_id, output=True)
        root.mkdir(parents=True, exist_ok=True)
        lock = (root / '.execution.lock').open('a')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        row = db.session.query(PublicationTask.params, PublicationTask.result, PublicationTask.status,
                               PublicationTask.celery_task_id).filter(
            PublicationTask.id == task_id, PublicationTask.feature == service.FEATURE_NAME).first()
        if not row or row.status not in ('pending', 'processing') or row.celery_task_id != delivery_id:
            return False
        source = dict(row.params[service.FEATURE_NAME])
        retry_history = list((row.result or {}).get('retry_history') or [])[-20:]
        release_db_session_quietly()
        if not update(task_id, delivery_id, status='processing', current_step='validating', progress=5):
            return False
        path = service.safe_file(service.task_root(task_id), source['source_path'])
        actual = service.fingerprint(path)
        if actual != {'bytes': source['source_bytes'], 'sha256': source['source_sha256']}:
            raise ValueError('原始音频缺失或内容已变化，请重新上传')
        duration = service.probe_audio(path)
        update(task_id, delivery_id, current_step='transcribing', progress=15)
        from services.asr_cache_service import transcribe_with_cache
        recognized = transcribe_with_cache(str(path), model_size='medium',
            initial_prompt=service.recognition_prompt(source.get('initial_prompt')),
            include_metadata=True)
        update(task_id, delivery_id, current_step='writing_outputs', progress=90)
        result = service.write_outputs(root, recognized['segments'])
        result['retry_history'] = retry_history
        result['duration_seconds'] = duration
        result['cache_hit'] = bool(recognized.get('metadata', {}).get('cache_hit'))
        return update(task_id, delivery_id, status='completed', current_step='completed',
                      progress=100, result=result, error_message=None, completed_at=datetime.utcnow())
    except Exception as exc:
        logger.exception('音频转写失败 task=%s', task_id)
        release_db_session_quietly()
        if is_retryable_db_error(exc):
            raise
        update(task_id, delivery_id, status='failed', current_step='failed',
               error_message='音频转写失败，请检查音频或识别服务后重试。', completed_at=datetime.utcnow())
        return False
    finally:
        release_db_session_quietly()
        if lock is not None:
            lock.close()


def update(task_id, delivery_id, **values):
    """窄写当前投递，提交后立即释放连接，不触发 ORM 全行回读。"""
    try:
        count = PublicationTask.query.filter(
            PublicationTask.id == task_id,
            PublicationTask.feature == service.FEATURE_NAME,
            PublicationTask.celery_task_id == delivery_id,
            PublicationTask.status.in_(('pending', 'processing')),
        ).update({**values, 'updated_at': datetime.utcnow()}, synchronize_session=False)
        db.session.commit()
        return bool(count)
    finally:
        release_db_session_quietly()
