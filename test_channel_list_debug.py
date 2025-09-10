#!/usr/bin/env python3
"""
调试频道列表显示0个频道的问题
"""
import sys
import os
import traceback

# 添加src/backend到Python路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src', 'backend'))

def test_channel_list_api():
    """测试频道列表API，诊断为什么显示0个频道"""
    
    print("🔍 调试频道列表API问题...")
    
    from app import create_app
    
    # 创建Flask应用上下文
    app = create_app()
    
    with app.app_context():
        try:
            # 1. 直接测试ChannelService
            print("\n1. 测试ChannelService.get_user_channels()...")
            from services.channel_service import ChannelService
            
            channels = ChannelService.get_user_channels(1, active_only=False)
            print(f"   ChannelService返回: {len(channels)} 个频道")
            
            if channels:
                print(f"   第一个频道: {channels[0].get('name', 'Unknown')}")
                print(f"   latest_data存在: {'✅' if channels[0].get('latest_data') else '❌'}")
            
            # 2. 测试分页版本
            print("\n2. 测试ChannelService.get_all_channels()...")
            result = ChannelService.get_all_channels(
                user_id=1,
                page=1,
                per_page=20,
                active_only=False
            )
            print(f"   get_all_channels返回类型: {type(result)}")
            print(f"   get_all_channels内容: {result}")
            
            # 3. 测试数据库直接查询
            print("\n3. 测试数据库直接查询...")
            from models.channel import Channel
            
            db_channels = Channel.get_user_channels(1, active_only=False)
            print(f"   数据库直接查询: {len(db_channels)} 个频道")
            
            # 4. 检查用户是否存在
            print("\n4. 检查用户...")
            from models.user import User
            user = User.query.get(1)
            print(f"   用户1存在: {'✅' if user else '❌'}")
            if user:
                print(f"   用户名: {user.username}")
            
            # 5. 测试实际API调用 
            print("\n5. 模拟API调用...")
            from apis.v1.channels import channels_bp
            from flask import request
            
            # 创建测试客户端
            with app.test_client() as client:
                # 模拟请求
                response = client.get('/api/v1/channels?page=1&per_page=20&active_only=false')
                print(f"   API响应状态码: {response.status_code}")
                print(f"   API响应内容: {response.get_json()}")
                
        except Exception as e:
            print(f"❌ 测试失败: {str(e)}")
            traceback.print_exc()

def test_service_error():
    """专门测试服务层可能的错误"""
    
    print("\n🧪 测试服务层可能的错误...")
    
    from app import create_app
    
    app = create_app()
    
    with app.app_context():
        try:
            from services.channel_service import ChannelService
            from models.channel import Channel
            from models.channel_data import ChannelData
            
            print("正在测试修改后的get_user_channels方法...")
            
            # 获取原始频道
            channels = Channel.get_user_channels(1, active_only=False)
            print(f"原始Channel.get_user_channels(): {len(channels)} 个频道")
            
            # 手动执行我们修改的逻辑
            result = []
            for channel in channels[:3]:  # 只测试前3个
                print(f"  处理频道: {channel.name}")
                channel_dict = channel.to_dict()
                print(f"    to_dict()成功: {bool(channel_dict)}")
                
                # 获取频道的最新数据
                try:
                    latest_data = ChannelData.query.filter_by(
                        channel_id=channel.id
                    ).order_by(ChannelData.date.desc()).first()
                    
                    channel_dict['latest_data'] = latest_data.to_dict() if latest_data else None
                    print(f"    latest_data添加: {'✅' if latest_data else '❌'}")
                    result.append(channel_dict)
                    
                except Exception as inner_e:
                    print(f"    ❌ latest_data获取失败: {inner_e}")
                    channel_dict['latest_data'] = None
                    result.append(channel_dict)
            
            print(f"手动处理结果: {len(result)} 个频道")
            
            # 现在测试实际的服务方法
            service_result = ChannelService.get_user_channels(1, active_only=False)
            print(f"ChannelService结果: {len(service_result) if isinstance(service_result, list) else type(service_result)}")
            
        except Exception as e:
            print(f"❌ 服务层测试失败: {str(e)}")
            traceback.print_exc()

if __name__ == "__main__":
    print("🚀 开始调试频道列表显示问题...\n")
    test_channel_list_api()
    test_service_error()
    print("\n✅ 调试完成！")