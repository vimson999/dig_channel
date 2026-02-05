import sys
import os
import logging

# 确保能找到 backend 模块
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from services.thumbnail_service import thumbnail_service, BACKGROUNDS

logging.basicConfig(level=logging.INFO)

def test_generate_all_styles():
    print(f"🎨 开始批量生成所有样式预览图 (共 {len(BACKGROUNDS)} 款)...")
    
    # 真实场景文案
    demo_data = {
        "main": "深度复盘：英伟达 2026 Q1 财报\nAI 芯片需求见顶了吗？\n华尔街为何产生分歧？",
        "sub": "数据中心营收突破 500 亿美元，毛利率维持高位",
        "tag": "Tech Insider · 科技前沿",
        "date": "Feb 2026 | 胶囊播客"
    }
    
    output_dir = thumbnail_service.output_dir
    print(f"📂 输出目录: {output_dir}")
    
    success_count = 0
    
    for index, bg in enumerate(BACKGROUNDS):
        bg_name = bg['name']
        # 为了方便排序，文件名加前缀
        file_name = f"preview_{index:02d}_{bg_name.replace(' ', '_').replace('·', '')}.jpg"
        
        print(f"\n[{index+1}/{len(BACKGROUNDS)}] 正在渲染: {bg_name}")
        try:
            path = thumbnail_service.generate_thumbnail(
                main_title=demo_data['main'],
                sub_title=demo_data['sub'],
                date_text=demo_data['date'],
                tag_text=demo_data['tag'],
                bg_preset_index=index,
                layout='left', # 强制左对齐标准
                output_filename=file_name
            )
            print(f"   ✅ 生成成功: {os.path.basename(path)}")
            success_count += 1
        except Exception as e:
            print(f"   ❌ 生成失败: {e}")
            import traceback
            traceback.print_exc()

    print("\n" + "="*50)
    print(f"🎉 批量生成完成！成功率: {success_count}/{len(BACKGROUNDS)}")
    print(f"请前往目录查看: {output_dir}")

if __name__ == "__main__":
    test_generate_all_styles()
