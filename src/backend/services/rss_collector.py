"""
RSS数据采集器 - 免费的商机数据源
支持Indie Hackers、Product Hunt、Reddit等RSS源
"""

import feedparser
import requests
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any
from dataclasses import dataclass
import re
import logging
from urllib.parse import urljoin, urlparse

from .data_collector import OpportunitySignal, PlatformType

logger = logging.getLogger(__name__)

@dataclass
class RSSConfig:
    """RSS源配置"""
    name: str
    url: str
    platform_type: PlatformType
    revenue_keywords: List[str]
    user_keywords: List[str]
    signal_patterns: List[str]

class RSSCollector:
    """RSS数据采集器"""
    
    def __init__(self):
        self.sources = self._get_rss_sources()
        
    def _get_rss_sources(self) -> List[RSSConfig]:
        """获取RSS数据源配置"""
        return [
            # Reddit RSS源 - 这些是已验证可工作的
            RSSConfig(
                name="Reddit - Entrepreneur",
                url="https://www.reddit.com/r/Entrepreneur/.rss",
                platform_type=PlatformType.REDDIT,
                revenue_keywords=["business", "startup", "revenue", "profit", "income", "MRR", "ARR"],
                user_keywords=["customers", "clients", "users", "subscribers"],
                signal_patterns=[
                    r'(?:made|earned|generated)\s+\$[\d,]+',
                    r'(?:reached|hit|achieved)\s+\$[\d,]+',
                    r'\$[\d,]+\s*/?(?:month|mo|monthly|MRR)',
                    r'[\d,]+\s+(?:customers|users|sales)'
                ]
            ),
            RSSConfig(
                name="Reddit - SideProject", 
                url="https://www.reddit.com/r/SideProject/.rss",
                platform_type=PlatformType.REDDIT,
                revenue_keywords=["project", "launched", "built", "created", "SaaS", "$", "revenue"],
                user_keywords=["users", "feedback", "beta", "downloads", "signups"],
                signal_patterns=[
                    r'(?:launched|built|created|made)\s+.{1,50}(?:app|tool|website|SaaS)',
                    r'(?:first|got|made)\s+\$[\d,]+',
                    r'\$[\d,]+\s*(?:month|revenue|income)',
                    r'[\d,]+\s+(?:users|signups|downloads)'
                ]
            ),
            RSSConfig(
                name="Reddit - StartUps", 
                url="https://www.reddit.com/r/startups/.rss",
                platform_type=PlatformType.REDDIT,
                revenue_keywords=["startup", "funding", "revenue", "growth", "business", "launched"],
                user_keywords=["users", "customers", "traction"],
                signal_patterns=[
                    r'(?:raised|funding|investment)\s+\$[\d,]+',
                    r'(?:revenue|MRR|ARR)\s+\$[\d,]+',
                    r'[\d,]+\s+(?:users|customers|downloads)'
                ]
            ),
            RSSConfig(
                name="Reddit - IndieBiz", 
                url="https://www.reddit.com/r/indiebiz/.rss",
                platform_type=PlatformType.REDDIT,
                revenue_keywords=["indie", "business", "revenue", "profit", "income"],
                user_keywords=["customers", "sales", "users"],
                signal_patterns=[
                    r'(?:made|earned)\s+\$[\d,]+',
                    r'\$[\d,]+\s*/?(?:month|revenue)',
                    r'[\d,]+\s+(?:customers|sales)'
                ]
            )
            # 注释掉失效的源，专注于可工作的Reddit RSS
            # RSSConfig(
            #     name="Indie Hackers - Feed",
            #     url="https://www.indiehackers.com/feed",  # 更新URL
            #     platform_type=PlatformType.INDIE_HACKERS,
            #     revenue_keywords=["$", "revenue", "MRR", "ARR", "income", "profit"],
            #     user_keywords=["users", "customers", "downloads", "subscribers"],
            #     signal_patterns=[
            #         r'\$[\d,]+\s*/?(?:month|mo|monthly|MRR)',
            #         r'\$[\d,]+\s*/?(?:year|yr|yearly|ARR)',
            #         r'[\d,]+\s+(?:users|customers|downloads)'
            #     ]
            # )
        ]
    
    def collect_all_sources(self, max_items_per_source: int = 20) -> List[OpportunitySignal]:
        """从所有RSS源采集数据"""
        all_signals = []
        
        for source in self.sources:
            try:
                logger.info(f"开始采集 {source.name}")
                signals = self._collect_from_source(source, max_items_per_source)
                all_signals.extend(signals)
                logger.info(f"从 {source.name} 采集到 {len(signals)} 个信号")
                
            except Exception as e:
                logger.error(f"采集 {source.name} 失败: {e}")
                continue
                
        # 按时间排序
        all_signals.sort(key=lambda x: x.created_at, reverse=True)
        
        logger.info(f"总共采集到 {len(all_signals)} 个商机信号")
        return all_signals
    
    def _collect_from_source(self, source: RSSConfig, max_items: int) -> List[OpportunitySignal]:
        """从单个RSS源采集数据"""
        signals = []
        
        try:
            # 获取RSS feed - 增强的请求配置
            headers = {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
                'Accept': 'application/rss+xml, application/xml, text/xml, */*',
                'Accept-Language': 'en-US,en;q=0.9',
                'Cache-Control': 'no-cache'
            }
            
            # 重试机制
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    response = requests.get(
                        source.url, 
                        timeout=15, 
                        headers=headers,
                        allow_redirects=True
                    )
                    response.raise_for_status()
                    break
                except requests.exceptions.Timeout:
                    if attempt == max_retries - 1:
                        raise
                    logger.warning(f"{source.name} 超时，重试 {attempt + 1}/{max_retries}")
                except requests.exceptions.RequestException as e:
                    if attempt == max_retries - 1:
                        raise
                    logger.warning(f"{source.name} 请求失败，重试 {attempt + 1}/{max_retries}: {e}")
            
            # 解析RSS
            feed = feedparser.parse(response.content)
            
            if feed.bozo:
                logger.warning(f"RSS解析警告 {source.name}: {feed.bozo_exception}")
                # 即使有解析警告，也尝试处理可用的条目
            
            if not feed.entries:
                logger.warning(f"{source.name} 没有返回任何条目")
                return signals
            
            logger.info(f"{source.name} 获取到 {len(feed.entries)} 个条目")
            
            # 处理每个条目
            processed = 0
            for entry in feed.entries[:max_items]:
                try:
                    signal = self._parse_entry_to_signal(entry, source)
                    if signal and self._validate_signal(signal, source):
                        signals.append(signal)
                        processed += 1
                except Exception as e:
                    logger.warning(f"处理条目失败 {source.name}: {e}")
                    continue
            
            logger.info(f"{source.name} 成功处理 {processed} 个有效信号")
                    
        except requests.RequestException as e:
            logger.error(f"请求RSS失败 {source.name}: {e}")
        except Exception as e:
            logger.error(f"解析RSS失败 {source.name}: {e}")
            
        return signals
    
    def _parse_entry_to_signal(self, entry: Any, source: RSSConfig) -> Optional[OpportunitySignal]:
        """将RSS条目转换为商机信号"""
        try:
            # 获取基础信息
            title = getattr(entry, 'title', '').strip()
            description = getattr(entry, 'summary', '') or getattr(entry, 'description', '')
            link = getattr(entry, 'link', '')
            
            # 获取发布时间
            published_time = getattr(entry, 'published_parsed', None)
            if published_time:
                published_at = datetime(*published_time[:6])
            else:
                published_at = datetime.now()
            
            # 获取作者信息
            author = getattr(entry, 'author', '') or 'Unknown'
            
            # 提取收入和用户信号
            content = f"{title} {description}".lower()
            revenue_signal = self._extract_revenue_signal(content, source)
            user_signal = self._extract_user_signal(content, source)
            
            # 计算置信度分数
            confidence_score = self._calculate_confidence(content, source)
            
            # 确定项目类型
            project_type = self._determine_project_type(content)
            
            signal = OpportunitySignal(
                id=f"{source.platform_type.value}_{hash(link)}",
                platform=source.platform_type,
                title=title,
                description=self._clean_html(description)[:500],
                author=author,
                author_followers=None,
                engagement_metrics={},
                keywords=self._extract_keywords(content, source),
                revenue_mention=revenue_signal,
                user_count_mention=user_signal,
                urls=[link] if link else [],
                created_at=published_at,
                collected_at=datetime.now(),
                confidence_score=confidence_score,
                category=project_type,
                raw_data={
                    'source': source.name,
                    'entry_id': getattr(entry, 'id', ''),
                    'tags': getattr(entry, 'tags', [])
                }
            )
            
            return signal
            
        except Exception as e:
            logger.error(f"解析RSS条目失败: {e}")
            return None
    
    def _extract_revenue_signal(self, content: str, source: RSSConfig) -> Optional[str]:
        """提取收入信号"""
        for pattern in source.signal_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            if matches:
                return matches[0]
        
        # 通用收入模式
        revenue_patterns = [
            r'\$[\d,]+\s*/?(?:month|mo|monthly|MRR|per month)',
            r'\$[\d,]+\s*/?(?:year|yr|yearly|ARR|per year)', 
            r'(?:revenue|income|profit|earning).*?\$[\d,]+',
            r'(?:made|earned|generated).*?\$[\d,]+'
        ]
        
        for pattern in revenue_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            if matches:
                return matches[0]
                
        return None
    
    def _extract_user_signal(self, content: str, source: RSSConfig) -> Optional[str]:
        """提取用户数量信号"""
        user_patterns = [
            r'[\d,]+\s+(?:users|customers|downloads|subscribers|members)',
            r'(?:reached|got|have).*?[\d,]+.*?(?:users|customers)',
            r'[\d,]+\s+(?:active|daily|monthly).*?users'
        ]
        
        for pattern in user_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            if matches:
                return matches[0]
                
        return None
    
    def _extract_keywords(self, content: str, source: RSSConfig) -> List[str]:
        """提取关键词"""
        keywords = []
        
        # 从配置中的关键词
        for keyword in source.revenue_keywords + source.user_keywords:
            if keyword.lower() in content:
                keywords.append(keyword)
        
        # 技术关键词
        tech_keywords = ['saas', 'ai', 'app', 'tool', 'platform', 'startup', 'chrome extension', 'mobile', 'web app']
        for keyword in tech_keywords:
            if keyword in content:
                keywords.append(keyword)
                
        return list(set(keywords))
    
    def _determine_project_type(self, content: str) -> str:
        """确定项目类型"""
        if any(word in content for word in ['ai', 'artificial intelligence', 'machine learning']):
            return 'AI工具'
        elif any(word in content for word in ['saas', 'software as a service', 'subscription']):
            return 'SaaS应用'
        elif any(word in content for word in ['mobile app', 'ios', 'android', 'app store']):
            return '移动应用'
        elif any(word in content for word in ['chrome extension', 'browser extension', 'firefox addon']):
            return '浏览器扩展'
        else:
            return '其他'
    
    def _calculate_confidence(self, content: str, source: RSSConfig) -> float:
        """计算置信度分数"""
        score = 0.5  # 基础分数
        
        # 有收入信号 +0.3
        if any(keyword in content for keyword in source.revenue_keywords):
            score += 0.3
            
        # 有用户数据 +0.2
        if any(keyword in content for keyword in source.user_keywords):
            score += 0.2
            
        # 有具体数字 +0.1
        if re.search(r'\$[\d,]+|\d+\s+(?:users|customers)', content):
            score += 0.1
            
        # 项目相关词汇 +0.1
        if any(word in content for word in ['launched', 'built', 'created', 'startup']):
            score += 0.1
            
        return min(score, 1.0)
    
    def _validate_signal(self, signal: OpportunitySignal, source: RSSConfig) -> bool:
        """验证信号质量"""
        # 基本验证
        if not signal.title or len(signal.title) < 10:
            return False
            
        # 时间验证（只保留7天内的）
        if (datetime.now() - signal.created_at).days > 7:
            return False
            
        # 置信度验证
        if signal.confidence_score < 0.6:
            return False
            
        return True
    
    def _clean_html(self, text: str) -> str:
        """清理HTML标签"""
        import re
        clean_text = re.sub(r'<[^>]+>', '', text)
        clean_text = re.sub(r'\s+', ' ', clean_text)
        return clean_text.strip()

# 测试函数
if __name__ == "__main__":
    collector = RSSCollector()
    signals = collector.collect_all_sources(max_items_per_source=5)
    
    print(f"采集到 {len(signals)} 个商机信号:")
    for signal in signals[:3]:
        print(f"- {signal.title}")
        print(f"  平台: {signal.platform.value}")
        print(f"  收入: {signal.revenue_mention}")
        print(f"  用户: {signal.user_count_mention}")
        print(f"  置信度: {signal.confidence_score:.2f}")
        print()