# 频道管理API
"""
频道管理相关的RESTful API接口

提供频道的CRUD操作、搜索、统计等功能
"""

from flask import Blueprint, request, jsonify
from services import ChannelService, YouTubeSyncService
from utils.api_utils import validate_json, handle_api_error
from utils.decorators import validate_request

# 创建蓝图
bp = Blueprint('channels', __name__, url_prefix='/api/v1/channels')

# 初始化服务
youtube_sync = YouTubeSyncService()

@bp.route('', methods=['GET'])
def get_channels():
    """获取频道列表
    
    Query Parameters:
        active_only (bool): 是否只返回活跃频道，默认True
        category (str): 按分类筛选
        search (str): 搜索关键词
        page (int): 页码，默认1
        per_page (int): 每页数量，默认10
    
    Returns:
        JSON: 频道列表和分页信息
    """
    try:
        # 获取查询参数
        active_only = request.args.get('active_only', 'true').lower() == 'true'
        category = request.args.get('category')
        search = request.args.get('search')
        page = int(request.args.get('page', 1))
        per_page = min(int(request.args.get('per_page', 10)), 100)  # 限制最大100
        
        # 根据不同条件获取频道
        if search:
            channels = ChannelService.search_channels(search, active_only)
        elif category:
            channels = ChannelService.get_channels_by_category(category, active_only)
        else:
            channels = ChannelService.get_all_channels(active_only)
        
        # 分页处理
        total_count = len(channels)
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        page_channels = channels[start_idx:end_idx]
        
        return jsonify({
            'success': True,
            'data': {
                'channels': page_channels,
                'pagination': {
                    'page': page,
                    'per_page': per_page,
                    'total': total_count,
                    'pages': (total_count + per_page - 1) // per_page,
                    'has_next': end_idx < total_count,
                    'has_prev': page > 1
                }
            }
        })
        
    except ValueError as e:
        return handle_api_error(400, f"参数错误: {str(e)}")
    except Exception as e:
        return handle_api_error(500, f"获取频道列表失败: {str(e)}")

@bp.route('/<int:channel_id>', methods=['GET'])
def get_channel(channel_id):
    """获取单个频道详情
    
    Args:
        channel_id (int): 频道ID
    
    Returns:
        JSON: 频道详情
    """
    try:
        channel = ChannelService.get_channel_by_id(channel_id)
        if not channel:
            return handle_api_error(404, "频道不存在")
        
        return jsonify({
            'success': True,
            'data': channel
        })
        
    except Exception as e:
        return handle_api_error(500, f"获取频道详情失败: {str(e)}")

@bp.route('', methods=['POST'])
@validate_request(['url'])
def create_channel():
    """创建新频道
    
    Request Body:
        url (str): YouTube频道URL (必填)
        name (str): 频道名称 (可选，将从API获取)
        cpm (float): CPM值，默认1.00
        category (str): 分类 (可选)
    
    Returns:
        JSON: 创建结果
    """
    try:
        data = request.get_json()
        
        # 验证URL
        url = data.get('url', '').strip()
        if not url:
            return handle_api_error(400, "频道URL不能为空")
        
        # 可选参数
        name = data.get('name', '').strip() or None
        cpm = float(data.get('cpm', 1.00))
        category = data.get('category', '').strip() or None
        
        # 验证CPM值
        if cpm < 0:
            return handle_api_error(400, "CPM值不能为负数")
        
        # 添加频道
        result = youtube_sync.add_channel_from_url(
            url=url,
            name=name,
            cpm=cpm,
            category=category
        )
        
        if result['success']:
            return jsonify({
                'success': True,
                'message': result['message'],
                'data': result['data']
            }), 201
        else:
            return handle_api_error(400, result['message'])
            
    except ValueError as e:
        return handle_api_error(400, f"参数错误: {str(e)}")
    except Exception as e:
        return handle_api_error(500, f"创建频道失败: {str(e)}")

