
import mlx_whisper
import os
import json

def diagnostic():
    # 使用你最近测试用的音频文件
    audio_path = "data/uploads/t1_1773736825575.mp3"
    model_name = "mlx-community/whisper-large-v3-mlx-8bit"
    
    if not os.path.exists(audio_path):
        # 尝试找 data/uploads 下最新的 mp3
        import glob
        files = glob.glob("data/uploads/*.mp3")
        if files:
            audio_path = max(files, key=os.path.getctime)
        else:
            print("❌ 找不到任何测试音频文件")
            return

    print(f"🔍 正在诊断音频: {audio_path}")
    print(f"🚀 加载模型: {model_name}...")
    
    try:
        result = mlx_whisper.transcribe(
            audio_path, 
            path_or_hf_repo=model_name,
            language="zh",
            initial_prompt="以下是简体中文内容，不要使用繁体字："
        )
        
        segments = result.get('segments', [])
        print(f"\n✅ 识别成功! 共获得 {len(segments)} 条片段\n")
        print("-" * 60)
        print(f"{'ID':<4} | {'Start':<8} | {'End':<8} | {'Text'}")
        print("-" * 60)
        
        for i, s in enumerate(segments):
            start = s.get('start', 0)
            end = s.get('end', 0)
            text = s.get('text', '').strip()
            print(f"{i:<4} | {start:<8.2f} | {end:<8.2f} | {text}")
            
        print("-" * 60)
        
        # 保存到本地方便你下载查看
        output_file = "data/processed/diagnostic_asr_result.json"
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(segments, f, ensure_ascii=False, indent=2)
        print(f"\n💾 详细数据已保存至: {output_file}")

    except Exception as e:
        print(f"❌ 诊断失败: {e}")

if __name__ == "__main__":
    diagnostic()
