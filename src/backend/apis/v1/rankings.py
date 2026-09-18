# 排行榜API
"""
排行榜相关的RESTful API接口

提供日、月、年三个周期的收入与新增订阅排行榜查询功能
"""

from flask import Blueprint, request, jsonify
from services import RankingService
from utils.api_utils import handle_api_error
from utils.auth_utils import require_login
from datetime import datetime, date

# 创建蓝图
bp = Blueprint('rankings', __name__, url_prefix='/api/v1/rankings')


def _parse_pagination(default_per_page=10, max_per_page=100):
    """Parse the shared ranking pagination contract."""
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', request.args.get('limit', default_per_page)))
    if page < 1:
        raise ValueError("页码必须大于等于1")
    if per_page < 1 or per_page > max_per_page:
        raise ValueError(f"每页条数必须在1-{max_per_page}之间")
    return page, per_page


def _parse_year():
    year = int(request.args.get('year', date.today().year))
    if not (2020 <= year <= 2030):
        raise ValueError("年份范围无效")
    return year

@bp.route('/daily-revenue', methods=['GET'])
@require_login
def get_daily_revenue_ranking(current_user):
    """获取日收入排行榜
    
    Query Parameters:
        page (int): 页码，默认1
        per_page (int): 每页条数，默认10，最大100
        limit (int): per_page 的兼容别名
        date (str): 查询日期，格式YYYY-MM-DD，默认今天
    
    Returns:
        JSON: 日收入排行榜数据
    """
    try:
        page, per_page = _parse_pagination()
        
        # 解析日期参数
        target_date = None
        date_str = request.args.get('date')
        if date_str:
            # 验证日期格式
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        
        result = RankingService.get_daily_revenue_ranking(
            per_page, target_date, current_user.id, page=page
        )
        
        if result['success']:
            return jsonify({
                'success': True,
                'data': result['data']
            })
        else:
            return handle_api_error(500, result['message'])
            
    except ValueError as e:
        return handle_api_error(400, f"参数错误: {str(e)}")
    except Exception as e:
        return handle_api_error(500, f"获取日收入排行榜失败: {str(e)}")

@bp.route('/monthly-revenue', methods=['GET'])
@require_login
def get_monthly_revenue_ranking(current_user):
    """获取月收入排行榜
    
    Query Parameters:
        page (int): 页码，默认1
        per_page (int): 每页条数，默认10，最大100
        limit (int): per_page 的兼容别名
        year (int): 年份，默认当前年
        month (int): 月份，默认当前月
    
    Returns:
        JSON: 月收入排行榜数据
    """
    try:
        page, per_page = _parse_pagination()
        
        # 解析年月参数
        target_date = None
        year = request.args.get('year')
        month = request.args.get('month')
        if year and month:
            year = int(year)
            month = int(month)
            if not (1 <= month <= 12):
                return handle_api_error(400, "月份必须在1-12之间")
            if not (2020 <= year <= 2030):
                return handle_api_error(400, "年份范围无效")
            target_date = date(year, month, 1)
        
        result = RankingService.get_monthly_revenue_ranking(
            per_page, target_date, current_user.id, page=page
        )
        
        if result['success']:
            return jsonify({
                'success': True,
                'data': result['data']
            })
        else:
            return handle_api_error(500, result['message'])
            
    except ValueError as e:
        return handle_api_error(400, f"参数错误: {str(e)}")
    except Exception as e:
        return handle_api_error(500, f"获取月收入排行榜失败: {str(e)}")

@bp.route('/daily-subscriber', methods=['GET'])
@require_login
def get_daily_subscriber_ranking(current_user):
    """获取日新增订阅排行榜
    
    Query Parameters:
        page (int): 页码，默认1
        per_page (int): 每页条数，默认10，最大100
        limit (int): per_page 的兼容别名
        date (str): 查询日期，格式YYYY-MM-DD，默认今天
    
    Returns:
        JSON: 日新增订阅排行榜数据
    """
    try:
        page, per_page = _parse_pagination()
        
        # 解析日期参数
        target_date = None
        date_str = request.args.get('date')
        if date_str:
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        
        result = RankingService.get_daily_subscriber_ranking(
            per_page, target_date, current_user.id, page=page
        )
        
        if result['success']:
            return jsonify({
                'success': True,
                'data': result['data']
            })
        else:
            return handle_api_error(500, result['message'])
            
    except ValueError as e:
        return handle_api_error(400, f"参数错误: {str(e)}")
    except Exception as e:
        return handle_api_error(500, f"获取日订阅增长排行榜失败: {str(e)}")


