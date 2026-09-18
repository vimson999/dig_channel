# 排行榜服务
"""
排行榜相关的业务逻辑服务

提供日、月、年三个周期的收入与新增订阅排行榜查询和计算功能
"""

from datetime import datetime, date, timedelta
from typing import List, Dict, Any
from sqlalchemy import and_, desc, func, or_
from models import db, Channel, ChannelData, Video

class RankingService:
    """排行榜服务类"""

    @staticmethod
    def _period_tables(rows):
        """Build portable day/month/year table rows from chronological snapshots."""
        def snapshot(record, period):
            return {
                'period': period,
                'subscribers': record.subscribers,
                'total_views': record.total_views,
                'video_count': record.video_count,
                'subscriber_change': record.subscriber_change,
                'view_change': record.view_change,
                'estimated_revenue': float(record.estimated_revenue),
            }

        def aggregate(key_for):
            grouped = {}
            for record in rows:
                key = key_for(record)
                if key not in grouped:
                    grouped[key] = snapshot(record, key)
                else:
                    grouped[key].update({
                        'subscribers': record.subscribers,
                        'total_views': record.total_views,
                        'video_count': record.video_count,
                    })
                    grouped[key]['subscriber_change'] += record.subscriber_change
                    grouped[key]['view_change'] += record.view_change
                    grouped[key]['estimated_revenue'] += float(record.estimated_revenue)
            return list(reversed(list(grouped.values())))

        daily = [snapshot(record, record.date.isoformat()) for record in reversed(rows[-90:])]
        monthly = aggregate(lambda record: record.date.strftime('%Y-%m'))[:24]
        yearly = aggregate(lambda record: str(record.date.year))
        return {'daily': daily, 'monthly': monthly, 'yearly': yearly}

    @staticmethod
    def get_channel_owner_profile(channel_id: int, user_id: int) -> Dict[str, Any]:
        """Return the factual, single-channel profile behind one ranking row.

        The product calls this a channel-owner profile, but the persisted unit is
        deliberately a single channel.  We do not infer a real person or merge
        multiple channels without an explicit ownership relation in the data.
        """
        channel = Channel.query.filter_by(id=channel_id, user_id=user_id).first()
        if not channel:
            return {'success': False, 'message': '频道不存在或无权访问', 'data': None}

        latest = (
            ChannelData.query.filter_by(channel_id=channel.id)
            .order_by(ChannelData.date.desc())
            .first()
        )
        if not latest:
            return {'success': False, 'message': '该频道暂无可用的数据记录', 'data': None}

        trend_start = latest.date - timedelta(days=89)
        trend_rows = (
            ChannelData.query.filter(
                ChannelData.channel_id == channel.id,
                ChannelData.date >= trend_start,
                ChannelData.date <= latest.date,
            )
            .order_by(ChannelData.date.asc())
            .all()
        )
        history_rows = (
            ChannelData.query.filter(
                ChannelData.channel_id == channel.id,
                ChannelData.date <= latest.date,
            )
            .order_by(ChannelData.date.asc())
            .all()
        )
        performance_start = latest.date - timedelta(days=29)
        recent_performance = db.session.query(
            func.coalesce(func.sum(ChannelData.estimated_revenue), 0),
            func.coalesce(func.sum(ChannelData.subscriber_change), 0),
            func.coalesce(func.sum(ChannelData.view_change), 0),
        ).filter(
            ChannelData.channel_id == channel.id,
            ChannelData.date >= performance_start,
            ChannelData.date <= latest.date,
        ).one()

        recent_videos = (
            Video.query.filter_by(user_id=user_id, channel_id=channel.id, is_active=True)
            .order_by(Video.published_at.desc(), Video.id.desc())
            .limit(6)
            .all()
        )
        top_video = (
            Video.query.filter_by(user_id=user_id, channel_id=channel.id, is_active=True)
            .order_by(Video.view_count.desc(), Video.published_at.desc(), Video.id.desc())
            .first()
        )

        daily_revenue_rank = db.session.query(func.count(ChannelData.id)).join(Channel).filter(
            Channel.user_id == user_id,
            Channel.is_active == True,
            ChannelData.date == latest.date,
            or_(
                ChannelData.estimated_revenue > latest.estimated_revenue,
                and_(
                    ChannelData.estimated_revenue == latest.estimated_revenue,
                    Channel.id < channel.id,
                ),
            ),
        ).scalar() + 1
        daily_subscriber_rank = db.session.query(func.count(ChannelData.id)).join(Channel).filter(
            Channel.user_id == user_id,
            Channel.is_active == True,
            ChannelData.date == latest.date,
            or_(
                ChannelData.subscriber_change > latest.subscriber_change,
                and_(
                    ChannelData.subscriber_change == latest.subscriber_change,
                    Channel.id < channel.id,
                ),
            ),
        ).scalar() + 1

        month_start = latest.date.replace(day=1)
        monthly_revenue = db.session.query(
            func.coalesce(func.sum(ChannelData.estimated_revenue), 0)
        ).filter(
            ChannelData.channel_id == channel.id,
            ChannelData.date >= month_start,
            ChannelData.date <= latest.date,
        ).scalar()
        monthly_subscriber = db.session.query(
            func.coalesce(func.sum(ChannelData.subscriber_change), 0)
        ).filter(
            ChannelData.channel_id == channel.id,
            ChannelData.date >= month_start,
            ChannelData.date <= latest.date,
        ).scalar()
        monthly_totals = db.session.query(
            Channel.id.label('channel_id'),
            func.sum(ChannelData.estimated_revenue).label('revenue'),
            func.sum(ChannelData.subscriber_change).label('subscriber_change'),
        ).join(ChannelData).filter(
            Channel.user_id == user_id,
            Channel.is_active == True,
            ChannelData.date >= month_start,
            ChannelData.date <= latest.date,
        ).group_by(Channel.id).subquery()
        monthly_revenue_rank = db.session.query(func.count()).select_from(monthly_totals).filter(
            or_(
                monthly_totals.c.revenue > monthly_revenue,
                and_(
                    monthly_totals.c.revenue == monthly_revenue,
                    monthly_totals.c.channel_id < channel.id,
                ),
            )
        ).scalar() + 1
        monthly_subscriber_rank = db.session.query(func.count()).select_from(monthly_totals).filter(
            or_(
                monthly_totals.c.subscriber_change > monthly_subscriber,
                and_(
                    monthly_totals.c.subscriber_change == monthly_subscriber,
                    monthly_totals.c.channel_id < channel.id,
                ),
            )
        ).scalar() + 1

        return {
            'success': True,
            'data': {
                'channel': {
                    'id': channel.id,
                    'name': channel.name,
                    'youtube_id': channel.channel_id,
                    'url': channel.url,
                    'category': channel.category,
                    'cpm': float(channel.cpm),
                },
                'latest_metrics': {
                    'date': latest.date.isoformat(),
                    'subscribers': latest.subscribers,
                    'total_views': latest.total_views,
                    'video_count': latest.video_count,
                },
                'recent_performance': {
                    'days': 30,
                    'revenue': float(recent_performance[0]),
                    'subscriber_change': int(recent_performance[1]),
                    'view_change': int(recent_performance[2]),
                },
                'rankings': {
                    'as_of': latest.date.isoformat(),
                    'daily_revenue_rank': daily_revenue_rank,
                    'daily_subscriber_rank': daily_subscriber_rank,
                    'monthly_revenue_rank': monthly_revenue_rank,
                    'monthly_subscriber_rank': monthly_subscriber_rank,
                },
                'trend': [{
                    'date': row.date.isoformat(),
                    'subscribers': row.subscribers,
                    'total_views': row.total_views,
                    'subscriber_change': row.subscriber_change,
                    'view_change': row.view_change,
                    'estimated_revenue': float(row.estimated_revenue),
                } for row in trend_rows],
                'periods': RankingService._period_tables(history_rows),
                'recent_videos': [video.to_card_dict() for video in recent_videos],
                'top_video': top_video.to_card_dict() if top_video else None,
            },
        }

    @staticmethod
    def _page(query, limit: int, page: int):
        """Return one SQL-backed page plus stable global-rank metadata."""
        per_page = max(1, int(limit))
        current_page = max(1, int(page))
        total = query.order_by(None).count()
        offset = (current_page - 1) * per_page
        rows = query.offset(offset).limit(per_page).all()
        return rows, offset, {
            'page': current_page,
            'per_page': per_page,
            'total': total,
            'total_pages': (total + per_page - 1) // per_page,
        }
    
    @staticmethod
    def get_daily_revenue_ranking(limit: int = 10, target_date: date = None, user_id: int = None, page: int = 1) -> Dict[str, Any]:
        """获取日收入排行榜
        
        Args:
            limit: 返回条数，默认10条
            target_date: 目标日期，默认今天
            user_id: 用户ID，用于数据过滤
            
        Returns:
            Dict: 排行榜数据
        """
        try:
            if target_date is None:
                target_date = date.today()
            
            # 查询指定日期收入排行
            query = db.session.query(
                Channel.id,
                Channel.name,
                Channel.channel_id,
                Channel.cpm,
                Channel.category,
                ChannelData.estimated_revenue
            ).join(ChannelData).filter(
                ChannelData.date == target_date,
                Channel.is_active == True
            )
            
            # 按用户过滤
            if user_id is not None:
                query = query.filter(Channel.user_id == user_id)
                
            ranking_data, rank_offset, pagination = RankingService._page(
                query.order_by(desc(ChannelData.estimated_revenue), Channel.id.asc()),
                limit,
                page,
            )
            
            # 格式化结果
            rankings = []
            for rank, (channel_id, name, youtube_id, cpm, category, revenue) in enumerate(ranking_data, rank_offset + 1):
                rankings.append({
                    'rank': rank,
                    'channel_id': channel_id,
                    'channel_name': name,
                    'youtube_id': youtube_id,
                    'cpm': float(cpm),
                    'category': category,
                    'revenue': float(revenue),
                    'formatted_revenue': f'${revenue:.2f}'
                })
            
            return {
                'success': True,
                'data': {
                    'type': 'daily_revenue',
                    'date': target_date.isoformat(),
                    'rankings': rankings,
                    'total_count': pagination['total'],
                    'pagination': pagination
                }
            }
            
        except Exception as e:
            return {
                'success': False,
                'message': f'获取日收入排行榜失败: {str(e)}',
                'data': None
            }
    
    @staticmethod
    def get_monthly_revenue_ranking(limit: int = 10, target_date: date = None, user_id: int = None, page: int = 1) -> Dict[str, Any]:
        """获取月收入排行榜
        
        Args:
            limit: 返回条数，默认10条
            target_date: 目标日期，默认今天所在月
            user_id: 用户ID，用于数据过滤
            
        Returns:
            Dict: 排行榜数据
        """
        try:
            if target_date is None:
                target_date = date.today()
            month_start = date(target_date.year, target_date.month, 1)
            
            # 查询指定月份收入排行
            # 计算月末日期
            if target_date.month == 12:
                month_end = date(target_date.year + 1, 1, 1) - timedelta(days=1)
            else:
                month_end = date(target_date.year, target_date.month + 1, 1) - timedelta(days=1)
            
            # 如果是当前月，则使用今天作为结束日期
            if target_date.year == date.today().year and target_date.month == date.today().month:
                month_end = min(month_end, date.today())
            
            query = db.session.query(
                Channel.id,
                Channel.name,
                Channel.channel_id,
                Channel.cpm,
                Channel.category,
                func.sum(ChannelData.estimated_revenue).label('total_revenue')
            ).join(ChannelData).filter(
                ChannelData.date >= month_start,
                ChannelData.date <= month_end,
                Channel.is_active == True
            )
            
            # 按用户过滤
            if user_id is not None:
                query = query.filter(Channel.user_id == user_id)
                
            grouped_query = query.group_by(
                Channel.id, Channel.name, Channel.channel_id, Channel.cpm, Channel.category
            ).order_by(desc('total_revenue'), Channel.id.asc())
            ranking_data, rank_offset, pagination = RankingService._page(
                grouped_query, limit, page
            )
            
            # 格式化结果
            rankings = []
            for rank, (channel_id, name, youtube_id, cpm, category, total_revenue) in enumerate(ranking_data, rank_offset + 1):
                rankings.append({
                    'rank': rank,
                    'channel_id': channel_id,
                    'channel_name': name,
                    'youtube_id': youtube_id,
                    'cpm': float(cpm),
                    'category': category,
                    'total_revenue': float(total_revenue),
                    'formatted_revenue': f'${total_revenue:.2f}'
                })
            
            return {
                'success': True,
                'data': {
                    'type': 'monthly_revenue',
                    'period': {
                        'start_date': month_start.isoformat(),
                        'end_date': month_end.isoformat()
                    },
                    'rankings': rankings,
                    'total_count': pagination['total'],
                    'pagination': pagination
                }
            }
            
        except Exception as e:
            return {
                'success': False,
                'message': f'获取月收入排行榜失败: {str(e)}',
                'data': None
            }

    @staticmethod
    def get_yearly_revenue_ranking(limit: int = 10, target_year: int = None, user_id: int = None, page: int = 1) -> Dict[str, Any]:
        """获取指定自然年的累计收入排行榜。"""
        try:
            target_year = target_year or date.today().year
            year_start = date(target_year, 1, 1)
            year_end = date(target_year, 12, 31)
            if target_year == date.today().year:
                year_end = date.today()

            query = db.session.query(
                Channel.id,
                Channel.name,
                Channel.channel_id,
                Channel.cpm,
                Channel.category,
                func.sum(ChannelData.estimated_revenue).label('total_revenue')
            ).join(ChannelData).filter(
                ChannelData.date >= year_start,
                ChannelData.date <= year_end,
                Channel.is_active == True
            )
            if user_id is not None:
                query = query.filter(Channel.user_id == user_id)

            grouped_query = query.group_by(
                Channel.id, Channel.name, Channel.channel_id, Channel.cpm, Channel.category
            ).order_by(desc('total_revenue'), Channel.id.asc())
            ranking_data, rank_offset, pagination = RankingService._page(grouped_query, limit, page)
            rankings = [{
                'rank': rank,
                'channel_id': channel_id,
                'channel_name': name,
                'youtube_id': youtube_id,
                'cpm': float(cpm),
                'category': category,
                'total_revenue': float(total_revenue),
                'formatted_revenue': f'${total_revenue:.2f}'
            } for rank, (channel_id, name, youtube_id, cpm, category, total_revenue) in enumerate(ranking_data, rank_offset + 1)]
            return {
                'success': True,
                'data': {
                    'type': 'yearly_revenue',
                    'period': {'start_date': year_start.isoformat(), 'end_date': year_end.isoformat()},
                    'rankings': rankings,
                    'total_count': pagination['total'],
                    'pagination': pagination,
                }
            }
        except Exception as e:
            return {'success': False, 'message': f'获取年收入排行榜失败: {str(e)}', 'data': None}
    
    @staticmethod
    def get_daily_subscriber_ranking(limit: int = 10, target_date: date = None, user_id: int = None, page: int = 1) -> Dict[str, Any]:
        """获取日新增订阅排行榜
        
        Args:
            limit: 返回条数，默认10条
            target_date: 目标日期，默认今天
            user_id: 用户ID，用于数据过滤
            
        Returns:
            Dict: 排行榜数据
        """
        try:
            if target_date is None:
                target_date = date.today()
            
            # 查询指定日期订阅增长排行
            query = db.session.query(
                Channel.id,
                Channel.name,
                Channel.channel_id,
                Channel.cpm,
                Channel.category,
                ChannelData.subscriber_change
            ).join(ChannelData).filter(
                ChannelData.date == target_date,
                Channel.is_active == True
            )
            
            # 按用户过滤
            if user_id is not None:
                query = query.filter(Channel.user_id == user_id)
                
            ranking_data, rank_offset, pagination = RankingService._page(
                query.order_by(desc(ChannelData.subscriber_change), Channel.id.asc()),
                limit,
                page,
            )
            
            # 格式化结果
            rankings = []
            for rank, (channel_id, name, youtube_id, cpm, category, subscriber_change) in enumerate(ranking_data, rank_offset + 1):
                rankings.append({
                    'rank': rank,
                    'channel_id': channel_id,
                    'channel_name': name,
                    'youtube_id': youtube_id,
                    'cpm': float(cpm),
                    'category': category,
                    'subscriber_change': subscriber_change,
                    'formatted_change': f'+{subscriber_change:,}' if subscriber_change > 0 else f'{subscriber_change:,}'
                })
            
            return {
                'success': True,
                'data': {
                    'type': 'daily_subscriber',
                    'date': target_date.isoformat(),
                    'rankings': rankings,
                    'total_count': pagination['total'],
                    'pagination': pagination
                }
            }
            
        except Exception as e:
            return {
                'success': False,
                'message': f'获取日订阅增长排行榜失败: {str(e)}',
                'data': None
            }
    
    @staticmethod
    def get_monthly_subscriber_ranking(limit: int = 10, target_date: date = None, user_id: int = None, page: int = 1) -> Dict[str, Any]:
        """获取月新增订阅排行榜
        
        Args:
            limit: 返回条数，默认10条
            target_date: 目标日期，默认今天所在月
            user_id: 用户ID，用于数据过滤
            
        Returns:
            Dict: 排行榜数据
        """
        try:
            if target_date is None:
                target_date = date.today()
            month_start = date(target_date.year, target_date.month, 1)
            
            # 查询指定月份订阅增长排行
            # 计算月末日期
            if target_date.month == 12:
                month_end = date(target_date.year + 1, 1, 1) - timedelta(days=1)
            else:
                month_end = date(target_date.year, target_date.month + 1, 1) - timedelta(days=1)
            
            # 如果是当前月，则使用今天作为结束日期
            if target_date.year == date.today().year and target_date.month == date.today().month:
                month_end = min(month_end, date.today())
            
            query = db.session.query(
                Channel.id,
                Channel.name,
                Channel.channel_id,
                Channel.cpm,
                Channel.category,
                func.sum(ChannelData.subscriber_change).label('total_subscriber_change')
            ).join(ChannelData).filter(
                ChannelData.date >= month_start,
                ChannelData.date <= month_end,
                Channel.is_active == True
            )
            
            # 按用户过滤
            if user_id is not None:
                query = query.filter(Channel.user_id == user_id)
                
            grouped_query = query.group_by(
                Channel.id, Channel.name, Channel.channel_id, Channel.cpm, Channel.category
            ).order_by(desc('total_subscriber_change'), Channel.id.asc())
            ranking_data, rank_offset, pagination = RankingService._page(
                grouped_query, limit, page
            )
            
            # 格式化结果
            rankings = []
            for rank, (channel_id, name, youtube_id, cpm, category, total_change) in enumerate(ranking_data, rank_offset + 1):
                rankings.append({
                    'rank': rank,
                    'channel_id': channel_id,
                    'channel_name': name,
                    'youtube_id': youtube_id,
                    'cpm': float(cpm),
                    'category': category,
                    'total_subscriber_change': total_change,
                    'formatted_change': f'+{total_change:,}' if total_change > 0 else f'{total_change:,}'
                })
            
            return {
                'success': True,
                'data': {
                    'type': 'monthly_subscriber',
                    'period': {
                        'start_date': month_start.isoformat(),
                        'end_date': month_end.isoformat()
                    },
                    'rankings': rankings,
                    'total_count': pagination['total'],
                    'pagination': pagination
                }
            }
            
        except Exception as e:
            return {
                'success': False,
                'message': f'获取月订阅增长排行榜失败: {str(e)}',
                'data': None
            }

    @staticmethod
    def get_yearly_subscriber_ranking(limit: int = 10, target_year: int = None, user_id: int = None, page: int = 1) -> Dict[str, Any]:
        """获取指定自然年的累计新增订阅排行榜。"""
        try:
            target_year = target_year or date.today().year
            year_start = date(target_year, 1, 1)
            year_end = date(target_year, 12, 31)
            if target_year == date.today().year:
                year_end = date.today()

            query = db.session.query(
                Channel.id,
                Channel.name,
                Channel.channel_id,
                Channel.cpm,
                Channel.category,
                func.sum(ChannelData.subscriber_change).label('total_subscriber_change')
            ).join(ChannelData).filter(
                ChannelData.date >= year_start,
                ChannelData.date <= year_end,
                Channel.is_active == True
            )
            if user_id is not None:
                query = query.filter(Channel.user_id == user_id)

            grouped_query = query.group_by(
                Channel.id, Channel.name, Channel.channel_id, Channel.cpm, Channel.category
            ).order_by(desc('total_subscriber_change'), Channel.id.asc())
            ranking_data, rank_offset, pagination = RankingService._page(grouped_query, limit, page)
            rankings = [{
                'rank': rank,
                'channel_id': channel_id,
                'channel_name': name,
                'youtube_id': youtube_id,
                'cpm': float(cpm),
                'category': category,
                'total_subscriber_change': total_change,
                'formatted_change': f'+{total_change:,}' if total_change > 0 else f'{total_change:,}'
            } for rank, (channel_id, name, youtube_id, cpm, category, total_change) in enumerate(ranking_data, rank_offset + 1)]
            return {
                'success': True,
                'data': {
                    'type': 'yearly_subscriber',
                    'period': {'start_date': year_start.isoformat(), 'end_date': year_end.isoformat()},
                    'rankings': rankings,
                    'total_count': pagination['total'],
                    'pagination': pagination,
                }
            }
        except Exception as e:
            return {'success': False, 'message': f'获取年订阅增长排行榜失败: {str(e)}', 'data': None}
    
    @staticmethod
    def get_all_rankings(limit: int = 3, user_id: int = None) -> Dict[str, Any]:
        """获取所有4个排行榜数据
        
        Args:
            limit: 每个排行榜返回的条数，默认3条
            user_id: 用户ID，用于数据过滤
            
        Returns:
            Dict: 所有排行榜数据
        """
        try:
            # 获取所有排行榜
            daily_revenue = RankingService.get_daily_revenue_ranking(limit, None, user_id)
            monthly_revenue = RankingService.get_monthly_revenue_ranking(limit, None, user_id)
            daily_subscriber = RankingService.get_daily_subscriber_ranking(limit, None, user_id)
            monthly_subscriber = RankingService.get_monthly_subscriber_ranking(limit, None, user_id)
            
            return {
                'success': True,
                'data': {
                    'daily_revenue': daily_revenue['data'] if daily_revenue['success'] else None,
                    'monthly_revenue': monthly_revenue['data'] if monthly_revenue['success'] else None,
                    'daily_subscriber': daily_subscriber['data'] if daily_subscriber['success'] else None,
                    'monthly_subscriber': monthly_subscriber['data'] if monthly_subscriber['success'] else None,
                    'updated_at': datetime.utcnow().isoformat()
                }
            }
            
        except Exception as e:
            return {
                'success': False,
                'message': f'获取排行榜数据失败: {str(e)}',
                'data': None
            }
    
    @staticmethod
    def get_channel_rankings(channel_id: int) -> Dict[str, Any]:
        """获取指定频道在各排行榜中的排名
        
        Args:
            channel_id: 频道ID
            
        Returns:
            Dict: 频道排名信息
        """
        try:
            channel = Channel.query.get(channel_id)
            if not channel:
                return {
                    'success': False,
                    'message': '频道不存在'
                }
            
            today = date.today()
            month_start = date(today.year, today.month, 1)
            
            # 获取今日数据
            today_data = ChannelData.query.filter_by(
                channel_id=channel_id,
                date=today
            ).first()
            
            # 计算各排行榜中的排名
            rankings = {}
            
            if today_data:
                # 日收入排名
                daily_revenue_rank = db.session.query(
                    func.count(ChannelData.id)
                ).join(Channel).filter(
                    ChannelData.date == today,
                    Channel.is_active == True,
                    ChannelData.estimated_revenue > today_data.estimated_revenue
                ).scalar() + 1
                
                # 日订阅增长排名
                daily_subscriber_rank = db.session.query(
                    func.count(ChannelData.id)
                ).join(Channel).filter(
                    ChannelData.date == today,
                    Channel.is_active == True,
                    ChannelData.subscriber_change > today_data.subscriber_change
                ).scalar() + 1
                
                rankings['daily_revenue_rank'] = daily_revenue_rank
                rankings['daily_subscriber_rank'] = daily_subscriber_rank
            else:
                rankings['daily_revenue_rank'] = None
                rankings['daily_subscriber_rank'] = None
            
            # 月收入排名（需要计算总和）
            channel_monthly_revenue = db.session.query(
                func.sum(ChannelData.estimated_revenue)
            ).filter(
                ChannelData.channel_id == channel_id,
                ChannelData.date >= month_start,
                ChannelData.date <= today
            ).scalar() or 0
            
            monthly_revenue_rank = db.session.query(
                func.count(func.distinct(Channel.id))
            ).join(ChannelData).filter(
                Channel.is_active == True,
                ChannelData.date >= month_start,
                ChannelData.date <= today
            ).having(
                func.sum(ChannelData.estimated_revenue) > channel_monthly_revenue
            ).group_by(Channel.id).count() + 1
            
            # 月订阅增长排名
            channel_monthly_subscriber = db.session.query(
                func.sum(ChannelData.subscriber_change)
            ).filter(
                ChannelData.channel_id == channel_id,
                ChannelData.date >= month_start,
                ChannelData.date <= today
            ).scalar() or 0
            
            monthly_subscriber_rank = db.session.query(
                func.count(func.distinct(Channel.id))
            ).join(ChannelData).filter(
                Channel.is_active == True,
                ChannelData.date >= month_start,
                ChannelData.date <= today
            ).having(
                func.sum(ChannelData.subscriber_change) > channel_monthly_subscriber
            ).group_by(Channel.id).count() + 1
            
            rankings['monthly_revenue_rank'] = monthly_revenue_rank
            rankings['monthly_subscriber_rank'] = monthly_subscriber_rank
            
            return {
                'success': True,
                'data': {
                    'channel': channel.to_dict(),
                    'rankings': rankings,
                    'today_data': today_data.to_dict() if today_data else None,
                    'monthly_totals': {
                        'revenue': float(channel_monthly_revenue),
                        'subscriber_change': channel_monthly_subscriber
                    }
                }
            }
            
        except Exception as e:
            return {
                'success': False,
                'message': f'获取频道排名失败: {str(e)}'
            }
