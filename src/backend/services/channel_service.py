# 频道管理服务
"""
频道管理相关的业务逻辑服务

提供频道的CRUD操作、搜索、状态管理等功能
"""

from datetime import datetime, date
from typing import List, Optional, Dict, Any
from sqlalchemy.exc import IntegrityError
from models import db, Channel, ChannelData

class ChannelService:
    """频道管理服务类"""
    
    @staticmethod
    def create_channel(name: str, channel_id: str, url: str, 
                      cpm: float = 1.00, category: str = None) -> Dict[str, Any]:
        """创建新频道
        
        Args:
            name: 频道名称
            channel_id: YouTube频道ID
            url: 频道URL
            cpm: CPM值，默认1.00
            category: 频道分类，可选
            
        Returns:
            Dict: 包含结果状态和数据的字典
        """
        try:
            # 检查频道是否已存在
            existing = Channel.query.filter_by(channel_id=channel_id).first()
            if existing:
                return {
                    'success': False,
                    'message': f'频道 {channel_id} 已存在',
                    'data': None
                }
            
            # 创建新频道
            channel = Channel(
                name=name,
                channel_id=channel_id,
                url=url,
                cpm=cpm,
                category=category
            )
            
            db.session.add(channel)
            db.session.commit()
            
            return {
                'success': True,
                'message': '频道创建成功',
                'data': channel.to_dict()
            }
            
        except IntegrityError as e:
            db.session.rollback()
            return {
                'success': False,
                'message': f'数据库约束错误: {str(e)}',
                'data': None
            }
        except Exception as e:
            db.session.rollback()
            return {
                'success': False,
                'message': f'创建频道失败: {str(e)}',
                'data': None
            }
    
    @staticmethod
    def get_all_channels(active_only: bool = True) -> List[Dict[str, Any]]:
        """获取所有频道
        
        Args:
            active_only: 是否只返回活跃频道，默认True
            
        Returns:
            List[Dict]: 频道列表
        """
        try:
            query = Channel.query
            if active_only:
                query = query.filter_by(is_active=True)
            
            channels = query.order_by(Channel.created_at.desc()).all()
            return [channel.to_dict() for channel in channels]
            
        except Exception as e:
            print(f"获取频道列表失败: {e}")
            return []
    
    @staticmethod
    def get_channel_by_id(channel_id: int) -> Optional[Dict[str, Any]]:
        """根据ID获取频道
        
        Args:
            channel_id: 频道数据库ID
            
        Returns:
            Optional[Dict]: 频道信息或None
        """
        try:
            channel = Channel.query.get(channel_id)
            return channel.to_dict() if channel else None
        except Exception as e:
            print(f"获取频道失败: {e}")
            return None
    
    @staticmethod
    def get_channel_by_youtube_id(youtube_id: str) -> Optional[Dict[str, Any]]:
        """根据YouTube频道ID获取频道
        
        Args:
            youtube_id: YouTube频道ID
            
        Returns:
            Optional[Dict]: 频道信息或None
        """
        try:
            channel = Channel.query.filter_by(channel_id=youtube_id).first()
            return channel.to_dict() if channel else None
        except Exception as e:
            print(f"获取频道失败: {e}")
            return None
    
    @staticmethod
    def update_channel(channel_id: int, **kwargs) -> Dict[str, Any]:
        """更新频道信息
        
        Args:
            channel_id: 频道数据库ID
            **kwargs: 要更新的字段
            
        Returns:
            Dict: 更新结果
        """
        try:
            channel = Channel.query.get(channel_id)
            if not channel:
                return {
                    'success': False,
                    'message': '频道不存在',
                    'data': None
                }
            
            # 更新允许的字段
            allowed_fields = ['name', 'url', 'cpm', 'category', 'is_active']
            for key, value in kwargs.items():
                if key in allowed_fields and hasattr(channel, key):
                    setattr(channel, key, value)
            
            channel.updated_at = datetime.utcnow()
            db.session.commit()
            
            return {
                'success': True,
                'message': '频道更新成功',
                'data': channel.to_dict()
            }
            
        except Exception as e:
            db.session.rollback()
            return {
                'success': False,
                'message': f'更新频道失败: {str(e)}',
                'data': None
            }
    
    @staticmethod
    def delete_channel(channel_id: int) -> Dict[str, Any]:
        """删除频道（软删除 - 设为非活跃）
        
        Args:
            channel_id: 频道数据库ID
            
        Returns:
            Dict: 删除结果
        """
        try:
            channel = Channel.query.get(channel_id)
            if not channel:
                return {
                    'success': False,
                    'message': '频道不存在',
                    'data': None
                }
            
            # 软删除：设为非活跃状态
            channel.is_active = False
            channel.updated_at = datetime.utcnow()
            db.session.commit()
            
            return {
                'success': True,
                'message': '频道已停用',
                'data': channel.to_dict()
            }
            
        except Exception as e:
            db.session.rollback()
            return {
                'success': False,
                'message': f'停用频道失败: {str(e)}',
                'data': None
            }
    
    @staticmethod
    def search_channels(keyword: str, active_only: bool = True) -> List[Dict[str, Any]]:
        """搜索频道
        
        Args:
            keyword: 搜索关键词
            active_only: 是否只搜索活跃频道
            
        Returns:
            List[Dict]: 匹配的频道列表
        """
        try:
            query = Channel.query.filter(
                Channel.name.ilike(f'%{keyword}%')
            )
            
            if active_only:
                query = query.filter_by(is_active=True)
            
            channels = query.order_by(Channel.created_at.desc()).all()
            return [channel.to_dict() for channel in channels]
            
        except Exception as e:
            print(f"搜索频道失败: {e}")
            return []
    
    @staticmethod
    def get_channels_by_category(category: str, active_only: bool = True) -> List[Dict[str, Any]]:
        """根据分类获取频道
        
        Args:
            category: 频道分类
            active_only: 是否只返回活跃频道
            
        Returns:
            List[Dict]: 频道列表
        """
        try:
            query = Channel.query.filter_by(category=category)
            
            if active_only:
                query = query.filter_by(is_active=True)
            
            channels = query.order_by(Channel.created_at.desc()).all()
            return [channel.to_dict() for channel in channels]
            
        except Exception as e:
            print(f"获取分类频道失败: {e}")
            return []
    
    @staticmethod
    def get_channel_statistics(channel_id: int) -> Dict[str, Any]:
        """获取频道统计信息
        
        Args:
            channel_id: 频道数据库ID
            
        Returns:
            Dict: 统计信息
        """
        try:
            channel = Channel.query.get(channel_id)
            if not channel:
                return {'success': False, 'message': '频道不存在'}
            
            # 获取最新数据
            latest_data = ChannelData.query.filter_by(
                channel_id=channel_id
            ).order_by(ChannelData.date.desc()).first()
            
            # 获取数据记录总数
            total_records = ChannelData.query.filter_by(channel_id=channel_id).count()
            
            # 获取最早和最晚数据日期
            earliest = ChannelData.query.filter_by(
                channel_id=channel_id
            ).order_by(ChannelData.date.asc()).first()
            
            # 计算总收入
            total_revenue = db.session.query(
                db.func.sum(ChannelData.estimated_revenue)
            ).filter_by(channel_id=channel_id).scalar() or 0
            
            return {
                'success': True,
                'data': {
                    'channel': channel.to_dict(),
                    'latest_data': latest_data.to_dict() if latest_data else None,
                    'total_records': total_records,
                    'earliest_date': earliest.date.isoformat() if earliest else None,
                    'latest_date': latest_data.date.isoformat() if latest_data else None,
                    'total_revenue': float(total_revenue)
                }
            }
            
        except Exception as e:
            return {
                'success': False,
                'message': f'获取统计信息失败: {str(e)}'
            }