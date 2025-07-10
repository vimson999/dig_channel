# 视频同步服务
import json
import logging
from datetime import datetime, timedelta, date
from typing import Dict, List, Optional, Tuple

from models import db, Channel, Video, VideoData
from utils.youtube_api import YouTubeAPI
from utils.colored_logger import setup_colored_logger

logger = setup_colored_logger("VIDEO_SYNC")

class VideoSyncService:
    def __init__(self):
        self.youtube_api = YouTubeAPI()
        
    def sync_all_channels_videos(self, days_back: int = 10, user_id: int = None) -> Dict:
        """同步所有活跃频道的视频"""
        logger.info("开始同步所有频道的视频...")
        
        try:
            # 获取活跃频道（按用户过滤）
            query = Channel.query.filter_by(is_active=True)
            if user_id is not None:
                query = query.filter_by(user_id=user_id)
            channels = query.all()
            
            if not channels:
                return {
                    'success': False,
                    'message': '没有找到活跃的频道',
                    'data': {'processed': 0, 'errors': 0}
                }
            
            results = {
                'processed': 0,
                'errors': 0,
                'new_videos': 0,
                'updated_videos': 0,
                'details': []
            }
            
            for channel in channels:
                try:
                    logger.info(f"同步频道: {channel.name}")
                    
                    # 同步频道视频
                    sync_result = self.sync_channel_videos(channel.id, days_back)
                    
                    if sync_result['success']:
                        results['processed'] += 1
                        results['new_videos'] += sync_result['data']['new_videos']
                        results['updated_videos'] += sync_result['data']['updated_videos']
                        
                        results['details'].append({
                            'channel_id': channel.id,
                            'channel_name': channel.name,
                            'status': 'success',
                            'new_videos': sync_result['data']['new_videos'],
                            'updated_videos': sync_result['data']['updated_videos']
                        })
                    else:
                        results['errors'] += 1
                        results['details'].append({
                            'channel_id': channel.id,
                            'channel_name': channel.name,
                            'status': 'error',
                            'message': sync_result['message']
                        })
                        
                except Exception as e:
                    from utils.colored_logger import log_detailed_error
                    
                    # 记录详细错误信息
                    log_detailed_error(
                        logger_name="VIDEO_SYNC",
                        context=f"同步频道失败: {channel.name}",
                        error=e,
                        频道ID=channel.id,
                        频道名称=channel.name,
                        频道URL=channel.url,
                        YouTube频道ID=channel.channel_id,
                        回溯天数=days_back,
                        是否首次同步="未知"
                    )
                    
                    results['errors'] += 1
                    results['details'].append({
                        'channel_id': channel.id,
                        'channel_name': channel.name,
                        'status': 'error',
                        'message': str(e),
                        'error_type': type(e).__name__
                    })
            
            logger.info(f"同步完成: 处理 {results['processed']} 个频道, 新增 {results['new_videos']} 个视频, 更新 {results['updated_videos']} 个视频")
            
            return {
                'success': True,
                'message': f'同步完成: 处理 {results["processed"]} 个频道',
                'data': results
            }
            
        except Exception as e:
            logger.error(f"同步所有频道视频失败: {str(e)}")
            return {
                'success': False,
                'message': f'同步失败: {str(e)}',
                'data': {'processed': 0, 'errors': 1}
            }
    
    def sync_channel_videos(self, channel_id: int, days_back: int = 10) -> Dict:
        """同步单个频道的视频"""
        try:
            # 获取频道信息
            channel = Channel.query.get(channel_id)
            if not channel:
                return {
                    'success': False,
                    'message': f'频道 {channel_id} 不存在',
                    'data': {}
                }
            
            # 确定时间范围
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days_back)
            
            # 检查是否为首次同步
            existing_videos = Video.query.filter_by(channel_id=channel_id).first()
            is_initial_sync = existing_videos is None
            
            logger.info(f"同步频道 {channel.name} 的视频 ({'首次同步' if is_initial_sync else '增量同步'})")
            
            # 获取视频列表
            videos_data = self.youtube_api.get_channel_videos(
                channel.channel_id,
                published_after=start_date,
                published_before=end_date,
                max_results=50 if is_initial_sync else 20
            )
            
            if not videos_data['success']:
                return {
                    'success': False,
                    'message': f'获取频道视频失败: {videos_data["message"]}',
                    'data': {}
                }
            
            videos = videos_data['data']['videos']
            
            if not videos:
                return {
                    'success': True,
                    'message': '没有找到新视频',
                    'data': {'new_videos': 0, 'updated_videos': 0}
                }
            
            # 处理视频数据
            new_videos = 0
            updated_videos = 0
            
            for video_info in videos:
                try:
                    # 过滤短视频（时长小于60秒）
                    if self._is_short_video(video_info.get('duration')):
                        logger.debug(f"跳过短视频: {video_info.get('title', 'unknown')}")
                        continue
                    
                    # 检查视频是否已存在（避免重复抓取）
                    existing_video = Video.query.filter_by(video_id=video_info['video_id']).first()
                    
                    if existing_video:
                        # 更新现有视频
                        updated = self._update_video(existing_video, video_info)
                        if updated:
                            updated_videos += 1
                    else:
                        # 创建新视频
                        self._create_video(channel_id, video_info)
                        new_videos += 1
                        
                except Exception as e:
                    from utils.colored_logger import log_detailed_error
                    
                    video_id = video_info.get('video_id', 'unknown')
                    video_title = video_info.get('title', 'unknown')
                    
                    # 记录详细错误信息
                    log_detailed_error(
                        logger_name="VIDEO_SYNC",
                        context=f"处理视频失败: {video_title[:50]}...",
                        error=e,
                        视频ID=video_id,
                        视频标题=video_title,
                        频道ID=channel_id,
                        视频信息=str(video_info)[:500] + "..." if len(str(video_info)) > 500 else str(video_info)
                    )
                    continue
            
            # 创建视频数据记录
            self._create_video_data_records(channel_id, date.today())
            
            return {
                'success': True,
                'message': f'同步完成: 新增 {new_videos} 个视频, 更新 {updated_videos} 个视频',
                'data': {
                    'new_videos': new_videos,
                    'updated_videos': updated_videos,
                    'total_processed': len(videos)
                }
            }
            
        except Exception as e:
            from utils.colored_logger import log_detailed_error
            
            # 记录详细错误信息
            log_detailed_error(
                logger_name="VIDEO_SYNC",
                context=f"单频道同步失败",
                error=e,
                频道ID=channel_id,
                频道信息=channel.name if 'channel' in locals() and channel else '未获取到频道信息',
                回溯天数=days_back,
                开始时间=start_date.isoformat() if 'start_date' in locals() else '未设置',
                结束时间=end_date.isoformat() if 'end_date' in locals() else '未设置'
            )
            
            return {
                'success': False,
                'message': f'同步失败: {str(e)}',
                'data': {},
                'error_details': {
                    'error_type': type(e).__name__,
                    'channel_id': channel_id,
                    'days_back': days_back
                }
            }
    
    def _create_video(self, channel_id: int, video_info: Dict) -> Video:
        """创建新视频记录"""
        # 获取频道的user_id
        channel = Channel.query.get(channel_id)
        if not channel:
            raise ValueError(f"频道ID {channel_id} 不存在")
        
        video_data = {
            'video_id': video_info['video_id'],
            'channel_id': channel_id,
            'user_id': channel.user_id,  # 添加user_id
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
            'language': video_info.get('language')
        }
        
        video = Video.create_video(video_data)
        logger.debug(f"创建新视频: {video.title}")
        return video
    
    def _update_video(self, video: Video, video_info: Dict) -> bool:
        """更新现有视频信息"""
        updated = False
        
        # 更新可能变化的字段
        if video.view_count != video_info.get('view_count', 0):
            video.view_count = video_info.get('view_count', 0)
            updated = True
        
        if video.like_count != video_info.get('like_count', 0):
            video.like_count = video_info.get('like_count', 0)
            updated = True
        
        if video.comment_count != video_info.get('comment_count', 0):
            video.comment_count = video_info.get('comment_count', 0)
            updated = True
        
        # 更新标题(可能会修改)
        if video.title != video_info.get('title', ''):
            video.title = video_info.get('title', '')
            updated = True
        
        # 更新描述
        if video.description != video_info.get('description', ''):
            video.description = video_info.get('description', '')
            updated = True
        
        if updated:
            video.updated_at = datetime.utcnow()
            db.session.commit()
            logger.debug(f"更新视频: {video.title}")
        
        return updated
    
    def _is_short_video(self, duration: str) -> bool:
        """检查是否为短视频（时长小于60秒）
        
        Args:
            duration: ISO 8601格式的时长字符串，如 PT4M13S 或 PT45S
            
        Returns:
            bool: 是否为短视频
        """
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
    
    def clean_short_videos(self) -> Dict:
        """清理数据库中的短视频记录"""
        try:
            # 找到所有短视频
            videos = Video.query.filter_by(is_active=True).all()
            short_videos = []
            
            for video in videos:
                if video.duration and self._is_short_video(video.duration):
                    short_videos.append(video)
            
            if not short_videos:
                return {
                    'success': True,
                    'message': '没有找到需要清理的短视频',
                    'data': {'cleaned_count': 0}
                }
            
            # 删除短视频及其相关数据
            cleaned_count = 0
            for video in short_videos:
                # 删除相关的VideoData记录
                VideoData.query.filter_by(video_id=video.id).delete()
                
                # 软删除视频
                video.is_active = False
                cleaned_count += 1
            
            db.session.commit()
            
            logger.info(f"清理短视频完成: {cleaned_count} 个")
            
            return {
                'success': True,
                'message': f'成功清理 {cleaned_count} 个短视频',
                'data': {'cleaned_count': cleaned_count}
            }
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"清理短视频失败: {str(e)}")
            return {
                'success': False,
                'message': f'清理短视频失败: {str(e)}',
                'data': {'cleaned_count': 0}
            }
    
    def _create_video_data_records(self, channel_id: int, record_date: date) -> None:
        """为频道的所有视频创建数据记录"""
        videos = Video.query.filter_by(channel_id=channel_id, is_active=True).all()
        
        for video in videos:
            # 检查今天是否已有记录
            existing_record = VideoData.query.filter_by(
                video_id=video.id,
                date=record_date
            ).first()
            
            if not existing_record:
                # 创建新的数据记录
                VideoData.create_video_data(
                    video_id=video.id,
                    date=record_date,
                    metrics={
                        'view_count': video.view_count,
                        'like_count': video.like_count,
                        'comment_count': video.comment_count
                    }
                )
    
    def get_channel_videos(self, channel_id: int, page: int = 1, per_page: int = 20, 
                          search: str = None, category: str = None, 
                          sort_by: str = 'published_at', order: str = 'desc', user_id: int = None) -> Dict:
        """获取频道视频列表"""
        try:
            # 构建查询 - 先验证频道权限
            if user_id is not None:
                channel = Channel.query.filter_by(id=channel_id, user_id=user_id).first()
                if not channel:
                    return {
                        'success': False,
                        'message': '频道不存在或无权限访问',
                        'data': {}
                    }
            
            query = Video.query.filter_by(channel_id=channel_id, is_active=True)
            
            # 搜索条件
            if search:
                search_term = f'%{search}%'
                query = query.filter(
                    db.or_(
                        Video.title.ilike(search_term),
                        Video.description.ilike(search_term),
                        Video.tags.ilike(search_term)
                    )
                )
            
            # 分类筛选
            if category:
                query = query.filter_by(category_name=category)
            
            # 排序
            if sort_by == 'view_count':
                query = query.order_by(Video.view_count.desc() if order == 'desc' else Video.view_count.asc())
            elif sort_by == 'like_count':
                query = query.order_by(Video.like_count.desc() if order == 'desc' else Video.like_count.asc())
            elif sort_by == 'comment_count':
                query = query.order_by(Video.comment_count.desc() if order == 'desc' else Video.comment_count.asc())
            else:  # published_at
                query = query.order_by(Video.published_at.desc() if order == 'desc' else Video.published_at.asc())
            
            # 分页
            pagination = query.paginate(
                page=page,
                per_page=per_page,
                error_out=False
            )
            
            videos = [video.to_dict() for video in pagination.items]
            
            return {
                'success': True,
                'data': {
                    'videos': videos,
                    'pagination': {
                        'page': page,
                        'per_page': per_page,
                        'total': pagination.total,
                        'pages': pagination.pages,
                        'has_next': pagination.has_next,
                        'has_prev': pagination.has_prev
                    }
                }
            }
            
        except Exception as e:
            logger.error(f"获取频道视频列表失败: {str(e)}")
            return {
                'success': False,
                'message': f'获取视频列表失败: {str(e)}',
                'data': {}
            }
    
    def search_videos(self, search: str, page: int = 1, per_page: int = 20, 
                     channel_id: int = None, category: str = None,
                     sort_by: str = 'published_at', order: str = 'desc', user_id: int = None) -> Dict:
        """搜索视频"""
        try:
            # 构建查询
            query = Video.query.join(Channel).filter(Video.is_active == True)
            
            # 用户数据隔离
            if user_id is not None:
                query = query.filter(Channel.user_id == user_id)
            
            # 频道筛选
            if channel_id:
                query = query.filter(Video.channel_id == channel_id)
            
            # 搜索条件
            if search:
                search_term = f'%{search}%'
                query = query.filter(
                    db.or_(
                        Video.title.ilike(search_term),
                        Video.description.ilike(search_term),
                        Video.tags.ilike(search_term)
                    )
                )
            
            # 分类筛选
            if category:
                query = query.filter_by(category_name=category)
            
            # 排序
            if sort_by == 'view_count':
                query = query.order_by(Video.view_count.desc() if order == 'desc' else Video.view_count.asc())
            elif sort_by == 'like_count':
                query = query.order_by(Video.like_count.desc() if order == 'desc' else Video.like_count.asc())
            elif sort_by == 'comment_count':
                query = query.order_by(Video.comment_count.desc() if order == 'desc' else Video.comment_count.asc())
            else:  # published_at
                query = query.order_by(Video.published_at.desc() if order == 'desc' else Video.published_at.asc())
            
            # 分页
            pagination = query.paginate(
                page=page,
                per_page=per_page,
                error_out=False
            )
            
            videos = [video.to_dict() for video in pagination.items]
            
            return {
                'success': True,
                'data': {
                    'videos': videos,
                    'search': search,
                    'pagination': {
                        'page': page,
                        'per_page': per_page,
                        'total': pagination.total,
                        'pages': pagination.pages,
                        'has_next': pagination.has_next,
                        'has_prev': pagination.has_prev
                    }
                }
            }
            
        except Exception as e:
            logger.error(f"搜索视频失败: {str(e)}")
            return {
                'success': False,
                'message': f'搜索失败: {str(e)}',
                'data': {}
            }
    
    def get_video_categories(self) -> Dict:
        """获取视频分类统计"""
        try:
            # 获取所有活跃视频的分类
            categories = db.session.query(
                Video.category_name,
                db.func.count(Video.id).label('count')
            ).filter_by(is_active=True).group_by(Video.category_name).all()
            
            categories_data = []
            for category, count in categories:
                if category:  # 排除空分类
                    categories_data.append({
                        'name': category,
                        'count': count
                    })
            
            return {
                'success': True,
                'data': {
                    'categories': categories_data,
                    'total': len(categories_data)
                }
            }
            
        except Exception as e:
            logger.error(f"获取视频分类失败: {str(e)}")
            return {
                'success': False,
                'message': f'获取分类失败: {str(e)}',
                'data': {}
            }
    
    def get_videos_stats(self) -> Dict:
        """获取视频统计信息"""
        try:
            # 总视频数
            total_videos = Video.query.filter_by(is_active=True).count()
            
            # 今天的视频数
            today = date.today()
            today_videos = Video.query.filter(
                Video.is_active == True,
                db.func.date(Video.published_at) == today
            ).count()
            
            # 本周的视频数
            week_start = today - timedelta(days=today.weekday())
            week_videos = Video.query.filter(
                Video.is_active == True,
                db.func.date(Video.published_at) >= week_start
            ).count()
            
            # 热门视频（按播放量排序）
            top_videos = Video.query.filter_by(is_active=True).order_by(
                Video.view_count.desc()
            ).limit(10).all()
            
            return {
                'success': True,
                'data': {
                    'total_videos': total_videos,
                    'today_videos': today_videos,
                    'week_videos': week_videos,
                    'top_videos': [video.to_dict() for video in top_videos]
                }
            }
            
        except Exception as e:
            logger.error(f"获取视频统计失败: {str(e)}")
            return {
                'success': False,
                'message': f'获取统计失败: {str(e)}',
                'data': {}
            }