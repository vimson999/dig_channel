
import os
import sys
import time
import json
import uuid

# 设置 PYTHONPATH
backend_dir = os.path.join(os.getcwd(), 'src', 'backend')
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app import create_app
from models import db
from models.task import PublicationTask
from tasks.publisher import run_publication_pipeline

def run_benchmark(model_size, label):
    print(f"\n--- 🚀 Starting Benchmark: {label} (Model: {model_size}) ---")
    app = create_app()
    with app.app_context():
        # 准备任务参数
        audio_path = "data/uploads/t1_1773741301070.mp3"
        image_path = "assets/cover/Gemini_Generated_Image_ifcvkkifcvkkifcv.png"
        
        params = {
            "video": {
                "enabled": True, "audio_path": audio_path, "image_path": image_path,
                "asr_enabled": True, "sub_burn_enabled": True, "asr_model": model_size
            },
            "vertical_video": {
                "enabled": True, "image_path": image_path,
                "asr_enabled": True, "sub_burn_enabled": True, "asr_model": model_size
            },
            "thumbnail": {"main_title": f"Benchmark: {label}", "bg_index": 0, "layout": "horizontal_reverse"}
        }
        
        task = PublicationTask(id=str(uuid.uuid4()), params=params, status='pending')
        db.session.add(task)
        db.session.commit()
        
        start_time = time.time()
        try:
            # 禁用 B 站上传以仅测试本地处理性能
            # 我们通过 mock 掉上传逻辑或直接在任务逻辑中处理
            # 这里简单起见，我们关注日志中的分步耗时
            run_publication_pipeline.run(task.id)
            total_duration = time.time() - start_time
            print(f"✅ {label} 总耗时: {total_duration:.2f}s")
            return total_duration
        except Exception as e:
            print(f"❌ {label} 失败: {e}")
            return None

if __name__ == "__main__":
    results = {}
    
    # 1. 测试 Tiny 模型 + FFmpeg (速度极限)
    results['Speed_Mode'] = run_benchmark("tiny", "Speed Mode (Tiny)")
    
    # 2. 测试 Large-v3 模型 + FFmpeg (画质极限)
    results['High_Quality_Mode'] = run_benchmark("large-v3", "High Quality Mode (Large-v3)")
    
    print("\n" + "="*40)
    print("📊 PERFORMANCE COMPARISON TABLE")
    print("="*40)
    print(f"{'Mode':<25} | {'Duration':<10}")
    print("-" * 40)
    for mode, dur in results.items():
        if dur: print(f"{mode:<25} | {dur:>8.2f}s")
    print("="*40)
    print("注：优化前 (Large-v3 + MoviePy) 总耗时约为 170s")
