
import os
import sys
import logging

# 设置 PYTHONPATH
backend_dir = os.path.join(os.getcwd(), 'src', 'backend')
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

# 配置根日志，确保输出到终端
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)

from app import create_app
from models import db
from tasks.publisher import run_publication_pipeline

def run_sync_test(task_id):
    print(f"🛠️ Starting Synchronous Test for Task: {task_id}")
    app = create_app()
    with app.app_context():
        try:
            # 直接调用任务函数
            run_publication_pipeline.run(task_id)
            print("✅ Synchronous Test Completed.")
        except Exception as e:
            print(f"❌ Synchronous Test Failed: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    # 使用之前创建的测试任务 ID
    tid = sys.argv[1] if len(sys.argv) > 1 else "c604facc-e0c0-4aa4-b9a6-31d77fe019d5"
    run_sync_test(tid)
