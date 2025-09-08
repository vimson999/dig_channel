from flask import Blueprint, request, jsonify, current_app
from datetime import datetime, timedelta
from sqlalchemy.orm import sessionmaker
from sqlalchemy import desc, asc, and_, or_
import requests
import json
import logging
from typing import List, Dict, Optional
from dataclasses import dataclass, asdict

from models.indie_navigator import OpportunitySignal, UserOpportunityInteraction, CollectionKeyword, CollectionJob
from services.data_collector import DataCollectorService, DEFAULT_CONFIG

indie_navigator_bp = Blueprint('indie_navigator', __name__, url_prefix='/api/indie-navigator')

@dataclass
class OpportunitySignal:
    """商机信号数据结构"""
    id: str
    title: str
    description: str
    creator_name: str
    creator_handle: str
    creator_avatar: str
    revenue_signal: str
    user_signal: str
    platform: str
    original_url: str
    published_at: datetime
    engagement_metrics: Dict
    project_type: str
    project_url: Optional[str] = None
    created_at: Optional[datetime] = None

class IndieNavigatorService:
    """独立开发者领航员核心服务"""
    
    def __init__(self, db_session=None):
        self.logger = logging.getLogger(__name__)
        self.db_session = db_session
        self.data_collector = DataCollectorService(DEFAULT_CONFIG)
        
    def collect_fresh_opportunities(self, keywords: List[str] = None) -> List[Dict]:
        """从各平台采集最新商机数据"""
        if not keywords:
            keywords = ["SaaS", "MRR", "indie hacker", "startup", "AI tool", "chrome extension", "mobile app"]
        
        try:
            signals = self.data_collector.collect_all_platforms(keywords, limit_per_platform=20)
            
            # 转换为前端需要的格式
            opportunities = []
            for signal in signals:
                opportunity = {
                    "id": signal.id,
                    "title": signal.title,
                    "description": signal.description,
                    "creator_name": signal.author,
                    "creator_handle": f"@{signal.author.lower().replace(' ', '_')}",
                    "creator_avatar": "/api/placeholder/40/40",
                    "revenue_signal": signal.revenue_mention or "未知收入",
                    "user_signal": signal.user_count_mention or "用户数未知",
                    "platform": signal.platform.value,
                    "original_url": signal.urls[0] if signal.urls else "#",
                    "published_at": signal.created_at.isoformat(),
                    "engagement_metrics": signal.engagement_metrics,
                    "project_type": self._map_category_to_type(signal.category),
                    "project_url": signal.urls[0] if signal.urls else None,
                    "confidence_score": signal.confidence_score
                }
                opportunities.append(opportunity)
            
            return opportunities
            
        except Exception as e:
            self.logger.error(f"采集数据失败: {e}")
            return self.get_mock_opportunities()  # fallback to mock data
    
    def _map_category_to_type(self, category: str) -> str:
        """映射分类到项目类型"""
        mapping = {
            'AI工具': 'ai',
            'SaaS应用': 'saas', 
            '移动应用': 'mobile',
            '浏览器扩展': 'extension',
            '其他': 'other'
        }
        return mapping.get(category, 'other')
    
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
        
        # 检查是否请求最新数据
        use_fresh_data = request.args.get('fresh', 'false').lower() == 'true'
        
        if use_fresh_data:
            opportunities = service.collect_fresh_opportunities()
        else:
            opportunities = service.get_mock_opportunities()
        
        # 获取查询参数
        search = request.args.get('search', '').lower()
        filter_type = request.args.get('filter', 'all')
        sort_by = request.args.get('sort', 'newest')
        limit = int(request.args.get('limit', 50))
        offset = int(request.args.get('offset', 0))
        
        # 应用搜索过滤
        if search:
            opportunities = [
                opp for opp in opportunities 
                if search in opp['title'].lower() or search in opp['description'].lower()
            ]
        
        # 应用类型过滤
        if filter_type != 'all':
            opportunities = [
                opp for opp in opportunities 
                if opp['project_type'] == filter_type
            ]
        
        # 应用排序
        if sort_by == 'newest':
            opportunities.sort(key=lambda x: x['published_at'], reverse=True)
        elif sort_by == 'revenue':
            # 简单的收入排序逻辑
            opportunities.sort(
                key=lambda x: int(x['revenue_signal'].replace('$', '').replace(',', '').replace('/月', '')), 
                reverse=True
            )
        elif sort_by == 'engagement':
            opportunities.sort(
                key=lambda x: x['engagement_metrics']['likes'] + x['engagement_metrics']['retweets'], 
                reverse=True
            )
        
        # 应用分页
        total = len(opportunities)
        opportunities = opportunities[offset:offset + limit]
        
        return jsonify({
            'success': True,
            'data': {
                'opportunities': opportunities,
                'total': total,
                'limit': limit,
                'offset': offset
            }
        })
        
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
        opportunities = service.get_mock_opportunities()
        
        opportunity = next((opp for opp in opportunities if opp['id'] == opportunity_id), None)
        
        if not opportunity:
            return jsonify({
                'success': False,
                'error': '商机不存在'
            }), 404
        
        # 添加详细信息
        opportunity['details'] = {
            'tech_stack': ['React', 'Node.js', 'PostgreSQL'],
            'pricing_model': 'SaaS订阅制',
            'target_market': 'B2B企业服务',
            'competition_level': '中等',
            'market_size': '大型市场',
            'growth_trend': '快速增长'
        }
        
        return jsonify({
            'success': True,
            'data': opportunity
        })
        
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
        opportunities = service.get_mock_opportunities()
        
        stats = {
            'total_opportunities': len(opportunities),
            'high_potential': len([opp for opp in opportunities if '$15' in opp['revenue_signal'] or '$20' in opp['revenue_signal']]),
            'trending_signals': 12,  # 模拟数据
            'my_saved': 3  # 模拟数据
        }
        
        return jsonify({
            'success': True,
            'data': stats
        })
        
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