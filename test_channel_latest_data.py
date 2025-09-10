#!/usr/bin/env python3
"""
测试频道服务是否正确返回latest_data字段
"""
import sys
import os

# 添加src/backend到Python路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src', 'backend'))

def test_channel_service():
    """测试频道服务是否包含latest_data字段"""
    from app import create_app
    from services.channel_service import ChannelService
    
    print("🧪 测试频道服务返回的数据结构...")
    
    # 创建Flask应用上下文
    app = create_app()
    
    with app.app_context():
        try:
            # 模拟获取用户频道
            test_user_id = 1
            
            print(f"正在获取用户 {test_user_id} 的频道数据...")
            
            # 获取所有频道（包括非活跃的）
            channels = ChannelService.get_user_channels(test_user_id, active_only=False)
            
            print(f"✅ 成功获取 {len(channels)} 个频道")
            
            if channels:
                # 检查第一个频道的数据结构
                first_channel = channels[0]
                print(f"\n📊 第一个频道数据结构:")
                print(f"  - 频道名称: {first_channel.get('name', 'Unknown')}")
                print(f"  - 频道ID: {first_channel.get('id')}")
                print(f"  - 是否活跃: {first_channel.get('is_active')}")
                
                # 检查latest_data字段
                latest_data = first_channel.get('latest_data')
                if latest_data:
                    print(f"  ✅ latest_data字段存在")
                    print(f"  - 订阅数: {latest_data.get('subscribers', 0):,}")
                    print(f"  - 视频数: {latest_data.get('video_count', 0):,}")
                    print(f"  - 日期: {latest_data.get('date')}")
                else:
                    print(f"  ❌ latest_data字段缺失或为空")
                    
                # 统计所有频道的latest_data情况
                channels_with_data = 0
                total_subscribers = 0
                total_videos = 0
                
                for channel in channels:
                    if channel.get('latest_data'):
                        channels_with_data += 1
                        latest = channel['latest_data']
                        total_subscribers += latest.get('subscribers', 0)
                        total_videos += latest.get('video_count', 0)
                        
                print(f"\n📈 统计结果:")
                print(f"  - 总频道数: {len(channels)}")
                print(f"  - 有latest_data的频道: {channels_with_data}")
                print(f"  - 总订阅数: {total_subscribers:,}")
                print(f"  - 总视频数: {total_videos:,}")
                
                if channels_with_data == len(channels):
                    print("  ✅ 所有频道都包含latest_data字段")
                elif channels_with_data > 0:
                    print(f"  ⚠️ {len(channels) - channels_with_data} 个频道缺少latest_data")
                else:
                    print("  ❌ 所有频道都缺少latest_data字段")
                    
            else:
                print("❌ 没有找到任何频道数据")
                
        except Exception as e:
            print(f"❌ 测试失败: {str(e)}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    print("🚀 开始测试频道服务latest_data字段...\n")
    test_channel_service()
    print("\n✅ 测试完成！")