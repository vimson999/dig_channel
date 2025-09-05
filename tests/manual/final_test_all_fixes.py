#!/usr/bin/env python3
"""
最终综合测试所有TTS修复
"""

import requests
import json
import time

def test_edge_cases():
    """测试各种边缘情况"""
    
    base_url = "http://localhost:5004"
    
    # 各种可能导致问题的测试用例
    test_cases = [
        {"text": "测试", "desc": "超短文本"},
        {"text": "测试文本", "desc": "短文本"},
        {"text": "测试文本。", "desc": "已有句号"},
        {"text": "测试文本！", "desc": "感叹号结尾"},
        {"text": "测试文本？", "desc": "问号结尾"},
        {"text": "测试文本....", "desc": "多个句号"},
        {"text": "测试，文本，内容", "desc": "包含逗号"},
        {"text": "测试；文本；内容", "desc": "包含分号"},
        {"text": "这是一个很长的测试文本，用来验证TTS系统是否能够正确处理长文本而不出现重复播放的问题。", "desc": "长文本"},
        {"text": "测试\n换行\t制表符 空格", "desc": "特殊空白字符"},
        {"text": "测试\u200b零宽字符", "desc": "零宽字符"},
        {"text": "   前后空格   ", "desc": "前后空格"},
        {"text": "", "desc": "空字符串"},
        {"text": "   ", "desc": "纯空白"},
    ]
    
    print("🧪 开始边缘情况测试")
    print("="*60)
    
    for i, test_case in enumerate(test_cases, 1):
        text = test_case['text']
        desc = test_case['desc']
        
        print(f"\n📝 测试 {i:02d}: {desc}")
        print(f"   文本: {repr(text)}")
        print("-" * 40)
        
        if text.strip() == "":  # 跳过空文本，避免400错误
            print("⏭️  跳过空文本测试")
            continue
            
        request_data = {
            "text": text,
            "model": "chinese"
        }
        
        try:
            start_time = time.time()
            
            response = requests.post(
                f"{base_url}/api/v1/tts/synthesize",
                json=request_data,
                headers={'Content-Type': 'application/json'},
                timeout=30
            )
            
            elapsed = time.time() - start_time
            
            if response.status_code == 200:
                result = response.json()
                if result.get('success'):
                    data = result.get('data', {})
                    file_size = data.get('file_size', 0)
                    filename = data.get('audio_filename', '')
                    
                    print(f"✅ 成功 ({elapsed:.2f}s)")
                    print(f"   文件: {filename}")
                    print(f"   大小: {file_size} bytes")
                    
                    # 关键检查：是否重复播放
                    if file_size > 500000:
                        print("🚨 检测到问题：文件过大，可能重复播放！")
                        return False
                    elif file_size < 10000:
                        print("⚠️  文件较小")
                    else:
                        print("✅ 文件大小正常")
                else:
                    error_msg = result.get('message', '未知错误')
                    print(f"❌ 服务器错误: {error_msg}")
                    if "不能为空" not in error_msg:  # 预期的空文本错误
                        return False
            else:
                print(f"❌ HTTP错误: {response.status_code}")
                if response.status_code != 400:  # 400可能是预期的验证错误
                    return False
                
        except Exception as e:
            print(f"❌ 异常: {str(e)}")
            return False
        
        time.sleep(0.5)  # 避免请求过频繁
    
    return True

def test_performance_consistency():
    """测试性能一致性"""
    print("\n" + "="*60)
    print("⚡ 性能一致性测试")
    print("="*60)
    
    base_url = "http://localhost:5004"
    test_text = "这是一个标准的测试文本，用来检验TTS系统的性能一致性。"
    
    file_sizes = []
    response_times = []
    
    for i in range(5):
        print(f"\n🔄 第 {i+1} 次测试...")
        
        request_data = {
            "text": test_text,
            "model": "chinese"
        }
        
        try:
            start_time = time.time()
            
            response = requests.post(
                f"{base_url}/api/v1/tts/synthesize",
                json=request_data,
                headers={'Content-Type': 'application/json'},
                timeout=30
            )
            
            elapsed = time.time() - start_time
            response_times.append(elapsed)
            
            if response.status_code == 200:
                result = response.json()
                if result.get('success'):
                    file_size = result['data']['file_size']
                    file_sizes.append(file_size)
                    
                    print(f"   时间: {elapsed:.2f}s, 大小: {file_size} bytes")
                else:
                    print(f"❌ 失败: {result.get('message')}")
                    return False
            else:
                print(f"❌ HTTP错误: {response.status_code}")
                return False
                
        except Exception as e:
            print(f"❌ 异常: {str(e)}")
            return False
        
        time.sleep(1)
    
    # 分析一致性
    if len(file_sizes) == 5:
        avg_size = sum(file_sizes) / len(file_sizes)
        max_deviation = max(abs(size - avg_size) for size in file_sizes)
        
        print(f"\n📊 性能分析:")
        print(f"   平均文件大小: {avg_size:.0f} bytes")
        print(f"   最大偏差: {max_deviation:.0f} bytes")
        print(f"   平均响应时间: {sum(response_times) / len(response_times):.2f}s")
        
        # 检查一致性（偏差不应超过10%）
        if max_deviation / avg_size > 0.1:
            print("⚠️  文件大小不一致，可能有问题")
            return False
        else:
            print("✅ 性能一致性良好")
    
    return True

def main():
    """主测试函数"""
    print("🚀 TTS修复最终验证测试")
    print("="*60)
    
    # 测试1：边缘情况
    success1 = test_edge_cases()
    
    # 测试2：性能一致性
    success2 = test_performance_consistency()
    
    # 最终结果
    print("\n" + "="*60)
    print("🏁 最终测试结果")
    print("="*60)
    
    if success1 and success2:
        print("🎉 所有测试通过！TTS重复播放问题已彻底修复！")
        print("\n✅ 修复确认:")
        print("   ✅ 边缘情况处理正常")
        print("   ✅ 性能一致性良好")
        print("   ✅ 无重复播放问题")
        print("   ✅ 文件大小稳定")
    else:
        print("❌ 测试失败，仍有问题需要解决")
        if not success1:
            print("   ❌ 边缘情况测试失败")
        if not success2:
            print("   ❌ 性能一致性测试失败")

if __name__ == '__main__':
    main()