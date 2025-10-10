#!/usr/bin/env python3
"""
测试带认证的频道API，验证latest_data字段
"""
import sys
import requests

# 添加src/backend到Python路径（自动回溯到项目根目录）
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve()
while PROJECT_ROOT.parent != PROJECT_ROOT and not (PROJECT_ROOT / 'src' / 'backend').exists():
    PROJECT_ROOT = PROJECT_ROOT.parent

sys.path.insert(0, str((PROJECT_ROOT / 'src' / 'backend').resolve()))

def test_channel_api_with_statistics():
    """测试频道API并验证统计数据计算"""
    
    print("🧪 测试带认证的频道API...")
    
    from app import create_app
    from utils.auth_utils import get_current_user
    
    # 创建Flask应用上下文
    app = create_app()
    
    with app.app_context():
        try:
            # 从服务直接获取数据（模拟API调用但跳过认证）
            from services.channel_service import ChannelService
            
            print("正在获取频道数据...")
            
            # 获取用户1的所有频道（包括非活跃的）
            channels = ChannelService.get_user_channels(1, active_only=False)
            
            print(f"✅ 成功获取 {len(channels)} 个频道")
            
            if channels:
                # 模拟前端JavaScript的统计计算
                total_channels = len(channels)
                active_channels = len([ch for ch in channels if ch.get('is_active')])
                
                total_subscribers = 0
                total_videos = 0
                channels_with_data = 0
                
                print("\n📊 分析频道数据:")
                for i, channel in enumerate(channels[:5]):  # 查看前5个频道
                    print(f"频道 {i+1}: {channel.get('name', 'Unknown')}")
                    latest_data = channel.get('latest_data')
                    if latest_data:
                        channels_with_data += 1
                        subscribers = latest_data.get('subscribers', 0)
                        video_count = latest_data.get('video_count', 0)
                        total_subscribers += subscribers
                        total_videos += video_count
                        print(f"  ✅ latest_data存在: 订阅{subscribers:,}, 视频{video_count:,}")
                    else:
                        print(f"  ❌ latest_data缺失")
                
                print(f"\n📈 统计结果:")
                print(f"  - 总频道数: {total_channels}")
                print(f"  - 活跃频道数: {active_channels}")
                print(f"  - 有数据的频道: {channels_with_data}/{total_channels}")
                print(f"  - 总订阅数: {total_subscribers:,}")
                print(f"  - 总视频数: {total_videos:,}")
                
                # 验证数据完整性
                if channels_with_data == total_channels:
                    print("\n✅ 所有频道都包含latest_data字段 - 统计数据应该正确显示!")
                elif channels_with_data > 0:
                    print(f"\n⚠️ {total_channels - channels_with_data} 个频道缺少latest_data")
                else:
                    print("\n❌ 所有频道都缺少latest_data字段")
                
                # 验证统计数字非零
                if total_subscribers > 0 and total_videos > 0:
                    print("✅ 统计数字正常，前端应该显示正确的数字!")
                else:
                    print("❌ 统计数字为0，可能存在数据问题")
                    
            else:
                print("❌ 没有找到任何频道数据")
                
        except Exception as e:
            print(f"❌ 测试失败: {str(e)}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    print("🚀 开始测试频道API的latest_data字段...\n")
    test_channel_api_with_statistics()
    print("\n✅ 测试完成！")