# 调度器管理API
"""
数据抓取调度器管理接口

提供调度器状态查询、手动触发等功能
"""

from flask import Blueprint, request, jsonify
from datetime import datetime, date, timedelta
from scheduler import (
    start_scheduler,
    stop_scheduler, 
    get_scheduler_status,
    manual_crawl
)
from utils.api_utils import handle_api_error
from utils.decorators import validate_request

# 创建蓝图
bp = Blueprint('scheduler', __name__, url_prefix='/api/v1/scheduler')

@bp.route('/status', methods=['GET'])
def get_status():
    """获取调度器状态
    
    Returns:
        JSON: 调度器运行状态信息
    """
    try:
        status = get_scheduler_status()
        
        return jsonify({
            'success': True,
            'data': status
        })
        
    except Exception as e:
        return handle_api_error(500, f"获取调度器状态失败: {str(e)}")

@bp.route('/start', methods=['POST'])
def start():
    """启动调度器
    
    Returns:
        JSON: 启动结果
    """
    try:
        start_scheduler()
        
        return jsonify({
            'success': True,
            'message': '调度器启动成功'
        })
        
    except Exception as e:
        return handle_api_error(500, f"启动调度器失败: {str(e)}")

@bp.route('/stop', methods=['POST'])
def stop():
    """停止调度器
    
    Returns:
        JSON: 停止结果
    """
    try:
        stop_scheduler()
        
        return jsonify({
            'success': True,
            'message': '调度器停止成功'
        })
        
    except Exception as e:
        return handle_api_error(500, f"停止调度器失败: {str(e)}")


@bp.route('/restart', methods=['POST'])
def restart():
    """重启调度器

    Returns:
        JSON: 重启结果
    """
    try:
        stop_scheduler()
        start_scheduler()
        return jsonify({
            'success': True,
            'message': '调度器重启成功',
            'data': get_scheduler_status()
        })

    except Exception as e:
        return handle_api_error(500, f"重启调度器失败: {str(e)}")


@bp.route('/manual-crawl', methods=['POST'])
@validate_request()
def manual_data_crawl():
    """手动触发数据抓取
    
    Request Body:
        date (str): 目标日期，格式YYYY-MM-DD，可选，默认今天
    
    Returns:
        JSON: 抓取结果
    """
    try:
        data = request.get_json() or {}
        
        # 解析目标日期
        target_date = None
        if 'date' in data and data['date']:
            try:
                target_date = datetime.strptime(data['date'], '%Y-%m-%d').date()
            except ValueError:
                return handle_api_error(400, "日期格式错误，请使用YYYY-MM-DD格式")
        
        # 验证日期不能是未来
        if target_date and target_date > date.today():
            return handle_api_error(400, "不能抓取未来日期的数据")
        
        # 执行手动抓取
        result = manual_crawl(target_date)
        
        if result['success']:
            return jsonify({
                'success': True,
                'message': '数据抓取完成',
                'data': result['data']
            })
        else:
            return handle_api_error(500, f"数据抓取失败: {result['message']}")
            
    except Exception as e:
        return handle_api_error(500, f"手动抓取失败: {str(e)}")

