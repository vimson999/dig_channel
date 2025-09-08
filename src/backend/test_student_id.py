#!/usr/bin/env python3
"""
测试学生证生成器
"""

import os
from PIL import Image
from api.student_id_generator import StudentIdGenerator

def test_student_id_generation():
    """测试学生证生成功能"""
    print("🧪 开始测试学生证生成器...")
    
    # 创建生成器实例
    generator = StudentIdGenerator()
    print("✅ 生成器实例创建成功")
    
    # 测试数据
    student_data = {
        'schoolName': '東京大学',
        'studentName': '山田 太郎',
        'studentNameKana': 'やまだ たろう',
        'studentId': '202400123',
        'grade': '3年生',
        'department': '工学部情報学科',
        'validFrom': '2024.04.01',
        'validTo': '2025.03.31'
    }
    print("✅ 测试数据准备完成")
    
    # 创建测试照片（纯色图像）
    test_photo = Image.new('RGB', (300, 400), (100, 150, 200))
    print("✅ 测试照片创建完成")
    
    try:
        # 生成学生证
        student_id_image = generator.create_student_id_image(student_data, test_photo)
        print("✅ 学生证图像生成成功")
        
        # 保存测试图像
        output_path = 'test_student_id.png'
        student_id_image.save(output_path)
        print(f"✅ 测试图像保存成功: {output_path}")
        
        # 检查图像尺寸
        width, height = student_id_image.size
        print(f"📏 生成的学生证尺寸: {width}x{height} 像素")
        
        if width == 340 and height == 215:
            print("✅ 图像尺寸正确")
        else:
            print("❌ 图像尺寸不正确")
            
        print("\n🎉 学生证生成器测试完成！")
        print(f"📁 生成的测试文件位置: {os.path.abspath(output_path)}")
        
        return True
        
    except Exception as e:
        print(f"❌ 测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_student_id_generation()
    exit(0 if success else 1)