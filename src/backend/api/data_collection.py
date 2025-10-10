"""
数据采集API端点
"""

from flask import Blueprint, request, jsonify, current_app
from datetime import datetime
from sqlalchemy.orm import sessionmaker
import logging

from services.data_collector import DataCollectorService
from models.indie_navigator import CollectionJob, CollectionKeyword
from api.indie_navigator import IndieNavigatorService
from config.data_collection_config import DataCollectionConfig

data_collection_bp = Blueprint('data_collection', __name__, url_prefix='/api/data-collection')

class DataCollectionAPI:
    """数据采集API服务"""
    
    def __init__(self, db_session=None):
        self.logger = logging.getLogger(__name__)
        self.db_session = db_session
        config = DataCollectionConfig()
        collector_config = {
            'twitter_enabled': config.TWITTER_ENABLED,
            'twitter_api_key': config.TWITTER_API_KEY,
            'twitter_api_secret': config.TWITTER_API_SECRET,
            'twitter_bearer_token': config.TWITTER_BEARER_TOKEN,
            'reddit_enabled': config.REDDIT_ENABLED,
            'reddit_client_id': config.REDDIT_CLIENT_ID,
            'reddit_client_secret': config.REDDIT_CLIENT_SECRET,
            'reddit_user_agent': config.REDDIT_USER_AGENT,
            'rss_enabled': True,
        }
        self.data_collector = DataCollectorService(collector_config)

