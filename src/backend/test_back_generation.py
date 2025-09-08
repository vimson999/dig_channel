#!/usr/bin/env python3
"""
测试背面生成功能
"""

from api.student_id_generator import StudentIdGenerator

def test_back_generation():
    """测试背面生成功能"""
    print("🧪 测试学生证背面生成功能...")
    
    try:
        # 创建生成器
        generator = StudentIdGenerator()
        
        # 测试数据
        test_data = {
            'schoolName': '東京大学',
            'studentName': '山田太郎',
            'studentNameKana': 'やまだ たろう',
            'studentId': '2024001',
            'grade': '3年生',
            'department': '工学部'
        }
        
        # 生成背面图片
        back_image = generator.create_student_id_back_image(test_data)
        
        # 保存测试图片
        test_filename = 'test_student_id_back.png'
        back_image.save(test_filename)
        
        print(f"✅ 背面生成成功！测试图片已保存为: {test_filename}")
        print(f"📏 图片尺寸: {back_image.size}")
        
        return True
        
    except Exception as e:
        print(f"❌ 背面生成测试失败: {str(e)}")
        return False

if __name__ == "__main__":
    test_back_generation()