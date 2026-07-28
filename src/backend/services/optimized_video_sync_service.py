# 优化的视频同步服务
import json
import logging
import os
import redis
import time
from datetime import datetime, timedelta, date
from typing import Dict, List, Optional, Tuple
from functools import wraps
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

from sqlalchemy import text
from sqlalchemy.orm import load_only

from models import db, Channel, Video
from utils.youtube_api import YouTubeAPI
from utils.colored_logger import setup_colored_logger
from config.base import BaseConfig
from services.runtime_guard import release_db_session_quietly

logger = setup_colored_logger("OPTIMIZED_VIDEO_SYNC")

CHANNEL_CACHE_COLUMNS = (
    Channel.id,
    Channel.user_id,
    Channel.name,
    Channel.channel_id,
    Channel.url,
    Channel.cpm,
    Channel.category,
    Channel.sort,
    Channel.is_active,
    Channel.created_at,
    Channel.updated_at,
)

VIDEO_UPDATE_COLUMNS = (
    Video.id,
    Video.video_id,
    Video.view_count,
    Video.like_count,
    Video.comment_count,
    Video.title,
    Video.description,
)

class VideoSyncCache:
    """视频同步缓存管理器"""

    _availability_lock = threading.Lock()
    _unavailable_until = 0.0
    _retry_delay_seconds = 30

    def __init__(self):
        self.redis_client = redis.Redis.from_url(
            BaseConfig.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
            health_check_interval=30,
        )
        self.cache_ttl = {
            'channel_info': 3600,      # 频道信息缓存1小时
            'video_exists': 1800,      # 视频存在性缓存30分钟  
            'sync_status': 300,        # 同步状态缓存5分钟
            'youtube_api': 600         # YouTube API结果缓存10分钟
        }

    @classmethod
    def _can_attempt(cls) -> bool:
        with cls._availability_lock:
            return time.monotonic() >= cls._unavailable_until

    @classmethod
    def _mark_available(cls) -> None:
        with cls._availability_lock:
            cls._unavailable_until = 0.0

    @classmethod
    def _mark_unavailable(cls, operation: str, error: Exception) -> None:
        now = time.monotonic()
        with cls._availability_lock:
            should_log = now >= cls._unavailable_until
            cls._unavailable_until = max(
                cls._unavailable_until,
                now + cls._retry_delay_seconds,
            )
        if should_log:
            logger.warning(
                "Redis 视频同步缓存不可用，%s 秒内熔断并降级串行 DB 路径: %s (%s)",
                cls._retry_delay_seconds,
                operation,
                type(error).__name__,
            )

    def _call(self, operation: str, callback, default=None, force=False):
        if not force and not self._can_attempt():
            return default
        try:
            result = callback()
            self._mark_available()
            return result
        except Exception as error:
            self._mark_unavailable(operation, error)
            return default

    def is_available(self, force=False) -> bool:
        result = self._call(
            "健康检查失败",
            self.redis_client.ping,
            default=False,
            force=force,
        )
        return bool(result)
    
    def cache_key(self, prefix: str, *args) -> str:
        """生成缓存键"""
        return f"video_sync:{prefix}:{':'.join(map(str, args))}"
    
    def get_cached_channel_info(self, channel_id: int) -> Optional[Dict]:
        """获取缓存的频道信息"""
        key = self.cache_key('channel', channel_id)
        cached = self._call("获取频道缓存失败", lambda: self.redis_client.get(key))
        if cached:
            return json.loads(cached)
        return None
    
    def cache_channel_info(self, channel_id: int, channel_data: Dict):
        """缓存频道信息"""
        key = self.cache_key('channel', channel_id)
        self._call(
            "缓存频道信息失败",
            lambda: self.redis_client.setex(
                key, 
                self.cache_ttl['channel_info'], 
                json.dumps(channel_data, default=str)
            ),
        )
    
    def get_video_exists_batch(self, channel_id: int, video_ids: List[str]) -> Dict[str, bool]:
        """批量获取视频存在性缓存"""
        if not video_ids:
            return {}
        keys = [self.cache_key('video_exists', channel_id, vid) for vid in video_ids]
        cached_values = self._call(
            "批量获取视频存在性缓存失败",
            lambda: self.redis_client.mget(keys),
        )
        if cached_values is None:
            return {}

        result = {}
        for i, video_id in enumerate(video_ids):
            cached = cached_values[i]
            if cached is not None:
                result[video_id] = cached == '1'
        return result
    
    def cache_video_exists_batch(self, channel_id: int, video_exists_map: Dict[str, bool]):
        """批量缓存视频存在性"""
        if not video_exists_map:
            return

        def write_batch():
            pipe = self.redis_client.pipeline()
            for video_id, exists in video_exists_map.items():
                key = self.cache_key('video_exists', channel_id, video_id)
                pipe.setex(key, self.cache_ttl['video_exists'], '1' if exists else '0')
            return pipe.execute()

        self._call("批量缓存视频存在性失败", write_batch)
    
    def get_sync_status(self, channel_id: int) -> Optional[Dict]:
        """获取频道同步状态"""
        key = self.cache_key('sync_status', channel_id)
        cached = self._call("获取同步状态缓存失败", lambda: self.redis_client.get(key))
        if cached:
            return json.loads(cached)
        return None
    
    def set_sync_status(self, channel_id: int, status: Dict):
        """设置频道同步状态"""
        key = self.cache_key('sync_status', channel_id)
        self._call(
            "设置同步状态缓存失败",
            lambda: self.redis_client.setex(
                key, 
                self.cache_ttl['sync_status'], 
                json.dumps(status, default=str)
            ),
        )
    
    def cache_youtube_api_result(self, cache_key: str, result: Dict, ttl: int = None):
        """缓存YouTube API结果"""
        if ttl is None:
            ttl = self.cache_ttl['youtube_api']
        
        self._call(
            "缓存 API 结果失败",
            lambda: self.redis_client.setex(
                cache_key,
                ttl,
                json.dumps(result, default=str),
            ),
        )
    
    def get_youtube_api_result(self, cache_key: str) -> Optional[Dict]:
        """获取缓存的YouTube API结果"""
        cached = self._call(
            "获取 API 缓存失败",
            lambda: self.redis_client.get(cache_key),
        )
        if cached:
            return json.loads(cached)
        return None

