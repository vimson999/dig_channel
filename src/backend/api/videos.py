# 视频管理API
from flask import Blueprint, request, jsonify
from datetime import datetime, timedelta
import logging
from sqlalchemy.orm import load_only

from services.video_sync_service import VideoSyncService
from services.optimized_video_sync_service import OptimizedVideoSyncService
from models import Video, VideoData, Channel
from middleware.error_handler import handle_api_error
from utils.colored_logger import setup_colored_logger
from utils.auth_utils import require_login

logger = setup_colored_logger("VIDEO_API")

videos_bp = Blueprint('videos', __name__, url_prefix='/api/v1/videos')

@videos_bp.route('', methods=['GET'])
@require_login
def get_videos(current_user):
    """获取视频列表"""
    try:
        # 获取查询参数
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 20, type=int), 100)
        search = request.args.get('search', '').strip()
        channel_id = request.args.get('channel_id', type=int)
        category = request.args.get('category', '').strip()
        sort_by = request.args.get('sort_by', 'published_at')
        order = request.args.get('order', 'desc')
        
        # 时间段参数（天数）
        time_period = request.args.get('time_period', type=int)
        
        # 验证排序字段
        valid_sort_fields = ['published_at', 'view_count', 'like_count', 'comment_count']
        if sort_by not in valid_sort_fields:
            sort_by = 'published_at'
        
        # 验证排序顺序
        if order not in ['asc', 'desc']:
            order = 'desc'
        
        # 验证时间段
        if time_period and (time_period < 1 or time_period > 3650):  # 最多10年
            time_period = None
        
        video_service = VideoSyncService()
        
        if search:
            # 使用搜索功能
            result = video_service.search_videos(
                search=search,
                page=page,
                per_page=per_page,
                channel_id=channel_id,
                category=category if category else None,
                sort_by=sort_by,
                order=order,
                time_period_days=time_period,
                user_id=current_user.id
            )
        elif channel_id:
            # 获取特定频道的视频
            result = video_service.get_channel_videos(
                channel_id=channel_id,
                page=page,
                per_page=per_page,
                search=None,
                category=category if category else None,
                sort_by=sort_by,
                order=order,
                time_period_days=time_period,
                user_id=current_user.id
            )
        else:
            # 获取所有视频
            result = video_service.search_videos(
                search='',
                page=page,
                per_page=per_page,
                channel_id=None,
                category=category if category else None,
                sort_by=sort_by,
                order=order,
                time_period_days=time_period,
                user_id=current_user.id
            )
        
        if result['success']:
            return jsonify({
                'success': True,
                'data': result['data'],
                'message': '视频列表获取成功'
            })
        else:
            return jsonify({
                'success': False,
                'message': result['message'],
                'data': {}
            }), 500
            
    except Exception as e:
        logger.error(f"获取视频列表失败: {str(e)}")
        return handle_api_error(500, f"操作失败: {str(e)}")

@videos_bp.route('/search', methods=['GET'])
@require_login
def search_videos(current_user):
    """搜索视频"""
    try:
        # 获取查询参数
        search = request.args.get('q', '').strip()
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 20, type=int), 100)
        channel_id = request.args.get('channel_id', type=int)
        category = request.args.get('category', '').strip()
        sort_by = request.args.get('sort_by', 'published_at')
        order = request.args.get('order', 'desc')
        
        # 时间段参数（天数）
        time_period_days = request.args.get('time_period', type=int)
        
        if not search:
            return jsonify({
                'success': False,
                'message': '搜索关键词不能为空',
                'data': {}
            }), 400
        
        # 验证参数
        valid_sort_fields = ['published_at', 'view_count', 'like_count', 'comment_count']
        if sort_by not in valid_sort_fields:
            sort_by = 'published_at'
        
        if order not in ['asc', 'desc']:
            order = 'desc'
            
        # 验证时间段
        if time_period_days and (time_period_days < 1 or time_period_days > 3650):  # 最多10年
            time_period_days = None
        
        video_service = VideoSyncService()
        result = video_service.search_videos(
            search=search,
            page=page,
            per_page=per_page,
            channel_id=channel_id,
            category=category if category else None,
            sort_by=sort_by,
            order=order,
            time_period_days=time_period_days if time_period_days else None,
            user_id=current_user.id
        )
        
        if result['success']:
            return jsonify({
                'success': True,
                'data': result['data'],
                'message': f'搜索到 {result["data"]["pagination"]["total"]} 个结果'
            })
        else:
            return jsonify({
                'success': False,
                'message': result['message'],
                'data': {}
            }), 500
            
    except Exception as e:
        logger.error(f"搜索视频失败: {str(e)}")
        return handle_api_error(500, f"搜索视频失败: {str(e)}")

