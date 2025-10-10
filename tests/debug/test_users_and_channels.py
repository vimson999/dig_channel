#!/usr/bin/env python3
"""
检查数据库中的用户和频道数据，找出用户ID不匹配的问题
"""
import sys

# 添加src/backend到Python路径（自动回溯到项目根目录）
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve()
while PROJECT_ROOT.parent != PROJECT_ROOT and not (PROJECT_ROOT / 'src' / 'backend').exists():
    PROJECT_ROOT = PROJECT_ROOT.parent

sys.path.insert(0, str((PROJECT_ROOT / 'src' / 'backend').resolve()))

def check_users_and_channels():
    """检查用户和频道数据"""
    
    print("🔍 检查数据库中的用户和频道数据...")
    
    from app import create_app
    
    # 创建Flask应用上下文
    app = create_app()
    
    with app.app_context():
        try:
            from models.user import User
            from models.channel import Channel
            
            print("\n👥 检查用户数据:")
            users = User.query.all()
            for user in users:
                print(f"  用户ID: {user.id}, 用户名: {user.username}, 邮箱: {user.email}, 是否活跃: {user.is_active}")
            
            print(f"\n📊 检查频道数据:")
            channels = Channel.query.all()
            print(f"  总频道数: {len(channels)}")
            
            # 按用户分组统计
            user_channel_counts = {}
            for channel in channels:
                user_id = channel.user_id
                if user_id not in user_channel_counts:
                    user_channel_counts[user_id] = 0
                user_channel_counts[user_id] += 1
            
            print(f"\n📈 按用户分组的频道统计:")
            for user_id, count in user_channel_counts.items():
                user = User.query.get(user_id)
                username = user.username if user else "未知用户"
                print(f"  用户ID {user_id} ({username}): {count} 个频道")
                
            # 检查测试用户和默认用户
            print(f"\n🔧 检查特殊用户:")
            test_user = User.query.filter_by(username='test_user').first()
            if test_user:
                print(f"  test_user ID: {test_user.id}")
                test_channels = Channel.query.filter_by(user_id=test_user.id).count()
                print(f"  test_user 拥有频道数: {test_channels}")
            else:
                print(f"  test_user: 不存在")
                
            # 检查ID=1的用户
            user_1 = User.query.get(1)
            if user_1:
                print(f"  用户ID=1: {user_1.username}")
                user_1_channels = Channel.query.filter_by(user_id=1).count()
                print(f"  用户ID=1 拥有频道数: {user_1_channels}")
            else:
                print(f"  用户ID=1: 不存在")
                
        except Exception as e:
            print(f"❌ 检查失败: {str(e)}")
            import traceback
            traceback.print_exc()

def fix_user_assignment():
    """修复用户分配问题的选项"""
    print(f"\n🔧 可能的解决方案:")
    print(f"1. 将所有频道重新分配给test_user (ID=3)")
    print(f"2. 将test_user的ID修改为1")  
    print(f"3. 创建一个ID=1的用户并将频道分配给它")
    print(f"4. 修改认证系统使其使用正确的用户ID")

if __name__ == "__main__":
    print("🚀 开始检查用户和频道数据...\n")
    check_users_and_channels()
    fix_user_assignment()
    print("\n✅ 检查完成！")