def cache_youtube_api(cache_time: int = 600):
    """装饰器：缓存YouTube API调用"""
    def decorator(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            # 生成缓存键
            cache_key = f"youtube_api:{func.__name__}:{hash(str(args) + str(kwargs))}"
            
            # 尝试从缓存获取
            cached_result = self.cache.get_youtube_api_result(cache_key)
            if cached_result:
                logger.debug(f"使用缓存的API结果: {func.__name__}")
                return cached_result
            
            # 调用实际API
            result = func(self, *args, **kwargs)
            
            # 缓存结果（只缓存成功的结果）
            if result.get('success'):
                self.cache.cache_youtube_api_result(cache_key, result, cache_time)
            
            return result
        return wrapper
    return decorator

class OptimizedVideoSyncService:
    """优化的视频同步服务"""

    _sync_in_progress = set()
    _sync_state_lock = threading.Lock()

    def __init__(self):
        self.youtube_api = YouTubeAPI()
        self.cache = VideoSyncCache()

    @classmethod
    def _acquire_sync_slot(cls, sync_key: str) -> bool:
        with cls._sync_state_lock:
            if sync_key in cls._sync_in_progress:
                return False
            cls._sync_in_progress.add(sync_key)
            return True

    @classmethod
    def _release_sync_slot(cls, sync_key: str) -> None:
        with cls._sync_state_lock:
            cls._sync_in_progress.discard(sync_key)

    @staticmethod
    def _bounded_max_workers(requested: int) -> int:
        try:
            configured_limit = int(
                os.environ.get("OPTIMIZED_VIDEO_SYNC_MAX_WORKERS", "3")
            )
        except (TypeError, ValueError):
            configured_limit = 3
        configured_limit = max(1, min(configured_limit, 10))
        try:
            requested = int(requested or 1)
        except (TypeError, ValueError):
            requested = 1
        return max(1, min(requested, configured_limit))
        
    def sync_all_channels_videos_parallel(self, days_back: int = 10, user_id: int = None, max_workers: int = 5) -> Dict:
        """并行同步所有频道的视频"""
        sync_key = f"all_channels_{user_id}"

        if not self._acquire_sync_slot(sync_key):
            logger.warning(f"用户 {user_id} 的同步任务已在进行中，跳过重复请求")
            return {
                'success': False,
                'message': '同步任务已在进行中，请稍后再试',
                'data': {'processed': 0, 'errors': 1}
            }
        
        try:
            max_workers = self._bounded_max_workers(max_workers)
            if not self.cache.is_available():
                max_workers = 1
                logger.warning("Redis 视频同步缓存不可用，本轮同步强制串行，避免并发回源数据库")

            logger.info(f"开始并行同步所有频道的视频，最大工作线程: {max_workers}")

            # 获取活跃频道
            query = db.session.query(Channel.id, Channel.name).filter(
                Channel.is_active.is_(True)
            )
            if user_id is not None:
                query = query.filter(Channel.user_id == user_id)
            channels = query.all()
            # Rows only contain scalar IDs/names, so the main thread can release
            # its read transaction before Redis and YouTube network work starts.
            release_db_session_quietly()
            
            if not channels:
                return {
                    'success': True,
                    'message': '您还没有添加任何频道，请先添加频道后再同步视频',
                    'data': {
                        'processed': 0, 
                        'errors': 0,
                        'new_videos': 0,
                        'updated_videos': 0,
                        'details': [],
                        'no_channels': True
                    }
                }
            
            results = {
                'processed': 0,
                'errors': 0,
                'new_videos': 0,
                'updated_videos': 0,
                'details': []
            }
            
            # 使用线程池并行处理
            from flask import current_app
            app = current_app._get_current_object()  # 获取应用实例
            
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # 提交所有任务
                future_to_channel = {
                    executor.submit(self._sync_single_channel_with_context, app, channel.id, days_back): channel
                    for channel in channels
                }
                
                # 收集结果
                completed = 0
                total = len(channels)
                
                for future in as_completed(future_to_channel):
                    channel = future_to_channel[future]
                    completed += 1
                    
                    try:
                        sync_result = future.result()
                        
                        logger.info(f"频道 {channel.name} 同步完成 ({completed}/{total})")
                        
                        if sync_result['success']:
                            results['processed'] += 1
                            results['new_videos'] += sync_result['data'].get('new_videos', 0)
                            results['updated_videos'] += sync_result['data'].get('updated_videos', 0)
                            results['details'].append({
                                'channel_id': channel.id,
                                'channel_name': channel.name,
                                'status': 'success',
                                'new_videos': sync_result['data'].get('new_videos', 0),
                                'updated_videos': sync_result['data'].get('updated_videos', 0),
                                'from_cache': sync_result.get('from_cache', False)
                            })
                        else:
                            results['errors'] += 1
                            results['details'].append({
                                'channel_id': channel.id,
                                'channel_name': channel.name,
                                'status': 'error',
                                'message': sync_result.get('message', '未知错误')
                            })
                            
                    except Exception as e:
                        results['errors'] += 1
                        logger.error(f"频道 {channel.name} 同步异常: {str(e)}")
                        results['details'].append({
                            'channel_id': channel.id,
                            'channel_name': channel.name,
                            'status': 'error',
                            'message': f"同步异常: {str(e)}",
                            'error_type': type(e).__name__
                        })
            
            logger.info(f"并行同步完成: 处理 {results['processed']} 个频道, 新增 {results['new_videos']} 个视频, 更新 {results['updated_videos']} 个视频")
            
            return {
                'success': True,
                'message': f'并行同步完成: 处理 {results["processed"]} 个频道',
                'data': results
            }
            
        except Exception as e:
            logger.error(f"并行同步所有频道视频失败: {str(e)}")
            return {
                'success': False,
                'message': f'同步失败: {str(e)}',
                'data': {'processed': 0, 'errors': 1}
            }
        finally:
            self._release_sync_slot(sync_key)
            logger.info(f"清理同步状态: {sync_key}")
    
    def _sync_single_channel_with_context(self, app, channel_id: int, days_back: int) -> Dict:
        """带上下文的单频道同步"""
        # 在线程中创建新的应用上下文
        with app.app_context():
            try:
                return self.sync_channel_videos_optimized(channel_id, days_back)
            except Exception as e:
                logger.error(f"频道 {channel_id} 同步失败: {str(e)}")
                return {
                    'success': False,
                    'message': str(e),
                    'data': {}
                }
            finally:
                release_db_session_quietly()
    
    def sync_channel_videos_optimized(self, channel_id: int, days_back: int = 10) -> Dict:
        """优化的频道视频同步"""
        try:
            # 1. 检查同步状态缓存
            sync_status = self.cache.get_sync_status(channel_id)
            if sync_status and self._is_recent_sync(sync_status, days_back):
                logger.info(f"频道 {channel_id} 最近已同步，使用缓存结果")
                return {
                    'success': True,
                    'message': '频道最近已同步，使用缓存结果',
                    'data': sync_status.get('data', {'new_videos': 0, 'updated_videos': 0}),
                    'from_cache': True
                }
            
            # 2. 获取频道信息（优先使用缓存）
            channel_info = self.cache.get_cached_channel_info(channel_id)
            if not channel_info:
                channel = Channel.query.options(load_only(*CHANNEL_CACHE_COLUMNS)).get(channel_id)
                if not channel:
                    return {'success': False, 'message': '频道不存在', 'data': {}}
                
                channel_info = channel.to_dict()
                release_db_session_quietly()
                self.cache.cache_channel_info(channel_id, channel_info)
            
            # 3. 获取YouTube视频数据（带API缓存）
            videos_data = self._get_channel_videos_cached(
                channel_info['channel_id'], 
                days_back,
                channel_id  # 传递数据库ID用于首次同步判断
            )
            
            if not videos_data['success']:
                return videos_data
            
            videos = videos_data['data'].get('videos', [])
            
            if not videos:
                result = {'success': True, 'data': {'new_videos': 0, 'updated_videos': 0}}
                # 缓存空结果
                self.cache.set_sync_status(channel_id, {
                    'last_sync': datetime.utcnow().isoformat(),
                    'days_back': days_back,
                    'data': result['data']
                })
                return result
            
            # 4. 使用缓存优化的批量处理
            result = self._process_videos_with_cache(channel_id, videos)
            
            # 5. 缓存同步结果
            self.cache.set_sync_status(channel_id, {
                'last_sync': datetime.utcnow().isoformat(),
                'days_back': days_back,
                'data': result['data']
            })
            
            # 6. 批量创建视频数据记录
            if result['success'] and (result['data']['new_videos'] > 0 or result['data']['updated_videos'] > 0):
                self._create_video_data_records_batch(channel_id, date.today())
            
            return result
            
        except Exception as e:
            logger.error(f"优化同步频道 {channel_id} 失败: {str(e)}")
            return {
                'success': False,
                'message': f'同步失败: {str(e)}',
                'data': {}
            }
    
    @cache_youtube_api(cache_time=600)
    def _get_channel_videos_cached(self, youtube_channel_id: str, days_back: int, db_channel_id: int):
        """带缓存的YouTube API调用"""
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days_back)
        
        # 检查是否为首次同步
        existing_videos = (
            db.session.query(Video.id)
            .filter(Video.channel_id == db_channel_id)
            .limit(1)
            .first()
        )
        is_initial_sync = existing_videos is None
        release_db_session_quietly()
        
        max_results = 50 if is_initial_sync else 20
        
        return self.youtube_api.get_channel_videos(
            youtube_channel_id,
            published_after=start_date,
            published_before=end_date,
            max_results=max_results
        )
    
    def _process_videos_with_cache(self, channel_id: int, videos: List[Dict]) -> Dict:
        """使用缓存优化的视频处理"""
        if not videos:
            return {'success': True, 'data': {'new_videos': 0, 'updated_videos': 0}}
        
        video_ids = [v['video_id'] for v in videos]
        
        # 1. 从缓存获取视频存在性
        cached_exists = self.cache.get_video_exists_batch(channel_id, video_ids)
        
        # 2. 查询缓存中没有的视频
        uncached_video_ids = [vid for vid in video_ids if vid not in cached_exists]
        
        existing_videos_map = {}
        if uncached_video_ids:
            existing_videos = (
                db.session.query(*VIDEO_UPDATE_COLUMNS)
                .filter(
                    Video.video_id.in_(uncached_video_ids),
                    Video.channel_id == channel_id,
                )
                .all()
            )
            existing_videos_map = {v.video_id: v for v in existing_videos}
            
            # 缓存查询结果
            exists_map = {vid: vid in existing_videos_map for vid in uncached_video_ids}
            self.cache.cache_video_exists_batch(channel_id, exists_map)
        
        # 3. 合并缓存和新查询的结果
        all_exists = {**cached_exists}
        for vid in uncached_video_ids:
            all_exists[vid] = vid in existing_videos_map
        
        # 4. 分离新视频和需更新的视频，同时过滤短视频
        new_videos = []
        update_videos = []
        
        for v in videos:
            if self._is_short_video(v.get('duration')):
                logger.debug(f"跳过短视频: {v.get('title', 'unknown')}")
                continue
                
            if not all_exists.get(v['video_id'], False):
                new_videos.append(v)
            else:
                update_videos.append(v)
        
        # 5. 批量处理
        new_count = self._batch_create_videos(channel_id, new_videos)
        update_count = self._batch_update_videos(channel_id, update_videos, existing_videos_map)
        
        return {
            'success': True,
            'data': {
                'new_videos': new_count,
                'updated_videos': update_count,
                'cache_hits': len(cached_exists),
                'total_processed': len(videos)
            }
        }
    
    def _batch_create_videos(self, channel_id: int, videos: List[Dict]) -> int:
        """批量创建视频记录"""
        if not videos:
            return 0
        
        try:
            # 获取频道信息
            channel = Channel.query.options(load_only(*CHANNEL_CACHE_COLUMNS)).get(channel_id)
            if not channel:
                raise ValueError(f"频道ID {channel_id} 不存在")
            
            # 准备批量插入数据
            videos_data = []
            for video_info in videos:
                video_data = self._prepare_video_data(channel, video_info)
                videos_data.append(video_data)
            
            # 批量插入
            if videos_data:
                db.session.bulk_insert_mappings(Video, videos_data)
                db.session.commit()
                logger.debug(f"批量创建 {len(videos_data)} 个新视频")
            
            return len(videos_data)
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"批量创建视频失败: {str(e)}")
            return 0
    
    def _batch_update_videos(self, channel_id: int, videos: List[Dict], existing_videos_map: Dict) -> int:
        """批量更新视频记录"""
        if not videos:
            return 0
        
        try:
            video_ids = [video_info['video_id'] for video_info in videos]
            missing_video_ids = [
                video_id
                for video_id in video_ids
                if video_id not in existing_videos_map
            ]
            if missing_video_ids:
                rows = (
                    db.session.query(*VIDEO_UPDATE_COLUMNS)
                    .filter(
                        Video.video_id.in_(missing_video_ids),
                        Video.channel_id == channel_id,
                    )
                    .all()
                )
                existing_videos_map.update({row.video_id: row for row in rows})

            updates_data = []
            
            for video_info in videos:
                video_id = video_info['video_id']
                existing_video = existing_videos_map.get(video_id)
                
                if existing_video:
                    update_data = self._prepare_update_data(existing_video, video_info)
                    if update_data:
                        updates_data.append(update_data)
            
            # 批量更新
            if updates_data:
                db.session.bulk_update_mappings(Video, updates_data)
                db.session.commit()
                logger.debug(f"批量更新 {len(updates_data)} 个视频")
            
            return len(updates_data)
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"批量更新视频失败: {str(e)}")
            return 0
    
    def _prepare_video_data(self, channel: Channel, video_info: Dict) -> Dict:
        """准备视频数据用于批量插入"""
        now = datetime.utcnow()
        
        return {
            'video_id': video_info['video_id'],
            'channel_id': channel.id,
            'user_id': channel.user_id,
            'title': video_info.get('title', ''),
            'description': video_info.get('description', ''),
            'published_at': datetime.fromisoformat(video_info['published_at'].replace('Z', '+00:00')),
            'duration': video_info.get('duration'),
            'view_count': video_info.get('view_count', 0),
            'like_count': video_info.get('like_count', 0),
            'comment_count': video_info.get('comment_count', 0),
            'tags': video_info.get('tags', []),
            'thumbnail_url': video_info.get('thumbnail_url'),
            'category_id': video_info.get('category_id'),
            'category_name': video_info.get('category_name'),
            'language': video_info.get('language'),
            'created_at': now,
            'updated_at': now,
            'is_active': True
        }
    
    def _prepare_update_data(self, existing_video: Video, video_info: Dict) -> Optional[Dict]:
        """准备更新数据"""
        update_data = {'id': existing_video.id}
        has_changes = False
        
        # 检查需要更新的字段
        updates = [
            ('view_count', video_info.get('view_count', 0)),
            ('like_count', video_info.get('like_count', 0)),
            ('comment_count', video_info.get('comment_count', 0)),
            ('title', video_info.get('title', '')),
            ('description', video_info.get('description', ''))
        ]
        
        for field, new_value in updates:
            if getattr(existing_video, field) != new_value:
                update_data[field] = new_value
                has_changes = True
        
        if has_changes:
            update_data['updated_at'] = datetime.utcnow()
            return update_data
        
        return None
    
    def _create_video_data_records_batch(self, channel_id: int, record_date: date) -> None:
        """在数据库内幂等创建频道当日快照，避免拉取整频道记录到应用内存。"""
        try:
            lock_key = f"video_data:{channel_id}:{record_date.isoformat()}"
            db.session.execute(
                text(
                    """
                    SELECT pg_advisory_xact_lock(
                        :lock_namespace,
                        hashtext(:lock_key)
                    )
                    """
                ),
                {
                    "lock_namespace": 86422023,
                    "lock_key": lock_key,
                },
            )

            inserted = db.session.execute(
                text(
                    """
                    INSERT INTO public.video_data (
                        video_id,
                        date,
                        view_count,
                        like_count,
                        comment_count,
                        view_change,
                        like_change,
                        comment_change,
                        engagement_rate,
                        estimated_revenue,
                        created_at,
                        updated_at
                    )
                    SELECT
                        video.id,
                        :record_date,
                        COALESCE(video.view_count, 0),
                        COALESCE(video.like_count, 0),
                        COALESCE(video.comment_count, 0),
                        0,
                        0,
                        0,
                        0,
                        0,
                        CURRENT_TIMESTAMP,
                        CURRENT_TIMESTAMP
                    FROM public.videos AS video
                    WHERE video.channel_id = :channel_id
                      AND video.is_active IS TRUE
                      AND NOT EXISTS (
                          SELECT 1
                          FROM public.video_data AS existing
                          WHERE existing.video_id = video.id
                            AND existing.date = :record_date
                      )
                    """
                ),
                {
                    "record_date": record_date,
                    "channel_id": channel_id,
                },
            )
            db.session.commit()
            if inserted.rowcount:
                logger.debug(f"数据库端批量创建 {inserted.rowcount} 条视频数据记录")
        except Exception as e:
            db.session.rollback()
            logger.error(f"批量创建视频数据记录失败: {str(e)}")
    
    def _is_recent_sync(self, sync_status: Dict, days_back: int) -> bool:
        """检查是否为最近的同步"""
        last_sync_str = sync_status.get('last_sync')
        last_days_back = sync_status.get('days_back')
        
        if not last_sync_str or last_days_back != days_back:
            return False
        
        try:
            last_sync = datetime.fromisoformat(last_sync_str)
            # 如果上次同步在30分钟内，且天数一致，认为是最近同步
            return (datetime.utcnow() - last_sync).total_seconds() < 1800
        except:
            return False
    
    def _is_short_video(self, duration: str) -> bool:
        """检查是否为短视频（时长小于60秒）"""
        if not duration:
            return False
            
        # 解析ISO 8601格式的时长 (PT4M13S)
        import re
        match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration)
        if not match:
            return False
        
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2) or 0)
        seconds = int(match.group(3) or 0)
        
        # 计算总秒数
        total_seconds = hours * 3600 + minutes * 60 + seconds
        
        # 小于60秒认为是短视频
        return total_seconds < 60

    # 保持兼容性，继承原有方法的接口
    def get_channel_videos(self, channel_id: int, page: int = 1, per_page: int = 20, 
                          search: str = None, category: str = None, 
                          sort_by: str = 'published_at', order: str = 'desc',
                          time_period_days: int = None, user_id: int = None) -> Dict:
        """获取频道视频列表（保持原有接口兼容）"""
        # 这里可以直接复用原VideoSyncService的方法，或者重新实现
        from services.video_sync_service import VideoSyncService
        original_service = VideoSyncService()
        return original_service.get_channel_videos(
            channel_id, page, per_page, search, category, 
            sort_by, order, time_period_days, user_id
        )
    
    def search_videos(self, search: str, page: int = 1, per_page: int = 20, 
                     channel_id: int = None, category: str = None,
                     sort_by: str = 'published_at', order: str = 'desc',
                     time_period_days: int = None, user_id: int = None) -> Dict:
        """搜索视频（保持原有接口兼容）"""
        from services.video_sync_service import VideoSyncService
        original_service = VideoSyncService()
        return original_service.search_videos(
            search, page, per_page, channel_id, category,
            sort_by, order, time_period_days, user_id
        )
    
    def get_video_categories(self) -> Dict:
        """获取视频分类统计（保持原有接口兼容）"""
        from services.video_sync_service import VideoSyncService
        original_service = VideoSyncService()
        return original_service.get_video_categories()
    
    def get_videos_stats(self) -> Dict:
        """获取视频统计信息（保持原有接口兼容）"""
        from services.video_sync_service import VideoSyncService
        original_service = VideoSyncService()
        return original_service.get_videos_stats()
    
    def clean_short_videos(self) -> Dict:
        """清理短视频（保持原有接口兼容）"""
        from services.video_sync_service import VideoSyncService
        original_service = VideoSyncService()
        return original_service.clean_short_videos()
