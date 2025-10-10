#!/usr/bin/env python3
"""
测试Reddit数据采集器
"""
import sys

# 添加src/backend到Python路径（自动回溯到项目根目录）
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve()
while PROJECT_ROOT.parent != PROJECT_ROOT and not (PROJECT_ROOT / 'src' / 'backend').exists():
    PROJECT_ROOT = PROJECT_ROOT.parent

sys.path.insert(0, str((PROJECT_ROOT / 'src' / 'backend').resolve()))

def test_reddit_collector():
    """测试Reddit采集器"""
    print("🔍 测试Reddit数据采集器...")
    
    try:
        from services.reddit_collector import RedditCollector
        
        # 测试配置（不使用API密钥，使用JSON接口）
        test_config = {
            'reddit_enabled': False,  # 使用JSON备用方案
            'reddit_client_id': 'test',
            'reddit_client_secret': 'test',
            'reddit_user_agent': 'OpportunityBot/1.0 Test'
        }
        
        print("✅ Reddit采集器导入成功")
        
        # 创建采集器实例
        collector = RedditCollector(test_config)
        print("✅ Reddit采集器初始化成功")
        
        # 测试采集
        print("\n🚀 开始采集Reddit数据...")
        signals = collector.collect_signals(['revenue', 'startup', 'launched'], limit=10)
        
        print(f"\n📊 采集结果: {len(signals)} 个商机信号")
        
        if signals:
            print("\n🎯 前3个信号预览:")
            for i, signal in enumerate(signals[:3], 1):
                print(f"\n{i}. {signal.title}")
                print(f"   来源: r/{signal.raw_data.get('subreddit', 'unknown')}")
                print(f"   作者: {signal.author}")
                print(f"   收入信号: {signal.revenue_mention or '无'}")
                print(f"   用户信号: {signal.user_count_mention or '无'}")
                print(f"   置信度: {signal.confidence_score:.2f}")
                print(f"   分数: {signal.engagement_metrics.get('score', 0)}")
                print(f"   分类: {signal.category}")
                print(f"   链接: {signal.urls[0] if signal.urls else 'N/A'}")
            
            print(f"\n📈 数据质量统计:")
            high_confidence = sum(1 for s in signals if s.confidence_score > 0.7)
            with_revenue = sum(1 for s in signals if s.revenue_mention)
            with_users = sum(1 for s in signals if s.user_count_mention)
            
            print(f"   高置信度信号 (>0.7): {high_confidence}/{len(signals)}")
            print(f"   包含收入信息: {with_revenue}/{len(signals)}")
            print(f"   包含用户信息: {with_users}/{len(signals)}")
            
            # 分平台统计
            subreddit_stats = {}
            for signal in signals:
                subreddit = signal.raw_data.get('subreddit', 'unknown')
                subreddit_stats[subreddit] = subreddit_stats.get(subreddit, 0) + 1
            
            print(f"\n📋 Subreddit分布:")
            for subreddit, count in subreddit_stats.items():
                print(f"   r/{subreddit}: {count} 个信号")
        else:
            print("⚠️ 未采集到任何信号，可能的原因:")
            print("   - 网络连接问题")
            print("   - Reddit服务暂时不可用") 
            print("   - 筛选条件过于严格")
            
    except ImportError as e:
        print(f"❌ 导入失败: {e}")
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()

def test_api_integration():
    """测试与商机发现API的集成"""
    print("\n🔗 测试API集成...")
    
    try:
        from api.indie_navigator import IndieNavigatorService
        
        # 创建服务实例
        service = IndieNavigatorService()
        
        # 使用真实数据采集
        print("📡 使用真实Reddit数据...")
        opportunities = service.collect_fresh_opportunities()
        
        print(f"📊 API集成结果: {len(opportunities)} 个商机")
        
        if opportunities:
            print("\n🎯 前2个API格式商机:")
            for i, opp in enumerate(opportunities[:2], 1):
                print(f"\n{i}. {opp.get('title', 'N/A')}")
                print(f"   创作者: {opp.get('creator_name', 'N/A')}")
                print(f"   平台: {opp.get('platform', 'N/A')}")
                print(f"   收入信号: {opp.get('revenue_signal', 'N/A')}")
                print(f"   用户信号: {opp.get('user_signal', 'N/A')}")
                print(f"   项目类型: {opp.get('project_type', 'N/A')}")
                print(f"   置信度: {opp.get('confidence_score', 0)}")
        
        print("✅ API集成测试成功")
        
    except Exception as e:
        print(f"❌ API集成测试失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    print("🚀 开始Reddit采集器测试...\n")
    test_reddit_collector()
    test_api_integration()
    print("\n✅ 测试完成！")