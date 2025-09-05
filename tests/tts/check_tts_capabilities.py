#!/usr/bin/env python3
"""
检查当前TTS模型的多说话人能力
"""

import sys
sys.path.insert(0, 'src/backend')

def check_current_model_capabilities():
    """检查当前中文模型的能力"""
    try:
        from TTS.api import TTS
        
        print("🔍 检查当前中文模型能力...")
        print("="*60)
        
        # 当前使用的中文模型
        model_name = 'tts_models/zh-CN/baker/tacotron2-DDC-GST'
        print(f"📋 当前模型: {model_name}")
        
        tts = TTS(model_name)
        
        # 检查模型属性
        print(f"\n🔬 模型分析:")
        print(f"   模型类型: {type(tts.tts).__name__}")
        
        # 检查是否支持多说话人
        if hasattr(tts.tts, 'speakers') and tts.tts.speakers:
            speakers = tts.tts.speakers
            print(f"   ✅ 支持多说话人: {len(speakers)} 个")
            for speaker in speakers:
                print(f"      - {speaker}")
        else:
            print(f"   ❌ 不支持多说话人 (单一声音模型)")
        
        # 检查是否支持情感/风格
        if hasattr(tts.tts, 'gst') and tts.tts.gst:
            print(f"   ✅ 支持情感风格控制")
        else:
            print(f"   ❌ 不支持情感风格控制")
            
        # 检查其他参数
        if hasattr(tts.tts, 'ap') and tts.tts.ap:
            print(f"   ✅ 音频处理器可用")
            
        return tts
        
    except Exception as e:
        print(f"❌ 检查失败: {str(e)}")
        return None

def test_voice_variation_methods():
    """测试声音变化方法"""
    print("\n" + "="*60)
    print("🎭 测试声音变化解决方案")
    print("="*60)
    
    try:
        from TTS.api import TTS
        import tempfile
        import os
        
        tts = TTS('tts_models/zh-CN/baker/tacotron2-DDC-GST')
        
        # 方案1：尝试不同的音频设置
        print("\n📊 方案1：调整音频参数")
        
        test_texts = [
            ("张三", "你好，我是张三"),
            ("李四", "我是李四，很高兴认识你")
        ]
        
        for speaker, text in test_texts:
            temp_dir = tempfile.gettempdir()
            audio_path = os.path.join(temp_dir, f"test_{speaker}.wav")
            
            try:
                # 基础合成
                tts.tts_to_file(text=text, file_path=audio_path)
                
                if os.path.exists(audio_path):
                    file_size = os.path.getsize(audio_path)
                    print(f"   {speaker}: 生成成功 ({file_size} bytes)")
                    os.remove(audio_path)  # 清理
                else:
                    print(f"   {speaker}: 生成失败")
                    
            except Exception as e:
                print(f"   {speaker}: 异常 - {str(e)}")
        
        # 方案2：检查是否可以用速度/音调参数
        print(f"\n🎛️  方案2：参数调整选项")
        print("   当前TTS API不直接支持速度/音调调整")
        print("   需要使用音频后处理库 (如 pydub, librosa)")
        
    except Exception as e:
        print(f"❌ 测试失败: {str(e)}")

def recommend_solutions():
    """推荐解决方案"""
    print("\n" + "="*60)
    print("💡 多人对话解决方案推荐")
    print("="*60)
    
    solutions = [
        {
            "name": "方案1: 音频后处理",
            "description": "使用pydub调整音调、语速来区分说话人",
            "pros": "简单实现，不需要新模型",
            "cons": "效果有限，可能不够自然",
            "complexity": "低"
        },
        {
            "name": "方案2: 多模型组合", 
            "description": "使用不同的TTS模型为不同说话人生成语音",
            "pros": "声音差异明显",
            "cons": "需要多个模型，增加资源占用",
            "complexity": "中"
        },
        {
            "name": "方案3: 专业多说话人模型",
            "description": "使用VITS、YourTTS等支持多说话人的模型",
            "pros": "专业级多说话人效果",
            "cons": "可能不支持中文或效果不佳",
            "complexity": "高"
        },
        {
            "name": "方案4: 文本标记",
            "description": "在文本中明确标记说话人，依赖用户理解",
            "pros": "实现简单，清晰标识",
            "cons": "没有实际的声音区别",
            "complexity": "极低"
        }
    ]
    
    for i, solution in enumerate(solutions, 1):
        print(f"\n{i}. {solution['name']}")
        print(f"   描述: {solution['description']}")
        print(f"   优点: {solution['pros']}")
        print(f"   缺点: {solution['cons']}")
        print(f"   复杂度: {solution['complexity']}")
    
    print(f"\n🎯 建议实现顺序:")
    print(f"   1. 立即实现方案4 (文本标记) - 让用户知道这是多人对话")
    print(f"   2. 短期实现方案1 (音频后处理) - 提供基础的声音差异")
    print(f"   3. 长期考虑方案2或3 - 提供专业级效果")

if __name__ == '__main__':
    print("🎤 TTS多说话人能力分析")
    
    # 检查当前模型能力
    check_current_model_capabilities()
    
    # 测试变化方法
    test_voice_variation_methods()
    
    # 推荐解决方案
    recommend_solutions()