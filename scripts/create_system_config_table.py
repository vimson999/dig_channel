import sys
import os
from sqlalchemy import create_engine, text

# 确保能找到 backend 目录
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src', 'backend'))
from config import get_config

def create_table():
    config = get_config()
    db_uri = config.SQLALCHEMY_DATABASE_URI
    
    print(f"🚀 连接数据库: {db_uri.split('@')[-1]}")
    engine = create_engine(db_uri)
    
    # 定义建表 SQL (PostgreSQL 语法)
    create_sql = """
    CREATE TABLE IF NOT EXISTS system_configs (
        id SERIAL PRIMARY KEY,
        key VARCHAR(100) NOT NULL,
        value TEXT,
        type VARCHAR(50) NOT NULL DEFAULT 'general',
        scope VARCHAR(20) NOT NULL DEFAULT 'production',
        status INTEGER DEFAULT 1,
        description VARCHAR(255),
        created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'UTC'),
        updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'UTC')
    );
    
    -- 创建唯一索引
    CREATE UNIQUE INDEX IF NOT EXISTS uq_config_key_scope 
    ON system_configs (key, scope);
    
    -- 创建普通索引
    CREATE INDEX IF NOT EXISTS ix_system_configs_type 
    ON system_configs (type);
    """
    
    try:
        with engine.connect() as conn:
            print("📦 正在创建 system_configs 表...")
            conn.execute(text(create_sql))
            conn.commit()
            print("✅ 表 system_configs 创建成功！")
            
    except Exception as e:
        print(f"❌ 创建失败: {e}")

if __name__ == "__main__":
    create_table()
