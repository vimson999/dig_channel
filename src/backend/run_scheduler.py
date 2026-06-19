#!/usr/bin/env python3
# 调度器启动脚本
"""
YouTube数据抓取调度器启动脚本

独立运行的调度器进程，负责定时抓取YouTube数据
"""

import os
import sys
import signal
import time
import argparse
from datetime import datetime, date

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scheduler import DataCrawlerScheduler, manual_crawl
import logging
from logging.handlers import RotatingFileHandler

# 配置日志
import os
logs_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'logs')
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

class SchedulerService:
    """调度器服务"""
    
    def __init__(self):
        self.scheduler = DataCrawlerScheduler()
        self.running = False
        
    def start(self):
        """启动调度器服务"""
        logger.info("启动YouTube数据抓取调度器服务")
        
        try:
            # 注册信号处理器
            signal.signal(signal.SIGINT, self._signal_handler)
            signal.signal(signal.SIGTERM, self._signal_handler)
            
            # 启动调度器
            self.scheduler.start()
            self.running = True
            
            logger.info("调度器服务启动成功")
            
            # 保持服务运行
            while self.running:
                time.sleep(1)
                
        except Exception as e:
            logger.error(f"调度器服务启动失败: {e}")
            self.stop()
    
    def stop(self):
        """停止调度器服务"""
        logger.info("停止调度器服务")
        
        self.running = False
        self.scheduler.stop()
        
        logger.info("调度器服务已停止")
    
    def _signal_handler(self, signum, frame):
        """信号处理器"""
        logger.info(f"收到信号 {signum}，准备停止服务")
        self.stop()
    
    def status(self):
        """获取服务状态"""
        status = self.scheduler.get_status()
        
        print("=" * 50)
        print("YouTube数据抓取调度器状态")
        print("=" * 50)
        print(f"运行状态: {'运行中' if status['running'] else '已停止'}")
        print(f"总运行次数: {status['total_runs']}")
        print(f"成功次数: {status['successful_runs']}")
        print(f"失败次数: {status['failed_runs']}")
        print(f"成功率: {status['success_rate']:.2%}")
        
        if status['last_run_time']:
            print(f"上次运行: {status['last_run_time']}")
            print(f"上次状态: {status['last_run_status']}")
        
        if status['next_run']:
            print(f"下次运行: {status['next_run']}")
        
        print("=" * 50)
    
    def manual_run(self, target_date=None):
        """手动运行一次数据抓取"""
        if target_date:
            try:
                target_date = datetime.strptime(target_date, '%Y-%m-%d').date()
            except ValueError:
                logger.error("日期格式错误，请使用YYYY-MM-DD格式")
                return
        
        logger.info(f"手动执行数据抓取: {target_date or '今天'}")
        
        result = manual_crawl(target_date)
        
        if result['success']:
            data = result['data']
            logger.info("手动抓取成功:")
            logger.info(f"- 总频道数: {data['total_channels']}")
            logger.info(f"- 成功: {data['success_count']}")
            logger.info(f"- 失败: {data['failed_count']}")
        else:
            logger.error(f"手动抓取失败: {result['message']}")

def main():
    parser = argparse.ArgumentParser(description='YouTube数据抓取调度器')
    parser.add_argument('command', choices=['start', 'stop', 'status', 'manual', 'test'],
                       help='命令: start(启动), stop(停止), status(状态), manual(手动运行), test(测试)')
    parser.add_argument('--date', type=str, help='手动运行时的目标日期 (YYYY-MM-DD)')
    parser.add_argument('--daemon', action='store_true', help='以守护进程方式运行')
    
    args = parser.parse_args()
    
    service = SchedulerService()
    
    if args.command == 'start':
        if args.daemon:
            # TODO: 实现守护进程模式
            logger.warning("守护进程模式尚未实现，使用前台模式")
        
        service.start()
        
    elif args.command == 'stop':
        # TODO: 实现停止运行中的服务
        logger.info("停止命令需要向运行中的进程发送信号")
        
    elif args.command == 'status':
        service.status()
        
    elif args.command == 'manual':
        service.manual_run(args.date)
        
    elif args.command == 'test':
        # 测试模式：初始化调度器并显示状态
        logger.info("测试调度器初始化...")
        
        if service.scheduler.initialize():
            logger.info("✅ 调度器初始化成功")
            
            # 测试API连接
            api_status = service.scheduler.sync_service.get_api_status()
            if api_status['api_available']:
                logger.info("✅ YouTube API连接正常")
                quota_info = api_status['quota_info']
                logger.info(f"   配额使用: {quota_info['quota_used']}/{quota_info['quota_limit']}")
            else:
                logger.error(f"❌ YouTube API连接失败: {api_status['message']}")
            
        else:
            logger.error("❌ 调度器初始化失败")

if __name__ == '__main__':
    main()
