#!/usr/bin/env python3
"""
测试更新后的学生证生成器功能
- 测试随机数据生成
- 测试有效期显示
- 测试布局优化
"""

import os
from PIL import Image
from api.student_id_generator import StudentIdGenerator

def test_validity_period_display():
    """测试有效期显示功能"""
    print("🧪 测试有效期显示...")
    
    generator = StudentIdGenerator()
    
    # 测试数据，包含长文本以测试布局
    student_data = {
        'schoolName': '東京工業大学情報理工学院',
        'studentName': '高橋 さくら',
        'studentNameKana': 'たかはし さくら',
        'studentId': '202512345',
        'grade': '大学院生',
        'department': '情報理工学研究科コンピュータサイエンス専攻',
        'validFrom': '2024.04.01',
        'validTo': '2025.03.31'
    }
    
    # 创建测试照片
    test_photo = Image.new('RGB', (300, 400), (150, 180, 210))
    
    try:
        # 生成学生证
        student_id_image = generator.create_student_id_image(student_data, test_photo)
        
        # 保存测试图像
        output_path = 'test_validity_display.png'
        student_id_image.save(output_path)
        
        print("✅ 有效期显示测试完成")
        print(f"📁 测试文件保存到: {os.path.abspath(output_path)}")
        
        return True
        
    except Exception as e:
        print(f"❌ 有效期显示测试失败: {str(e)}")
        return False

def test_layout_boundaries():
    """测试布局边界"""
    print("\n🧪 测试布局边界...")
    
    generator = StudentIdGenerator()
    
    # 极端长度的测试数据
    student_data = {
        'schoolName': '非常に長い名前の大学工学部情報工学科データサイエンス専攻',
        'studentName': '非常に長い姓名の学生',
        'studentNameKana': 'ひじょうに ながい せいめいの がくせい',
        'studentId': '202567890123',
        'grade': '4年生',
        'department': '工学部情報工学科データサイエンス専攻人工知能分野',
        'validFrom': '2024.04.01',
        'validTo': '2025.03.31'
    }
    
    test_photo = Image.new('RGB', (300, 400), (200, 150, 100))
    
    try:
        student_id_image = generator.create_student_id_image(student_data, test_photo)
        
        output_path = 'test_layout_boundaries.png'
        student_id_image.save(output_path)
        
        print("✅ 布局边界测试完成")
        print(f"📁 测试文件保存到: {os.path.abspath(output_path)}")
        
        return True
        
    except Exception as e:
        print(f"❌ 布局边界测试失败: {str(e)}")
        return False

def test_text_truncation():
    """测试文本截断功能"""
    print("\n🧪 测试文本截断功能...")
    
    generator = StudentIdGenerator()
    
    # 测试截断函数
    test_cases = [
        ("短い文字", 10, "短い文字"),
        ("これは非常に長い文字列です", 8, "これは非常に長…"),
        ("English text", 5, "Engl…"),
        ("", 5, ""),
    ]
    
    all_passed = True
    for text, max_len, expected in test_cases:
        result = generator.truncate_text(text, max_len)
        if result == expected:
            print(f"  ✅ '{text}' → '{result}'")
        else:
            print(f"  ❌ '{text}' → '{result}' (期待: '{expected}')")
            all_passed = False
    
    return all_passed

def main():
    """主测试函数"""
    print("🧪 开始测试更新后的学生证生成器功能...\n")
    
    tests = [
        ("文本截断功能", test_text_truncation),
        ("有效期显示", test_validity_period_display),
        ("布局边界", test_layout_boundaries),
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
    
    print("=" * 50)
    print(f"🎯 测试结果: {passed}/{total} 通过")
    
    if passed == total:
        print("🎉 所有测试通过！更新功能正常工作。")
        return True
    else:
        print("⚠️ 部分测试失败，请检查代码。")
        return False

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)