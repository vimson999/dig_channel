# 收藏视频API
"""
提供视频收藏相关的功能
"""

from flask import Blueprint, request, jsonify
from utils.api_utils import handle_api_error
from utils.auth_utils import require_login
from models import db, FavoriteVideo
from datetime import datetime

# 创建蓝图
bp = Blueprint('favorites', __name__, url_prefix='/api/v1/favorites')

@bp.route('/', methods=['GET'])
@require_login
def get_favorites(current_user):
    """获取用户收藏列表
    
    Query Parameters:
        page (int): 页码，默认1
        per_page (int): 每页数量，默认20，最大100
        order_by (str): 排序字段 (favorited_at, hot_score, published_at), 默认 'favorited_at'
        order (str): 排序方向 (desc, asc), 默认 'desc'
    
    Returns:
        JSON: 收藏视频列表
    """
    try:
        # 获取参数
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 20, type=int), 100)
        order_by = request.args.get('order_by', 'favorited_at')
        order = request.args.get('order', 'desc')
        
        # 验证排序字段
        valid_order_fields = ['favorited_at', 'hot_score', 'published_at', 'view_count']
        if order_by not in valid_order_fields:
            order_by = 'favorited_at'
        
        # 构建查询
        query = FavoriteVideo.query.filter_by(user_id=current_user.id)
        
        # 应用排序
        if order_by == 'favorited_at':
            order_field = FavoriteVideo.favorited_at
        elif order_by == 'hot_score':
            order_field = FavoriteVideo.hot_score
        elif order_by == 'published_at':
            order_field = FavoriteVideo.published_at
        elif order_by == 'view_count':
            order_field = FavoriteVideo.view_count
        
        if order == 'desc':
            query = query.order_by(order_field.desc())
        else:
            query = query.order_by(order_field.asc())
        
        # 分页
        pagination = query.paginate(
            page=page, 
            per_page=per_page, 
            error_out=False
        )
        
        # 转换为字典
        favorites = [fav.to_dict() for fav in pagination.items]
        
        return jsonify({
            'success': True,
            'data': {
                'favorites': favorites,
                'pagination': {
                    'page': page,
                    'per_page': per_page,
                    'total': pagination.total,
                    'pages': pagination.pages,
                    'has_next': pagination.has_next,
                    'has_prev': pagination.has_prev
                }
            }
        })
        
    except Exception as e:
        return handle_api_error(500, f"获取收藏列表失败: {str(e)}")

@bp.route('/', methods=['POST'])
@require_login
def add_favorite(current_user):
    """添加视频到收藏
    
    Request Body:
        video_data (dict): 完整的视频数据对象
    
    Returns:
        JSON: 收藏结果
    """
    try:
        data = request.get_json()
        print(f"收到收藏请求数据: {data}")
        
        if not data or 'video_data' not in data:
            print("错误: 缺少video_data字段")
            return handle_api_error(400, "视频数据不能为空")
        
        video_data = data['video_data']
        print(f"视频数据: {video_data}")
        
        if not video_data.get('video_id'):
            print("错误: 缺少video_id字段")
            return handle_api_error(400, "视频ID不能为空")
        
        # 创建收藏
        favorite, is_new = FavoriteVideo.create_favorite(current_user.id, video_data)
        
        if is_new:
            return jsonify({
                'success': True,
                'message': '收藏成功',
                'data': {
                    'favorite': favorite.to_dict(),
                    'is_new': True
                }
            })
        else:
            return jsonify({
                'success': True,
                'message': '视频已在收藏中',
                'data': {
                    'favorite': favorite.to_dict(),
                    'is_new': False
                }
            })
            
    except Exception as e:
        return handle_api_error(500, f"添加收藏失败: {str(e)}")

@bp.route('/<video_id>', methods=['DELETE'])
@require_login
def remove_favorite(current_user, video_id):
    """移除收藏
    
    Path Parameters:
        video_id (str): 视频ID
    
    Returns:
        JSON: 移除结果
    """
    try:
        success = FavoriteVideo.remove_favorite(current_user.id, video_id)
        
        if success:
            return jsonify({
                'success': True,
                'message': '已从收藏中移除'
            })
        else:
            return handle_api_error(404, "收藏记录不存在")
            
    except Exception as e:
        return handle_api_error(500, f"移除收藏失败: {str(e)}")

@bp.route('/check/<video_id>', methods=['GET'])
@require_login
def check_favorite(current_user, video_id):
    """检查视频是否已收藏
    
    Path Parameters:
        video_id (str): 视频ID
    
    Returns:
        JSON: 收藏状态
    """
    try:
        is_favorited = FavoriteVideo.is_favorited(current_user.id, video_id)
        
        return jsonify({
            'success': True,
            'data': {
                'is_favorited': is_favorited,
                'video_id': video_id
            }
        })
        
    except Exception as e:
        return handle_api_error(500, f"检查收藏状态失败: {str(e)}")