@data_collection_bp.route('/start', methods=['POST'])
def start_collection():
    """启动数据采集任务"""
    try:
        data = request.get_json() or {}
        
        # 获取参数
        keywords = data.get('keywords', ["SaaS", "MRR", "indie hacker", "startup", "AI tool"])
        platforms = data.get('platforms', ['reddit'])
        limit_per_platform = data.get('limit_per_platform', 50)
        
        service = IndieNavigatorService()
        opportunities = service.collect_fresh_opportunities(
            keywords=keywords,
            limit_per_platform=limit_per_platform,
            platforms=platforms,
        )

        platform_stats = {}
        for opportunity in opportunities:
            platform = opportunity.get('platform', 'unknown')
            platform_stats[platform] = platform_stats.get(platform, 0) + 1
        
        return jsonify({
            'success': True,
            'data': {
                'job_id': f'job_{int(datetime.now().timestamp())}',
                'status': 'completed',
                'total_signals': len(opportunities),
                'platform_breakdown': platform_stats,
                'keywords_used': keywords,
                'started_at': datetime.now().isoformat(),
                'completed_at': datetime.now().isoformat()
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"启动采集任务失败: {str(e)}")
        return jsonify({
            'success': False,
            'error': '启动采集任务失败'
        }), 500

@data_collection_bp.route('/keywords', methods=['GET', 'POST', 'PUT', 'DELETE'])
def manage_keywords():
    """管理采集关键词"""
    
    if request.method == 'GET':
        # 获取关键词列表
        try:
            # 这里应该从数据库获取，现在返回默认关键词
            default_keywords = [
                {'id': 1, 'keyword': 'SaaS', 'category': '软件服务', 'priority': 5, 'is_active': True},
                {'id': 2, 'keyword': 'MRR', 'category': '收入指标', 'priority': 5, 'is_active': True},
                {'id': 3, 'keyword': 'indie hacker', 'category': '独立开发', 'priority': 4, 'is_active': True},
                {'id': 4, 'keyword': 'startup', 'category': '创业', 'priority': 4, 'is_active': True},
                {'id': 5, 'keyword': 'AI tool', 'category': 'AI工具', 'priority': 5, 'is_active': True},
                {'id': 6, 'keyword': 'chrome extension', 'category': '浏览器扩展', 'priority': 3, 'is_active': True},
                {'id': 7, 'keyword': 'mobile app', 'category': '移动应用', 'priority': 4, 'is_active': True}
            ]
            
            return jsonify({
                'success': True,
                'data': {
                    'keywords': default_keywords,
                    'total': len(default_keywords)
                }
            })
            
        except Exception as e:
            return jsonify({
                'success': False,
                'error': '获取关键词失败'
            }), 500
    
    elif request.method == 'POST':
        # 添加新关键词
        data = request.get_json()
        keyword = data.get('keyword')
        category = data.get('category', '其他')
        priority = data.get('priority', 1)
        
        # 这里应该保存到数据库
        return jsonify({
            'success': True,
            'message': f'关键词 "{keyword}" 已添加'
        })
    
    elif request.method == 'PUT':
        # 更新关键词
        data = request.get_json()
        keyword_id = data.get('id')
        
        # 这里应该更新数据库
        return jsonify({
            'success': True,
            'message': '关键词已更新'
        })
    
    elif request.method == 'DELETE':
        # 删除关键词
        keyword_id = request.args.get('id')
        
        # 这里应该从数据库删除
        return jsonify({
            'success': True,
            'message': '关键词已删除'
        })

@data_collection_bp.route('/platforms', methods=['GET'])
def get_platform_status():
    """获取平台状态"""
    try:
        config = DataCollectionConfig()
        collector = DataCollectorService({
            'twitter_enabled': config.TWITTER_ENABLED,
            'twitter_api_key': config.TWITTER_API_KEY,
            'twitter_api_secret': config.TWITTER_API_SECRET,
            'twitter_bearer_token': config.TWITTER_BEARER_TOKEN,
            'reddit_enabled': config.REDDIT_ENABLED,
            'reddit_client_id': config.REDDIT_CLIENT_ID,
            'reddit_client_secret': config.REDDIT_CLIENT_SECRET,
            'reddit_user_agent': config.REDDIT_USER_AGENT,
            'rss_enabled': True,
        })
        stats = collector.get_platform_stats()
        
        # 添加平台配置信息
        platform_configs = {
            'twitter': {
                'name': 'Twitter/X',
                'enabled': config.TWITTER_ENABLED,
                'description': '从Twitter获取独立开发者分享的项目信息',
                'rate_limit': '300 requests/15min',
                'last_collection': None
            },
            'reddit': {
                'name': 'Reddit',
                'enabled': config.REDDIT_ENABLED,
                'description': '从相关subreddit获取项目分享',
                'rate_limit': '60 requests/min',
                'last_collection': None
            },
            'hacker_news': {
                'name': 'Hacker News',
                'enabled': False,
                'description': '从HN获取Show HN项目',
                'rate_limit': 'No limit',
                'last_collection': None
            },
            'indie_hackers': {
                'name': 'Indie Hackers',
                'enabled': False,
                'description': '从IH社区获取项目milestone',
                'rate_limit': 'TBD',
                'last_collection': None
            }
        }
        
        return jsonify({
            'success': True,
            'data': {
                'platforms': platform_configs,
                'stats': stats
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"获取平台状态失败: {str(e)}")
        return jsonify({
            'success': False,
            'error': '获取平台状态失败'
        }), 500

@data_collection_bp.route('/jobs', methods=['GET'])
def get_collection_jobs():
    """获取采集任务历史"""
    try:
        # 这里应该从数据库获取，现在返回模拟数据
        mock_jobs = [
            {
                'id': 1,
                'job_type': 'full',
                'status': 'completed',
                'signals_collected': 45,
                'signals_processed': 45,
                'signals_saved': 42,
                'started_at': '2024-08-15T10:30:00Z',
                'completed_at': '2024-08-15T10:35:00Z',
                'config': {
                    'keywords': ['SaaS', 'MRR', 'AI tool'],
                    'platforms': ['twitter'],
                    'limit_per_platform': 50
                }
            },
            {
                'id': 2,
                'job_type': 'keyword',
                'status': 'completed',
                'signals_collected': 23,
                'signals_processed': 23,
                'signals_saved': 20,
                'started_at': '2024-08-15T09:15:00Z',
                'completed_at': '2024-08-15T09:18:00Z',
                'config': {
                    'keywords': ['indie hacker'],
                    'platforms': ['twitter'],
                    'limit_per_platform': 30
                }
            }
        ]
        
        return jsonify({
            'success': True,
            'data': {
                'jobs': mock_jobs,
                'total': len(mock_jobs)
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"获取采集任务失败: {str(e)}")
        return jsonify({
            'success': False,
            'error': '获取采集任务失败'
        }), 500

@data_collection_bp.route('/health', methods=['GET'])
def health_check():
    """数据采集服务健康检查"""
    try:
        config = DataCollectionConfig()
        collector = DataCollectorService({
            'twitter_enabled': config.TWITTER_ENABLED,
            'twitter_api_key': config.TWITTER_API_KEY,
            'twitter_api_secret': config.TWITTER_API_SECRET,
            'twitter_bearer_token': config.TWITTER_BEARER_TOKEN,
            'reddit_enabled': config.REDDIT_ENABLED,
            'reddit_client_id': config.REDDIT_CLIENT_ID,
            'reddit_client_secret': config.REDDIT_CLIENT_SECRET,
            'reddit_user_agent': config.REDDIT_USER_AGENT,
            'rss_enabled': True,
        })
        platform_stats = collector.get_platform_stats()
        
        health_status = {
            'service': 'healthy',
            'platforms': {},
            'timestamp': datetime.now().isoformat()
        }
        
        # 检查各平台状态
        for platform in platform_stats.get('enabled_platforms', []):
            platform_name = platform.value if hasattr(platform, 'value') else str(platform)
            health_status['platforms'][platform_name] = 'available'
        
        return jsonify({
            'success': True,
            'data': health_status
        })
        
    except Exception as e:
        current_app.logger.error(f"健康检查失败: {str(e)}")
        return jsonify({
            'success': False,
            'error': '健康检查失败'
        }), 500

# 错误处理
@data_collection_bp.errorhandler(404)
def not_found(error):
    return jsonify({
        'success': False,
        'error': '请求的资源不存在'
    }), 404

@data_collection_bp.errorhandler(500)
def internal_error(error):
    return jsonify({
        'success': False,
        'error': '服务器内部错误'
    }), 500
