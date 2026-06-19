# 定时任务调度器
"""
YouTube数据增量抓取定时任务

负责定时抓取所有活跃频道的最新数据
"""

import schedule
import time
import threading
from datetime import datetime, date, timedelta
from typing import List, Dict, Any
import logging
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler

from app import create_app
from services import YouTubeSyncService, ChannelService
from models import db, Channel, ChannelData
from utils.youtube_api import YouTubeAPI, YouTubeAPIError

# 配置日志
import os
logs_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), 'logs')
os.makedirs(logs_dir, exist_ok=True)
scheduler_log_path = os.path.join(logs_dir, 'scheduler.log')
scheduler_log_max_bytes = int(os.environ.get('SCHEDULER_LOG_MAX_MB', '50')) * 1024 * 1024
scheduler_log_backup_count = int(os.environ.get('SCHEDULER_LOG_BACKUP_COUNT', '5'))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler(
            scheduler_log_path,
            maxBytes=scheduler_log_max_bytes,
            backupCount=scheduler_log_backup_count,
            encoding='utf-8',
        ),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

class DataCrawlerScheduler:
    """数据抓取调度器"""
    
    def __init__(self):
        self.app = None
        self.sync_service = None
        self.running = False
        self.thread = None
        
        # 任务状态
        self.last_run_time = None
        self.last_run_status = None
        self.total_runs = 0
        self.successful_runs = 0
        self.failed_runs = 0
        
    def initialize(self):
        """初始化调度器"""
        try:
            # 创建应用上下文
            self.app = create_app()
            
            with self.app.app_context():
                # 初始化同步服务
                self.sync_service = YouTubeSyncService()
                
                # 测试API连接
                api_status = self.sync_service.get_api_status()
                if not api_status['api_available']:
                    raise Exception(f"YouTube API不可用: {api_status['message']}")
                
            logger.info("数据抓取调度器初始化成功")
            return True
            
        except Exception as e:
            logger.error(f"调度器初始化失败: {e}")
            return False
    
    @contextmanager
    def app_context(self):
        """应用上下文管理器"""
        with self.app.app_context():
            yield
    
    def schedule_tasks(self):
        """设置定时任务"""
        # 每天早上8点执行数据抓取
        schedule.every().day.at("08:00").do(self.daily_data_crawl)
        
        # 每天中午12点执行数据抓取
        schedule.every().day.at("12:00").do(self.daily_data_crawl)
        
        # 每天晚上8点执行数据抓取
        schedule.every().day.at("20:00").do(self.daily_data_crawl)
        
        # 每小时检查一次遗漏的数据
        schedule.every().hour.do(self.check_missing_data)
        
        # 每周日凌晨2点清理旧日志
        schedule.every().sunday.at("02:00").do(self.cleanup_old_logs)
        
        logger.info("定时任务已设置:")
        logger.info("- 每天 08:00, 12:00, 20:00 执行数据抓取")
        logger.info("- 每小时检查遗漏数据")
        logger.info("- 每周日 02:00 清理旧日志")
    
    def daily_data_crawl(self):
        """每日数据抓取任务"""
        start_time = datetime.now()
        logger.info(f"开始执行每日数据抓取任务: {start_time}")
        
        try:
            with self.app_context():
                # 获取今天的日期
                today = date.today()
                
                # 检查今天是否已经抓取过数据
                if self.is_data_already_crawled(today):
                    logger.info(f"今天({today})的数据已经抓取过，跳过本次任务")
                    return
                
                # 执行数据同步
                result = self.sync_service.sync_all_channels(today)
                
                # 记录任务执行结果
                self.total_runs += 1
                self.last_run_time = start_time
                
                if result['success']:
                    self.successful_runs += 1
                    self.last_run_status = 'success'
                    
                    data = result['data']
                    logger.info(f"数据抓取成功完成:")
                    logger.info(f"- 总频道数: {data['total_channels']}")
                    logger.info(f"- 成功: {data['success_count']}")
                    logger.info(f"- 失败: {data['failed_count']}")
                    logger.info(f"- API配额使用: {data['quota_info']['quota_used']}/{data['quota_info']['quota_limit']}")
                    
                    # 发送成功通知
                    self.send_notification('success', result)
                    
                else:
                    self.failed_runs += 1
                    self.last_run_status = 'failed'
                    logger.error(f"数据抓取失败: {result['message']}")
                    
                    # 发送失败通知
                    self.send_notification('error', result)
                
        except Exception as e:
            self.total_runs += 1
            self.failed_runs += 1
            self.last_run_status = 'error'
            self.last_run_time = start_time
            
            logger.error(f"数据抓取任务执行异常: {e}")
            
            # 发送异常通知
            self.send_notification('error', {'message': str(e)})
        
        finally:
            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()
            logger.info(f"数据抓取任务结束，耗时: {duration:.2f}秒")
    
    def is_data_already_crawled(self, target_date: date) -> bool:
        """检查指定日期是否已经抓取过数据"""
        try:
            # 获取活跃频道数量
            active_channels_count = Channel.query.filter_by(is_active=True).count()
            
            if active_channels_count == 0:
                return True  # 没有活跃频道，认为已抓取
            
            # 检查今天有数据的频道数量
            today_data_count = ChannelData.query.filter_by(date=target_date).count()
            
            # 如果今天的数据记录数等于或接近活跃频道数，认为已抓取
            coverage_rate = today_data_count / active_channels_count
            
            logger.info(f"数据覆盖率: {today_data_count}/{active_channels_count} ({coverage_rate:.2%})")
            
            # 如果覆盖率超过80%，认为已抓取
            return coverage_rate >= 0.8
            
        except Exception as e:
            logger.error(f"检查数据抓取状态失败: {e}")
            return False
    
    def check_missing_data(self):
        """检查遗漏的数据"""
        logger.info("开始检查遗漏数据")
        
        try:
            with self.app_context():
                # 检查最近3天的数据
                for days_ago in range(3):
                    check_date = date.today() - timedelta(days=days_ago)
                    
                    if not self.is_data_already_crawled(check_date):
                        logger.info(f"发现遗漏数据: {check_date}，尝试补抓取")
                        
                        result = self.sync_service.sync_all_channels(check_date)
                        
                        if result['success']:
                            logger.info(f"补抓取成功: {check_date}")
                        else:
                            logger.error(f"补抓取失败: {check_date} - {result['message']}")
                
        except Exception as e:
            logger.error(f"检查遗漏数据失败: {e}")
    
    def cleanup_old_logs(self):
        """清理旧日志"""
        logger.info("开始清理旧日志")
        
        try:
            # 清理30天前的数据（可选）
            cutoff_date = date.today() - timedelta(days=30)
            
            with self.app_context():
                # 这里可以添加清理旧数据的逻辑
                # 比如删除30天前的日志记录等
                logger.info(f"清理{cutoff_date}之前的数据")
                
        except Exception as e:
            logger.error(f"清理旧日志失败: {e}")
    
    def send_notification(self, level: str, result: Dict[str, Any]):
        """发送通知（可扩展为邮件、钉钉等）"""
        try:
            if level == 'success':
                data = result.get('data', {})
                message = f"✅ YouTube数据抓取成功\n"
                message += f"总频道: {data.get('total_channels', 0)}\n"
                message += f"成功: {data.get('success_count', 0)}\n"
                message += f"失败: {data.get('failed_count', 0)}"
                
            elif level == 'error':
                message = f"❌ YouTube数据抓取失败\n"
                message += f"错误信息: {result.get('message', '未知错误')}"
            
            # 目前只记录到日志，后续可扩展为其他通知方式
            logger.info(f"通知消息: {message}")
            
            # TODO: 集成邮件通知
            # TODO: 集成钉钉/微信通知
            # TODO: 集成Slack通知
            
        except Exception as e:
            logger.error(f"发送通知失败: {e}")
    
    def get_status(self) -> Dict[str, Any]:
        """获取调度器状态"""
        return {
            'running': self.running,
            'last_run_time': self.last_run_time.isoformat() if self.last_run_time else None,
            'last_run_status': self.last_run_status,
            'total_runs': self.total_runs,
            'successful_runs': self.successful_runs,
            'failed_runs': self.failed_runs,
            'success_rate': self.successful_runs / self.total_runs if self.total_runs > 0 else 0,
            'next_run': self.get_next_run_time()
        }
    
    def get_next_run_time(self) -> str:
        """获取下次运行时间"""
        try:
            next_run = schedule.next_run()
            return next_run.isoformat() if next_run else None
        except:
            return None
    
    def start(self):
        """启动调度器"""
        if self.running:
            logger.warning("调度器已在运行中")
            return
        
        if not self.initialize():
            logger.error("调度器初始化失败，无法启动")
            return
        
        self.schedule_tasks()
        self.running = True
        
        def run_scheduler():
            logger.info("调度器已启动，开始执行定时任务")
            while self.running:
                try:
                    schedule.run_pending()
                    time.sleep(60)  # 每分钟检查一次
                except Exception as e:
                    logger.error(f"调度器运行异常: {e}")
                    time.sleep(60)
        
        self.thread = threading.Thread(target=run_scheduler, daemon=True)
        self.thread.start()
        
        logger.info("数据抓取调度器已启动")
    
    def stop(self):
        """停止调度器"""
        if not self.running:
            logger.warning("调度器未在运行")
            return
        
        self.running = False
        schedule.clear()
        
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)
        
        logger.info("数据抓取调度器已停止")
    
    def manual_crawl(self, target_date: date = None) -> Dict[str, Any]:
        """手动触发数据抓取"""
        if not target_date:
            target_date = date.today()
        
        logger.info(f"手动触发数据抓取: {target_date}")
        
        try:
            with self.app_context():
                result = self.sync_service.sync_all_channels(target_date)
                
                if result['success']:
                    logger.info("手动数据抓取成功")
                else:
                    logger.error(f"手动数据抓取失败: {result['message']}")
                
                return result
                
        except Exception as e:
            logger.error(f"手动数据抓取异常: {e}")
            return {
                'success': False,
                'message': str(e)
            }

# 全局调度器实例
scheduler_instance = DataCrawlerScheduler()

# 便捷函数
def start_scheduler():
    """启动调度器"""
    scheduler_instance.start()

def stop_scheduler():
    """停止调度器"""
    scheduler_instance.stop()

def restart_scheduler():
    """重启调度器"""
    scheduler_instance.stop()
    time.sleep(0.2)
    scheduler_instance.start()
    return scheduler_instance.get_status()

def get_scheduler_status():
    """获取调度器状态"""
    return scheduler_instance.get_status()

def manual_crawl(target_date: date = None):
    """手动触发抓取"""
    return scheduler_instance.manual_crawl(target_date)

if __name__ == '__main__':
    # 直接运行调度器
    try:
        start_scheduler()
        
        # 保持程序运行
        while True:
            time.sleep(60)
            
    except KeyboardInterrupt:
        logger.info("收到停止信号")
        stop_scheduler()
    except Exception as e:
        logger.error(f"调度器运行异常: {e}")
        stop_scheduler()