@bp.route('/yearly-revenue', methods=['GET'])
@require_login
def get_yearly_revenue_ranking(current_user):
    """获取指定自然年的累计收入排行榜。"""
    try:
        page, per_page = _parse_pagination()
        result = RankingService.get_yearly_revenue_ranking(
            per_page, _parse_year(), current_user.id, page=page
        )
        if result['success']:
            return jsonify({'success': True, 'data': result['data']})
        return handle_api_error(500, result['message'])
    except ValueError as e:
        return handle_api_error(400, f"参数错误: {str(e)}")
    except Exception as e:
        return handle_api_error(500, f"获取年收入排行榜失败: {str(e)}")

@bp.route('/monthly-subscriber', methods=['GET'])
@require_login
def get_monthly_subscriber_ranking(current_user):
    """获取月新增订阅排行榜
    
    Query Parameters:
        page (int): 页码，默认1
        per_page (int): 每页条数，默认10，最大100
        limit (int): per_page 的兼容别名
        year (int): 年份，默认当前年
        month (int): 月份，默认当前月
    
    Returns:
        JSON: 月新增订阅排行榜数据
    """
    try:
        page, per_page = _parse_pagination()
        
        # 解析年月参数
        target_date = None
        year = request.args.get('year')
        month = request.args.get('month')
        if year and month:
            year = int(year)
            month = int(month)
            if not (1 <= month <= 12):
                return handle_api_error(400, "月份必须在1-12之间")
            if not (2020 <= year <= 2030):
                return handle_api_error(400, "年份范围无效")
            target_date = date(year, month, 1)
        
        result = RankingService.get_monthly_subscriber_ranking(
            per_page, target_date, current_user.id, page=page
        )
        
        if result['success']:
            return jsonify({
                'success': True,
                'data': result['data']
            })
        else:
            return handle_api_error(500, result['message'])
            
    except ValueError as e:
        return handle_api_error(400, f"参数错误: {str(e)}")
    except Exception as e:
        return handle_api_error(500, f"获取月订阅增长排行榜失败: {str(e)}")


@bp.route('/yearly-subscriber', methods=['GET'])
@require_login
def get_yearly_subscriber_ranking(current_user):
    """获取指定自然年的累计新增订阅排行榜。"""
    try:
        page, per_page = _parse_pagination()
        result = RankingService.get_yearly_subscriber_ranking(
            per_page, _parse_year(), current_user.id, page=page
        )
        if result['success']:
            return jsonify({'success': True, 'data': result['data']})
        return handle_api_error(500, result['message'])
    except ValueError as e:
        return handle_api_error(400, f"参数错误: {str(e)}")
    except Exception as e:
        return handle_api_error(500, f"获取年订阅增长排行榜失败: {str(e)}")

@bp.route('/all', methods=['GET'])
@require_login
def get_all_rankings(current_user):
    """获取所有4个排行榜数据
    
    Query Parameters:
        limit (int): 每个排行榜返回的条数，默认3，最大10
    
    Returns:
        JSON: 所有排行榜数据
    """
    try:
        limit = min(int(request.args.get('limit', 3)), 10)
        
        result = RankingService.get_all_rankings(limit, current_user.id)
        
        if result['success']:
            return jsonify({
                'success': True,
                'data': result['data']
            })
        else:
            return handle_api_error(500, result['message'])
            
    except ValueError as e:
        return handle_api_error(400, f"参数错误: {str(e)}")
    except Exception as e:
        return handle_api_error(500, f"获取排行榜数据失败: {str(e)}")

@bp.route('/channel/<int:channel_id>', methods=['GET'])
def get_channel_rankings(channel_id):
    """获取指定频道在各排行榜中的排名
    
    Args:
        channel_id (int): 频道ID
    
    Returns:
        JSON: 频道在各排行榜中的排名
    """
    try:
        result = RankingService.get_channel_rankings(channel_id)
        
        if result['success']:
            return jsonify({
                'success': True,
                'data': result['data']
            })
        else:
            return handle_api_error(404, result['message'])
            
    except Exception as e:
        return handle_api_error(500, f"获取频道排名失败: {str(e)}")


