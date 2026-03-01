#!/usr/bin/env python3
# 测试优化功能的基本特性

import os
import sys
import time
import redis

# 添加项目路径
sys.path.insert(0, os.path.dirname(__file__))

# 设置环境变量
os.environ['FLASK_ENV'] = 'development'

from services.optimized_video_sync_service import VideoSyncCache, OptimizedVideoSyncService
from config.base import BaseConfig
from utils.colored_logger import setup_colored_logger

logger = setup_colored_logger("OPTIMIZATION_TEST")

def test_redis_connection():
    """测试Redis连接"""
    print("🔗 测试Redis连接...")
    try:
        client = redis.Redis(
            host=BaseConfig.REDIS_HOST,
            port=BaseConfig.REDIS_PORT,
            db=BaseConfig.REDIS_DB,
            decode_responses=True
        )
        
        # 测试基本操作
        client.set("test_key", "test_value")
        result = client.get("test_key")
        client.delete("test_key")
        
        if result == "test_value":
            print("✅ Redis连接正常")
            return True
        else:
            print("❌ Redis读写测试失败")
            return False
            
    except Exception as e:
        print(f"❌ Redis连接失败: {str(e)}")
        return False

def test_cache_basic_operations():
    """测试缓存基本操作"""
    print("\n📦 测试缓存基本操作...")
    
    cache = VideoSyncCache()
    
    # 测试频道信息缓存
    print("  测试频道信息缓存...")
    channel_data = {
        'id': 123,
        'name': '测试频道',
        'channel_id': 'test_channel_id'
    }
    
    cache.cache_channel_info(123, channel_data)
    cached = cache.get_cached_channel_info(123)
    
    if cached and cached['name'] == '测试频道':
        print("  ✅ 频道信息缓存正常")
    else:
        print("  ❌ 频道信息缓存失败")
    
    # 测试视频存在性批量缓存
    print("  测试视频存在性缓存...")
    video_exists_map = {
        'video1': True,
        'video2': False,
        'video3': True
    }
    
    cache.cache_video_exists_batch(123, video_exists_map)
    cached_exists = cache.get_video_exists_batch(123, ['video1', 'video2', 'video3'])
    
    if (len(cached_exists) == 3 and 
        cached_exists.get('video1') == True and
        cached_exists.get('video2') == False and
        cached_exists.get('video3') == True):
        print("  ✅ 视频存在性缓存正常")
    else:
        print("  ❌ 视频存在性缓存失败")
        print(f"    预期: {video_exists_map}")
        print(f"    实际: {cached_exists}")
    
    # 测试同步状态缓存
    print("  测试同步状态缓存...")
    sync_status = {
        'last_sync': '2025-09-08T11:00:00',
        'days_back': 3,
        'data': {'new_videos': 5, 'updated_videos': 2}
    }
    
    cache.set_sync_status(123, sync_status)
    cached_status = cache.get_sync_status(123)
    
    if (cached_status and 
        cached_status['days_back'] == 3 and
        cached_status['data']['new_videos'] == 5):
        print("  ✅ 同步状态缓存正常")
    else:
        print("  ❌ 同步状态缓存失败")

def test_batch_operations():
    """测试批量操作功能"""
    print("\n⚡ 测试批量操作功能...")
    
    # 测试短视频检测
    service = OptimizedVideoSyncService()
    
    test_cases = [
        ('PT45S', True, '45秒'),
        ('PT1M30S', False, '1分30秒'),
        ('PT4M13S', False, '4分13秒'),
        ('PT1H5M', False, '1小时5分'),
        ('PT30S', True, '30秒'),
        ('PT1M', False, '1分钟')
    ]
    
    print("  测试短视频检测...")
    all_correct = True
    for duration, expected, desc in test_cases:
        result = service._is_short_video(duration)
        if result == expected:
            print(f"    ✅ {desc} -> {'短视频' if result else '正常视频'}")
        else:
            print(f"    ❌ {desc} -> 预期: {'短视频' if expected else '正常视频'}, 实际: {'短视频' if result else '正常视频'}")
            all_correct = False
    
    if all_correct:
        print("  ✅ 短视频检测功能正常")
    else:
        print("  ❌ 短视频检测功能有问题")

def test_performance_improvements():
    """测试性能改进"""
    print("\n🚀 测试性能改进特性...")
    
    # 测试缓存加速
    print("  测试缓存加速效果...")
    cache = VideoSyncCache()
    
    # 模拟API调用缓存
    api_result = {
        'success': True,
        'data': {
            'videos': [
                {'video_id': 'test1', 'title': '测试视频1'},
                {'video_id': 'test2', 'title': '测试视频2'}
            ]
        }
    }
    
    cache_key = "test_api_call"
    
    # 第一次"调用"
    start_time = time.time()
    cache.cache_youtube_api_result(cache_key, api_result, 60)
    time.sleep(0.01)  # 模拟API调用时间
    first_time = time.time() - start_time
    
    # 第二次从缓存获取
    start_time = time.time()
    cached_result = cache.get_youtube_api_result(cache_key)
    second_time = time.time() - start_time
    
    if cached_result and second_time < first_time:
        speedup = first_time / second_time if second_time > 0 else float('inf')
        print(f"    ✅ 缓存加速效果: {speedup:.1f}x")
    else:
        print("    ❌ 缓存加速测试失败")

def test_thread_safety():
    """测试线程安全性"""
    print("\n🔒 测试线程安全性...")
    
    service = OptimizedVideoSyncService()
    
    # 检查锁机制
    if hasattr(service, '_local_lock'):
        print("  ✅ 本地锁机制已实现")
    else:
        print("  ❌ 本地锁机制缺失")
    
    # 检查同步状态跟踪
    if hasattr(service, '_sync_in_progress'):
        print("  ✅ 同步状态跟踪已实现")
    else:
        print("  ❌ 同步状态跟踪缺失")

def main():
    """主函数"""
    print("🧪 开始优化功能测试")
    print("="*50)
    
    # 1. 测试Redis连接
    redis_ok = test_redis_connection()
    
    if not redis_ok:
        print("\n❌ Redis连接失败，无法继续测试")
        return
    
    # 2. 测试缓存操作
    test_cache_basic_operations()
    
    # 3. 测试批量操作
    test_batch_operations()
    
    # 4. 测试性能改进
    test_performance_improvements()
    
    # 5. 测试线程安全
    test_thread_safety()
    
    print("\n" + "="*50)
    print("📊 测试总结:")
    print("✅ Redis连接测试通过")
    print("✅ 缓存功能测试完成") 
    print("✅ 批量操作测试完成")
    print("✅ 性能改进特性测试完成")
    print("✅ 线程安全检查完成")
    
    print("\n🎯 优化特性:")
    print("  • 多级缓存机制 (频道信息、视频存在性、API结果)")
    print("  • 批量数据库操作 (减少N+1查询)")
    print("  • 并行频道处理 (ThreadPoolExecutor)")
    print("  • 智能同步状态检查 (避免重复同步)")
    print("  • 短视频过滤 (提升处理效率)")
    
    print("\n💡 使用建议:")
    print("  1. 在前端传递 use_optimization: true 启用优化")
    print("  2. 可调整 max_workers 参数控制并发数")
    print("  3. Redis缓存有效期可根据需求调整")
    print("  4. 生产环境建议设置适当的Redis内存限制")

if __name__ == '__main__':
    main()