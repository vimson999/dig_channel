# 视频管理API
from flask import Blueprint, request, jsonify
from flask_cors import cross_origin
from datetime import datetime, timedelta
import logging

from services.video_sync_service import VideoSyncService
from models import Video, VideoData, Channel
from middleware.error_handler import handle_api_error
from utils.colored_logger import setup_colored_logger

logger = setup_colored_logger("VIDEO_API")

videos_bp = Blueprint('videos', __name__, url_prefix='/api/v1/videos')

@videos_bp.route('', methods=['GET'])
@cross_origin()
def get_videos():
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
        
        # 验证排序字段
        valid_sort_fields = ['published_at', 'view_count', 'like_count', 'comment_count']
        if sort_by not in valid_sort_fields:
            sort_by = 'published_at'
        
        # 验证排序顺序
        if order not in ['asc', 'desc']:
            order = 'desc'
        
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
                order=order
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
                order=order
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
                order=order
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
        return handle_api_error(e)

@videos_bp.route('/search', methods=['GET'])
@cross_origin()
def search_videos():
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
        
        video_service = VideoSyncService()
        result = video_service.search_videos(
            search=search,
            page=page,
            per_page=per_page,
            channel_id=channel_id,
            category=category if category else None,
            sort_by=sort_by,
            order=order
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
        return handle_api_error(e)

@videos_bp.route('/<int:video_id>', methods=['GET'])
@cross_origin()
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
        return handle_api_error(e)

@videos_bp.route('/sync', methods=['POST'])
@cross_origin()
def sync_videos():
    """同步所有频道的视频"""
    try:
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
        result = video_service.sync_all_channels_videos(days_back=days_back)
        
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
        logger.error(f"同步视频失败: {str(e)}")
        return handle_api_error(e)

@videos_bp.route('/sync/<int:channel_id>', methods=['POST'])
@cross_origin()
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
        return handle_api_error(e)

@videos_bp.route('/categories', methods=['GET'])
@cross_origin()
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
        return handle_api_error(e)

@videos_bp.route('/stats', methods=['GET'])
@cross_origin()
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
        return handle_api_error(e)

@videos_bp.route('/clean-shorts', methods=['POST'])
@cross_origin()
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
        return handle_api_error(e)

@videos_bp.route('/channels', methods=['GET'])
@cross_origin()
def get_channels_with_video_count():
    """获取频道列表及其视频数量"""
    try:
        from sqlalchemy import func
        
        # 查询频道及其视频数量
        channels = Channel.query.filter_by(is_active=True)\
            .outerjoin(Video)\
            .add_columns(func.count(Video.id).label('video_count'))\
            .group_by(Channel.id)\
            .all()
        
        channels_data = []
        for channel, video_count in channels:
            channel_dict = channel.to_dict()
            channel_dict['video_count'] = video_count
            channels_data.append(channel_dict)
        
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
        return handle_api_error(e)

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