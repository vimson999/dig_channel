"""
Reddit API数据采集器 - 免费获取独立开发者商机数据
"""

import praw
import requests
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any
import re
import logging
from urllib.parse import urljoin

from .data_collector import OpportunitySignal, PlatformType

logger = logging.getLogger(__name__)

class RedditCollector:
    """Reddit数据采集器"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.reddit = None
        self._init_reddit_client()
        
        # 目标subreddit配置
        self.target_subreddits = [
            {
                'name': 'entrepreneur',
                'keywords': ['revenue', 'income', 'profit', 'MRR', 'ARR', '$', 'business', 'startup'],
                'signal_weight': 0.9
            },
            {
                'name': 'SideProject', 
                'keywords': ['launched', 'built', 'project', 'app', 'tool', 'SaaS', 'revenue', '$'],
                'signal_weight': 0.95
            },
            {
                'name': 'startups',
                'keywords': ['startup', 'funding', 'revenue', 'growth', 'business', 'launched'],
                'signal_weight': 0.8
            },
            {
                'name': 'EntrepreneurRideAlong',
                'keywords': ['month', 'revenue', 'update', 'progress', '$', 'income'],
                'signal_weight': 0.85
            },
            {
                'name': 'indiehackers',
                'keywords': ['indie', 'hacker', 'revenue', 'MRR', 'project', 'launched'],
                'signal_weight': 0.9
            }
        ]
    
    def _init_reddit_client(self):
        """初始化Reddit客户端"""
        try:
            if self.config.get('reddit_enabled', False):
                self.reddit = praw.Reddit(
                    client_id=self.config.get('reddit_client_id'),
                    client_secret=self.config.get('reddit_client_secret'),
                    user_agent=self.config.get('reddit_user_agent', 'OpportunityBot/1.0')
                )
                logger.info("Reddit API客户端初始化成功")
            else:
                logger.info("Reddit API未启用，将使用备用方案")
        except Exception as e:
            logger.error(f"Reddit API初始化失败: {e}")
            self.reddit = None
    
    def collect_signals(self, keywords: List[str], limit: int = 50) -> List[OpportunitySignal]:
        """采集Reddit商机信号"""
        all_signals = []
        
        for subreddit_config in self.target_subreddits:
            try:
                subreddit_name = subreddit_config['name']
                logger.info(f"开始采集 r/{subreddit_name}")
                
                signals = self._collect_from_subreddit(subreddit_config, limit // len(self.target_subreddits))
                all_signals.extend(signals)
                
                logger.info(f"r/{subreddit_name} 采集到 {len(signals)} 个信号")
                
            except Exception as e:
                logger.error(f"采集 r/{subreddit_config['name']} 失败: {e}")
                continue
        
        # 按置信度和时间排序
        all_signals.sort(key=lambda x: (x.confidence_score, x.created_at), reverse=True)
        
        return all_signals[:limit]
    
    def _collect_from_subreddit(self, subreddit_config: Dict, limit: int) -> List[OpportunitySignal]:
        """从单个subreddit采集数据"""
        signals = []
        subreddit_name = subreddit_config['name']
        
        try:
            if self.reddit:
                # 使用官方API
                signals = self._collect_with_api(subreddit_config, limit)
            else:
                # 使用备用方案（JSON接口）
                signals = self._collect_with_json(subreddit_config, limit)
                
        except Exception as e:
            logger.error(f"采集 r/{subreddit_name} 失败: {e}")
            # 尝试备用方案
            try:
                signals = self._collect_with_json(subreddit_config, limit)
            except Exception as e2:
                logger.error(f"备用方案也失败: {e2}")
        
        return signals
    
    def _collect_with_api(self, subreddit_config: Dict, limit: int) -> List[OpportunitySignal]:
        """使用Reddit API采集"""
        signals = []
        subreddit_name = subreddit_config['name']
        
        try:
            subreddit = self.reddit.subreddit(subreddit_name)
            
            # 获取热门和新帖子
            submissions = list(subreddit.hot(limit=limit//2)) + list(subreddit.new(limit=limit//2))
            
            for submission in submissions:
                signal = self._parse_submission_to_signal(submission, subreddit_config)
                if signal and self._validate_signal(signal, subreddit_config):
                    signals.append(signal)
                    
        except Exception as e:
            logger.error(f"API采集失败: {e}")
            raise
            
        return signals
    
    def _collect_with_json(self, subreddit_config: Dict, limit: int) -> List[OpportunitySignal]:
        """使用Reddit JSON接口采集（备用方案）"""
        signals = []
        subreddit_name = subreddit_config['name']
        
        try:
            # Reddit JSON API（无需认证）
            headers = {
                'User-Agent': 'web:shioa-opportunity-bot:v1.0 (by /u/shioatech)'
            }
            
            # 获取热门帖子
            hot_url = f"https://www.reddit.com/r/{subreddit_name}/hot.json?limit={limit//2}"
            hot_response = requests.get(hot_url, headers=headers, timeout=10)
            hot_response.raise_for_status()
            
            # 获取新帖子
            new_url = f"https://www.reddit.com/r/{subreddit_name}/new.json?limit={limit//2}"
            new_response = requests.get(new_url, headers=headers, timeout=10)
            new_response.raise_for_status()
            
            # 解析数据
            all_posts = []
            if hot_response.status_code == 200:
                hot_data = hot_response.json()
                all_posts.extend(hot_data['data']['children'])
            
            if new_response.status_code == 200:
                new_data = new_response.json()
                all_posts.extend(new_data['data']['children'])
            
            # 转换为信号
            for post_data in all_posts:
                post = post_data['data']
                signal = self._parse_post_to_signal(post, subreddit_config)
                if signal and self._validate_signal(signal, subreddit_config):
                    signals.append(signal)
                    
        except requests.RequestException as e:
            logger.error(f"JSON API请求失败: {e}")
            raise
        except Exception as e:
            logger.error(f"JSON解析失败: {e}")
            raise
            
        return signals
    
    def _parse_submission_to_signal(self, submission, subreddit_config: Dict) -> Optional[OpportunitySignal]:
        """将Reddit submission转换为商机信号（API方式）"""
        try:
            # 获取帖子内容
            title = submission.title
            content = getattr(submission, 'selftext', '') or ''
            full_text = f"{title} {content}".lower()
            
            # 检查是否包含商机关键词
            if not self._contains_opportunity_keywords(full_text, subreddit_config):
                return None
            
            # 提取信号
            revenue_signal = self._extract_revenue_signal(full_text)
            user_signal = self._extract_user_signal(full_text)
            
            # 如果没有明确的商业信号，跳过
            if not revenue_signal and not user_signal:
                return None
            
            signal = OpportunitySignal(
                id=f"reddit_{submission.id}",
                platform=PlatformType.REDDIT,
                title=title,
                description=content[:500] if content else title,
                author=str(submission.author) if submission.author else 'Unknown',
                author_followers=None,
                engagement_metrics={
                    'score': submission.score,
                    'upvote_ratio': getattr(submission, 'upvote_ratio', 0),
                    'num_comments': submission.num_comments
                },
                keywords=self._extract_keywords(full_text, subreddit_config),
                revenue_mention=revenue_signal,
                user_count_mention=user_signal,
                urls=[f"https://reddit.com{submission.permalink}"],
                created_at=datetime.fromtimestamp(submission.created_utc),
                collected_at=datetime.now(),
                confidence_score=self._calculate_confidence(full_text, subreddit_config, submission.score),
                category=self._determine_project_type(full_text),
                raw_data={
                    'subreddit': subreddit_config['name'],
                    'reddit_id': submission.id,
                    'score': submission.score,
                    'num_comments': submission.num_comments
                }
            )
            
            return signal
            
        except Exception as e:
            logger.error(f"解析submission失败: {e}")
            return None
    
    def _parse_post_to_signal(self, post: Dict, subreddit_config: Dict) -> Optional[OpportunitySignal]:
        """将Reddit post转换为商机信号（JSON方式）"""
        try:
            # 获取帖子内容
            title = post.get('title', '')
            content = post.get('selftext', '') or ''
            full_text = f"{title} {content}".lower()
            
            # 检查是否包含商机关键词
            if not self._contains_opportunity_keywords(full_text, subreddit_config):
                return None
            
            # 提取信号
            revenue_signal = self._extract_revenue_signal(full_text)
            user_signal = self._extract_user_signal(full_text)
            
            # 如果没有明确的商业信号，跳过
            if not revenue_signal and not user_signal:
                return None
            
            signal = OpportunitySignal(
                id=f"reddit_{post['id']}",
                platform=PlatformType.REDDIT,
                title=title,
                description=content[:500] if content else title,
                author=post.get('author', 'Unknown'),
                author_followers=None,
                engagement_metrics={
                    'score': post.get('score', 0),
                    'upvote_ratio': post.get('upvote_ratio', 0),
                    'num_comments': post.get('num_comments', 0)
                },
                keywords=self._extract_keywords(full_text, subreddit_config),
                revenue_mention=revenue_signal,
                user_count_mention=user_signal,
                urls=[f"https://reddit.com{post.get('permalink', '')}"],
                created_at=datetime.fromtimestamp(post.get('created_utc', 0)),
                collected_at=datetime.now(),
                confidence_score=self._calculate_confidence(full_text, subreddit_config, post.get('score', 0)),
                category=self._determine_project_type(full_text),
                raw_data={
                    'subreddit': subreddit_config['name'],
                    'reddit_id': post['id'],
                    'score': post.get('score', 0),
                    'num_comments': post.get('num_comments', 0)
                }
            )
            
            return signal
            
        except Exception as e:
            logger.error(f"解析post失败: {e}")
            return None
    
    def _contains_opportunity_keywords(self, text: str, subreddit_config: Dict) -> bool:
        """检查是否包含商机关键词"""
        keywords = subreddit_config['keywords']
        return any(keyword.lower() in text for keyword in keywords)
    
    def _extract_revenue_signal(self, text: str) -> Optional[str]:
        """提取收入信号"""
        revenue_patterns = [
            r'\$[\d,]+\s*/?(?:month|mo|monthly|MRR|per month)',
            r'\$[\d,]+\s*/?(?:year|yr|yearly|ARR|per year)',
            r'(?:revenue|income|profit|earned|made).*?\$[\d,]+',
            r'(?:reached|hit|achieved|generated).*?\$[\d,]+',
            r'\$[\d,]+.*?(?:revenue|income|profit)',
            r'(?:first|my|our)\s+\$[\d,]+',
            r'[\d,]+k?\s*/?(?:month|monthly|MRR)',
            r'(?:making|earning)\s+\$?[\d,]+k?'
        ]
        
        for pattern in revenue_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                return matches[0]
                
        return None
    
    def _extract_user_signal(self, text: str) -> Optional[str]:
        """提取用户数量信号"""
        user_patterns = [
            r'[\d,]+\s+(?:users|customers|downloads|subscribers|members|visitors)',
            r'(?:reached|got|have|gained).*?[\d,]+.*?(?:users|customers|subscribers)',
            r'[\d,]+\s+(?:active|daily|monthly).*?(?:users|customers)',
            r'(?:users|customers|subscribers).*?[\d,]+',
            r'[\d,]+k?\s+(?:downloads|installs|signups)'
        ]
        
        for pattern in user_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                return matches[0]
                
        return None
    
    def _extract_keywords(self, text: str, subreddit_config: Dict) -> List[str]:
        """提取关键词"""
        keywords = []
        
        # 从配置中的关键词
        for keyword in subreddit_config['keywords']:
            if keyword.lower() in text:
                keywords.append(keyword)
        
        # 技术和项目关键词
        tech_keywords = [
            'saas', 'ai', 'app', 'tool', 'platform', 'startup', 'chrome extension', 
            'mobile', 'web app', 'software', 'service', 'business', 'project',
            'bootstrap', 'indie', 'side project', 'mvp'
        ]
        for keyword in tech_keywords:
            if keyword in text:
                keywords.append(keyword)
                
        return list(set(keywords))
    
    def _determine_project_type(self, text: str) -> str:
        """确定项目类型"""
        if any(word in text for word in ['ai', 'artificial intelligence', 'machine learning', 'ml', 'gpt']):
            return 'AI工具'
        elif any(word in text for word in ['saas', 'software as a service', 'subscription', 'b2b']):
            return 'SaaS应用'
        elif any(word in text for word in ['mobile app', 'ios', 'android', 'app store', 'play store']):
            return '移动应用'
        elif any(word in text for word in ['chrome extension', 'browser extension', 'firefox addon']):
            return '浏览器扩展'
        elif any(word in text for word in ['e-commerce', 'ecommerce', 'shopify', 'store', 'selling']):
            return '电商'
        elif any(word in text for word in ['course', 'education', 'teaching', 'tutorial', 'learning']):
            return '教育产品'
        else:
            return '其他'
    
    def _calculate_confidence(self, text: str, subreddit_config: Dict, score: int) -> float:
        """计算置信度分数"""
        confidence = 0.3  # 基础分数
        
        # Subreddit权重
        confidence += subreddit_config['signal_weight'] * 0.3
        
        # 有明确收入数字 +0.4
        if re.search(r'\$[\d,]+', text):
            confidence += 0.4
            
        # 有用户数据 +0.2
        if re.search(r'[\d,]+\s+(?:users|customers|downloads)', text):
            confidence += 0.2
            
        # 项目相关词汇 +0.1
        if any(word in text for word in ['launched', 'built', 'created', 'startup', 'project']):
            confidence += 0.1
            
        # Reddit分数权重（热门帖子更可信）
        if score > 100:
            confidence += 0.1
        elif score > 50:
            confidence += 0.05
            
        # MRR/ARR等专业术语 +0.2
        if any(term in text for term in ['mrr', 'arr', 'revenue', 'profit']):
            confidence += 0.2
            
        return min(confidence, 1.0)
    
    def _validate_signal(self, signal: OpportunitySignal, subreddit_config: Dict) -> bool:
        """验证信号质量"""
        # 基本验证
        if not signal.title or len(signal.title) < 10:
            return False
            
        # 时间验证（只保留30天内的）
        if (datetime.now() - signal.created_at).days > 30:
            return False
            
        # 置信度验证
        if signal.confidence_score < 0.5:
            return False
            
        # 必须有收入或用户信号之一
        if not signal.revenue_mention and not signal.user_count_mention:
            return False
            
        return True

# 测试函数
if __name__ == "__main__":
    # 测试配置（使用JSON备用方案）
    test_config = {
        'reddit_enabled': False,  # 测试备用方案
        'reddit_client_id': 'test',
        'reddit_client_secret': 'test',
        'reddit_user_agent': 'OpportunityBot/1.0'
    }
    
    collector = RedditCollector(test_config)
    signals = collector.collect_signals(['revenue', 'startup'], limit=10)
    
    print(f"采集到 {len(signals)} 个Reddit商机信号:")
    for signal in signals[:3]:
        print(f"- {signal.title}")
        print(f"  来源: r/{signal.raw_data['subreddit']}")
        print(f"  收入: {signal.revenue_mention}")
        print(f"  用户: {signal.user_count_mention}")
        print(f"  置信度: {signal.confidence_score:.2f}")
        print(f"  链接: {signal.urls[0] if signal.urls else 'N/A'}")
        print()