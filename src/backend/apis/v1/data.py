# 数据查询API
"""
数据查询相关的RESTful API接口

提供表格数据、图表数据、趋势分析等功能
"""

from flask import Blueprint, request, jsonify
from services import DataService
from utils.api_utils import handle_api_error
from utils.auth_utils import require_login
from datetime import datetime, date, timedelta

# 创建蓝图
bp = Blueprint('data', __name__, url_prefix='/api/v1/data')

@bp.route('/table', methods=['GET'])
@require_login
def get_table_data(current_user):
    """获取表格显示数据
    
    Query Parameters:
        start_date (str): 开始日期，格式YYYY-MM-DD，默认30天前
        end_date (str): 结束日期，格式YYYY-MM-DD，默认今天
        active_only (bool): 是否只包含活跃频道，默认true
        channel_ids (str): 频道ID列表，逗号分隔，可选
        page (int): 页码，默认1
        per_page (int): 每页数量，默认10
    
    Returns:
        JSON: 表格数据
    """
    try:
        # 解析查询参数
        start_date = None
        end_date = None
        
        if request.args.get('start_date'):
            start_date = datetime.strptime(request.args.get('start_date'), '%Y-%m-%d').date()
        
        if request.args.get('end_date'):
            end_date = datetime.strptime(request.args.get('end_date'), '%Y-%m-%d').date()
        
        # 验证日期范围
        if start_date and end_date and start_date > end_date:
            return handle_api_error(400, "开始日期不能晚于结束日期")
        
        # 限制日期范围（最多90天）
        if start_date and end_date:
            if (end_date - start_date).days > 90:
                return handle_api_error(400, "日期范围不能超过90天")
        
        active_only = request.args.get('active_only', 'true').lower() == 'true'
        
        # 解析频道ID列表
        channel_ids = None
        if request.args.get('channel_ids'):
            try:
                channel_ids = [int(cid.strip()) for cid in request.args.get('channel_ids').split(',') if cid.strip()]
            except ValueError:
                return handle_api_error(400, "频道ID格式错误")
        
        # 获取表格数据（只获取当前用户的数据）
        result = DataService.get_table_data(start_date, end_date, active_only, current_user.id, channel_ids)
        
        if result['success']:
            data = result['data']
            
            # 分页处理
            page = int(request.args.get('page', 1))
            per_page = min(int(request.args.get('per_page', 10)), 100)
            
            rows = data['rows']
            total_rows = len(rows)
            start_idx = (page - 1) * per_page
            end_idx = start_idx + per_page
            page_rows = rows[start_idx:end_idx]
            
            # 更新数据
            data['rows'] = page_rows
            data['pagination'] = {
                'page': page,
                'per_page': per_page,
                'total': total_rows,
                'pages': (total_rows + per_page - 1) // per_page,
                'has_next': end_idx < total_rows,
                'has_prev': page > 1
            }
            
            return jsonify({
                'success': True,
                'data': data
            })
        else:
            return handle_api_error(500, result['message'])
            
    except ValueError as e:
        return handle_api_error(400, f"日期格式错误: {str(e)}")
    except Exception as e:
        return handle_api_error(500, f"获取表格数据失败: {str(e)}")


@bp.route('/trend/<int:channel_id>', methods=['GET'])
@require_login
def get_channel_trend(current_user, channel_id):
    """获取单个频道的趋势数据
    
    Args:
        channel_id (int): 频道ID
    
    Query Parameters:
        days (int): 天数，默认30，最大90
    
    Returns:
        JSON: 趋势数据
    """
    try:
        days = min(int(request.args.get('days', 30)), 90)
        
        # 检查频道所有权
        user_filter = None if current_user.is_admin else current_user.id
        result = DataService.get_channel_trend(channel_id, days, user_filter)
        
        if result['success']:
            return jsonify({
                'success': True,
                'data': result['data']
            })
        else:
            return handle_api_error(404, result['message'])
            
    except ValueError as e:
        return handle_api_error(400, f"参数错误: {str(e)}")
    except Exception as e:
        return handle_api_error(500, f"获取趋势数据失败: {str(e)}")

