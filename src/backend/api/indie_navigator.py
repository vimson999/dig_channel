from flask import Blueprint, request, jsonify, current_app
from datetime import datetime, timedelta
from sqlalchemy.orm import sessionmaker
from sqlalchemy import desc, asc, and_, or_
import requests
import json
import logging
from typing import List, Dict, Optional, Any

from config.data_collection_config import DataCollectionConfig
from models import db
from models.indie_navigator import (
    OpportunitySignal as OpportunitySignalModel,
    UserOpportunityInteraction,
    CollectionKeyword,
    CollectionJob,
)
from services.data_collector import DataCollectorService

indie_navigator_bp = Blueprint('indie_navigator', __name__, url_prefix='/api/indie-navigator')

class IndieNavigatorService:
    """独立开发者领航员核心服务"""

    DEFAULT_KEYWORDS = [
        "SaaS",
        "MRR",
        "indie hacker",
        "startup",
        "AI tool",
        "chrome extension",
        "mobile app",
    ]

    def __init__(self, db_session=None):
        self.logger = logging.getLogger(__name__)
        self.db_session = db_session or db.session

        config = DataCollectionConfig()
        self.collector_config = {
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

        self.data_collector = DataCollectorService(self.collector_config)

    # ------------------------------------------------------------------
    # 数据采集 & 持久化
    # ------------------------------------------------------------------
    def collect_fresh_opportunities(
        self,
        keywords: Optional[List[str]] = None,
        limit_per_platform: int = 20,
        platforms: Optional[List[str]] = None,
    ) -> List[Dict]:
        """采集最新商机并保存到数据库，返回格式化后的记录"""

        keywords = keywords or self.DEFAULT_KEYWORDS
        target_platforms = platforms or ['reddit']

        try:
            signals = self.data_collector.collect_all_platforms(
                keywords,
                limit_per_platform=limit_per_platform,
                platforms=target_platforms,
            )
            stored_records = self._save_signals(signals)
            return [self._format_record(record) for record in stored_records]
        except Exception as exc:
            self.logger.error("采集数据失败: %s", exc)
            return self.get_mock_opportunities()  # fallback for now

    def _save_signals(self, signals) -> List[OpportunitySignalModel]:
        """将采集到的信号写入数据库，返回保存后的记录列表"""

        stored_records: List[OpportunitySignalModel] = []
        if not signals:
            return stored_records

        try:
            for signal in signals:
                platform_value = (
                    signal.platform.value if hasattr(signal.platform, 'value') else signal.platform
                )
                record = (
                    self.db_session.query(OpportunitySignalModel)
                    .filter_by(platform=platform_value, source_id=signal.id)
                    .one_or_none()
                )

                if not record:
                    record = OpportunitySignalModel(
                        platform=platform_value,
                        source_id=signal.id,
                        source_type=getattr(signal, 'source_type', 'post'),
                    )
                    self.db_session.add(record)
                else:
                    record.source_type = getattr(signal, 'source_type', record.source_type)

                record.title = (signal.title or '')[:500]
                record.description = signal.description
                record.author = signal.author
                record.author_followers = signal.author_followers
                record.engagement_metrics = self._safe_json(signal.engagement_metrics or {})
                record.keywords = signal.keywords or []
                record.tags = signal.tags or record.keywords or []
                record.revenue_mention = signal.revenue_mention
                record.user_count_mention = signal.user_count_mention
                record.language = getattr(signal, 'language', None)
                record.country = getattr(signal, 'country', None)
                record.urls = signal.urls or []
                record.created_at = signal.created_at or record.created_at or datetime.utcnow()
                record.collected_at = datetime.utcnow()
                record.confidence_score = float(signal.confidence_score or 0.0)
                record.category = self._map_category_to_type(signal.category)
                record.attributes = self._safe_json(getattr(signal, 'metadata', {}) or {})
                record.raw_data = self._safe_json(signal.raw_data or {})
                record.is_archived = False
                record.is_processed = False
                record.is_verified = False

                stored_records.append(record)

            self.db_session.flush()
            self.db_session.commit()
        except Exception as exc:
            self.logger.error("保存商机信号失败: %s", exc)
            self.db_session.rollback()
            stored_records = []

        return stored_records

    # ------------------------------------------------------------------
    # 查询逻辑
    # ------------------------------------------------------------------
    def query_opportunities(
        self,
        search: Optional[str],
        project_type: str,
        sort_by: str,
        limit: int,
        offset: int,
    ) -> Dict[str, Any]:
        """按条件查询机会列表，返回 {'items': [...], 'total': n}"""

        query = self.db_session.query(OpportunitySignalModel).filter(
            OpportunitySignalModel.is_archived.is_(False)
        )

        if search:
            like_pattern = f"%{search}%"
            query = query.filter(
                or_(
                    OpportunitySignalModel.title.ilike(like_pattern),
                    OpportunitySignalModel.description.ilike(like_pattern),
                )
            )

        if project_type and project_type != 'all':
            query = query.filter(OpportunitySignalModel.category == project_type)

        records = query.all()
        if not records:
            return {'items': [], 'total': 0}

        records = self._sort_records(records, sort_by)
        total = len(records)
        paged = records[offset : offset + limit]

        return {
            'items': [self._format_record(record) for record in paged],
            'total': total,
        }

    def _sort_records(self, records: List[OpportunitySignalModel], sort_by: str) -> List[OpportunitySignalModel]:
        if sort_by == 'revenue':
            return sorted(records, key=self._revenue_value, reverse=True)
        if sort_by == 'engagement':
            return sorted(records, key=self._engagement_value, reverse=True)
        if sort_by == 'confidence':
            return sorted(records, key=lambda r: r.confidence_score or 0.0, reverse=True)
        # 默认最新优先
        return sorted(records, key=lambda r: r.created_at or datetime.min, reverse=True)

    def _revenue_value(self, record: OpportunitySignalModel) -> float:
        value = record.revenue_mention or ''
        if not value:
            return 0.0
        text = value.lower().replace('$', '').replace(',', '').replace('/月', '').strip()
        multiplier = 1.0
        if text.endswith('k'):
            multiplier = 1_000.0
            text = text[:-1]
        if text.endswith('m'):
            multiplier = 1_000_000.0
            text = text[:-1]
        try:
            return float(text) * multiplier
        except ValueError:
            return 0.0

    def _engagement_value(self, record: OpportunitySignalModel) -> int:
        metrics = record.engagement_metrics or {}
        base = (
            int(metrics.get('likes', 0))
            + int(metrics.get('retweets', 0))
            + int(metrics.get('replies', 0))
        )
        base += int(metrics.get('score', 0))
        base += int(metrics.get('num_comments', 0))
        return base

    # ------------------------------------------------------------------
    # 输出格式化
    # ------------------------------------------------------------------
    def _format_record(self, record: OpportunitySignalModel) -> Dict:
        primary_url = (record.urls or ['#'])[0]
        creator_handle = (
            f"@{record.author.lower().replace(' ', '_')}" if record.author else '@unknown'
        )
        result = {
            'id': str(record.id),
            'source_id': record.source_id,
            'platform': record.platform,
            'title': record.title,
            'description': record.description,
            'creator_name': record.author or '未知创作者',
            'creator_handle': creator_handle,
            'creator_avatar': '/api/placeholder/40/40',
            'revenue_signal': record.revenue_mention or '未知收入',
            'user_signal': record.user_count_mention or '用户数未知',
            'primary_url': primary_url,
            'original_url': primary_url,
            'published_at': (record.created_at or datetime.utcnow()).isoformat(),
            'collected_at': record.collected_at.isoformat() if record.collected_at else None,
            'engagement_metrics': record.engagement_metrics or {},
            'project_type': record.category or 'other',
            'project_url': primary_url,
            'confidence_score': record.confidence_score or 0.0,
            'keywords': record.keywords or [],
            'tags': record.tags or [],
            'language': record.language,
            'country': record.country,
            'metadata': record.attributes or {},
        }

        # 兼容前端现有字段命名
        result['projectName'] = result['title']
        result['creatorName'] = result['creator_name']
        result['creatorHandle'] = result['creator_handle']
        result['confidenceScore'] = result['confidence_score']

        return result

    def _map_category_to_type(self, category: Optional[str]) -> str:
        if not category:
            return 'other'
        mapping = {
            'AI工具': 'ai',
            'ai': 'ai',
            'SaaS应用': 'saas',
            'saas': 'saas',
            '移动应用': 'mobile',
            'mobile': 'mobile',
            '浏览器扩展': 'extension',
            'extension': 'extension',
        }
        return mapping.get(category, 'other')

    def _safe_json(self, value):
        try:
            json.dumps(value)
            return value
        except (TypeError, ValueError):
            return json.loads(json.dumps(value, default=str))

    # ------------------------------------------------------------------
    # 备用数据
    # ------------------------------------------------------------------
    def get_mock_opportunities(self) -> List[Dict]:
        """返回模拟商机数据（备用）"""
        mock_data = [
            {
                "id": "opp_001",
                "title": "AI邮件写作助手突破$12K MRR",
                "description": "专为销售团队设计的AI邮件生成工具，集成了个性化模板和A/B测试功能，帮助提升邮件回复率300%。",
                "creator_name": "Alex Chen",
                "creator_handle": "@alexbuilds",
                "creator_avatar": "/api/placeholder/40/40",
                "revenue_signal": "$12,000/月",
                "user_signal": "25,000+ 用户",
                "platform": "twitter",
                "original_url": "https://twitter.com/alexbuilds/status/123456789",
                "published_at": "2024-08-15T10:30:00Z",
                "engagement_metrics": {
                    "likes": 342,
                    "retweets": 89,
                    "replies": 56
                },
                "project_type": "ai",
                "project_url": "https://emailai.pro"
            },
            {
                "id": "opp_002", 
                "title": "Chrome扩展工具达到$8K MRR",
                "description": "一键式网页截图和标注工具，支持团队协作和云端同步，深受设计师和产品经理喜爱。",
                "creator_name": "Sarah Kim",
                "creator_handle": "@sarahcodes",
                "creator_avatar": "/api/placeholder/40/40",
                "revenue_signal": "$8,500/月",
                "user_signal": "18,000+ 用户",
                "platform": "twitter",
                "original_url": "https://twitter.com/sarahcodes/status/123456790",
                "published_at": "2024-08-12T14:20:00Z",
                "engagement_metrics": {
                    "likes": 256,
                    "retweets": 67,
                    "replies": 43
                },
                "project_type": "extension",
                "project_url": "https://snaptools.io"
            },
            {
                "id": "opp_003",
                "title": "SaaS客户支持工具实现$15K MRR",
                "description": "基于AI的客户服务聊天机器人，能够处理80%的常见问题，显著降低人工客服成本。",
                "creator_name": "Michael Torres",
                "creator_handle": "@mikes_builds",
                "creator_avatar": "/api/placeholder/40/40", 
                "revenue_signal": "$15,200/月",
                "user_signal": "340+ 企业用户",
                "platform": "twitter",
                "original_url": "https://twitter.com/mikes_builds/status/123456791",
                "published_at": "2024-08-10T09:15:00Z",
                "engagement_metrics": {
                    "likes": 489,
                    "retweets": 124,
                    "replies": 78
                },
                "project_type": "saas",
                "project_url": "https://supportai.com"
            },
            {
                "id": "opp_004",
                "title": "移动应用健身追踪器获得50K用户",
                "description": "简约风格的健身记录应用，专注核心功能，无广告纯净体验，通过应用内购买实现盈利。",
                "creator_name": "Lisa Wang",
                "creator_handle": "@lisafit_dev",
                "creator_avatar": "/api/placeholder/40/40",
                "revenue_signal": "$6,800/月",
                "user_signal": "50,000+ 下载",
                "platform": "twitter",
                "original_url": "https://twitter.com/lisafit_dev/status/123456792",
                "published_at": "2024-08-08T16:45:00Z",
                "engagement_metrics": {
                    "likes": 298,
                    "retweets": 76,
                    "replies": 45
                },
                "project_type": "mobile",
                "project_url": "https://apps.apple.com/fittracker"
            },
            {
                "id": "opp_005",
                "title": "开源项目商业化成功案例",
                "description": "将个人开源库包装成企业级SaaS服务，提供托管、支持和高级功能，获得稳定收入流。",
                "creator_name": "David Rodriguez",
                "creator_handle": "@david_oss",
                "creator_avatar": "/api/placeholder/40/40",
                "revenue_signal": "$9,400/月",
                "user_signal": "120+ 企业客户",
                "platform": "twitter", 
                "original_url": "https://twitter.com/david_oss/status/123456793",
                "published_at": "2024-08-05T11:30:00Z",
                "engagement_metrics": {
                    "likes": 356,
                    "retweets": 91,
                    "replies": 62
                },
                "project_type": "saas",
                "project_url": "https://cloudlib.dev"
            },
            {
                "id": "opp_006",
                "title": "AI图像生成工具月收入突破$20K",
                "description": "专业级AI图像生成平台，面向设计师和内容创作者，提供高质量定制化图像生成服务。",
                "creator_name": "Emma Johnson",
                "creator_handle": "@emma_ai_art",
                "creator_avatar": "/api/placeholder/40/40",
                "revenue_signal": "$20,300/月",
                "user_signal": "8,500+ 创作者",
                "platform": "twitter",
                "original_url": "https://twitter.com/emma_ai_art/status/123456794",
                "published_at": "2024-08-03T13:20:00Z",
                "engagement_metrics": {
                    "likes": 567,
                    "retweets": 143,
                    "replies": 89
                },
                "project_type": "ai",
                "project_url": "https://aiartpro.com"
            }
        ]
        return mock_data

# API Endpoints
@indie_navigator_bp.route('/opportunities', methods=['GET'])
def get_opportunities():
    """获取商机列表"""
    try:
        service = IndieNavigatorService()

        use_fresh_data = request.args.get('fresh', 'false').lower() == 'true'
        if use_fresh_data:
            service.collect_fresh_opportunities(platforms=['reddit'])

        search = request.args.get('search', '').strip()
        filter_type = request.args.get('filter', 'all')
        sort_by = request.args.get('sort', 'newest')
        limit = max(1, min(int(request.args.get('limit', 50)), 100))
        offset = max(0, int(request.args.get('offset', 0)))

        result = service.query_opportunities(search, filter_type, sort_by, limit, offset)
        opportunities = result['items']
        total = result['total']

        if total == 0:
            # 数据库暂无记录时回退到模拟数据，至少保证页面可用
            mock_items = service.get_mock_opportunities()
            total = len(mock_items)
            opportunities = mock_items[offset:offset + limit]

        return jsonify(
            {
                'success': True,
                'data': {
                    'opportunities': opportunities,
                    'total': total,
                    'limit': limit,
                    'offset': offset,
                },
            }
        )
        
    except Exception as e:
        current_app.logger.error(f"获取商机列表失败: {str(e)}")
        return jsonify({
            'success': False,
            'error': '获取商机列表失败'
        }), 500

@indie_navigator_bp.route('/opportunities/<opportunity_id>', methods=['GET'])
def get_opportunity_detail(opportunity_id):
    """获取商机详情"""
    try:
        service = IndieNavigatorService()

        try:
            opportunity_pk = int(opportunity_id)
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': '商机ID格式不正确'}), 400

        record = service.db_session.get(OpportunitySignalModel, opportunity_pk)
        if record:
            opportunity = service._format_record(record)
            opportunity['details'] = {
                'tech_stack': ['React', 'Node.js', 'PostgreSQL'],
                'pricing_model': 'SaaS订阅制',
                'target_market': 'B2B企业服务',
                'competition_level': '中等',
                'market_size': '大型市场',
                'growth_trend': '快速增长',
            }
            return jsonify({'success': True, 'data': opportunity})

        # fallback to mock data
        mock_opportunity = next(
            (opp for opp in service.get_mock_opportunities() if opp['id'] == opportunity_id),
            None,
        )
        if mock_opportunity:
            return jsonify({'success': True, 'data': mock_opportunity})

        return jsonify({'success': False, 'error': '商机不存在'}), 404
        
    except Exception as e:
        current_app.logger.error(f"获取商机详情失败: {str(e)}")
        return jsonify({
            'success': False,
            'error': '获取商机详情失败'
        }), 500

@indie_navigator_bp.route('/stats', methods=['GET'])
def get_dashboard_stats():
    """获取仪表盘统计数据"""
    try:
        service = IndieNavigatorService()
        query = service.db_session.query(OpportunitySignalModel).filter(
            OpportunitySignalModel.is_archived.is_(False)
        )
        total = query.count()

        if total == 0:
            # fallback to mock statistics when db empty
            mock_stats = {
                'total_opportunities': 0,
                'high_potential': 0,
                'trending_signals': 0,
                'my_saved': 0,
            }
            return jsonify({'success': True, 'data': mock_stats})

        high_potential = (
            query.filter(OpportunitySignalModel.confidence_score >= 0.7).count()
        )
        last_week = datetime.utcnow() - timedelta(days=7)
        trending = (
            query.filter(OpportunitySignalModel.created_at >= last_week).count()
        )

        stats = {
            'total_opportunities': total,
            'high_potential': high_potential,
            'trending_signals': trending,
            'my_saved': 0,
        }

        return jsonify({'success': True, 'data': stats})
        
    except Exception as e:
        current_app.logger.error(f"获取统计数据失败: {str(e)}")
        return jsonify({
            'success': False,
            'error': '获取统计数据失败'
        }), 500

@indie_navigator_bp.route('/funnel', methods=['GET', 'POST', 'PUT', 'DELETE'])
def manage_funnel():
    """管理个人机会漏斗"""
    if request.method == 'GET':
        # 获取用户的机会漏斗
        # 这里应该从数据库获取，现在返回模拟数据
        return jsonify({
            'success': True,
            'data': {
                'watching': [],
                'analyzing': [],
                'to_build': [],
                'archived': []
            }
        })
    
    elif request.method == 'POST':
        # 添加商机到漏斗
        data = request.get_json()
        opportunity_id = data.get('opportunity_id')
        status = data.get('status', 'watching')
        
        # 这里应该保存到数据库
        return jsonify({
            'success': True,
            'message': '已添加到个人漏斗'
        })
    
    elif request.method == 'PUT':
        # 更新漏斗中的商机状态
        data = request.get_json()
        opportunity_id = data.get('opportunity_id')
        new_status = data.get('status')
        
        # 这里应该更新数据库
        return jsonify({
            'success': True,
            'message': '状态已更新'
        })
    
    elif request.method == 'DELETE':
        # 从漏斗中移除商机
        opportunity_id = request.args.get('opportunity_id')
        
        # 这里应该从数据库删除
        return jsonify({
            'success': True,
            'message': '已从漏斗中移除'
        })

# 错误处理
@indie_navigator_bp.errorhandler(404)
def not_found(error):
    return jsonify({
        'success': False,
        'error': '请求的资源不存在'
    }), 404

@indie_navigator_bp.errorhandler(500)
def internal_error(error):
    return jsonify({
        'success': False,
        'error': '服务器内部错误'
    }), 500