@videos_bp.route('/<int:video_id>', methods=['GET'])
def get_video_detail(video_id):
    """获取视频详情"""
    try:
        video = Video.query.get(video_id)
        if not video:
            return jsonify({
                'success': False,
                'message': '视频不存在',
                'data': {}
            }), 404
        
        # 获取视频数据历史（最近30天）
        video_data = VideoData.query.filter_by(video_id=video_id)\
            .filter(VideoData.date >= datetime.now().date() - timedelta(days=30))\
            .order_by(VideoData.date.desc()).all()
        
        video_dict = video.to_dict()
        video_dict['data_history'] = [data.to_dict() for data in video_data]
        video_dict['engagement_rate'] = video.get_engagement_rate()
        video_dict['duration_display'] = video.get_duration_display()
        
        return jsonify({
            'success': True,
            'data': {'video': video_dict},
            'message': '视频详情获取成功'
        })
        
    except Exception as e:
        logger.error(f"获取视频详情失败: {str(e)}")
        return handle_api_error(500, f"操作失败: {str(e)}")

@videos_bp.route('/sync', methods=['POST'])
@require_login
def sync_videos(current_user):
    """同步所有频道的视频（优化版本）"""
    try:
        # 获取参数
        data = request.get_json() or {}
        days_back = data.get('days_back', 10)
        use_optimization = data.get('use_optimization', True)  # 默认使用优化版本
        max_workers = data.get('max_workers', 5)  # 并行线程数
        
        # 验证参数
        if not isinstance(days_back, int) or days_back < 1 or days_back > 30:
            return jsonify({
                'success': False,
                'message': '回溯天数必须在1-30之间',
                'data': {}
            }), 400
        
        if not isinstance(max_workers, int) or max_workers < 1 or max_workers > 10:
            max_workers = 5  # 默认值
        
        # 选择使用优化版本还是原版本
        if use_optimization:
            logger.info(f"使用优化同步服务，并行线程数: {max_workers}")
            video_service = OptimizedVideoSyncService()
            result = video_service.sync_all_channels_videos_parallel(
                days_back=days_back, 
                user_id=current_user.id,
                max_workers=max_workers
            )
        else:
            logger.info("使用传统同步服务")
            video_service = VideoSyncService()
            result = video_service.sync_all_channels_videos(days_back=days_back, user_id=current_user.id)
        
        if result['success']:
            # 检查是否是因为没有频道
            if result['data'].get('no_channels'):
                return jsonify({
                    'success': True,
                    'data': result['data'],
                    'message': result['message']
                }), 200
            else:
                return jsonify({
                    'success': True,
                    'data': result['data'],
                    'message': result['message']
                })
        else:
            return jsonify({
                'success': False,
                'message': result['message'],
                'data': result['data']
            }), 500
            
    except Exception as e:
        logger.error(f"同步视频失败: {str(e)}")
        return handle_api_error(500, f"操作失败: {str(e)}")

