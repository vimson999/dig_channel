#!/usr/bin/env python3
"""
生产环境调度器配置
频道同步: 每天1次 (凌晨2点)
视频同步: 每天8次 (每3小时一次)
"""

import schedule
import time
import threading
from datetime import datetime
import logging
import os
import sys

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.colored_logger import setup_colored_logger
from services.youtube_sync_service import YouTubeSyncService
from services.video_sync_service import VideoSyncService

logger = setup_colored_logger("PRODUCTION_SCHEDULER")

class ProductionScheduler:
    def __init__(self):
        self.running = False
        self.youtube_sync = YouTubeSyncService()
        self.video_sync = VideoSyncService()
        
    def sync_all_channels(self):
        """同步所有频道信息 - 每天1次"""
        logger.info("🔄 开始频道信息同步任务")
        try:
            result = self.youtube_sync.sync_all_channels()
            if result['success']:
                logger.info(f"✅ 频道同步成功: 处理{result['data']['success_count']}个频道")
            else:
                logger.error(f"❌ 频道同步失败: {result['message']}")
        except Exception as e:
            logger.error(f"💥 频道同步异常: {str(e)}")
    
    def sync_all_videos(self):
        """同步所有视频数据 - 每天8次"""
        logger.info("📺 开始视频数据同步任务")
        try:
            result = self.video_sync.sync_all_channels_videos(days_back=3)
            if result['success']:
                data = result['data']
                logger.info(f"✅ 视频同步成功: 处理{data['processed']}个频道, 新增{data['new_videos']}个视频")
            else:
                logger.error(f"❌ 视频同步失败: {result['message']}")
        except Exception as e:
            logger.error(f"💥 视频同步异常: {str(e)}")
    
    def setup_schedule(self):
        """配置调度任务"""
        # 频道同步: 每天凌晨2点
        schedule.every().day.at("02:00").do(self.sync_all_channels)
        
        # 视频同步: 每3小时一次 (8次/天)
        schedule.every().day.at("00:00").do(self.sync_all_videos)
        schedule.every().day.at("03:00").do(self.sync_all_videos)
        schedule.every().day.at("06:00").do(self.sync_all_videos)
        schedule.every().day.at("09:00").do(self.sync_all_videos)
        schedule.every().day.at("12:00").do(self.sync_all_videos)
        schedule.every().day.at("15:00").do(self.sync_all_videos)
        schedule.every().day.at("18:00").do(self.sync_all_videos)
        schedule.every().day.at("21:00").do(self.sync_all_videos)
        
        logger.info("📅 调度任务配置完成:")
        logger.info("   - 频道同步: 每天 02:00")
        logger.info("   - 视频同步: 每天 00:00, 03:00, 06:00, 09:00, 12:00, 15:00, 18:00, 21:00")
    
    def start(self):
        """启动调度器"""
        logger.info("🚀 启动生产环境调度器")
        self.setup_schedule()
        self.running = True
        
        while self.running:
            schedule.run_pending()
            time.sleep(60)  # 每分钟检查一次
    
    def stop(self):
        """停止调度器"""
        logger.info("🛑 停止调度器")
        self.running = False

if __name__ == "__main__":
    scheduler = ProductionScheduler()
    try:
        scheduler.start()
    except KeyboardInterrupt:
        scheduler.stop()