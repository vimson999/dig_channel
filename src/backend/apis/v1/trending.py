
# 爆款视频搜索API
"""
提供爆款视频搜索相关的功能
"""

from flask import Blueprint, request, jsonify
from utils.api_utils import handle_api_error
from utils.auth_utils import require_login
from utils.youtube_api import YouTubeAPI
from datetime import datetime, timedelta

# 创建蓝图
bp = Blueprint('trending', __name__, url_prefix='/api/v1/trending')

# 初始化YouTube API客户端
youtube_api = YouTubeAPI()

@bp.route('/search', methods=['POST'])
@require_login
def search_trending_videos(current_user):
    """搜索爆款视频
    
    Request Body:
        keywords (str): 搜索关键词 (必填)
        time_range (str): 时间范围 (today, week, month, year, all), 默认 'week'
        video_type (str): 视频类型 (short, long, all), 默认 'all'
        subscriber_ranges (list): 博主订阅者范围列表 (mega, macro, mid, micro, newbie, all), 默认 ['all']
            - mega: 大V (>100万订阅者)
            - macro: 中V (10万-100万订阅者)
            - mid: 小V (1万-10万订阅者)
            - micro: 新人 (2K-1万订阅者)
            - newbie: 菜鸟 (<2K订阅者)
            - all: 所有博主
        order (str): 排序方式 (viewCount, date, relevance, rating), 默认 'viewCount'
        max_results (int): 返回结果数量, 默认 25, 最大 50
    
    Returns:
        JSON: 搜索到的视频列表
    """
    try:
        data = request.get_json()
        if not data or 'keywords' not in data:
            return handle_api_error(400, "搜索关键词(keywords)不能为空")

        # --- 参数解析 ---
        keywords = data.get('keywords')
        time_range = data.get('time_range', 'week')
        video_type = data.get('video_type', 'all')
        subscriber_ranges = data.get('subscriber_ranges', ['all'])
        order = data.get('order', 'viewCount')
        max_results = min(int(data.get('max_results', 25)), 50)
        
        # 确保subscriber_ranges是列表
        if isinstance(subscriber_ranges, str):
            subscriber_ranges = [subscriber_ranges]
        elif not isinstance(subscriber_ranges, list):
            subscriber_ranges = ['all']

        # --- 构建API查询参数 ---
        # 如果需要按订阅者数筛选，增加搜索结果数量以确保筛选后有足够结果
        need_filter = 'all' not in subscriber_ranges
        search_count = max_results * 2 if need_filter else max_results
        search_count = min(search_count, 50)  # YouTube API限制最大50个结果
        
        api_params = {
            'part': 'snippet',
            'q': keywords,
            'type': 'video',
            'order': order,
            'maxResults': search_count
        }

        # 处理时间范围
        now = datetime.utcnow()
        if time_range == 'today':
            api_params['publishedAfter'] = (now - timedelta(days=1)).isoformat("T") + "Z"
        elif time_range == 'week':
            api_params['publishedAfter'] = (now - timedelta(weeks=1)).isoformat("T") + "Z"
        elif time_range == 'month':
            api_params['publishedAfter'] = (now - timedelta(days=30)).isoformat("T") + "Z"
        elif time_range == 'year':
            api_params['publishedAfter'] = (now - timedelta(days=365)).isoformat("T") + "Z"
        
        # 处理视频类型
        if video_type == 'short':
            api_params['videoDuration'] = 'short' # 小于4分钟
        elif video_type == 'long':
            api_params['videoDuration'] = 'long' # 大于20分钟

        # --- 调用YouTube API ---
        search_result = youtube_api.search(api_params)

        videos = []
        if 'items' in search_result:
            # 1. 收集视频ID和频道ID
            video_ids = []
            channel_ids = set()
            for item in search_result['items']:
                video_id = item['id']['videoId']
                channel_id = item['snippet']['channelId']
                video_ids.append(video_id)
                channel_ids.add(channel_id)

            # 2. 批量获取视频详细统计数据
            video_details = {}
            if video_ids:
                try:
                    videos_info = youtube_api._get_videos_details(video_ids)
                    for info in videos_info:
                        video_details[info['video_id']] = info
                except Exception as e:
                    print(f"批量获取视频详细信息失败: {e}")

            # 3. 批量获取频道信息和统计数据
            channel_info = {}
            channel_stats = {}
            if channel_ids:
                try:
                    # 获取频道基本信息（包括缩略图）
                    channels_info = youtube_api.get_channels_info(list(channel_ids))
                    for info in channels_info:
                        channel_info[info['channel_id']] = info
                    
                    # 获取频道统计数据（粉丝数等）
                    channels_stats = youtube_api.get_multiple_channels_statistics(list(channel_ids))
                    for stats in channels_stats:
                        channel_stats[stats['channel_id']] = stats
                except Exception as e:
                    print(f"批量获取频道信息失败: {e}")

            # 4. 组合所有信息
            for item in search_result['items']:
                snippet = item['snippet']
                video_id = item['id']['videoId']
                channel_id = snippet['channelId']
                
                # 基础视频信息
                video = {
                    'video_id': video_id,
                    'title': snippet['title'],
                    'description': snippet['description'],
                    'published_at': snippet['publishedAt'],
                    'thumbnail_url': snippet['thumbnails']['high']['url'],
                    'channel_id': channel_id,
                    'channel_title': snippet['channelTitle']
                }
                
                # 添加视频统计数据
                if video_id in video_details:
                    detail = video_details[video_id]
                    video.update({
                        'view_count': detail.get('view_count', 0),
                        'like_count': detail.get('like_count', 0),
                        'comment_count': detail.get('comment_count', 0),
                        'duration': detail.get('duration', ''),
                    })
                
                # 添加频道信息
                if channel_id in channel_info:
                    info = channel_info[channel_id]
                    video['channel_thumbnail_url'] = info.get('thumbnail_url')
                    video['channel_published_at'] = info.get('published_at')  # 频道创建时间
                
                # 添加频道统计数据
                if channel_id in channel_stats:
                    stats = channel_stats[channel_id]
                    video.update({
                        'channel_subscriber_count': stats.get('subscriber_count', 0),
                        'channel_video_count': stats.get('video_count', 0),
                        'channel_view_count': stats.get('view_count', 0)
                    })
                
                videos.append(video)

        # --- 根据订阅者数进行筛选 ---
        if 'all' not in subscriber_ranges:
            filtered_videos = []
            for video in videos:
                subscriber_count = video.get('channel_subscriber_count', 0)
                
                # 检查是否匹配任何一个选中的范围
                match_any_range = False
                for range_type in subscriber_ranges:
                    if range_type == 'mega' and subscriber_count > 1000000:  # 大V (>100万)
                        match_any_range = True
                        break
                    elif range_type == 'macro' and 100000 <= subscriber_count <= 1000000:  # 中V (10万-100万)
                        match_any_range = True
                        break
                    elif range_type == 'mid' and 10000 <= subscriber_count < 100000:  # 小V (1万-10万)
                        match_any_range = True
                        break
                    elif range_type == 'micro' and 2000 <= subscriber_count < 10000:  # 新人 (2K-1万)
                        match_any_range = True
                        break
                    elif range_type == 'newbie' and subscriber_count < 2000:  # 菜鸟 (<2K)
                        match_any_range = True
                        break
                
                if match_any_range:
                    filtered_videos.append(video)
            
            videos = filtered_videos
        
        # 限制最终结果数量
        videos = videos[:max_results]

        # --- 处理并返回结果 ---
        return jsonify({
            'success': True,
            'data': {
                'videos': videos,
                'count': len(videos)
            }
        })

    except Exception as e:
        # 假设的日志记录
        print(f"Error in search_trending_videos: {e}")
        return handle_api_error(500, f"搜索失败: {str(e)}")

