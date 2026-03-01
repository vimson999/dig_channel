#!/usr/bin/env python3
"""
山田さくら测试数据生成
创建石川県立看護大学的测试学生证
"""

from api.student_id_generator import StudentIdGenerator

def generate_sakura_test():
    """生成山田さくら的测试学生证"""
    print("🌸 生成山田さくら的测试学生证...")
    
    try:
        generator = StudentIdGenerator()
        
        # 山田さくら的测试数据
        sakura_data = {
            'schoolName': '石川県立看護大学',
            'studentName': '山田 さくら',
            'studentNameKana': 'やまだ さくら',
            'studentId': 'N2023-0456',
            'grade': '2年生',
            'department': '看護学部 看護学科',
            'validFrom': '2023.04.01',
            'validTo': '2027.03.31'
        }
        
        print("📊 学生信息:")
        for key, value in sakura_data.items():
            print(f"   {key}: {value}")
        
        # 生成正面
        front_image = generator.create_student_id_image(sakura_data, None)
        front_filename = 'sakura_yamada_front.png'
        front_image.save(front_filename)
        
        # 生成背面
        back_image = generator.create_student_id_back_image(sakura_data)
        back_filename = 'sakura_yamada_back.png'
        back_image.save(back_filename)
        
        print(f"✅ 正面生成成功: {front_filename}")
        print(f"✅ 背面生成成功: {back_filename}")
        
        print("\n🎨 特色显示:")
        print("   • 学校名称: 石川県立看護大学 (显示在蓝色顶栏)")
        print("   • 学生姓名: 山田 さくら (平假名)")
        print("   • 学籍番号: N2023-0456 (护理学院格式)")
        print("   • 专业信息: 看護学部 看護学科")
        print("   • 有效期限: 2023.04.01 ～ 2027.03.31 (4年制)")
        
        print(f"\n📏 图片尺寸: {front_image.size} (标准信用卡尺寸)")
        
        return True
        
    except Exception as e:
        print(f"❌ 生成失败: {str(e)}")
        return False

if __name__ == "__main__":
    generate_sakura_test()