import os
import time
import logging
from datetime import datetime
from celery import shared_task
from flask import current_app

from models import db
from models.tts import TTSArticle, TTSSegment
from utils.volc_tts import VolcTTS

logger = logging.getLogger("TTS_TASK")

@shared_task(bind=True, name='tts.process_article')
def process_tts_article(self, article_id):
    """
    处理TTS文章合成任务 (串行处理所有段落)
    """
    # 获取应用上下文以便操作数据库
    # 注意：在celery中通常需要手动推送app context
    # 这里假设celery_app已经正确配置了flask app context
    
    article = TTSArticle.query.get(article_id)
    if not article:
        logger.error(f"❌ 未找到TTS文章任务: {article_id}")
        return False

    article.status = 'processing'
    db.session.commit()

    segments = TTSSegment.query.filter_by(article_id=article_id).order_by(TTSSegment.sequence).all()
    volc = VolcTTS()
    
    success_count = 0
    total_count = len(segments)
    
    # 获取音频保存基路径 (根据项目规范使用 instance/downloads/tts)
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    base_save_dir = os.path.join(project_root, 'instance', 'downloads', 'tts', datetime.now().strftime('%Y%m%d'))
    os.makedirs(base_save_dir, exist_ok=True)

    logger.info(f"🎤 开始处理文章: {article.title}, 共 {total_count} 段")

    # 获取音色配置
    config = article.config or {}
    voice_type = config.get('voice_type', 'bv001_streaming')
    speed = config.get('speed', 1.0)

    try:
        for seg in segments:
            seg.status = 'processing'
            db.session.commit()

            logger.info(f"🔄 正在处理段落 {seg.sequence}/{total_count}")
            
            # 调用火山API
            success, result = volc.synthesize(
                text=seg.text, 
                voice_type=voice_type, 
                speed_ratio=speed
            )

            if success:
                # 命名规范: YYYYMMDD_标题_段落几.mp3
                # 安全文件名处理
                safe_title = "".join([c for c in article.title if c.isalnum() or c in (' ', '_')]).rstrip()
                filename = f"{datetime.now().strftime('%Y%m%d')}_{safe_title}_段落{seg.sequence:02d}.mp3"
                save_path = os.path.join(base_save_dir, filename)
                
                if volc.save_to_file(result, save_path):
                    seg.status = 'success'
                    # 存储相对于 instance 的路径
                    seg.audio_path = os.path.relpath(save_path, project_root)
                    success_count += 1
                else:
                    seg.status = 'failed'
                    seg.error_log = "文件保存本地失败"
            else:
                seg.status = 'failed'
                seg.error_log = result # 火山返回的错误信息
                logger.error(f"❌ 段落 {seg.sequence} 失败: {result}")

            seg.processed_at = datetime.utcnow()
            db.session.commit()

            # 串行限制：QPS=1
            time.sleep(1.2)

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