@bp.route('/<int:channel_id>', methods=['PUT'])
@validate_request()
def update_channel(channel_id):
    """更新频道信息
    
    Args:
        channel_id (int): 频道ID
    
    Request Body:
        name (str): 频道名称
        cpm (float): CPM值
        category (str): 分类
        is_active (bool): 是否活跃
    
    Returns:
        JSON: 更新结果
    """
    try:
        data = request.get_json()
        
        # 构建更新参数
        update_params = {}
        
        if 'name' in data:
            name = data['name'].strip()
            if not name:
                return handle_api_error(400, "频道名称不能为空")
            update_params['name'] = name
        
        if 'cpm' in data:
            cpm = float(data['cpm'])
            if cpm < 0:
                return handle_api_error(400, "CPM值不能为负数")
            update_params['cpm'] = cpm
        
        if 'category' in data:
            update_params['category'] = data['category'].strip() or None
        
        if 'is_active' in data:
            update_params['is_active'] = bool(data['is_active'])
        
        if not update_params:
            return handle_api_error(400, "没有需要更新的字段")
        
        # 更新频道
        result = ChannelService.update_channel(channel_id, **update_params)
        
        if result['success']:
            return jsonify({
                'success': True,
                'message': result['message'],
                'data': result['data']
            })
        else:
            return handle_api_error(400, result['message'])
            
    except ValueError as e:
        return handle_api_error(400, f"参数错误: {str(e)}")
    except Exception as e:
        return handle_api_error(500, f"更新频道失败: {str(e)}")

@bp.route('/<int:channel_id>', methods=['DELETE'])
def delete_channel(channel_id):
    """删除频道（软删除）
    
    Args:
        channel_id (int): 频道ID
    
    Returns:
        JSON: 删除结果
    """
    try:
        result = ChannelService.delete_channel(channel_id)
        
        if result['success']:
            return jsonify({
                'success': True,
                'message': result['message'],
                'data': result['data']
            })
        else:
            return handle_api_error(400, result['message'])
            
    except Exception as e:
        return handle_api_error(500, f"删除频道失败: {str(e)}")

@bp.route('/<int:channel_id>/statistics', methods=['GET'])
def get_channel_statistics(channel_id):
    """获取频道统计信息
    
    Args:
        channel_id (int): 频道ID
    
    Returns:
        JSON: 统计信息
    """
    try:
        result = ChannelService.get_channel_statistics(channel_id)
        
        if result['success']:
            return jsonify({
                'success': True,
                'data': result['data']
            })
        else:
            return handle_api_error(404, result['message'])
            
    except Exception as e:
        return handle_api_error(500, f"获取统计信息失败: {str(e)}")

@bp.route('/<int:channel_id>/sync', methods=['POST'])
def sync_channel_data(channel_id):
    """同步频道数据
    
    Args:
        channel_id (int): 频道ID
    
    Request Body:
        date (str): 目标日期，格式YYYY-MM-DD，可选，默认今天
    
    Returns:
        JSON: 同步结果
    """
    try:
        data = request.get_json() or {}
        
        # 解析日期
        target_date = None
        if 'date' in data:
            from datetime import datetime
            target_date = datetime.strptime(data['date'], '%Y-%m-%d').date()
        
        # 同步数据
        result = youtube_sync.sync_channel_data(channel_id, target_date)
        
        if result['success']:
            return jsonify({
                'success': True,
                'message': result['message'],
                'data': result['data']
            })
        else:
            return handle_api_error(400, result['message'])
            
    except ValueError as e:
        return handle_api_error(400, f"日期格式错误: {str(e)}")
    except Exception as e:
        return handle_api_error(500, f"同步数据失败: {str(e)}")

