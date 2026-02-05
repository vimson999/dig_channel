import sys
import os
from sqlalchemy import create_engine, inspect

# 确保能找到配置文件
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src', 'backend'))
from config import get_config

def check_db():
    config = get_config()
    db_uri = config.SQLALCHEMY_DATABASE_URI
    # 安全打印 URI
    parts = db_uri.split('@')
    safe_uri = parts[-1] if len(parts) > 1 else db_uri
    print(f"🚀 正在连接数据库: {safe_uri}")
    
    try:
        engine = create_engine(db_uri)
        inspector = inspect(engine)
        
        # 获取所有表名
        tables = inspector.get_table_names()
        print(f"📊 当前数据库中存在的表 ({len(tables)}个):")
        for table in sorted(tables):
            print(f"  - {table}")
            
        if 'publication_tasks' in tables:
            print("\n✅ 发现 'publication_tasks' 表！")
        else:
            print("\n❌ 未发现 'publication_tasks' 表！")
            
    except Exception as e:
        print(f"❌ 失败: {e}")

if __name__ == "__main__":
    check_db()