@bp.route('/crawl-history', methods=['GET'])
def get_crawl_history():
    """获取抓取历史记录
    
    Query Parameters:
        days (int): 查询天数，默认7天
    
    Returns:
        JSON: 抓取历史记录
    """
    try:
        from models import db, ChannelData
        from sqlalchemy import func, desc
        
        days = int(request.args.get('days', 7))
        if days > 30:
            days = 30  # 限制最多30天
        
        # 获取最近几天的数据统计
        start_date = date.today() - timedelta(days=days-1)
        
        # 查询每天的数据记录数
        daily_stats = db.session.query(
            ChannelData.date,
            func.count(ChannelData.id).label('record_count'),
            func.count(func.distinct(ChannelData.channel_id)).label('channel_count'),
            func.sum(ChannelData.estimated_revenue).label('total_revenue')
        ).filter(
            ChannelData.date >= start_date
        ).group_by(
            ChannelData.date
        ).order_by(
            desc(ChannelData.date)
        ).all()
        
        # 格式化结果
        history = []
        for stat in daily_stats:
            history.append({
                'date': stat.date.isoformat(),
                'record_count': stat.record_count,
                'channel_count': stat.channel_count,
                'total_revenue': float(stat.total_revenue or 0),
                'avg_revenue_per_channel': float(stat.total_revenue or 0) / stat.channel_count if stat.channel_count > 0 else 0
            })
        
        return jsonify({
            'success': True,
            'data': {
                'history': history,
                'total_days': len(history),
                'query_period': {
                    'start_date': start_date.isoformat(),
                    'end_date': date.today().isoformat()
                }
            }
        })
        
    except ValueError as e:
        return handle_api_error(400, f"参数错误: {str(e)}")
    except Exception as e:
        return handle_api_error(500, f"获取抓取历史失败: {str(e)}")

@bp.route('/missing-data', methods=['GET'])
def check_missing_data():
    """检查缺失的数据
    
    Query Parameters:
        days (int): 检查天数，默认7天
    
    Returns:
        JSON: 缺失数据报告
    """
    try:
        from models import db, Channel, ChannelData
        from datetime import timedelta
        
        days = int(request.args.get('days', 7))
        if days > 30:
            days = 30
        
        # 获取活跃频道数量
        active_channels_count = Channel.query.filter_by(is_active=True).count()
        
        missing_dates = []
        
        # 检查最近几天的数据
        for i in range(days):
            check_date = date.today() - timedelta(days=i)
            
            # 查询该日期的数据记录数
            record_count = ChannelData.query.filter_by(date=check_date).count()
            
            # 计算覆盖率
            coverage_rate = record_count / active_channels_count if active_channels_count > 0 else 1
            
            if coverage_rate < 0.8:  # 覆盖率低于80%认为数据缺失
                missing_dates.append({
                    'date': check_date.isoformat(),
                    'record_count': record_count,
                    'expected_count': active_channels_count,
                    'coverage_rate': coverage_rate,
                    'missing_count': active_channels_count - record_count
                })
        
        return jsonify({
            'success': True,
            'data': {
                'missing_dates': missing_dates,
                'total_missing_days': len(missing_dates),
                'active_channels_count': active_channels_count,
                'check_period': {
                    'days': days,
                    'start_date': (date.today() - timedelta(days=days-1)).isoformat(),
                    'end_date': date.today().isoformat()
                }
            }
        })
        
    except ValueError as e:
        return handle_api_error(400, f"参数错误: {str(e)}")
    except Exception as e:
        return handle_api_error(500, f"检查缺失数据失败: {str(e)}")

@bp.route('/schedule-info', methods=['GET'])
def get_schedule_info():
    """获取调度计划信息
    
    Returns:
        JSON: 调度计划详情
    """
    try:
        import schedule
        
        # 获取所有计划任务
        jobs = schedule.jobs
        
        schedule_info = []
        for job in jobs:
            schedule_info.append({
                'job_func': job.job_func.__name__,
                'interval': str(job.interval),
                'unit': str(job.unit),
                'at_time': str(job.at_time) if job.at_time else None,
                'next_run': job.next_run.isoformat() if job.next_run else None
            })
        
        return jsonify({
            'success': True,
            'data': {
                'jobs': schedule_info,
                'total_jobs': len(schedule_info),
                'next_run': schedule.next_run().isoformat() if schedule.jobs else None
            }
        })
        
    except Exception as e:
        return handle_api_error(500, f"获取调度信息失败: {str(e)}")

# 错误处理
@bp.errorhandler(404)
def not_found(error):
    return handle_api_error(404, "调度器API端点不存在")

@bp.errorhandler(405)
def method_not_allowed(error):
    return handle_api_error(405, "请求方法不被允许")

@bp.errorhandler(400)
def bad_request(error):
    return handle_api_error(400, "请求参数错误")
