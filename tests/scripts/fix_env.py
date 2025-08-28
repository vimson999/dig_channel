#!/usr/bin/env python3
"""
修复环境变量问题 - 确保应用使用PostgreSQL
"""

import os
import sys

def set_postgres_env():
    """设置PostgreSQL环境变量"""
    postgres_url = "postgresql://cappa_rw:RWcappaDb!!!2025@101.35.56.140:5432/show_ytb_data"
    
    # 设置环境变量
    os.environ['DATABASE_URL'] = postgres_url
    os.environ['DATABASE_TYPE'] = 'postgresql'
    
    print("✅ 环境变量已设置:")
    print(f"DATABASE_URL = {postgres_url}")
    print(f"DATABASE_TYPE = postgresql")
    
    return True

def test_app_connection():
    """测试应用连接"""
    try:
        # 添加项目路径
        sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'src', 'backend'))
        
        from app import create_app
        from models import db
        from sqlalchemy import text
        
        app = create_app()
        
        with app.app_context():
            # 检查连接URL
            engine_url = str(db.engine.url)
            print(f"\n数据库连接: {engine_url}")
            
            if 'postgresql' in engine_url.lower():
                print("✅ 应用正确连接到PostgreSQL")
                
                # 测试查询
                result = db.session.execute(text('SELECT COUNT(*) FROM channel_data')).scalar()
                print(f"channel_data记录数: {result}")
                
                return True
            else:
                print("❌ 应用仍连接到SQLite")
                return False
                
    except Exception as e:
        print(f"❌ 测试连接失败: {e}")
        return False

if __name__ == '__main__':
    print("修复数据库连接问题...")
    
    # 设置环境变量
    set_postgres_env()
    
    # 测试连接
    if test_app_connection():
        print("\n🎉 问题修复成功！现在同步功能应该使用PostgreSQL了")
    else:
        print("\n❌ 修复失败，请检查配置")