@bp.route('/summary', methods=['GET'])
@require_login
def get_summary_statistics(current_user):
    """获取总体统计信息
    
    Returns:
        JSON: 统计信息
    """
    try:
        # 获取用户的统计信息
        result = DataService.get_summary_statistics(current_user.id)
        
        if result['success']:
            return jsonify({
                'success': True,
                'data': result['data']
            })
        else:
            return handle_api_error(500, result['message'])
            
    except Exception as e:
        return handle_api_error(500, f"获取统计信息失败: {str(e)}")

@bp.route('/export', methods=['GET'])
@require_login
def export_data(current_user):
    """导出数据
    
    Query Parameters:
        format (str): 导出格式 ('csv', 'excel')，默认csv
        start_date (str): 开始日期，格式YYYY-MM-DD
        end_date (str): 结束日期，格式YYYY-MM-DD
        active_only (bool): 是否只包含活跃频道，默认true
        channel_ids (str): 频道ID列表，逗号分隔，可选
    
    Returns:
        File: 导出的文件
    """
    try:
        # 解析参数
        export_format = request.args.get('format', 'csv').lower()
        if export_format not in ['csv', 'excel']:
            return handle_api_error(400, "导出格式必须是 csv 或 excel")
        
        # 解析日期
        start_date = None
        end_date = None
        
        if request.args.get('start_date'):
            start_date = datetime.strptime(request.args.get('start_date'), '%Y-%m-%d').date()
        
        if request.args.get('end_date'):
            end_date = datetime.strptime(request.args.get('end_date'), '%Y-%m-%d').date()
        
        # 验证日期范围（导出限制更严格）
        if start_date and end_date:
            if start_date > end_date:
                return handle_api_error(400, "开始日期不能晚于结束日期")
            if (end_date - start_date).days > 180:  # 导出最多6个月
                return handle_api_error(400, "导出日期范围不能超过180天")
        
        active_only = request.args.get('active_only', 'true').lower() == 'true'
        
        # 解析频道ID列表
        channel_ids = None
        if request.args.get('channel_ids'):
            try:
                channel_ids = [int(cid.strip()) for cid in request.args.get('channel_ids').split(',') if cid.strip()]
            except ValueError:
                return handle_api_error(400, "频道ID格式错误")
        
        # 获取数据
        result = DataService.get_table_data(start_date, end_date, active_only, current_user.id)
        
        if not result['success']:
            return handle_api_error(500, result['message'])
        
        # 筛选频道（如果指定了频道ID）
        data = result['data']
        if channel_ids:
            # 过滤频道
            filtered_channels = [ch for ch in data['channels'] if ch['channel_id'] in channel_ids]
            filtered_channel_ids = [ch['channel_id'] for ch in filtered_channels]
            
            # 过滤行数据
            filtered_rows = []
            for row in data['rows']:
                filtered_channels_data = [
                    ch_data for ch_data in row['channels_data'] 
                    if ch_data['channel_id'] in filtered_channel_ids
                ]
                if filtered_channels_data:  # 只保留有数据的行
                    filtered_row = row.copy()
                    filtered_row['channels_data'] = filtered_channels_data
                    filtered_rows.append(filtered_row)
            
            data['channels'] = filtered_channels
            data['rows'] = filtered_rows
        
        # 生成文件
        if export_format == 'csv':
            return export_to_csv(data)
        else:
            return export_to_excel(data)
            
    except ValueError as e:
        return handle_api_error(400, f"日期格式错误: {str(e)}")
    except Exception as e:
        return handle_api_error(500, f"导出数据失败: {str(e)}")

