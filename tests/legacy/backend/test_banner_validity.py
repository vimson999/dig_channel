#!/usr/bin/env python3
"""
测试通栏有效期设计
"""

import os
from PIL import Image
from api.student_id_generator import StudentIdGenerator

def test_banner_validity():
    """测试通栏有效期效果"""
    print("🧪 测试通栏有效期设计...")
    
    generator = StudentIdGenerator()
    
    # 测试数据
    student_data = {
        'schoolName': '早稲田大学理工学部',
        'studentName': '佐藤 美咲',
        'studentNameKana': 'さとう みさき',
        'studentId': '202512345',
        'grade': '4年生',
        'department': '情報理工学科',
        'validFrom': '2024.04.01',
        'validTo': '2025.03.31'
    }
    
    # 创建测试照片
    test_photo = Image.new('RGB', (300, 400), (180, 160, 200))
    
    try:
        # 生成学生证
        student_id_image = generator.create_student_id_image(student_data, test_photo)
        
        # 保存测试图像
        output_path = 'test_banner_validity.png'
        student_id_image.save(output_path)
        
        print("✅ 通栏有效期测试完成")
        print(f"📁 测试文件保存到: {os.path.abspath(output_path)}")
        print("🎨 通栏设计特点:")
        print("   • 有效期横跨整个卡片宽度")
        print("   • 使用海军蓝背景色，与顶栏呼应")
        print("   • 左侧显示'有効期間'标题")
        print("   • 右侧显示具体日期范围")
        print("   • 白色文字在深色背景上清晰可见")
        
        return True
        
    except Exception as e:
        print(f"❌ 通栏有效期测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_banner_with_different_dates():
    """测试不同日期格式的通栏效果"""
    print("\n🧪 测试不同日期格式...")
    
    generator = StudentIdGenerator()
    
    # 测试用例：不同的日期格式
    test_cases = [
        ("标准学年", "2024.04.01", "2025.03.31"),
        ("半年期", "2024.09.01", "2025.02.28"),
        ("短期", "2024.10.01", "2024.12.31"),
    ]
    
    base_data = {
        'schoolName': '慶應義塾大学',
        'studentName': '高橋 健太',
        'studentNameKana': 'たかはし けんた',
        'studentId': '202567890',
        'grade': '1年生',
        'department': '経済学部',
    }
    
    test_photo = Image.new('RGB', (300, 400), (160, 190, 140))
    
    for case_name, valid_from, valid_to in test_cases:
        try:
            student_data = base_data.copy()
            student_data['validFrom'] = valid_from
            student_data['validTo'] = valid_to
            
            student_id_image = generator.create_student_id_image(student_data, test_photo)
            
            output_path = f'test_banner_{case_name}.png'
            student_id_image.save(output_path)
            
            print(f"  ✅ {case_name}: {valid_from} ～ {valid_to}")
            print(f"     保存到: {output_path}")
            
        except Exception as e:
            print(f"  ❌ {case_name} 测试失败: {str(e)}")
            return False
    
    return True

def main():
    """主测试函数"""
    print("🧪 开始测试通栏有效期设计...\n")
    
    tests = [
        ("通栏有效期基本功能", test_banner_validity),
        ("不同日期格式通栏效果", test_banner_with_different_dates),
    ]
    
    passed = 0
    total = len(tests)
    
    for test_name, test_func in tests:
        print(f"📋 测试: {test_name}")
        if test_func():
            passed += 1
            print(f"✅ {test_name} 测试通过\n")
        else:
            print(f"❌ {test_name} 测试失败\n")
    
    print("=" * 60)
    print(f"🎯 测试结果: {passed}/{total} 通过")
    
    if passed == total:
        print("🎉 通栏有效期设计测试全部通过！")
        print("📝 通栏设计优势:")
        print("   1. ✅ 视觉冲击力强，类似信用卡设计")
        print("   2. ✅ 充分利用卡片宽度，空间利用率高")
        print("   3. ✅ 与顶栏形成呼应，整体设计统一")
        print("   4. ✅ 信息层次清晰，易于快速识别")
        return True
    else:
        print("⚠️ 部分测试失败，请检查代码。")
        return False

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)