#!/usr/bin/env python3
"""
检查TTS库支持的多说话人模型
"""

import sys
sys.path.insert(0, 'src/backend')

def check_multispeaker_models():
    """检查可用的多说话人模型"""
    try:
        from TTS.api import TTS
        
        print("🔍 检查TTS支持的多说话人模型...")
        print("="*60)
        
        # 获取所有可用模型
        manager = TTS.list_models()
        
        print("\n🎭 查找多说话人模型:")
        multispeaker_models = []
        
        for model_name in manager:
            if any(keyword in model_name.lower() for keyword in ['multi', 'speaker', 'voice']):
                multispeaker_models.append(model_name)
                print(f"   ✅ {model_name}")
        
        print(f"\n📊 找到 {len(multispeaker_models)} 个可能的多说话人模型")
        
        # 检查一些常见的多说话人模型
        common_multispeaker = [
            "tts_models/en/vctk/vits",  # VCTK数据集，多说话人
            "tts_models/en/multi-dataset/tortoise-v2",  # Tortoise TTS
            "tts_models/multilingual/multi-dataset/your-tts",  # YourTTS
            "tts_models/multilingual/multi-dataset/bark",  # Bark
        ]
        
        print("\n🌟 测试常见多说话人模型:")
        for model in common_multispeaker:
            try:
                print(f"\n🧪 测试: {model}")
                tts = TTS(model)
                
                # 检查是否支持多说话人
                if hasattr(tts.tts, 'speakers') and tts.tts.speakers:
                    speakers = tts.tts.speakers
                    print(f"   ✅ 支持 {len(speakers)} 个说话人:")
                    for i, speaker in enumerate(speakers[:5]):  # 只显示前5个
                        print(f"      {i+1}. {speaker}")
                    if len(speakers) > 5:
                        print(f"      ... 还有 {len(speakers)-5} 个")
                else:
                    print(f"   ❌ 不支持多说话人")
                    
            except Exception as e:
                print(f"   ❌ 加载失败: {str(e)}")
        
    except ImportError:
        print("❌ TTS库未安装")
    except Exception as e:
        print(f"❌ 检查失败: {str(e)}")

def test_chinese_multispeaker():
    """测试中文多说话人选项"""
    print("\n" + "="*60)
    print("🇨🇳 检查中文多说话人解决方案")
    print("="*60)
    
    solutions = [
        "1. 使用不同的中文模型组合（男/女声）",
        "2. 使用音频后处理改变音调/语速",
        "3. 使用多语言模型的不同说话人",
        "4. 使用声音克隆技术"
    ]
    
    for solution in solutions:
        print(f"   {solution}")
    
    print("\n💡 推荐方案：")
    print("   - 短期：使用音调变化模拟不同说话人")
    print("   - 长期：集成专业的多说话人TTS模型")

if __name__ == '__main__':
    print("🎤 TTS多说话人模型检查")
    check_multispeaker_models()
    test_chinese_multispeaker()