def export_to_csv(data):
    """导出为CSV格式"""
    import csv
    import io
    from flask import make_response
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    # 写入表头
    headers = ['日期']
    for channel in data['channels']:
        headers.extend([
            f"{channel['channel_name']}-订阅数",
            f"{channel['channel_name']}-观看数", 
            f"{channel['channel_name']}-视频数",
            f"{channel['channel_name']}-订阅变化",
            f"{channel['channel_name']}-观看变化",
            f"{channel['channel_name']}-视频变化",
            f"{channel['channel_name']}-预估收入"
        ])
    writer.writerow(headers)
    
    # 写入数据
    for row in data['rows']:
        row_data = [row['date']]
        for channel_data in row['channels_data']:
            row_data.extend([
                channel_data['subscribers'],
                channel_data['total_views'],
                channel_data['video_count'],
                channel_data['subscriber_change'],
                channel_data['view_change'],
                channel_data['video_change'],
                channel_data['estimated_revenue']
            ])
        writer.writerow(row_data)
    
    # 创建响应
    output.seek(0)
    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv; charset=utf-8'
    response.headers['Content-Disposition'] = f'attachment; filename=youtube_data_{date.today().isoformat()}.csv'
    
    return response

def export_to_excel(data):
    """导出为Excel格式"""
    # TODO: 实现Excel导出功能
    return handle_api_error(501, "Excel导出功能尚未实现")

@bp.route('/chart-data', methods=['GET'])
@require_login
def get_chart_data(current_user):
    """获取图表数据
    
    Query Parameters:
        chart_type (str): 图表类型 ('revenue', 'subscribers', 'views')
        time_range (int): 时间范围（天数），默认30天
        channel_ids (str): 频道ID列表，逗号分隔，可选
    
    Returns:
        JSON: 图表数据
    """
    try:
        chart_type = request.args.get('chart_type', 'revenue')
        time_range = min(int(request.args.get('time_range', 30)), 90)
        group = request.args.get('group', 'daily').lower()
        months_param = request.args.get('months')
        channel_ids_str = request.args.get('channel_ids', '')

        if group not in ('daily', 'monthly'):
            group = 'daily'

        months = None
        if group == 'monthly':
            try:
                months = int(months_param) if months_param is not None else 6
            except ValueError:
                months = 6
            months = max(1, min(months, 24))
        
        # 解析频道ID列表
        channel_ids = []
        if channel_ids_str:
            channel_ids = [cid.strip() for cid in channel_ids_str.split(',') if cid.strip()]
        
        # 获取图表数据（只获取当前用户的数据）
        result = DataService.get_chart_data(
            chart_type=chart_type,
            time_range=time_range,
            channel_ids=channel_ids,
            user_id=current_user.id,
            group=group,
            months=months
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
        return handle_api_error(500, f"获取图表数据失败: {str(e)}")

@bp.route('/chart-types', methods=['GET'])
def get_chart_types():
    """获取支持的图表类型
    
    Returns:
        JSON: 图表类型列表
    """
    try:
        chart_types = [
            {
                'key': 'subscribers',
                'name': '订阅者数量',
                'description': '显示频道订阅者数量变化趋势',
                'unit': '人'
            },
            {
                'key': 'views',
                'name': '总观看数',
                'description': '显示频道总观看数变化趋势',
                'unit': '次'
            },
            {
                'key': 'revenue',
                'name': '预估收入',
                'description': '显示频道预估收入变化趋势',
                'unit': '美元'
            }
        ]
        
        return jsonify({
            'success': True,
            'data': {
                'types': chart_types,
                'total': len(chart_types)
            }
        })
        
    except Exception as e:
        return handle_api_error(500, f"获取图表类型失败: {str(e)}")

# 错误处理
@bp.errorhandler(404)
def not_found(error):
    return handle_api_error(404, "数据API端点不存在")

@bp.errorhandler(405)
def method_not_allowed(error):
    return handle_api_error(405, "请求方法不被允许")

@bp.errorhandler(400)
def bad_request(error):
    return handle_api_error(400, "请求参数错误")