@bp.route('/batch-check', methods=['POST'])
@require_login
def batch_check_favorites(current_user):
    """批量检查视频收藏状态
    
    Request Body:
        video_ids (list): 视频ID列表
    
    Returns:
        JSON: 批量收藏状态
    """
    try:
        data = request.get_json()
        if not data or 'video_ids' not in data:
            return handle_api_error(400, "视频ID列表不能为空")
        
        video_ids = data['video_ids']
        if not isinstance(video_ids, list) or len(video_ids) > 100:
            return handle_api_error(400, "视频ID列表格式错误或超过最大限制(100个)")
        
        # 查询收藏状态
        favorites = FavoriteVideo.query.filter(
            FavoriteVideo.user_id == current_user.id,
            FavoriteVideo.video_id.in_(video_ids)
        ).all()
        
        favorited_ids = {fav.video_id for fav in favorites}
        
        # 构建结果
        result = {}
        for video_id in video_ids:
            result[video_id] = video_id in favorited_ids
        
        return jsonify({
            'success': True,
            'data': {
                'favorites_status': result
            }
        })
        
    except Exception as e:
        return handle_api_error(500, f"批量检查收藏状态失败: {str(e)}")

@bp.route('/stats', methods=['GET'])
@require_login
def get_favorite_stats(current_user):
    """获取收藏统计信息
    
    Returns:
        JSON: 收藏统计
    """
    try:
        from sqlalchemy import func
        
        # 基本统计
        total_count = FavoriteVideo.get_user_favorite_count(current_user.id)
        
        # 按日期统计（最近30天）
        from datetime import date, timedelta
        thirty_days_ago = date.today() - timedelta(days=30)
        
        recent_count = FavoriteVideo.query.filter(
            FavoriteVideo.user_id == current_user.id,
            FavoriteVideo.favorited_at >= thirty_days_ago
        ).count()
        
        # 平均热度评分
        avg_hot_score = db.session.query(func.avg(FavoriteVideo.hot_score)).filter(
            FavoriteVideo.user_id == current_user.id
        ).scalar() or 0
        
        # 最高热度评分
        max_hot_score = db.session.query(func.max(FavoriteVideo.hot_score)).filter(
            FavoriteVideo.user_id == current_user.id
        ).scalar() or 0
        
        # 总播放量
        total_views = db.session.query(func.sum(FavoriteVideo.view_count)).filter(
            FavoriteVideo.user_id == current_user.id
        ).scalar() or 0
        
        return jsonify({
            'success': True,
            'data': {
                'total_count': total_count,
                'recent_count': recent_count,
                'avg_hot_score': round(float(avg_hot_score), 2),
                'max_hot_score': round(float(max_hot_score), 2),
                'total_views': int(total_views)
            }
        })
        
    except Exception as e:
        return handle_api_error(500, f"获取收藏统计失败: {str(e)}")

@bp.route('/clear', methods=['DELETE'])
@require_login
def clear_all_favorites(current_user):
    """清空所有收藏
    
    Returns:
        JSON: 清空结果
    """
    try:
        # 删除用户所有收藏
        deleted_count = FavoriteVideo.query.filter_by(user_id=current_user.id).delete()
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'已清空所有收藏 ({deleted_count}个)',
            'data': {
                'deleted_count': deleted_count
            }
        })
        
    except Exception as e:
        db.session.rollback()
        return handle_api_error(500, f"清空收藏失败: {str(e)}")

@bp.route('/<int:favorite_id>/note', methods=['PUT'])
@require_login
def update_favorite_note(current_user, favorite_id):
    """更新收藏备注
    
    Path Parameters:
        favorite_id (int): 收藏记录ID
    
    Request Body:
        note (str): 备注内容
    
    Returns:
        JSON: 更新结果
    """
    try:
        data = request.get_json()
        note = data.get('note', '') if data else ''
        
        # 查找收藏记录
        favorite = FavoriteVideo.query.filter_by(
            id=favorite_id, 
            user_id=current_user.id
        ).first()
        
        if not favorite:
            return handle_api_error(404, "收藏记录不存在")
        
        # 更新备注
        favorite.note = note
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '备注更新成功',
            'data': {
                'favorite': favorite.to_dict()
            }
        })
        
    except Exception as e:
        db.session.rollback()
        return handle_api_error(500, f"更新备注失败: {str(e)}")