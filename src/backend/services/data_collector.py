"""
统一数据采集服务
支持多平台数据源的扩展架构
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from datetime import datetime
import requests
import json
import logging
from enum import Enum

logger = logging.getLogger(__name__)

class PlatformType(Enum):
    """支持的平台类型"""
    TWITTER = "twitter"
    YOUTUBE = "youtube"
    REDDIT = "reddit"
    HACKER_NEWS = "hacker_news"
    INDIE_HACKERS = "indie_hackers"
    PRODUCT_HUNT = "product_hunt"

@dataclass
class OpportunitySignal:
    """商机信号数据结构"""

    id: str
    platform: PlatformType
    title: str
    description: str
    author: str
    author_followers: Optional[int]
    engagement_metrics: Dict[str, int]
    keywords: List[str]
    revenue_mention: Optional[str]
    user_count_mention: Optional[str]
    urls: List[str]
    created_at: datetime
    collected_at: datetime
    confidence_score: float
    category: str
    raw_data: Dict[str, Any]
    language: Optional[str] = None
    country: Optional[str] = None
    tags: Optional[List[str]] = None
    source_type: str = 'post'
    metadata: Optional[Dict[str, Any]] = None

class BasePlatformCollector(ABC):
    """平台数据采集器基类"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.platform = None
        
    @abstractmethod
    def collect_signals(self, keywords: List[str], limit: int = 50) -> List[OpportunitySignal]:
        """采集商机信号"""
        pass
    
    @abstractmethod
    def validate_signal(self, signal: OpportunitySignal) -> bool:
        """验证信号质量"""
        pass
    
    def extract_metrics(self, raw_data: Dict[str, Any]) -> Dict[str, int]:
        """提取互动指标"""
        return {}
    
    def calculate_confidence_score(self, signal: OpportunitySignal) -> float:
        """计算可信度分数"""
        score = 0.5  # 基础分数
        
        # 作者影响力权重
        if signal.author_followers:
            if signal.author_followers > 10000:
                score += 0.2
            elif signal.author_followers > 1000:
                score += 0.1
        
        # 互动指标权重
        engagement_sum = sum(signal.engagement_metrics.values())
        if engagement_sum > 100:
            score += 0.2
        elif engagement_sum > 10:
            score += 0.1
        
        # 收入提及权重
        if signal.revenue_mention:
            score += 0.15
            
        # 用户数量提及权重
        if signal.user_count_mention:
            score += 0.1
        
        return min(score, 1.0)

class TwitterCollector(BasePlatformCollector):
    """Twitter/X 平台采集器"""
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.platform = PlatformType.TWITTER
        self.api_key = config.get('twitter_api_key')
        self.bearer_token = config.get('twitter_bearer_token')
        
    def collect_signals(self, keywords: List[str], limit: int = 50) -> List[OpportunitySignal]:
        """采集 Twitter 商机信号"""
        signals = []
        
        try:
            for keyword in keywords:
                tweets = self._search_tweets(keyword, limit//len(keywords))
                for tweet in tweets:
                    signal = self._parse_tweet_to_signal(tweet)
                    if signal and self.validate_signal(signal):
                        signals.append(signal)
                        
        except Exception as e:
            logger.error(f"Twitter 采集失败: {e}")
            
        return signals
    
    def _search_tweets(self, keyword: str, limit: int) -> List[Dict]:
        """搜索推文"""
        # 模拟 Twitter API 调用
        # 实际实现需要使用 Twitter API v2
        mock_tweets = [
            {
                'id': f'tweet_{keyword}_{i}',
                'text': f'Just launched my {keyword} app and got first $1000 MRR! 🚀',
                'author': f'indie_dev_{i}',
                'author_followers': 1500 + i * 100,
                'likes': 25 + i * 5,
                'retweets': 8 + i * 2,
                'replies': 3 + i,
                'created_at': datetime.now(),
                'urls': [f'https://myapp{i}.com']
            }
            for i in range(min(limit, 5))  # 限制mock数据数量
        ]
        
        return mock_tweets
    
    def _parse_tweet_to_signal(self, tweet: Dict) -> OpportunitySignal:
        """将推文解析为商机信号"""
        keywords = self._extract_keywords(tweet['text'])
        return OpportunitySignal(
            id=tweet['id'],
            platform=self.platform,
            title=tweet['text'][:100] + '...' if len(tweet['text']) > 100 else tweet['text'],
            description=tweet['text'],
            author=tweet['author'],
            author_followers=tweet['author_followers'],
            engagement_metrics={
                'likes': tweet['likes'],
                'retweets': tweet['retweets'],
                'replies': tweet['replies']
            },
            keywords=keywords,
            revenue_mention=self._extract_revenue(tweet['text']),
            user_count_mention=self._extract_user_count(tweet['text']),
            urls=tweet['urls'],
            created_at=tweet['created_at'],
            collected_at=datetime.now(),
            confidence_score=0,  # 稍后计算
            category=self._classify_category(tweet['text']),
            raw_data=tweet,
            language=tweet.get('lang', 'en'),
            tags=keywords,
            source_type='tweet',
            metadata={'permalink': tweet.get('permalink')}
        )
    
    def _extract_keywords(self, text: str) -> List[str]:
        """提取关键词"""
        keywords = []
        tech_keywords = ['SaaS', 'AI', 'app', 'tool', 'startup', 'MRR', 'ARR', 'users']
        for keyword in tech_keywords:
            if keyword.lower() in text.lower():
                keywords.append(keyword)
        return keywords
    
    def _extract_revenue(self, text: str) -> Optional[str]:
        """提取收入信息"""
        import re
        revenue_patterns = [
            r'\$(\d+(?:,\d{3})*)\s*(?:MRR|ARR|revenue|sales)',
            r'(\d+(?:k|K))\s*(?:MRR|ARR|revenue)',
            r'made\s*\$(\d+(?:,\d{3})*)'
        ]
        
        for pattern in revenue_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1)
        return None
    
    def _extract_user_count(self, text: str) -> Optional[str]:
        """提取用户数量信息"""
        import re
        user_patterns = [
            r'(\d+(?:,\d{3})*)\s*(?:users|customers|subscribers)',
            r'(\d+(?:k|K|m|M))\s*(?:users|customers|subscribers)'
        ]
        
        for pattern in user_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1)
        return None
    
    def _classify_category(self, text: str) -> str:
        """分类项目类型"""
        text_lower = text.lower()
        
        if any(word in text_lower for word in ['ai', 'ml', 'gpt', 'chatbot']):
            return 'AI工具'
        elif any(word in text_lower for word in ['saas', 'software', 'platform']):
            return 'SaaS应用'
        elif any(word in text_lower for word in ['app', 'mobile', 'ios', 'android']):
            return '移动应用'
        elif any(word in text_lower for word in ['extension', 'chrome', 'browser']):
            return '浏览器扩展'
        else:
            return '其他'
    
    def validate_signal(self, signal: OpportunitySignal) -> bool:
        """验证信号质量"""
        # 基本验证规则
        if not signal.title or len(signal.title) < 10:
            return False
            
        # 必须包含商业相关关键词
        business_keywords = ['mrr', 'revenue', 'users', 'customers', 'launch', 'startup', 'saas']
        if not any(keyword in signal.description.lower() for keyword in business_keywords):
            return False
            
        # 互动量过滤
        if sum(signal.engagement_metrics.values()) < 5:
            return False
            
        return True

