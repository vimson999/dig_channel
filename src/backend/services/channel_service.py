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
    def create_channel(user_id: int, name: str, channel_id: str, url: str, 
                      cpm: float = 1.00, category: str = None, sort: int = 0) -> Dict[str, Any]:
        """创建新频道
        
        Args:
            user_id: 用户ID
            name: 频道名称
            channel_id: YouTube频道ID
            url: 频道URL
            cpm: CPM值，默认1.00
            category: 频道分类，可选
            sort: 排序值，默认0
            
        Returns:
            Dict: 包含结果状态和数据的字典
        """
        try:
            # 检查用户下是否已存在相同频道
            existing = Channel.query.filter_by(user_id=user_id, channel_id=channel_id).first()
            if existing:
                return {
                    'success': False,
                    'message': f'您已添加过频道 {channel_id}',
                    'data': None
                }
            
            # 创建新频道
            channel = Channel(
                user_id=user_id,
                name=name,
                channel_id=channel_id,
                url=url,
                cpm=cpm,
                category=category,
                sort=sort
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
    def get_user_channels(user_id: int, active_only: bool = True, order_by_sort: bool = False) -> List[Dict[str, Any]]:
        """获取用户的频道列表
        
        Args:
            user_id: 用户ID
            active_only: 是否只返回活跃频道，默认True
            order_by_sort: 是否按sort字段排序，默认False
            
        Returns:
            List[Dict]: 频道列表
        """
        try:
            query = Channel.query.filter_by(user_id=user_id)
            if active_only:
                query = query.filter_by(is_active=True)
            
            # 选择排序方式
            if order_by_sort:
                channels = query.order_by(Channel.sort.asc(), Channel.id.asc()).all()
            else:
                channels = query.order_by(Channel.created_at.desc()).all()
                
            return [channel.to_dict() for channel in channels]
            
        except Exception as e:
            print(f"获取用户频道列表失败: {e}")
            return []
    
    @staticmethod
    def get_all_channels(active_only: bool = True, order_by_sort: bool = False) -> List[Dict[str, Any]]:
        """获取所有频道（管理员用）
        
        Args:
            active_only: 是否只返回活跃频道，默认True
            order_by_sort: 是否按sort字段排序，默认False
            
        Returns:
            List[Dict]: 频道列表
        """
        try:
            query = Channel.query
            if active_only:
                query = query.filter_by(is_active=True)
            
            # 选择排序方式
            if order_by_sort:
                channels = query.order_by(Channel.sort.asc(), Channel.id.asc()).all()
            else:
                channels = query.order_by(Channel.created_at.desc()).all()
                
            return [channel.to_dict() for channel in channels]
            
        except Exception as e:
            print(f"获取频道列表失败: {e}")
            return []
    
    @staticmethod
    def get_channel_by_id(channel_id: int, user_id: int = None) -> Optional[Dict[str, Any]]:
        """根据ID获取频道
        
        Args:
            channel_id: 频道数据库ID
            user_id: 用户ID，如果提供则检查权限
            
        Returns:
            Optional[Dict]: 频道信息或None
        """
        try:
            query = Channel.query
            if user_id is not None:
                query = query.filter_by(user_id=user_id)
            
            channel = query.filter_by(id=channel_id).first()
            return channel.to_dict() if channel else None
        except Exception as e:
            print(f"获取频道失败: {e}")
            return None
    
    @staticmethod
    def get_channel_by_youtube_id(youtube_id: str, user_id: int = None) -> Optional[Dict[str, Any]]:
        """根据YouTube频道ID获取频道
        
        Args:
            youtube_id: YouTube频道ID
            user_id: 用户ID，如果提供则检查权限
            
        Returns:
            Optional[Dict]: 频道信息或None
        """
        try:
            query = Channel.query.filter_by(channel_id=youtube_id)
            if user_id is not None:
                query = query.filter_by(user_id=user_id)
            
            channel = query.first()
            return channel.to_dict() if channel else None
        except Exception as e:
            print(f"获取频道失败: {e}")
            return None
    
    @staticmethod
    def update_channel(channel_id: int, user_id: int = None, **kwargs) -> Dict[str, Any]:
        """更新频道信息
        
        Args:
            channel_id: 频道数据库ID
            user_id: 用户ID，如果提供则检查权限
            **kwargs: 要更新的字段
            
        Returns:
            Dict: 更新结果
        """
        try:
            query = Channel.query
            if user_id is not None:
                query = query.filter_by(user_id=user_id)
            
            channel = query.filter_by(id=channel_id).first()
            if not channel:
                return {
                    'success': False,
                    'message': '频道不存在或无权限访问',
                    'data': None
                }
            
            # 更新允许的字段
            allowed_fields = ['name', 'url', 'cpm', 'category', 'sort', 'is_active']
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
    def delete_channel(channel_id: int, user_id: int = None) -> Dict[str, Any]:
        """删除频道（软删除 - 设为非活跃）
        
        Args:
            channel_id: 频道数据库ID
            user_id: 用户ID，如果提供则检查权限
            
        Returns:
            Dict: 删除结果
        """
        try:
            query = Channel.query
            if user_id is not None:
                query = query.filter_by(user_id=user_id)
            
            channel = query.filter_by(id=channel_id).first()
            if not channel:
                return {
                    'success': False,
                    'message': '频道不存在或无权限访问',
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
    def search_user_channels(user_id: int, keyword: str, active_only: bool = True, order_by_sort: bool = False) -> List[Dict[str, Any]]:
        """搜索用户的频道
        
        Args:
            user_id: 用户ID
            keyword: 搜索关键词
            active_only: 是否只搜索活跃频道
            order_by_sort: 是否按sort字段排序，默认False
            
        Returns:
            List[Dict]: 匹配的频道列表
        """
        try:
            query = Channel.query.filter(
                Channel.user_id == user_id,
                Channel.name.ilike(f'%{keyword}%')
            )
            
            if active_only:
                query = query.filter_by(is_active=True)
            
            # 选择排序方式
            if order_by_sort:
                channels = query.order_by(Channel.sort.asc(), Channel.id.asc()).all()
            else:
                channels = query.order_by(Channel.created_at.desc()).all()
                
            return [channel.to_dict() for channel in channels]
            
        except Exception as e:
            print(f"搜索用户频道失败: {e}")
            return []
    
    @staticmethod
    def search_channels(keyword: str, active_only: bool = True, order_by_sort: bool = False) -> List[Dict[str, Any]]:
        """搜索所有频道（管理员用）
        
        Args:
            keyword: 搜索关键词
            active_only: 是否只搜索活跃频道
            order_by_sort: 是否按sort字段排序，默认False
            
        Returns:
            List[Dict]: 匹配的频道列表
        """
        try:
            query = Channel.query.filter(
                Channel.name.ilike(f'%{keyword}%')
            )
            
            if active_only:
                query = query.filter_by(is_active=True)
            
            # 选择排序方式
            if order_by_sort:
                channels = query.order_by(Channel.sort.asc(), Channel.id.asc()).all()
            else:
                channels = query.order_by(Channel.created_at.desc()).all()
                
            return [channel.to_dict() for channel in channels]
            
        except Exception as e:
            print(f"搜索频道失败: {e}")
            return []
    
    @staticmethod
    def get_user_channels_by_category(user_id: int, category: str, active_only: bool = True, order_by_sort: bool = False) -> List[Dict[str, Any]]:
        """根据分类获取用户的频道
        
        Args:
            user_id: 用户ID
            category: 频道分类
            active_only: 是否只返回活跃频道
            order_by_sort: 是否按sort字段排序，默认False
            
        Returns:
            List[Dict]: 频道列表
        """
        try:
            query = Channel.query.filter_by(user_id=user_id, category=category)
            
            if active_only:
                query = query.filter_by(is_active=True)
            
            # 选择排序方式
            if order_by_sort:
                channels = query.order_by(Channel.sort.asc(), Channel.id.asc()).all()
            else:
                channels = query.order_by(Channel.created_at.desc()).all()
                
            return [channel.to_dict() for channel in channels]
            
        except Exception as e:
            print(f"获取用户分类频道失败: {e}")
            return []
    
    @staticmethod
    def get_channels_by_category(category: str, active_only: bool = True, order_by_sort: bool = False) -> List[Dict[str, Any]]:
        """根据分类获取所有频道（管理员用）
        
        Args:
            category: 频道分类
            active_only: 是否只返回活跃频道
            order_by_sort: 是否按sort字段排序，默认False
            
        Returns:
            List[Dict]: 频道列表
        """
        try:
            query = Channel.query.filter_by(category=category)
            
            if active_only:
                query = query.filter_by(is_active=True)
            
            # 选择排序方式
            if order_by_sort:
                channels = query.order_by(Channel.sort.asc(), Channel.id.asc()).all()
            else:
                channels = query.order_by(Channel.created_at.desc()).all()
                
            return [channel.to_dict() for channel in channels]
            
        except Exception as e:
            print(f"获取分类频道失败: {e}")
            return []
    
    @staticmethod
    def get_channel_statistics(channel_id: int, user_id: int = None) -> Dict[str, Any]:
        """获取频道统计信息
        
        Args:
            channel_id: 频道数据库ID
            user_id: 用户ID，如果提供则检查权限
            
        Returns:
            Dict: 统计信息
        """
        try:
            query = Channel.query
            if user_id is not None:
                query = query.filter_by(user_id=user_id)
            
            channel = query.filter_by(id=channel_id).first()
            if not channel:
                return {'success': False, 'message': '频道不存在或无权限访问'}
            
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