@bp.route('/preview', methods=['POST'])
@validate_request(['url'])
def preview_channel():
    """预览频道信息（不添加到数据库）
    
    Request Body:
        url (str): YouTube频道URL
    
    Returns:
        JSON: 频道预览信息
    """
    try:
        data = request.get_json()
        url = data.get('url', '').strip()
        
        if not url:
            return handle_api_error(400, "频道URL不能为空")
        
        # 预览频道
        result = youtube_sync.validate_and_preview_channel(url)
        
        if result['success']:
            return jsonify({
                'success': True,
                'data': result['data']
            })
        else:
            return handle_api_error(400, result['message'])
            
    except Exception as e:
        return handle_api_error(500, f"预览频道失败: {str(e)}")

@bp.route('/sync/all', methods=['POST'])
def sync_all_channels():
    """批量同步所有频道数据
    
    Request Body:
        date (str): 目标日期，格式YYYY-MM-DD，可选，默认今天
    
    Returns:
        JSON: 批量同步结果
    """
    try:
        data = request.get_json() or {}
        
        # 解析日期
        target_date = None
        if 'date' in data:
            from datetime import datetime
            target_date = datetime.strptime(data['date'], '%Y-%m-%d').date()
        
        # 批量同步
        result = youtube_sync.sync_all_channels(target_date)
        
        if result['success']:
            return jsonify({
                'success': True,
                'message': result['message'],
                'data': result['data']
            })
        else:
            return handle_api_error(500, result['message'])
            
    except ValueError as e:
        return handle_api_error(400, f"日期格式错误: {str(e)}")
    except Exception as e:
        return handle_api_error(500, f"批量同步失败: {str(e)}")

@bp.route('/categories', methods=['GET'])
def get_categories():
    """获取所有频道分类
    
    Returns:
        JSON: 分类列表
    """
    try:
        from models import db, Channel
        from sqlalchemy import func
        
        # 查询所有不为空的分类
        categories = db.session.query(
            Channel.category,
            func.count(Channel.id).label('count')
        ).filter(
            Channel.category.isnot(None),
            Channel.category != '',
            Channel.is_active == True
        ).group_by(Channel.category).order_by(
            func.count(Channel.id).desc()
        ).all()
        
        category_list = [
            {
                'name': cat[0],
                'count': cat[1]
            }
            for cat in categories
        ]
        
        return jsonify({
            'success': True,
            'data': {
                'categories': category_list,
                'total': len(category_list)
            }
        })
        
    except Exception as e:
        return handle_api_error(500, f"获取分类失败: {str(e)}")

@bp.route('/search', methods=['GET'])
def search_channels():
    """搜索频道
    
    Query Parameters:
        q (str): 搜索关键词
        active_only (bool): 是否只搜索活跃频道，默认True
        limit (int): 返回数量限制，默认10
    
    Returns:
        JSON: 搜索结果
    """
    try:
        keyword = request.args.get('q', '').strip()
        if not keyword:
            return handle_api_error(400, "搜索关键词不能为空")
        
        active_only = request.args.get('active_only', 'true').lower() == 'true'
        limit = min(int(request.args.get('limit', 10)), 50)  # 限制最大50
        
        # 搜索频道
        channels = ChannelService.search_channels(keyword, active_only)
        
        # 应用限制
        if limit > 0:
            channels = channels[:limit]
        
        return jsonify({
            'success': True,
            'data': {
                'channels': channels,
                'keyword': keyword,
                'total': len(channels)
            }
        })
        
    except ValueError as e:
        return handle_api_error(400, f"参数错误: {str(e)}")
    except Exception as e:
        return handle_api_error(500, f"搜索失败: {str(e)}")

# 错误处理
@bp.errorhandler(404)
def not_found(error):
    return handle_api_error(404, "API端点不存在")

@bp.errorhandler(405)
def method_not_allowed(error):
    return handle_api_error(405, "请求方法不被允许")

@bp.errorhandler(400)
def bad_request(error):
    return handle_api_error(400, "请求参数错误")