class DataCollectorService:
    """统一数据采集服务"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.collectors = {}
        self._initialize_collectors()
        
    def _initialize_collectors(self):
        """初始化各平台采集器"""
        if self.config.get('twitter_enabled', False):
            if all(
                self.config.get(key)
                for key in ('twitter_api_key', 'twitter_api_secret', 'twitter_bearer_token')
            ):
                self.collectors[PlatformType.TWITTER] = TwitterCollector(self.config)
            else:
                logger.info("Twitter API 未配置完整，跳过初始化 TwitterCollector")
            
        # Reddit采集器 - 免费API
        if self.config.get('reddit_enabled', True):  # 默认启用Reddit
            from .reddit_collector import RedditCollector
            self.collectors[PlatformType.REDDIT] = RedditCollector(self.config)
            
        # RSS采集器 - 免费数据源
        if self.config.get('rss_enabled', True):  # 默认启用RSS
            from .rss_collector import RSSCollector
            self.rss_collector = RSSCollector()
    
    def collect_all_platforms(
        self,
        keywords: List[str],
        limit_per_platform: int = 50,
        platforms: Optional[List[str]] = None,
    ) -> List[OpportunitySignal]:
        """从所有平台采集数据"""
        all_signals = []
        platform_whitelist = None
        if platforms:
            platform_whitelist = {platform.lower() for platform in platforms}
        
        # 首先从RSS源采集数据（免费且可靠）
        if hasattr(self, 'rss_collector') and (
            not platform_whitelist or 'rss' in platform_whitelist
        ):
            try:
                logger.info("开始从RSS源采集数据")
                rss_signals = self.rss_collector.collect_all_sources(limit_per_platform)
                all_signals.extend(rss_signals)
                logger.info(f"RSS源采集到 {len(rss_signals)} 个信号")
            except Exception as e:
                logger.error(f"RSS采集失败: {e}")
        
        # 然后从其他平台采集
        for platform, collector in self.collectors.items():
            if platform_whitelist and platform.value not in platform_whitelist:
                logger.debug("跳过未在白名单的平台: %s", platform.value)
                continue
            try:
                logger.info(f"开始采集 {platform.value} 平台数据")
                signals = collector.collect_signals(keywords, limit_per_platform)
                
                # 计算可信度分数
                for signal in signals:
                    if hasattr(collector, 'calculate_confidence_score'):
                        signal.confidence_score = collector.calculate_confidence_score(signal)
                
                all_signals.extend(signals)
                logger.info(f"{platform.value} 平台采集到 {len(signals)} 个信号")
                
            except Exception as e:
                logger.error(f"{platform.value} 平台采集失败: {e}")
        
        # 按可信度排序
        all_signals.sort(key=lambda x: x.confidence_score, reverse=True)
        
        return all_signals
    
    def get_platform_stats(self) -> Dict[str, Any]:
        """获取平台统计信息"""
        return {
            'enabled_platforms': [platform.value for platform in self.collectors.keys()],
            'total_platforms': len(self.collectors),
            'last_update': datetime.now().isoformat()
        }

# 配置示例
DEFAULT_CONFIG = {
    'twitter_enabled': False,  # 禁用昂贵的Twitter API
    'twitter_api_key': 'your_api_key',
    'twitter_bearer_token': 'your_bearer_token',
    'reddit_enabled': True,  # 启用免费Reddit API
    'reddit_client_id': 'your_reddit_client_id',
    'reddit_client_secret': 'your_reddit_client_secret',
    'reddit_user_agent': 'OpportunityBot/1.0',
    'rss_enabled': True,  # 启用免费RSS数据源
    'collection_interval': 3600,  # 1小时
    'max_signals_per_run': 200,
    'min_confidence_threshold': 0.3
}
