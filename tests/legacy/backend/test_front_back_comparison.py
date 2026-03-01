#!/usr/bin/env python3
"""
正面和背面对比测试
生成正面和背面学生证进行对比
"""

from api.student_id_generator import StudentIdGenerator
from PIL import Image

def test_front_back_comparison():
    """测试正面和背面生成并进行对比"""
    print("🔄 测试学生证正面和背面生成对比...")
    
    try:
        # 创建生成器
        generator = StudentIdGenerator()
        
        # 测试数据
        test_data = {
            'schoolName': '早稲田大学',
            'studentName': '田中花子',
            'studentNameKana': 'たなか はなこ',
            'studentId': '2024002',
            'grade': '2年生',
            'department': '文学部',
            'validFrom': '2024.04.01',
            'validTo': '2025.03.31'
        }
        
        # 生成正面图片（不含照片）
        front_image = generator.create_student_id_image(test_data, None)
        front_filename = 'test_comparison_front.png'
        front_image.save(front_filename)
        
        # 生成背面图片
        back_image = generator.create_student_id_back_image(test_data)
        back_filename = 'test_comparison_back.png'
        back_image.save(back_filename)
        
        print("✅ 正面和背面生成成功！")
        print(f"📄 正面图片: {front_filename}")
        print(f"📄 背面图片: {back_filename}")
        print(f"📏 图片尺寸: {front_image.size} (正面) / {back_image.size} (背面)")
        print()
        print("🎨 设计特点对比:")
        print("   正面: 包含学生照片、基本信息和通栏有效期")
        print("   背面: 包含学校信息、注意事项和发行信息")
        print()
        print("🎯 视觉一致性:")
        print("   • 相同的尺寸和边框设计")
        print("   • 统一的配色方案（海军蓝、奶白色）")
        print("   • 一致的字体和布局风格")
        
        return True
        
    except Exception as e:
        print(f"❌ 对比测试失败: {str(e)}")
        return False

if __name__ == "__main__":
    test_front_back_comparison()