"""
日志管理工具
提供日志轮转、清理和归档功能
"""
import os
import gzip
import shutil
import logging
from datetime import datetime, timedelta
from pathlib import Path
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from config.base import BaseConfig
from .colored_logger import setup_colored_logger

logger = setup_colored_logger(__name__)

class LogManager:
    def __init__(self, log_dir=None):
        self.log_dir = Path(log_dir) if log_dir else Path(BaseConfig.LOG_DIR)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # 日志保留策略
        self.retention_days = int(os.environ.get('LOG_RETENTION_DAYS', '30'))
        self.max_log_size = int(os.environ.get('MAX_LOG_SIZE_MB', '100')) * 1024 * 1024  # MB转字节
        self.max_backup_count = int(os.environ.get('MAX_BACKUP_COUNT', '10'))
    
    def setup_rotating_logger(self, name, filename, level=logging.INFO):
        """设置轮转日志记录器"""
        logger_instance = logging.getLogger(name)
        logger_instance.setLevel(level)

        log_file = self.log_dir / filename

        for handler in logger_instance.handlers:
            if isinstance(handler, RotatingFileHandler) and getattr(handler, 'baseFilename', None) == str(log_file):
                handler.setLevel(level)
                return logger_instance

        handler = RotatingFileHandler(
            str(log_file),
            maxBytes=self.max_log_size,
            backupCount=self.max_backup_count,
            encoding='utf-8'
        )
        
        # 设置日志格式
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        handler.setFormatter(formatter)
        logger_instance.addHandler(handler)
        
        return logger_instance
    
    def setup_timed_rotating_logger(self, name, filename, when='midnight', interval=1):
        """设置基于时间的轮转日志记录器"""
        logger_instance = logging.getLogger(name)
        logger_instance.setLevel(logging.INFO)

        log_file = self.log_dir / filename

        for handler in logger_instance.handlers:
            if isinstance(handler, TimedRotatingFileHandler) and getattr(handler, 'baseFilename', None) == str(log_file):
                return logger_instance

        handler = TimedRotatingFileHandler(
            str(log_file),
            when=when,
            interval=interval,
            backupCount=self.retention_days,
            encoding='utf-8'
        )
        
        # 设置日志格式
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        handler.setFormatter(formatter)
        logger_instance.addHandler(handler)
        
        return logger_instance
    
    def compress_old_logs(self):
        """压缩旧日志文件"""
        try:
            compressed_count = 0
            for log_file in self.log_dir.glob('*.log.*'):
                # 跳过已压缩的文件
                if log_file.suffix == '.gz':
                    continue
                
                # 检查文件是否超过1天
                if self._is_file_old(log_file, days=1):
                    compressed_path = log_file.with_suffix(log_file.suffix + '.gz')
                    
                    with open(log_file, 'rb') as f_in:
                        with gzip.open(compressed_path, 'wb') as f_out:
                            shutil.copyfileobj(f_in, f_out)
                    
                    # 删除原文件
                    log_file.unlink()
                    compressed_count += 1
                    logger.info(f"🗜️ 压缩日志文件: {log_file.name}")
            
            if compressed_count > 0:
                logger.info(f"✅ 压缩完成，共处理 {compressed_count} 个日志文件")
        
        except Exception as e:
            logger.error(f"❌ 压缩日志文件失败: {e}")
    
    def clean_old_logs(self):
        """清理过期日志文件"""
        try:
            deleted_count = 0
            cutoff_date = datetime.now() - timedelta(days=self.retention_days)
            
            # 清理普通日志文件
            for log_file in self.log_dir.glob('*.log.*'):
                if self._is_file_old(log_file, days=self.retention_days):
                    log_file.unlink()
                    deleted_count += 1
                    logger.info(f"🗑️ 删除过期日志: {log_file.name}")
            
            # 清理压缩日志文件
            for gz_file in self.log_dir.glob('*.gz'):
                if self._is_file_old(gz_file, days=self.retention_days):
                    gz_file.unlink()
                    deleted_count += 1
                    logger.info(f"🗑️ 删除过期压缩日志: {gz_file.name}")
            
            if deleted_count > 0:
                logger.info(f"✅ 清理完成，共删除 {deleted_count} 个过期日志文件")
            else:
                logger.info("✅ 没有过期的日志文件需要清理")
        
        except Exception as e:
            logger.error(f"❌ 清理日志文件失败: {e}")
    
    def _is_file_old(self, file_path, days):
        """检查文件是否超过指定天数"""
        try:
            file_time = datetime.fromtimestamp(file_path.stat().st_mtime)
            cutoff_time = datetime.now() - timedelta(days=days)
            return file_time < cutoff_time
        except:
            return False
    
    def get_log_stats(self):
        """获取日志统计信息"""
        try:
            total_size = 0
            file_count = 0
            compressed_count = 0
            
            for log_file in self.log_dir.glob('*'):
                if log_file.is_file():
                    file_count += 1
                    total_size += log_file.stat().st_size
                    
                    if log_file.suffix == '.gz':
                        compressed_count += 1
            
            return {
                'total_files': file_count,
                'total_size_mb': round(total_size / 1024 / 1024, 2),
                'compressed_files': compressed_count,
                'log_dir': str(self.log_dir)
            }
        
        except Exception as e:
            logger.error(f"❌ 获取日志统计失败: {e}")
            return {}
    
    def maintenance(self):
        """执行日志维护任务"""
        logger.info("🔧 开始日志维护任务...")
        
        # 获取维护前的统计
        stats_before = self.get_log_stats()
        logger.info(f"📊 维护前统计: {stats_before}")
        
        # 压缩旧日志
        self.compress_old_logs()
        
        # 清理过期日志
        self.clean_old_logs()
        
        # 获取维护后的统计
        stats_after = self.get_log_stats()
        logger.info(f"📊 维护后统计: {stats_after}")
        
        # 计算节省的空间
        if stats_before.get('total_size_mb', 0) > stats_after.get('total_size_mb', 0):
            saved_mb = stats_before['total_size_mb'] - stats_after['total_size_mb']
            logger.info(f"💾 节省磁盘空间: {saved_mb:.2f}MB")
        
        logger.info("✅ 日志维护任务完成")