@videos_bp.route('/sync/<int:channel_id>', methods=['POST'])
def sync_channel_videos(channel_id):
    """同步单个频道的视频"""
    try:
        # 检查频道是否存在
        channel = Channel.query.get(channel_id)
        if not channel:
            return jsonify({
                'success': False,
                'message': '频道不存在',
                'data': {}
            }), 404
        
        # 获取参数
        data = request.get_json() or {}
        days_back = data.get('days_back', 10)
        
        # 验证参数
        if not isinstance(days_back, int) or days_back < 1 or days_back > 30:
            return jsonify({
                'success': False,
                'message': '回溯天数必须在1-30之间',
                'data': {}
            }), 400
        
        video_service = VideoSyncService()
        result = video_service.sync_channel_videos(channel_id=channel_id, days_back=days_back)
        
        if result['success']:
            return jsonify({
                'success': True,
                'data': result['data'],
                'message': result['message']
            })
        else:
            return jsonify({
                'success': False,
                'message': result['message'],
                'data': result['data']
            }), 500
            
    except Exception as e:
        logger.error(f"同步频道 {channel_id} 视频失败: {str(e)}")
        return handle_api_error(500, f"操作失败: {str(e)}")

@videos_bp.route('/categories', methods=['GET'])
def get_video_categories():
    """获取视频分类统计"""
    try:
        video_service = VideoSyncService()
        result = video_service.get_video_categories()
        
        if result['success']:
            return jsonify({
                'success': True,
                'data': result['data'],
                'message': '分类统计获取成功'
            })
        else:
            return jsonify({
                'success': False,
                'message': result['message'],
                'data': {}
            }), 500
            
    except Exception as e:
        logger.error(f"获取视频分类失败: {str(e)}")
        return handle_api_error(500, f"操作失败: {str(e)}")

@videos_bp.route('/stats', methods=['GET'])
def get_videos_stats():
    """获取视频统计信息"""
    try:
        video_service = VideoSyncService()
        result = video_service.get_videos_stats()
        
        if result['success']:
            return jsonify({
                'success': True,
                'data': result['data'],
                'message': '统计信息获取成功'
            })
        else:
            return jsonify({
                'success': False,
                'message': result['message'],
                'data': {}
            }), 500
            
    except Exception as e:
        logger.error(f"获取视频统计失败: {str(e)}")
        return handle_api_error(500, f"操作失败: {str(e)}")

@videos_bp.route('/clean-shorts', methods=['POST'])
def clean_short_videos():
    """清理数据库中的短视频记录"""
    try:
        video_service = VideoSyncService()
        result = video_service.clean_short_videos()
        
        if result['success']:
            return jsonify({
                'success': True,
                'data': result['data'],
                'message': result['message']
            })
        else:
            return jsonify({
                'success': False,
                'message': result['message'],
                'data': result['data']
            }), 500
            
    except Exception as e:
        logger.error(f"清理短视频失败: {str(e)}")
        return handle_api_error(500, f"操作失败: {str(e)}")

@videos_bp.route('/channels', methods=['GET'])
def get_channels_with_video_count():
    """获取频道列表及其视频数量"""
    try:
        from sqlalchemy import func
        
        # 获取参数
        include_inactive = request.args.get('include_inactive', 'false').lower() == 'true'
        
        # 构建查询
        query = Channel.query
        if not include_inactive:
            query = query.filter_by(is_active=True)
        
        # 查询频道及其视频数量
        channels = query.options(
            load_only(Channel.id, Channel.name, Channel.category)
        ).outerjoin(Video)\
            .add_columns(func.count(Video.id).label('video_count'))\
            .group_by(Channel.id)\
            .all()
        
        channels_data = []
        for channel, video_count in channels:
            channels_data.append(channel.to_video_filter_dict(video_count=video_count))
        
        return jsonify({
            'success': True,
            'data': {
                'channels': channels_data,
                'total': len(channels_data)
            },
            'message': '频道列表获取成功'
        })
        
    except Exception as e:
        logger.error(f"获取频道列表失败: {str(e)}")
        return handle_api_error(500, f"操作失败: {str(e)}")

# 错误处理
@videos_bp.errorhandler(404)
def not_found(error):
    return jsonify({
        'success': False,
        'message': '请求的资源不存在',
        'data': {}
    }), 404

@videos_bp.errorhandler(400)
def bad_request(error):
    return jsonify({
        'success': False,
        'message': '请求参数错误',
        'data': {}
    }), 400

@videos_bp.errorhandler(500)
def internal_error(error):
    return jsonify({
        'success': False,
        'message': '服务器内部错误',
        'data': {}
    }), 500
