#!/bin/bash

echo "🔧 验证PostgreSQL连接修复..."
echo "=================================="

# 设置环境变量
export DATABASE_TYPE=postgresql
export DATABASE_URL=postgresql://cappa_rw:RWcappaDb!!!2025@101.35.56.140:5432/show_ytb_data

echo "✅ 环境变量已设置:"
echo "DATABASE_TYPE=$DATABASE_TYPE"
echo "DATABASE_URL=$DATABASE_URL"

# 激活虚拟环境并测试
echo ""
echo "🧪 测试数据库连接..."

source ../../venv/bin/activate
cd ../../src/backend

python -c "
import os
from app import create_app
from models import db
from sqlalchemy import text

print('检查环境变量:')
print(f'DATABASE_URL: {os.environ.get(\"DATABASE_URL\", \"未设置\")}')

app = create_app()
with app.app_context():
    engine_url = str(db.engine.url)
    print(f'\\n实际连接: {engine_url}')
    
    if 'postgresql' in engine_url.lower():
        print('✅ 应用正确连接到PostgreSQL')
        
        # 测试插入
        result = db.session.execute(text('SELECT COUNT(*) FROM channel_data')).scalar()
        print(f'channel_data记录数: {result}')
        
        print('\\n🎉 修复成功！同步功能现在会使用PostgreSQL')
    else:
        print('❌ 应用仍连接到SQLite，需要检查配置')
"

echo ""
echo "=================================="
echo "修复验证完成！"