
import mlx_whisper
import os
import sys

def main():
    audio_path = "data/uploads/a1_1773301592006.mp3"
    model_name = "mlx-community/whisper-base-mlx-8bit"
    
    if not os.path.exists(audio_path):
        print(f"❌ 错误: 找不到测试音频 {audio_path}")
        sys.exit(1)
        
    print(f"🚀 正在尝试加载并下载模型: {model_name}")
    print("💡 提示: 如果网络不通，请确保已配置代理。")
    
    try:
        # 这个操作会触发模型下载
        result = mlx_whisper.transcribe(
            audio_path, 
            path_or_hf_repo=model_name,
            language="zh"
        )
        print("✅ 模型下载并运行成功!")
        print(f"📝 识别结果预览: {result.get('text', '')[:100]}...")
    except Exception as e:
        print(f"❌ 运行失败: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
