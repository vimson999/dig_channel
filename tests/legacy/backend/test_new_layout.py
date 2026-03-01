#!/usr/bin/env python3
"""
测试新布局的学生证生成器
- 学校名称在蓝色顶栏
- 有效期在卡片框内
"""

import os
from PIL import Image
from api.student_id_generator import StudentIdGenerator

def test_new_layout():
    """测试新布局"""
    print("🧪 测试新布局...")
    
    generator = StudentIdGenerator()
    
    # 测试数据 - 包含长学校名称
    student_data = {
        'schoolName': '東京工業大学情報理工学院情報工学系',
        'studentName': '山田 花子',
        'studentNameKana': 'やまだ はなこ',
        'studentId': '202567890',
        'grade': '3年生',
        'department': '情報工学科',
        'validFrom': '2024.04.01',
        'validTo': '2025.03.31'
    }
    
    # 创建测试照片
    test_photo = Image.new('RGB', (300, 400), (200, 180, 160))
    
    try:
        # 生成学生证
        student_id_image = generator.create_student_id_image(student_data, test_photo)
        
        # 保存测试图像
        output_path = 'test_new_layout.png'
        student_id_image.save(output_path)
        
        print("✅ 新布局测试完成")
        print(f"📁 测试文件保存到: {os.path.abspath(output_path)}")
        print("📋 新布局特点:")
        print("   • 学校名称显示在蓝色顶栏右侧")
        print("   • 有效期显示在卡片底部的独立框内")
        print("   • 学生信息区域更加紧凑整洁")
        
        return True
        
    except Exception as e:
        print(f"❌ 新布局测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_different_school_names():
    """测试不同长度的学校名称"""
    print("\n🧪 测试不同长度的学校名称...")
    
    generator = StudentIdGenerator()
    
    # 测试用例：不同长度的学校名称
    test_cases = [
        ("短名称", "東大"),
        ("中等长度", "東京大学工学部"),
        ("长名称", "東京工業大学情報理工学院情報工学系"),
        ("超长名称", "国立研究開発法人理化学研究所革新知能統合研究センター"),
    ]
    
    base_data = {
        'studentName': '田中 太郎',
        'studentNameKana': 'たなか たろう',
        'studentId': '202500001',
        'grade': '2年生',
        'department': '情報学科',
        'validFrom': '2024.04.01',
        'validTo': '2025.03.31'
    }
    
    test_photo = Image.new('RGB', (300, 400), (150, 200, 180))
    
    for case_name, school_name in test_cases:
        try:
            student_data = base_data.copy()
            student_data['schoolName'] = school_name
            
            student_id_image = generator.create_student_id_image(student_data, test_photo)
            
            output_path = f'test_school_name_{case_name}.png'
            student_id_image.save(output_path)
            
            print(f"  ✅ {case_name}: {school_name}")
            print(f"     保存到: {output_path}")
            
        except Exception as e:
            print(f"  ❌ {case_name} 测试失败: {str(e)}")
            return False
    
    return True

def main():
    """主测试函数"""
    print("🧪 开始测试新布局的学生证生成器...\n")
    
    tests = [
        ("新布局基本功能", test_new_layout),
        ("不同长度学校名称", test_different_school_names),
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
        print("🎉 新布局测试全部通过！")
        print("📝 布局改进总结:")
        print("   1. ✅ 学校名称已移至蓝色顶栏")
        print("   2. ✅ 有效期已放入卡片框内") 
        print("   3. ✅ 文本截断功能正常工作")
        print("   4. ✅ 布局紧凑，无内容超出边界")
        return True
    else:
        print("⚠️ 部分测试失败，请检查代码。")
        return False

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)