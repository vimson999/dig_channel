import sys
import os

# 确保 src/backend 在路径中
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src', 'backend'))

from app import create_app
from models import db
from models.task import PublicationTask

def create_tables():
    print("🚀 启动数据库初始化流程...")
    app = create_app()
    with app.app_context():
        print(f"🔍 正在连接数据库: {app.config['SQLALCHEMY_DATABASE_URI']}")
        print("🔍 正在检查并创建 PublicationTask 数据表...")
        try:
            # 这里的 db 就是在 app 中 init_app 的那个 db
            db.create_all()
            print("✅ 成功！'publication_tasks' 表已就绪。")
        except Exception as e:
            print(f"❌ 失败: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    create_tables()