# 全局日志管理器实例
log_manager = LogManager()

def _get_flag(config, key: str) -> bool:
    if config is None:
        return False
    if hasattr(config, 'get'):
        try:
            return bool(config.get(key, False))
        except Exception:
            pass
    return bool(getattr(config, key, False))


def setup_application_logging(config=None):
    """设置应用程序日志"""
    # 应用主日志
    log_manager.setup_timed_rotating_logger(
        'app', 'app.log', when='midnight', interval=1
    )
    
    # 错误日志
    log_manager.setup_rotating_logger(
        'error', 'error.log', level=logging.ERROR
    )
    
    # 慢查询日志
    log_manager.setup_rotating_logger(
        'slow_query', 'slow_query.log', level=logging.WARNING
    )
    
    # 性能日志
    log_manager.setup_timed_rotating_logger(
        'performance', 'performance.log', when='midnight', interval=1
    )
    
    # 安全日志
    log_manager.setup_rotating_logger(
        'security', 'security.log', level=logging.INFO
    )

    # Google API key轮询日志
    log_manager.setup_rotating_logger(
        'google_key_manager', 'google_key_manager.log', level=logging.INFO
    )

    # Bilibili 业务日志
    log_manager.setup_rotating_logger(
        'bilibili', 'bilibili.log', level=logging.INFO
    )

    # YouTube 业务日志
    log_manager.setup_rotating_logger(
        'youtube', 'youtube.log', level=logging.INFO
    )

    # 可选功能日志
    if _get_flag(config, 'ENABLE_TTS_API'):
        log_manager.setup_rotating_logger('TTS_API', 'tts.log')

    if _get_flag(config, 'ENABLE_MHTML_API'):
        log_manager.setup_rotating_logger('MHTML_PDF_API', 'mhtml_pdf.log')
        log_manager.setup_rotating_logger('MHTML_CONVERTER', 'mhtml_converter.log')
    
    logger.info("📝 应用程序日志配置完成")

def schedule_log_maintenance():
    """调度日志维护任务"""
    import threading
    import time
    
    def maintenance_worker():
        while True:
            try:
                # 每天凌晨2点执行维护
                now = datetime.now()
                next_run = now.replace(hour=2, minute=0, second=0, microsecond=0)
                if next_run <= now:
                    next_run += timedelta(days=1)
                
                sleep_seconds = (next_run - now).total_seconds()
                time.sleep(sleep_seconds)
                
                # 执行维护
                log_manager.maintenance()
                
            except Exception as e:
                logger.error(f"❌ 日志维护任务异常: {e}")
                time.sleep(3600)  # 出错后等待1小时再试
    
    # 启动维护线程
    maintenance_thread = threading.Thread(target=maintenance_worker, daemon=True)
    maintenance_thread.start()
    logger.info("⏰ 日志维护任务已启动")
