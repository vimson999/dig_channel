# 排行榜服务
"""
排行榜相关的业务逻辑服务

提供4个排行榜的数据查询和计算功能
"""

from datetime import datetime, date, timedelta
from typing import List, Dict, Any
from sqlalchemy import func, desc
from models import db, Channel, ChannelData

class RankingService:
    """排行榜服务类"""
    
    @staticmethod
    def get_daily_revenue_ranking(limit: int = 10, target_date: date = None) -> Dict[str, Any]:
        """获取日收入排行榜
        
        Args:
            limit: 返回条数，默认10条
            target_date: 目标日期，默认今天
            
        Returns:
            Dict: 排行榜数据
        """
        try:
            if target_date is None:
                target_date = date.today()
            
            # 查询指定日期收入排行
            ranking_data = db.session.query(
                Channel.id,
                Channel.name,
                Channel.channel_id,
                ChannelData.estimated_revenue
            ).join(ChannelData).filter(
                ChannelData.date == target_date,
                Channel.is_active == True
            ).order_by(desc(ChannelData.estimated_revenue)).limit(limit).all()
            
            # 格式化结果
            rankings = []
            for rank, (channel_id, name, youtube_id, revenue) in enumerate(ranking_data, 1):
                rankings.append({
                    'rank': rank,
                    'channel_id': channel_id,
                    'channel_name': name,
                    'youtube_id': youtube_id,
                    'revenue': float(revenue),
                    'formatted_revenue': f'${revenue:.2f}'
                })
            
            return {
                'success': True,
                'data': {
                    'type': 'daily_revenue',
                    'date': target_date.isoformat(),
                    'rankings': rankings,
                    'total_count': len(rankings)
                }
            }
            
        except Exception as e:
            return {
                'success': False,
                'message': f'获取日收入排行榜失败: {str(e)}',
                'data': None
            }
    
    @staticmethod
    def get_monthly_revenue_ranking(limit: int = 10, target_date: date = None) -> Dict[str, Any]:
        """获取月收入排行榜
        
        Args:
            limit: 返回条数，默认10条
            target_date: 目标日期，默认今天所在月
            
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
            
            ranking_data = db.session.query(
                Channel.id,
                Channel.name,
                Channel.channel_id,
                func.sum(ChannelData.estimated_revenue).label('total_revenue')
            ).join(ChannelData).filter(
                ChannelData.date >= month_start,
                ChannelData.date <= month_end,
                Channel.is_active == True
            ).group_by(
                Channel.id, Channel.name, Channel.channel_id
            ).order_by(desc('total_revenue')).limit(limit).all()
            
            # 格式化结果
            rankings = []
            for rank, (channel_id, name, youtube_id, total_revenue) in enumerate(ranking_data, 1):
                rankings.append({
                    'rank': rank,
                    'channel_id': channel_id,
                    'channel_name': name,
                    'youtube_id': youtube_id,
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
                    'total_count': len(rankings)
                }
            }
            
        except Exception as e:
            return {
                'success': False,
                'message': f'获取月收入排行榜失败: {str(e)}',
                'data': None
            }
    
    @staticmethod
    def get_daily_subscriber_ranking(limit: int = 10, target_date: date = None) -> Dict[str, Any]:
        """获取日新增订阅排行榜
        
        Args:
            limit: 返回条数，默认10条
            target_date: 目标日期，默认今天
            
        Returns:
            Dict: 排行榜数据
        """
        try:
            if target_date is None:
                target_date = date.today()
            
            # 查询指定日期订阅增长排行
            ranking_data = db.session.query(
                Channel.id,
                Channel.name,
                Channel.channel_id,
                ChannelData.subscriber_change
            ).join(ChannelData).filter(
                ChannelData.date == target_date,
                Channel.is_active == True
            ).order_by(desc(ChannelData.subscriber_change)).limit(limit).all()
            
            # 格式化结果
            rankings = []
            for rank, (channel_id, name, youtube_id, subscriber_change) in enumerate(ranking_data, 1):
                rankings.append({
                    'rank': rank,
                    'channel_id': channel_id,
                    'channel_name': name,
                    'youtube_id': youtube_id,
                    'subscriber_change': subscriber_change,
                    'formatted_change': f'+{subscriber_change:,}' if subscriber_change > 0 else f'{subscriber_change:,}'
                })
            
            return {
                'success': True,
                'data': {
                    'type': 'daily_subscriber',
                    'date': target_date.isoformat(),
                    'rankings': rankings,
                    'total_count': len(rankings)
                }
            }
            
        except Exception as e:
            return {
                'success': False,
                'message': f'获取日订阅增长排行榜失败: {str(e)}',
                'data': None
            }
    
    @staticmethod
    def get_monthly_subscriber_ranking(limit: int = 10, target_date: date = None) -> Dict[str, Any]:
        """获取月新增订阅排行榜
        
        Args:
            limit: 返回条数，默认10条
            target_date: 目标日期，默认今天所在月
            
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
            
            ranking_data = db.session.query(
                Channel.id,
                Channel.name,
                Channel.channel_id,
                func.sum(ChannelData.subscriber_change).label('total_subscriber_change')
            ).join(ChannelData).filter(
                ChannelData.date >= month_start,
                ChannelData.date <= month_end,
                Channel.is_active == True
            ).group_by(
                Channel.id, Channel.name, Channel.channel_id
            ).order_by(desc('total_subscriber_change')).limit(limit).all()
            
            # 格式化结果
            rankings = []
            for rank, (channel_id, name, youtube_id, total_change) in enumerate(ranking_data, 1):
                rankings.append({
                    'rank': rank,
                    'channel_id': channel_id,
                    'channel_name': name,
                    'youtube_id': youtube_id,
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
                    'total_count': len(rankings)
                }
            }
            
        except Exception as e:
            return {
                'success': False,
                'message': f'获取月订阅增长排行榜失败: {str(e)}',
                'data': None
            }
    
    @staticmethod
    def get_all_rankings(limit: int = 3) -> Dict[str, Any]:
        """获取所有4个排行榜数据
        
        Args:
            limit: 每个排行榜返回的条数，默认3条
            
        Returns:
            Dict: 所有排行榜数据
        """
        try:
            # 获取所有排行榜
            daily_revenue = RankingService.get_daily_revenue_ranking(limit)
            monthly_revenue = RankingService.get_monthly_revenue_ranking(limit)
            daily_subscriber = RankingService.get_daily_subscriber_ranking(limit)
            monthly_subscriber = RankingService.get_monthly_subscriber_ranking(limit)
            
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