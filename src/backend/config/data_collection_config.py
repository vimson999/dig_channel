"""
数据采集配置文件
"""

import os
from typing import Dict, List

class DataCollectionConfig:
    """数据采集配置类"""
    
    def __init__(self):
        self.load_config()
    
    def load_config(self):
        """加载配置"""
        
        # Twitter/X API 配置
        self.TWITTER_ENABLED = os.getenv('TWITTER_ENABLED', 'true').lower() == 'true'
        self.TWITTER_API_KEY = os.getenv('TWITTER_API_KEY', '')
        self.TWITTER_API_SECRET = os.getenv('TWITTER_API_SECRET', '')
        self.TWITTER_BEARER_TOKEN = os.getenv('TWITTER_BEARER_TOKEN', '')
        self.TWITTER_ACCESS_TOKEN = os.getenv('TWITTER_ACCESS_TOKEN', '')
        self.TWITTER_ACCESS_TOKEN_SECRET = os.getenv('TWITTER_ACCESS_TOKEN_SECRET', '')
        
        # Reddit API 配置
        self.REDDIT_ENABLED = os.getenv('REDDIT_ENABLED', 'false').lower() == 'true'
        self.REDDIT_CLIENT_ID = os.getenv('REDDIT_CLIENT_ID', '')
        self.REDDIT_CLIENT_SECRET = os.getenv('REDDIT_CLIENT_SECRET', '')
        self.REDDIT_USER_AGENT = os.getenv('REDDIT_USER_AGENT', 'IndieNavigator/1.0')
        
        # 通用采集配置
        self.COLLECTION_INTERVAL = int(os.getenv('COLLECTION_INTERVAL', '3600'))  # 1小时
        self.MAX_SIGNALS_PER_RUN = int(os.getenv('MAX_SIGNALS_PER_RUN', '200'))
        self.MIN_CONFIDENCE_THRESHOLD = float(os.getenv('MIN_CONFIDENCE_THRESHOLD', '0.3'))
        
        # 默认关键词
        self.DEFAULT_KEYWORDS = [
            'SaaS', 'MRR', 'ARR', 'indie hacker', 'startup',
            'AI tool', 'chrome extension', 'mobile app',
            'side project', 'product launch', 'bootstrapped',
            'revenue', 'users', 'customers', 'launch'
        ]
        
        # 关键词分类
        self.KEYWORD_CATEGORIES = {
            'revenue': ['MRR', 'ARR', 'revenue', 'income', 'profit', 'sales'],
            'metrics': ['users', 'customers', 'downloads', 'signups', 'growth'],
            'project_types': ['SaaS', 'AI tool', 'chrome extension', 'mobile app', 'webapp'],
            'community': ['indie hacker', 'startup', 'entrepreneur', 'maker', 'bootstrap'],
            'actions': ['launch', 'shipped', 'released', 'built', 'created']
        }
        
        # 平台特定配置
        self.PLATFORM_CONFIGS = {
            'twitter': {
                'enabled': self.TWITTER_ENABLED,
                'rate_limits': {
                    'search_tweets': {'requests': 300, 'window': 900},  # 15分钟
                    'user_tweets': {'requests': 300, 'window': 900}
                },
                'search_params': {
                    'result_type': 'recent',
                    'lang': 'en',
                    'tweet_fields': 'public_metrics,created_at,author_id',
                    'user_fields': 'public_metrics',
                    'expansions': 'author_id'
                }
            },
            'reddit': {
                'enabled': self.REDDIT_ENABLED,
                'subreddits': [
                    'entrepreneur', 'SideProject', 'startups',
                    'webdev', 'programming', 'indiehackers'
                ],
                'rate_limits': {
                    'api_calls': {'requests': 60, 'window': 60}  # 1分钟
                }
            }
        }
        
        # 信号质量过滤规则
        self.QUALITY_FILTERS = {
            'min_engagement': 5,  # 最小互动数
            'min_followers': 100,  # 作者最小关注者数
            'required_keywords': ['revenue', 'users', 'launch', 'mrr', 'customers'],
            'exclude_keywords': ['spam', 'fake', 'scam', 'bot'],
            'min_text_length': 50,  # 最小文本长度
            'max_text_length': 5000  # 最大文本长度
        }
    
    def get_platform_config(self, platform: str) -> Dict:
        """获取平台配置"""
        return self.PLATFORM_CONFIGS.get(platform, {})
    
    def get_keywords_by_category(self, category: str) -> List[str]:
        """根据分类获取关键词"""
        return self.KEYWORD_CATEGORIES.get(category, [])
    
    def is_platform_enabled(self, platform: str) -> bool:
        """检查平台是否启用"""
        config = self.get_platform_config(platform)
        return config.get('enabled', False)
    
    def to_dict(self) -> Dict:
        """转换为字典格式"""
        return {
            'twitter_enabled': self.TWITTER_ENABLED,
            'reddit_enabled': self.REDDIT_ENABLED,
            'collection_interval': self.COLLECTION_INTERVAL,
            'max_signals_per_run': self.MAX_SIGNALS_PER_RUN,
            'min_confidence_threshold': self.MIN_CONFIDENCE_THRESHOLD,
            'default_keywords': self.DEFAULT_KEYWORDS,
            'platform_configs': self.PLATFORM_CONFIGS,
            'quality_filters': self.QUALITY_FILTERS
        }

# 全局配置实例
config = DataCollectionConfig()

# 导出常用配置
COLLECTION_CONFIG = config.to_dict()

# Twitter API 配置检查
def check_twitter_config() -> bool:
    """检查Twitter API配置是否完整"""
    required_keys = [
        config.TWITTER_API_KEY,
        config.TWITTER_API_SECRET,
        config.TWITTER_BEARER_TOKEN
    ]
    return all(key for key in required_keys)

# Reddit API 配置检查
def check_reddit_config() -> bool:
    """检查Reddit API配置是否完整"""
    required_keys = [
        config.REDDIT_CLIENT_ID,
        config.REDDIT_CLIENT_SECRET
    ]
    return all(key for key in required_keys)