@bp.route('/channel/<int:channel_id>/profile', methods=['GET'])
@require_login
def get_channel_owner_profile(current_user, channel_id):
    """Return the current user's single-channel owner profile."""
    try:
        result = RankingService.get_channel_owner_profile(channel_id, current_user.id)
        if result['success']:
            return jsonify({'success': True, 'data': result['data']})
        return handle_api_error(404, result['message'])
    except Exception as e:
        return handle_api_error(500, f"获取频道主档案失败: {str(e)}")

@bp.route('/summary', methods=['GET'])
def get_rankings_summary():
    """获取排行榜概要信息
    
    Returns:
        JSON: 排行榜概要统计
    """
    try:
        # 获取前3名的所有排行榜
        all_rankings = RankingService.get_all_rankings(3)
        
        if not all_rankings['success']:
            return handle_api_error(500, all_rankings['message'])
        
        rankings_data = all_rankings['data']
        
        # 构建概要信息
        summary = {
            'daily_revenue_top1': None,
            'monthly_revenue_top1': None,
            'daily_subscriber_top1': None,
            'monthly_subscriber_top1': None,
            'total_channels_with_data': 0,
            'last_updated': rankings_data.get('updated_at')
        }
        
        # 提取各排行榜的第一名
        if rankings_data.get('daily_revenue') and rankings_data['daily_revenue']['rankings']:
            summary['daily_revenue_top1'] = rankings_data['daily_revenue']['rankings'][0]
        
        if rankings_data.get('monthly_revenue') and rankings_data['monthly_revenue']['rankings']:
            summary['monthly_revenue_top1'] = rankings_data['monthly_revenue']['rankings'][0]
        
        if rankings_data.get('daily_subscriber') and rankings_data['daily_subscriber']['rankings']:
            summary['daily_subscriber_top1'] = rankings_data['daily_subscriber']['rankings'][0]
        
        if rankings_data.get('monthly_subscriber') and rankings_data['monthly_subscriber']['rankings']:
            summary['monthly_subscriber_top1'] = rankings_data['monthly_subscriber']['rankings'][0]
        
        # 统计有数据的频道数（取日收入排行榜的数量作为参考）
        if rankings_data.get('daily_revenue'):
            summary['total_channels_with_data'] = rankings_data['daily_revenue'].get('total_count', 0)
        
        return jsonify({
            'success': True,
            'data': summary
        })
        
    except Exception as e:
        return handle_api_error(500, f"获取排行榜概要失败: {str(e)}")

@bp.route('/types', methods=['GET'])
def get_ranking_types():
    """获取支持的排行榜类型
    
    Returns:
        JSON: 排行榜类型列表
    """
    try:
        ranking_types = [
            {
                'key': 'daily_revenue',
                'name': '日收入排行榜',
                'description': '按当日预估收入排序',
                'endpoint': '/api/v1/rankings/daily-revenue'
            },
            {
                'key': 'monthly_revenue',
                'name': '月收入排行榜',
                'description': '按本月累计预估收入排序',
                'endpoint': '/api/v1/rankings/monthly-revenue'
            },
            {
                'key': 'yearly_revenue',
                'name': '年收入排行榜',
                'description': '按指定自然年累计预估收入排序',
                'endpoint': '/api/v1/rankings/yearly-revenue'
            },
            {
                'key': 'daily_subscriber',
                'name': '日新增订阅排行榜',
                'description': '按当日订阅者增长排序',
                'endpoint': '/api/v1/rankings/daily-subscriber'
            },
            {
                'key': 'monthly_subscriber',
                'name': '月新增订阅排行榜',
                'description': '按本月累计订阅者增长排序',
                'endpoint': '/api/v1/rankings/monthly-subscriber'
            },
            {
                'key': 'yearly_subscriber',
                'name': '年新增订阅排行榜',
                'description': '按指定自然年累计订阅者增长排序',
                'endpoint': '/api/v1/rankings/yearly-subscriber'
            }
        ]
        
        return jsonify({
            'success': True,
            'data': {
                'types': ranking_types,
                'total': len(ranking_types)
            }
        })
        
    except Exception as e:
        return handle_api_error(500, f"获取排行榜类型失败: {str(e)}")

# 错误处理
@bp.errorhandler(404)
def not_found(error):
    return handle_api_error(404, "排行榜API端点不存在")

@bp.errorhandler(405)
def method_not_allowed(error):
    return handle_api_error(405, "请求方法不被允许")

@bp.errorhandler(400)
def bad_request(error):
    return handle_api_error(400, "请求参数错误")
