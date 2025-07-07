# YouTube数据同步服务
"""
YouTube数据同步服务

负责从YouTube API获取数据并同步到本地数据库
"""

from datetime import datetime, date, timedelta
from typing import List, Dict, Any, Optional
from utils.youtube_api import YouTubeAPI, YouTubeAPIError
from models import db, Channel, ChannelData
from .channel_service import ChannelService

class YouTubeSyncService:
    """YouTube数据同步服务类"""
    
    def __init__(self, api_key: str = None):
        """初始化同步服务
        
        Args:
            api_key: YouTube API密钥
        """
        try:
            self.youtube_api = YouTubeAPI(api_key)
        except YouTubeAPIError as e:
            print(f"YouTube API初始化失败: {e}")
            self.youtube_api = None
    
    def add_channel_from_url(self, url: str, name: str = None, 
                           cpm: float = 1.00, category: str = None) -> Dict[str, Any]:
        """从YouTube URL添加频道
        
        Args:
            url: YouTube频道URL
            name: 频道名称（可选，将从API获取）
            cpm: CPM值
            category: 分类
            
        Returns:
            Dict: 添加结果
        """
        try:
            if not self.youtube_api:
                return {
                    'success': False,
                    'message': 'YouTube API未初始化'
                }
            
            # 解析频道ID
            channel_id = self.youtube_api.parse_channel_url(url)
            if not channel_id:
                return {
                    'success': False,
                    'message': '无法解析YouTube频道URL'
                }
            
            # 检查频道是否已存在
            existing = ChannelService.get_channel_by_youtube_id(channel_id)
            if existing:
                return {
                    'success': False,
                    'message': f'频道 {existing["name"]} 已存在'
                }
            
            # 获取频道信息
            channel_info = self.youtube_api.get_channel_info(channel_id)
            if not channel_info:
                return {
                    'success': False,
                    'message': '无法获取频道信息，请检查URL是否正确'
                }
            
            # 使用API获取的名称（如果未提供）
            if not name:
                name = channel_info['title']
            
            # 创建频道记录
            result = ChannelService.create_channel(
                name=name,
                channel_id=channel_id,
                url=url,
                cpm=cpm,
                category=category
            )
            
            if result['success']:
                # 立即同步一次数据
                sync_result = self.sync_channel_data(result['data']['id'])
                if sync_result['success']:
                    result['message'] += f"，已同步初始数据"
                else:
                    result['message'] += f"，但初始数据同步失败: {sync_result['message']}"
            
            return result
            
        except Exception as e:
            return {
                'success': False,
                'message': f'添加频道失败: {str(e)}'
            }
    
    def sync_channel_data(self, db_channel_id: int, target_date: date = None) -> Dict[str, Any]:
        """同步单个频道的数据
        
        Args:
            db_channel_id: 数据库中的频道ID
            target_date: 目标日期，默认今天
            
        Returns:
            Dict: 同步结果
        """
        try:
            if not self.youtube_api:
                return {
                    'success': False,
                    'message': 'YouTube API未初始化'
                }
            
            # 获取频道信息
            channel = Channel.query.get(db_channel_id)
            if not channel:
                return {
                    'success': False,
                    'message': '频道不存在'
                }
            
            if not channel.is_active:
                return {
                    'success': False,
                    'message': '频道已停用'
                }
            
            # 设置目标日期
            if not target_date:
                target_date = date.today()
            
            # 检查当日数据是否已存在
            existing_data = ChannelData.query.filter_by(
                channel_id=db_channel_id,
                date=target_date
            ).first()
            
            # 获取YouTube统计数据
            stats = self.youtube_api.get_channel_statistics(channel.channel_id)
            if not stats:
                return {
                    'success': False,
                    'message': '无法获取频道统计数据'
                }
            
            # 获取前一天的数据用于计算变化量
            previous_date = target_date - timedelta(days=1)
            previous_data = ChannelData.query.filter_by(
                channel_id=db_channel_id,
                date=previous_date
            ).first()
            
            if existing_data:
                # 更新现有数据
                existing_data.subscribers = stats['subscriber_count']
                existing_data.total_views = stats['view_count']
                existing_data.video_count = stats['video_count']
                existing_data.calculate_changes(previous_data)
                
                db.session.commit()
                
                return {
                    'success': True,
                    'message': '频道数据更新成功',
                    'data': existing_data.to_dict()
                }
            else:
                # 创建新数据记录
                new_data = ChannelData(
                    channel_id=db_channel_id,
                    date=target_date,
                    subscribers=stats['subscriber_count'],
                    total_views=stats['view_count'],
                    video_count=stats['video_count']
                )
                
                # 计算变化量
                new_data.calculate_changes(previous_data)
                
                db.session.add(new_data)
                db.session.commit()
                
                return {
                    'success': True,
                    'message': '频道数据同步成功',
                    'data': new_data.to_dict()
                }
                
        except Exception as e:
            db.session.rollback()
            return {
                'success': False,
                'message': f'同步失败: {str(e)}'
            }
    
    def sync_all_channels(self, target_date: date = None) -> Dict[str, Any]:
        """同步所有活跃频道的数据
        
        Args:
            target_date: 目标日期，默认今天
            
        Returns:
            Dict: 同步结果汇总
        """
        try:
            if not self.youtube_api:
                return {
                    'success': False,
                    'message': 'YouTube API未初始化'
                }
            
            if not target_date:
                target_date = date.today()
            
            # 获取所有活跃频道
            channels = ChannelService.get_all_channels(active_only=True)
            if not channels:
                return {
                    'success': True,
                    'message': '没有活跃频道需要同步',
                    'data': {
                        'total_channels': 0,
                        'success_count': 0,
                        'failed_count': 0,
                        'results': []
                    }
                }
            
            results = []
            success_count = 0
            failed_count = 0
            
            # 批量获取YouTube数据（提高效率）
            channel_ids = [ch['channel_id'] for ch in channels]
            
            # 分批处理（每批最多50个）
            batch_size = 50
            for i in range(0, len(channel_ids), batch_size):
                batch_ids = channel_ids[i:i + batch_size]
                
                try:
                    # 批量获取统计数据
                    stats_list = self.youtube_api.get_multiple_channels_statistics(batch_ids)
                    stats_dict = {stat['channel_id']: stat for stat in stats_list}
                    
                    # 处理这批频道
                    for channel in channels[i:i + batch_size]:
                        db_channel_id = channel['id']
                        youtube_channel_id = channel['channel_id']
                        
                        try:
                            stats = stats_dict.get(youtube_channel_id)
                            if not stats:
                                results.append({
                                    'channel_id': db_channel_id,
                                    'channel_name': channel['name'],
                                    'success': False,
                                    'message': '未获取到统计数据'
                                })
                                failed_count += 1
                                continue
                            
                            # 检查当日数据是否已存在
                            existing_data = ChannelData.query.filter_by(
                                channel_id=db_channel_id,
                                date=target_date
                            ).first()
                            
                            # 获取前一天数据
                            previous_date = target_date - timedelta(days=1)
                            previous_data = ChannelData.query.filter_by(
                                channel_id=db_channel_id,
                                date=previous_date
                            ).first()
                            
                            if existing_data:
                                # 更新现有数据
                                existing_data.subscribers = stats['subscriber_count']
                                existing_data.total_views = stats['view_count']
                                existing_data.video_count = stats['video_count']
                                existing_data.calculate_changes(previous_data)
                                
                                results.append({
                                    'channel_id': db_channel_id,
                                    'channel_name': channel['name'],
                                    'success': True,
                                    'message': '数据更新成功',
                                    'data': existing_data.to_dict()
                                })
                            else:
                                # 创建新数据
                                new_data = ChannelData(
                                    channel_id=db_channel_id,
                                    date=target_date,
                                    subscribers=stats['subscriber_count'],
                                    total_views=stats['view_count'],
                                    video_count=stats['video_count']
                                )
                                
                                new_data.calculate_changes(previous_data)
                                db.session.add(new_data)
                                
                                results.append({
                                    'channel_id': db_channel_id,
                                    'channel_name': channel['name'],
                                    'success': True,
                                    'message': '数据同步成功',
                                    'data': new_data.to_dict()
                                })
                            
                            success_count += 1
                            
                        except Exception as e:
                            results.append({
                                'channel_id': db_channel_id,
                                'channel_name': channel['name'],
                                'success': False,
                                'message': f'同步失败: {str(e)}'
                            })
                            failed_count += 1
                    
                    # 提交这批的数据库更改
                    db.session.commit()
                    
                except Exception as e:
                    # 批量获取失败，回退到单个获取
                    db.session.rollback()
                    
                    for channel in channels[i:i + batch_size]:
                        sync_result = self.sync_channel_data(channel['id'], target_date)
                        results.append({
                            'channel_id': channel['id'],
                            'channel_name': channel['name'],
                            'success': sync_result['success'],
                            'message': sync_result['message'],
                            'data': sync_result.get('data')
                        })
                        
                        if sync_result['success']:
                            success_count += 1
                        else:
                            failed_count += 1
            
            return {
                'success': True,
                'message': f'批量同步完成: 成功 {success_count}，失败 {failed_count}',
                'data': {
                    'total_channels': len(channels),
                    'success_count': success_count,
                    'failed_count': failed_count,
                    'date': target_date.isoformat(),
                    'results': results,
                    'quota_info': self.youtube_api.get_quota_info()
                }
            }
            
        except Exception as e:
            db.session.rollback()
            return {
                'success': False,
                'message': f'批量同步失败: {str(e)}'
            }
    
    def get_api_status(self) -> Dict[str, Any]:
        """获取API状态信息
        
        Returns:
            Dict: API状态
        """
        if not self.youtube_api:
            return {
                'api_available': False,
                'message': 'YouTube API未初始化'
            }
        
        # 测试API连接
        test_result = self.youtube_api.test_api_connection()
        quota_info = self.youtube_api.get_quota_info()
        
        return {
            'api_available': test_result['success'],
            'message': test_result['message'],
            'quota_info': quota_info,
            'test_result': test_result
        }
    
    def validate_and_preview_channel(self, url: str) -> Dict[str, Any]:
        """验证并预览频道信息（不添加到数据库）
        
        Args:
            url: YouTube频道URL
            
        Returns:
            Dict: 预览结果
        """
        try:
            if not self.youtube_api:
                return {
                    'success': False,
                    'message': 'YouTube API未初始化'
                }
            
            # 解析频道ID
            channel_id = self.youtube_api.parse_channel_url(url)
            if not channel_id:
                return {
                    'success': False,
                    'message': '无法解析YouTube频道URL'
                }
            
            # 获取频道信息
            channel_info = self.youtube_api.get_channel_info(channel_id)
            if not channel_info:
                return {
                    'success': False,
                    'message': '无法获取频道信息，请检查URL是否正确'
                }
            
            # 检查是否已存在
            existing = ChannelService.get_channel_by_youtube_id(channel_id)
            
            return {
                'success': True,
                'message': '频道信息获取成功',
                'data': {
                    'channel_info': channel_info,
                    'already_exists': existing is not None,
                    'existing_channel': existing
                }
            }
            
        except Exception as e:
            return {
                'success': False,
                'message': f'预览失败: {str(e)}'
            }