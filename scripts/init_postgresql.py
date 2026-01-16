#!/usr/bin/env python3
"""
PostgreSQL数据库初始化脚本

此脚本用于：
1. 创建PostgreSQL数据库表结构
2. 创建默认管理员用户
3. 设置基础配置
"""

import os
import sys

# 添加项目路径
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src', 'backend'))

def init_postgresql_db():
    """初始化PostgreSQL数据库"""
    print("🐘 初始化PostgreSQL数据库...")
    
    # 设置环境变量使用PostgreSQL
    os.environ['USE_POSTGRESQL'] = 'true'
    
    try:
        from app import create_app
        from models import db, User
        
        app = create_app()
        with app.app_context():
            print("🗑️  清空现有表...")
            db.drop_all()
            
            print("🔧 创建表结构...")
            db.create_all()
            
            print("👤 创建默认管理员用户...")
            
            # 从环境变量读取管理员信息，提供默认值
            admin_username = os.environ.get('ADMIN_USERNAME', 'admin')
            admin_email = os.environ.get('ADMIN_EMAIL', 'admin@example.com')
            admin_password = os.environ.get('ADMIN_PASSWORD', 'admin123')
            admin_display_name = os.environ.get('ADMIN_DISPLAY_NAME', '系统管理员')
            
            # 检查是否已存在管理员用户
            admin_user = User.query.filter_by(username=admin_username).first()
            if not admin_user:
                admin_user = User.create_user(
                    username=admin_username,
                    email=admin_email,
                    password=admin_password,
                    display_name=admin_display_name
                )
                admin_user.is_admin = True
                admin_user.is_verified = True
                db.session.commit()
                
                print("✅ 管理员用户创建成功")
                print(f"   用户名: {admin_username}")
                print(f"   邮箱: {admin_email}")
                if admin_password == 'admin123':
                    print("   密码: admin123")
                    print("   ⚠️  使用默认密码，请登录后立即修改！")
                else:
                    print("   密码: [已自定义]")
            else:
                print(f"✅ 管理员用户 '{admin_username}' 已存在")
            
            print("🎉 PostgreSQL数据库初始化完成！")
            
    except Exception as e:
        print(f"❌ 数据库初始化失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True

def main():
    """主函数"""
    print("=" * 50)
    print("  PostgreSQL数据库初始化工具")
    print("=" * 50)
    
    # 检查环境变量
    required_vars = ['POSTGRES_HOST', 'POSTGRES_DB', 'POSTGRES_USER', 'POSTGRES_PASSWORD']
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        print(f"❌ 缺少环境变量: {', '.join(missing_vars)}")
        print("请设置PostgreSQL连接参数")
        return False
    
    success = init_postgresql_db()
    
    if success:
        print("\n✅ 初始化完成！")
        print("\n下一步：")
        print("1. 启动应用: python src/backend/app.py")
        print("2. 访问: http://localhost:5001")
        print("3. 使用admin/admin123登录并修改密码")
    else:
        print("\n❌ 初始化失败！")
        return False
    
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)