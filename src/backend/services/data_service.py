# 数据查询服务
"""
频道数据查询相关的业务逻辑服务

提供历史数据查询、趋势分析、表格数据生成等功能
"""

from datetime import datetime, date, timedelta
from typing import List, Optional, Dict, Any, Tuple
from collections import defaultdict
from sqlalchemy import func, desc, asc
from models import db, Channel, ChannelData

class DataService:
    """数据查询服务类"""
    
    @staticmethod
    def get_table_data(start_date: Optional[date] = None, 
                      end_date: Optional[date] = None,
                      active_only: bool = True,
                      user_id: Optional[int] = None,
                      channel_ids: Optional[List[int]] = None,
                      *,
                      page: Optional[int] = None,
                      per_page: Optional[int] = None) -> Dict[str, Any]:
        """获取表格显示数据
        
        Args:
            start_date: 开始日期，默认最近30天
            end_date: 结束日期，默认今天
            active_only: 是否只包含活跃频道
            user_id: 用户ID，用于数据过滤
            channel_ids: 指定的频道ID列表，可选
            page: 日期页码；与 per_page 同时提供时在 SQL 层分页
            per_page: 每页日期数；与 page 同时提供时在 SQL 层分页
            
        Returns:
            Dict: 表格数据
        """
        try:
            # 设置默认日期范围
            if not end_date:
                end_date = date.today()
            if not start_date:
                start_date = end_date - timedelta(days=30)

            paged = page is not None or per_page is not None
            if paged:
                if page is None or per_page is None:
                    raise ValueError("page 和 per_page 必须同时提供")
                page = int(page)
                per_page = int(per_page)
                if page < 1 or per_page < 1:
                    raise ValueError("page 和 per_page 必须为正整数")
            
            # 获取频道（按用户过滤）
            channels_query = Channel.query
            if user_id is not None:
                channels_query = channels_query.filter_by(user_id=user_id)
            if active_only:
                channels_query = channels_query.filter_by(is_active=True)
            if channel_ids:
                channels_query = channels_query.filter(Channel.id.in_(channel_ids))
            channels = channels_query.order_by(Channel.created_at.asc()).all()
            
            # 获取指定日期范围的所有数据
            data_query = ChannelData.query.join(Channel).filter(
                ChannelData.date >= start_date,
                ChannelData.date <= end_date
            )
            
            if user_id is not None:
                data_query = data_query.filter(Channel.user_id == user_id)
            if active_only:
                data_query = data_query.filter(Channel.is_active == True)
            if channel_ids:
                data_query = data_query.filter(Channel.id.in_(channel_ids))
            
            if paged:
                # 日期是表格的行维度。先在数据库中选出当前日期页，再只物化这些
                # 日期对应的频道数据；窗口计数与数据加载共用一次数据库往返。
                filtered_dates = (
                    data_query.with_entities(ChannelData.date.label("date"))
                    .distinct()
                    .cte("filtered_channel_data_dates")
                )
                paged_dates = (
                    db.session.query(
                        filtered_dates.c.date.label("date"),
                        func.count().over().label("total_days"),
                    )
                    .order_by(desc(filtered_dates.c.date))
                    .offset((page - 1) * per_page)
                    .limit(per_page)
                    .cte("paged_channel_data_dates")
                )
                rows_with_total = (
                    data_query.join(
                        paged_dates,
                        ChannelData.date == paged_dates.c.date,
                    )
                    .add_columns(paged_dates.c.total_days)
                    .order_by(
                        desc(ChannelData.date),
                        asc(Channel.created_at),
                    )
                    .all()
                )
                all_data = [row[0] for row in rows_with_total]
                if rows_with_total:
                    total_days = int(rows_with_total[0][1])
                else:
                    # 仅越界空页需要一次补充计数；正常页面仍保持两次总 SQL
                    # （频道列 + 当前日期页数据）。
                    total_days = int(
                        db.session.query(func.count())
                        .select_from(filtered_dates)
                        .scalar()
                        or 0
                    )
            else:
                all_data = data_query.order_by(
                    desc(ChannelData.date),
                    asc(Channel.created_at)
                ).all()
                total_days = len({data.date for data in all_data})
            
            # 组织数据为表格格式
            # 获取日期列表
            dates = sorted(list(set([data.date for data in all_data])), reverse=True)
            data_by_channel_date = {
                (data.channel_id, data.date): data
                for data in all_data
            }
            
            # 组织频道数据
            channel_columns = []
            for channel in channels:
                channel_columns.append({
                    'channel_id': channel.id,
                    'channel_name': channel.name,
                    'cpm': float(channel.cpm),
                    'category': channel.category
                })
            
            # 组织每日数据
            table_rows = []
            for date_val in dates:
                row = {
                    'date': date_val.isoformat(),
                    'is_today': date_val == date.today(),
                    'channels_data': []
                }
                
                for channel in channels:
                    channel_data = data_by_channel_date.get((channel.id, date_val))
                    
                    if channel_data:
                        row['channels_data'].append({
                            'channel_id': channel.id,
                            'has_data': True,
                            'subscribers': channel_data.subscribers,
                            'total_views': channel_data.total_views,
                            'video_count': channel_data.video_count,
                            'subscriber_change': channel_data.subscriber_change,
                            'view_change': channel_data.view_change,
                            'video_change': channel_data.video_change,
                            'estimated_revenue': float(channel_data.estimated_revenue)
                        })
                    else:
                        # 没有数据的情况
                        row['channels_data'].append({
                            'channel_id': channel.id,
                            'has_data': False,
                            'subscribers': 0,
                            'total_views': 0,
                            'video_count': 0,
                            'subscriber_change': 0,
                            'view_change': 0,
                            'video_change': 0,
                            'estimated_revenue': 0.0
                        })
                
                table_rows.append(row)
            
            return {
                'success': True,
                'data': {
                    'start_date': start_date.isoformat(),
                    'end_date': end_date.isoformat(),
                    'channels': channel_columns,
                    'rows': table_rows,
                    'total_channels': len(channels),
                    'total_days': total_days
                }
            }
            
        except Exception as e:
            return {
                'success': False,
                'message': f'获取表格数据失败: {str(e)}',
                'data': None
            }
    
    
    @staticmethod
    def get_channel_trend(channel_id: int, days: int = 30, user_id: Optional[int] = None) -> Dict[str, Any]:
        """获取单个频道的趋势数据
        
        Args:
            channel_id: 频道ID
            days: 天数
            user_id: 用户ID，用于权限检查
            
        Returns:
            Dict: 趋势数据
        """
        try:
            # 检查频道存在和权限
            query = Channel.query
            if user_id is not None:
                query = query.filter_by(user_id=user_id)
            
            channel = query.filter_by(id=channel_id).first()
            if not channel:
                return {
                    'success': False,
                    'message': '频道不存在或无权限访问'
                }
            
            # 获取趋势数据
            end_date = date.today()
            start_date = end_date - timedelta(days=days)
            
            data = ChannelData.query.filter(
                ChannelData.channel_id == channel_id,
                ChannelData.date >= start_date,
                ChannelData.date <= end_date
            ).order_by(ChannelData.date.asc()).all()
            
            # 计算统计信息
            if data:
                latest = data[-1]
                earliest = data[0]
                
                total_subscriber_growth = latest.subscribers - earliest.subscribers
                total_view_growth = latest.total_views - earliest.total_views
                total_revenue = sum(float(d.estimated_revenue) for d in data)
                
                avg_daily_subscriber_growth = total_subscriber_growth / len(data) if data else 0
                avg_daily_view_growth = total_view_growth / len(data) if data else 0
                avg_daily_revenue = total_revenue / len(data) if data else 0
            else:
                total_subscriber_growth = 0
                total_view_growth = 0
                total_revenue = 0
                avg_daily_subscriber_growth = 0
                avg_daily_view_growth = 0
                avg_daily_revenue = 0
            
            return {
                'success': True,
                'data': {
                    'channel': channel.to_dict(),
                    'period': {
                        'start_date': start_date.isoformat(),
                        'end_date': end_date.isoformat(),
                        'days': days
                    },
                    'trend_data': [d.to_dict() for d in data],
                    'statistics': {
                        'total_subscriber_growth': total_subscriber_growth,
                        'total_view_growth': total_view_growth,
                        'total_revenue': total_revenue,
                        'avg_daily_subscriber_growth': avg_daily_subscriber_growth,
                        'avg_daily_view_growth': avg_daily_view_growth,
                        'avg_daily_revenue': avg_daily_revenue
                    }
                }
            }
            
        except Exception as e:
            return {
                'success': False,
                'message': f'获取趋势数据失败: {str(e)}'
            }
    
    @staticmethod
    def get_summary_statistics(user_id: Optional[int] = None) -> Dict[str, Any]:
        """获取统计信息
        
        Args:
            user_id: 用户ID，用于获取用户专属统计
            
        Returns:
            Dict: 统计信息
        """
        try:
            # 构建频道查询
            channel_query = Channel.query
            if user_id is not None:
                channel_query = channel_query.filter_by(user_id=user_id)
            
            # 活跃频道数
            active_channels = channel_query.filter_by(is_active=True).count()
            
            # 今日数据
            today = date.today()
            today_query = ChannelData.query.filter_by(date=today)
            if user_id is not None:
                today_query = today_query.join(Channel).filter(Channel.user_id == user_id)
            today_data = today_query.all()
            
            today_revenue = sum(float(d.estimated_revenue) for d in today_data)
            today_subscriber_growth = sum(d.subscriber_change for d in today_data)
            today_view_growth = sum(d.view_change for d in today_data)
            
            # 本月数据
            month_start = date(today.year, today.month, 1)
            month_query = ChannelData.query.filter(
                ChannelData.date >= month_start,
                ChannelData.date <= today
            )
            if user_id is not None:
                month_query = month_query.join(Channel).filter(Channel.user_id == user_id)
            month_data = month_query.all()
            
            month_revenue = sum(float(d.estimated_revenue) for d in month_data)
            month_subscriber_growth = sum(d.subscriber_change for d in month_data)
            month_view_growth = sum(d.view_change for d in month_data)
            
            # 总数据记录数
            total_records = ChannelData.query.count()
            
            return {
                'success': True,
                'data': {
                    'active_channels': active_channels,
                    'total_records': total_records,
                    'today': {
                        'revenue': today_revenue,
                        'subscriber_growth': today_subscriber_growth,
                        'view_growth': today_view_growth,
                        'channels_with_data': len(today_data)
                    },
                    'this_month': {
                        'revenue': month_revenue,
                        'subscriber_growth': month_subscriber_growth,
                        'view_growth': month_view_growth,
                        'total_days': (today - month_start).days + 1
                    }
                }
            }
            
        except Exception as e:
            return {
                'success': False,
                'message': f'获取统计信息失败: {str(e)}'
            }
    
    @staticmethod
    def get_chart_data(chart_type: str, time_range: int = 30,
                      channel_ids: Optional[List[str]] = None,
                      user_id: int = None,
                      group: str = 'daily',
                      months: Optional[int] = None) -> Dict[str, Any]:
        """获取图表数据
        
        Args:
            chart_type: 图表类型 ('revenue', 'subscribers', 'views')
            time_range: 时间范围（天数）
            channel_ids: 指定频道ID列表，为空则包含所有活跃频道
            user_id: 用户ID，用于数据过滤
            
        Returns:
            Dict: 图表数据
        """
        try:
            # 计算日期范围
            end_date = date.today()
            grouping = (group or 'daily').lower()
            if grouping not in ('daily', 'monthly'):
                grouping = 'daily'

            if grouping == 'monthly':
                months = max(1, min(int(months or 6), 24))

                def shift_month(base: date, offset: int) -> date:
                    year = base.year + (base.month - 1 + offset) // 12
                    month = (base.month - 1 + offset) % 12 + 1
                    return date(year, month, 1)

                current_month_start = end_date.replace(day=1)
                start_month = shift_month(current_month_start, -(months - 1))
                start_date = start_month
            else:
                start_date = end_date - timedelta(days=time_range - 1)

            # 获取频道列表
            channels_query = Channel.query.filter_by(is_active=True)
            if user_id is not None:
                channels_query = channels_query.filter(Channel.user_id == user_id)
            if channel_ids:
                channels_query = channels_query.filter(Channel.id.in_(channel_ids))
            channels = channels_query.order_by(Channel.name.asc()).all()
            
            if not channels:
                return {
                    'success': False,
                    'message': '没有找到匹配的频道'
                }
            
            # 获取数据
            data_query = ChannelData.query.join(Channel).filter(
                ChannelData.date >= start_date,
                ChannelData.date <= end_date,
                Channel.is_active == True
            )
            
            if user_id is not None:
                data_query = data_query.filter(Channel.user_id == user_id)
            if channel_ids:
                data_query = data_query.filter(Channel.id.in_(channel_ids))
            
            data_records = data_query.order_by(
                ChannelData.date.asc(),
                Channel.name.asc()
            ).all()
            
            if grouping == 'monthly':
                def shift_month(base: date, offset: int) -> date:
                    year = base.year + (base.month - 1 + offset) // 12
                    month = (base.month - 1 + offset) % 12 + 1
                    return date(year, month, 1)

                months_list = []
                month_pointer = start_date.replace(day=1)
                last_month = end_date.replace(day=1)
                while month_pointer <= last_month:
                    months_list.append(month_pointer)
                    month_pointer = shift_month(month_pointer, 1)

                chart_data = {
                    'dates': [m.isoformat() for m in months_list],
                    'labels': [m.strftime('%Y-%m') for m in months_list],
                    'series': [],
                    'granularity': 'monthly',
                    'label_format': 'YYYY-MM'
                }

                monthly_buckets = defaultdict(lambda: defaultdict(lambda: {
                    'revenue': 0.0,
                    'subscribers': None,
                    'views': None
                }))

                month_min = months_list[0] if months_list else start_date.replace(day=1)
                month_max = months_list[-1] if months_list else end_date.replace(day=1)

                for record in data_records:
                    month_key = record.date.replace(day=1)
                    if month_key < month_min or month_key > month_max:
                        continue
                    bucket = monthly_buckets[record.channel_id][month_key]
                    bucket['revenue'] += float(record.estimated_revenue or 0)
                    bucket['subscribers'] = record.subscribers or bucket['subscribers'] or 0
                    bucket['views'] = record.total_views or bucket['views'] or 0

                for channel in channels:
                    series_data = []
                    channel_month_data = monthly_buckets.get(channel.id, {})
                    for month_key in months_list:
                        bucket = channel_month_data.get(month_key)
                        if bucket:
                            if chart_type == 'revenue':
                                value = round(bucket['revenue'], 2)
                            elif chart_type == 'subscribers':
                                value = bucket['subscribers'] or 0
                            elif chart_type == 'views':
                                value = bucket['views'] or 0
                            else:
                                value = 0
                        else:
                            value = None
                        series_data.append(value)

                    chart_data['series'].append({
                        'id': channel.id,
                        'name': channel.name,
                        'channel_id': channel.channel_id,
                        'data': series_data
                    })
            else:
                dates = []
                current_date = start_date
                while current_date <= end_date:
                    dates.append(current_date)
                    current_date += timedelta(days=1)

                chart_data = {
                    'dates': [d.isoformat() for d in dates],
                    'series': [],
                    'granularity': 'daily',
                    'label_format': 'YYYY-MM-DD'
                }

                for channel in channels:
                    channel_data = {}
                    for record in data_records:
                        if record.channel_id == channel.id:
                            channel_data[record.date] = record

                    series_data = []
                    for d in dates:
                        record = channel_data.get(d)
                        if record:
                            if chart_type == 'revenue':
                                value = float(record.estimated_revenue or 0)
                            elif chart_type == 'subscribers':
                                value = record.subscribers or 0
                            elif chart_type == 'views':
                                value = record.total_views or 0
                            else:
                                value = 0
                        else:
                            value = None

                        series_data.append(value)

                    chart_data['series'].append({
                        'id': channel.id,
                        'name': channel.name,
                        'channel_id': channel.channel_id,
                        'data': series_data
                    })

            response_meta = {
                'latest_data_date': max((record.date for record in data_records), default=None),
                'pending_channels': len(channels) - len({
                    record.channel_id for record in data_records if record.date == end_date
                }),
                'chart_type': chart_type,
                'time_range': time_range,
                'months': months if grouping == 'monthly' else None,
                'total_channels': len(channels),
                'date_range': {
                    'start_date': start_date.isoformat(),
                    'end_date': end_date.isoformat()
                },
                'granularity': chart_data.get('granularity', 'daily')
            }
            if response_meta['latest_data_date'] is not None:
                response_meta['latest_data_date'] = response_meta['latest_data_date'].isoformat()

            return {
                'success': True,
                'data': {
                    'chart_data': chart_data,
                    **response_meta
                }
            }

        except Exception as e:
            return {
                'success': False,
                'message': f'获取图表数据失败: {str(e)}'
            }
