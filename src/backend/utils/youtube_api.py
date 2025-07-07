# YouTube API 集成工具
"""
YouTube Data API v3 集成模块

提供频道数据获取、URL解析、API配额管理等功能
"""

import re
import time
import requests
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs
from config import get_config

class YouTubeAPIError(Exception):
    """YouTube API 相关异常"""
    pass

class YouTubeAPI:
    """YouTube Data API v3 客户端"""
    
    def __init__(self, api_key: str = None):
        """初始化YouTube API客户端
        
        Args:
            api_key: YouTube Data API v3 密钥
        """
        self.api_key = api_key or get_config().YOUTUBE_API_KEY
        if not self.api_key:
            raise YouTubeAPIError("YouTube API密钥未配置")
        
        self.base_url = "https://www.googleapis.com/youtube/v3"
        self.session = requests.Session()
        self.quota_used = 0
        self.quota_limit = 10000  # 每日配额限制
        self.request_count = 0
        self.last_reset = datetime.now().date()
    
    def _make_request(self, endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """发起API请求
        
        Args:
            endpoint: API端点
            params: 请求参数
            
        Returns:
            Dict: API响应数据
            
        Raises:
            YouTubeAPIError: API请求失败
        """
        # 检查配额
        self._check_quota()
        
        # 添加API密钥
        params['key'] = self.api_key
        
        # 构建请求URL
        url = f"{self.base_url}/{endpoint}"
        
        try:
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            
            data = response.json()
            
            # 更新配额使用情况
            self._update_quota(endpoint, params)
            
            if 'error' in data:
                raise YouTubeAPIError(f"API错误: {data['error']['message']}")
            
            return data
            
        except requests.exceptions.RequestException as e:
            raise YouTubeAPIError(f"网络请求失败: {str(e)}")
        except ValueError as e:
            raise YouTubeAPIError(f"JSON解析失败: {str(e)}")
    
    def _check_quota(self):
        """检查API配额"""
        current_date = datetime.now().date()
        
        # 如果是新的一天，重置配额
        if current_date > self.last_reset:
            self.quota_used = 0
            self.request_count = 0
            self.last_reset = current_date
        
        # 检查配额是否超限
        if self.quota_used >= self.quota_limit:
            raise YouTubeAPIError(f"API配额已用完，已使用: {self.quota_used}/{self.quota_limit}")
    
    def _update_quota(self, endpoint: str, params: Dict[str, Any]):
        """更新配额使用情况
        
        Args:
            endpoint: API端点
            params: 请求参数
        """
        self.request_count += 1
        
        # 不同端点的配额消耗不同
        if endpoint == 'channels':
            cost = 1
        elif endpoint == 'videos':
            cost = 1
        elif endpoint == 'search':
            cost = 100
        else:
            cost = 1
        
        self.quota_used += cost
    
    def parse_channel_url(self, url: str) -> Optional[str]:
        """解析YouTube频道URL，提取频道ID
        
        Args:
            url: YouTube频道URL
            
        Returns:
            Optional[str]: 频道ID，解析失败返回None
        """
        try:
            parsed = urlparse(url)
            
            # 检查是否为YouTube域名
            if parsed.netloc not in ['www.youtube.com', 'youtube.com', 'm.youtube.com']:
                return None
            
            path = parsed.path
            
            # 处理不同的URL格式
            if '/channel/' in path:
                # https://www.youtube.com/channel/UCxxxxxxxxxxxxxxx
                channel_id = path.split('/channel/')[-1].split('/')[0]
                return channel_id
            
            elif '/c/' in path or '/user/' in path:
                # https://www.youtube.com/c/channelname 或 /user/username
                # 需要通过用户名查找频道ID
                username = path.split('/')[-1]
                return self._get_channel_id_by_username(username)
            
            elif path.startswith('/@'):
                # https://www.youtube.com/@channelhandle
                handle = path[2:]  # 去掉 /@
                return self._get_channel_id_by_handle(handle)
            
            elif path == '/' and 'v' in parse_qs(parsed.query):
                # 从视频URL提取频道ID
                video_id = parse_qs(parsed.query)['v'][0]
                return self._get_channel_id_by_video(video_id)
            
            return None
            
        except Exception as e:
            print(f"解析URL失败: {e}")
            return None
    
    def _get_channel_id_by_username(self, username: str) -> Optional[str]:
        """通过用户名获取频道ID
        
        Args:
            username: YouTube用户名
            
        Returns:
            Optional[str]: 频道ID
        """
        try:
            params = {
                'part': 'id',
                'forUsername': username
            }
            
            data = self._make_request('channels', params)
            
            if data.get('items'):
                return data['items'][0]['id']
            
            return None
            
        except Exception as e:
            print(f"通过用户名获取频道ID失败: {e}")
            return None
    
    def _get_channel_id_by_handle(self, handle: str) -> Optional[str]:
        """通过频道句柄获取频道ID
        
        Args:
            handle: 频道句柄（不包含@）
            
        Returns:
            Optional[str]: 频道ID
        """
        try:
            # YouTube API目前不直接支持句柄搜索，使用搜索API
            params = {
                'part': 'snippet',
                'type': 'channel',
                'q': handle,
                'maxResults': 1
            }
            
            data = self._make_request('search', params)
            
            if data.get('items'):
                return data['items'][0]['snippet']['channelId']
            
            return None
            
        except Exception as e:
            print(f"通过句柄获取频道ID失败: {e}")
            return None
    
    def _get_channel_id_by_video(self, video_id: str) -> Optional[str]:
        """通过视频ID获取频道ID
        
        Args:
            video_id: 视频ID
            
        Returns:
            Optional[str]: 频道ID
        """
        try:
            params = {
                'part': 'snippet',
                'id': video_id
            }
            
            data = self._make_request('videos', params)
            
            if data.get('items'):
                return data['items'][0]['snippet']['channelId']
            
            return None
            
        except Exception as e:
            print(f"通过视频ID获取频道ID失败: {e}")
            return None
    
    def get_channel_info(self, channel_id: str) -> Optional[Dict[str, Any]]:
        """获取频道基本信息
        
        Args:
            channel_id: 频道ID
            
        Returns:
            Optional[Dict]: 频道信息
        """
        try:
            params = {
                'part': 'snippet,statistics',
                'id': channel_id
            }
            
            data = self._make_request('channels', params)
            
            if not data.get('items'):
                return None
            
            item = data['items'][0]
            snippet = item['snippet']
            statistics = item['statistics']
            
            return {
                'channel_id': channel_id,
                'title': snippet['title'],
                'description': snippet.get('description', ''),
                'custom_url': snippet.get('customUrl', ''),
                'published_at': snippet['publishedAt'],
                'thumbnail_url': snippet['thumbnails']['high']['url'],
                'subscriber_count': int(statistics.get('subscriberCount', 0)),
                'video_count': int(statistics.get('videoCount', 0)),
                'view_count': int(statistics.get('viewCount', 0)),
                'country': snippet.get('country', ''),
                'hidden_subscriber_count': statistics.get('hiddenSubscriberCount', False)
            }
            
        except Exception as e:
            print(f"获取频道信息失败: {e}")
            return None
    
    def get_channel_statistics(self, channel_id: str) -> Optional[Dict[str, Any]]:
        """获取频道统计数据（用于日常更新）
        
        Args:
            channel_id: 频道ID
            
        Returns:
            Optional[Dict]: 统计数据
        """
        try:
            params = {
                'part': 'statistics',
                'id': channel_id
            }
            
            data = self._make_request('channels', params)
            
            if not data.get('items'):
                return None
            
            statistics = data['items'][0]['statistics']
            
            return {
                'channel_id': channel_id,
                'subscriber_count': int(statistics.get('subscriberCount', 0)),
                'video_count': int(statistics.get('videoCount', 0)),
                'view_count': int(statistics.get('viewCount', 0)),
                'fetched_at': datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            print(f"获取频道统计数据失败: {e}")
            return None
    
    def get_multiple_channels_statistics(self, channel_ids: List[str]) -> List[Dict[str, Any]]:
        """批量获取多个频道的统计数据
        
        Args:
            channel_ids: 频道ID列表（最多50个）
            
        Returns:
            List[Dict]: 统计数据列表
        """
        try:
            # YouTube API单次最多查询50个频道
            if len(channel_ids) > 50:
                raise YouTubeAPIError("单次查询频道数量不能超过50个")
            
            params = {
                'part': 'statistics',
                'id': ','.join(channel_ids)
            }
            
            data = self._make_request('channels', params)
            
            results = []
            for item in data.get('items', []):
                statistics = item['statistics']
                results.append({
                    'channel_id': item['id'],
                    'subscriber_count': int(statistics.get('subscriberCount', 0)),
                    'video_count': int(statistics.get('videoCount', 0)),
                    'view_count': int(statistics.get('viewCount', 0)),
                    'fetched_at': datetime.utcnow().isoformat()
                })
            
            return results
            
        except Exception as e:
            print(f"批量获取频道统计数据失败: {e}")
            return []
    
    def test_api_connection(self) -> Dict[str, Any]:
        """测试API连接
        
        Returns:
            Dict: 测试结果
        """
        try:
            # 使用一个已知的频道ID进行测试（YouTube官方频道）
            test_channel_id = 'UCBR8-60-B28hp2BmDPdntcQ'  # YouTube Spotlight
            
            params = {
                'part': 'snippet',
                'id': test_channel_id
            }
            
            data = self._make_request('channels', params)
            
            return {
                'success': True,
                'message': 'API连接正常',
                'quota_used': self.quota_used,
                'quota_remaining': self.quota_limit - self.quota_used,
                'request_count': self.request_count
            }
            
        except Exception as e:
            return {
                'success': False,
                'message': f'API连接失败: {str(e)}',
                'quota_used': self.quota_used,
                'quota_remaining': self.quota_limit - self.quota_used,
                'request_count': self.request_count
            }
    
    def get_quota_info(self) -> Dict[str, Any]:
        """获取配额使用情况
        
        Returns:
            Dict: 配额信息
        """
        return {
            'quota_used': self.quota_used,
            'quota_limit': self.quota_limit,
            'quota_remaining': self.quota_limit - self.quota_used,
            'quota_percentage': (self.quota_used / self.quota_limit) * 100,
            'request_count': self.request_count,
            'last_reset': self.last_reset.isoformat()
        }

# 工具函数
def validate_youtube_url(url: str) -> bool:
    """验证是否为有效的YouTube URL
    
    Args:
        url: 要验证的URL
        
    Returns:
        bool: 是否有效
    """
    youtube_patterns = [
        r'^https?://(www\.)?youtube\.com/channel/[a-zA-Z0-9_-]+',
        r'^https?://(www\.)?youtube\.com/c/[a-zA-Z0-9_-]+',
        r'^https?://(www\.)?youtube\.com/user/[a-zA-Z0-9_-]+',
        r'^https?://(www\.)?youtube\.com/@[a-zA-Z0-9_-]+',
        r'^https?://(www\.)?youtube\.com/watch\?v=[a-zA-Z0-9_-]+',
    ]
    
    return any(re.match(pattern, url) for pattern in youtube_patterns)

def extract_video_id(url: str) -> Optional[str]:
    """从YouTube视频URL提取视频ID
    
    Args:
        url: YouTube视频URL
        
    Returns:
        Optional[str]: 视频ID
    """
    patterns = [
        r'(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11})',
        r'youtube\.com/embed/([a-zA-Z0-9_-]{11})',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    
    return None