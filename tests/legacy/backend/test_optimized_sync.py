#!/usr/bin/env python3
# 测试优化后的视频同步性能

import os
import sys
import time
import logging
from datetime import datetime

# 添加项目路径
sys.path.insert(0, os.path.dirname(__file__))

# 设置环境变量
os.environ['FLASK_ENV'] = 'development'

from app import create_app
from services.video_sync_service import VideoSyncService
from services.optimized_video_sync_service import OptimizedVideoSyncService
from models import User, Channel
from utils.colored_logger import setup_colored_logger

logger = setup_colored_logger("SYNC_TEST")

def test_sync_performance():
    """测试同步性能对比"""
    app = create_app()
    
    with app.app_context():
        # 获取测试用户
        user = User.query.first()
        if not user:
            logger.error("没有找到测试用户，请先注册一个用户")
            return
        
        # 获取用户的频道
        channels = Channel.query.filter_by(user_id=user.id, is_active=True).all()
        if not channels:
            logger.error("没有找到活跃频道，请先添加一些频道")
            return
        
        logger.info(f"找到用户 {user.username}，共有 {len(channels)} 个频道")
        for channel in channels:
            logger.info(f"  - {channel.name}")
        
        # 测试参数
        days_back = 3
        max_workers = 3  # 减少并发数避免API限制
        
        print("\n" + "="*60)
        print("📊 视频同步性能测试")
        print("="*60)
        
        # 测试1: 传统同步
        print(f"\n🔄 测试1: 传统串行同步 (回溯 {days_back} 天)")
        print("-" * 40)
        
        traditional_service = VideoSyncService()
        start_time = time.time()
        
        try:
            result1 = traditional_service.sync_all_channels_videos(
                days_back=days_back, 
                user_id=user.id
            )
            traditional_time = time.time() - start_time
            
            print(f"✅ 传统同步完成")
            print(f"⏱️  用时: {traditional_time:.2f} 秒")
            print(f"📊 结果: {result1['data']}")
            
        except Exception as e:
            logger.error(f"传统同步失败: {str(e)}")
            traditional_time = float('inf')
            result1 = {'success': False, 'message': str(e), 'data': {}}
        
        # 等待一下避免频繁请求
        time.sleep(2)
        
        # 测试2: 优化同步
        print(f"\n🚀 测试2: 优化并行同步 (回溯 {days_back} 天, {max_workers} 线程)")
        print("-" * 40)
        
        optimized_service = OptimizedVideoSyncService()
        start_time = time.time()
        
        try:
            result2 = optimized_service.sync_all_channels_videos_parallel(
                days_back=days_back, 
                user_id=user.id,
                max_workers=max_workers
            )
            optimized_time = time.time() - start_time
            
            print(f"✅ 优化同步完成")
            print(f"⏱️  用时: {optimized_time:.2f} 秒")
            print(f"📊 结果: {result2['data']}")
            
        except Exception as e:
            logger.error(f"优化同步失败: {str(e)}")
            optimized_time = float('inf')
            result2 = {'success': False, 'message': str(e), 'data': {}}
        
        # 性能对比
        print(f"\n📈 性能对比结果")
        print("=" * 40)
        print(f"传统同步用时: {traditional_time:.2f} 秒")
        print(f"优化同步用时: {optimized_time:.2f} 秒")
        
        if optimized_time > 0 and traditional_time > 0:
            speedup = traditional_time / optimized_time
            print(f"🎯 性能提升: {speedup:.1f}x")
            
            if speedup > 1.5:
                print("🎉 优化效果显著！")
            elif speedup > 1.1:
                print("✨ 优化有效果")
            else:
                print("⚠️  优化效果不明显，可能需要更多频道或数据")
        else:
            print("❌ 无法比较性能（有同步失败）")
        
        # 功能对比
        print(f"\n🔍 功能测试结果")
        print("=" * 40)
        
        if result1['success'] and result2['success']:
            data1 = result1['data']
            data2 = result2['data']
            
            print(f"传统同步: 处理 {data1.get('processed', 0)} 个频道")
            print(f"优化同步: 处理 {data2.get('processed', 0)} 个频道")
            
            if data1.get('processed', 0) == data2.get('processed', 0):
                print("✅ 处理频道数一致")
            else:
                print("⚠️  处理频道数不一致")
            
            # 检查缓存命中
            cache_hits = data2.get('cache_hits', 0)
            if cache_hits > 0:
                print(f"📦 缓存命中: {cache_hits} 次")
            
        else:
            print("❌ 功能测试失败")
            if not result1['success']:
                print(f"传统同步错误: {result1['message']}")
            if not result2['success']:
                print(f"优化同步错误: {result2['message']}")

def test_cache_functionality():
    """测试缓存功能"""
    app = create_app()
    
    with app.app_context():
        user = User.query.first()
        if not user:
            return
        
        channels = Channel.query.filter_by(user_id=user.id, is_active=True).first()
        if not channels:
            return
        
        print(f"\n🧪 测试缓存功能")
        print("=" * 40)
        
        optimized_service = OptimizedVideoSyncService()
        
        # 第一次同步 - 应该从API获取
        print("第一次同步（从API获取）...")
        start_time = time.time()
        result1 = optimized_service.sync_channel_videos_optimized(channels.id, days_back=1)
        time1 = time.time() - start_time
        
        print(f"第一次用时: {time1:.2f} 秒")
        print(f"结果: {result1.get('from_cache', False) and '来自缓存' or '来自API'}")
        
        # 第二次同步 - 应该使用缓存
        print("\n第二次同步（应该使用缓存）...")
        start_time = time.time()
        result2 = optimized_service.sync_channel_videos_optimized(channels.id, days_back=1)
        time2 = time.time() - start_time
        
        print(f"第二次用时: {time2:.2f} 秒")
        print(f"结果: {result2.get('from_cache', False) and '来自缓存' or '来自API'}")
        
        if result2.get('from_cache', False):
            speedup = time1 / time2 if time2 > 0 else float('inf')
            print(f"🎯 缓存加速: {speedup:.1f}x")
            print("✅ 缓存功能正常工作")
        else:
            print("⚠️  缓存功能可能有问题")

def main():
    """主函数"""
    print("🎬 开始视频同步优化测试")
    print(f"📅 测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # 测试同步性能
    test_sync_performance()
    
    # 测试缓存功能
    test_cache_functionality()
    
    print(f"\n✨ 测试完成！")
    print("📝 建议:")
    print("  1. 如果性能提升不明显，尝试增加更多频道")
    print("  2. 检查Redis是否正常运行")
    print("  3. 在生产环境中可以增加max_workers值")

if __name__ == '__main__':
    main()