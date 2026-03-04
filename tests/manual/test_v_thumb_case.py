import os
import sys

# 设置项目根目录
root_dir = "/Users/v9/Documents/3---work/06---dev---python/dig_ytb/dig_ytb_channel"
backend_dir = os.path.join(root_dir, "src/backend")
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from services.thumbnail_service import thumbnail_service

# 测试用例参数
main_title = "高盛研报速读\n中国车市1月疲软\n只是假象？"
sub_title = "8000元“下乡补贴”杀手锏"
date_text = "Mar 2026 | 胶囊播客"
screenshot_path = "/Users/v9/Documents/3---work/06---dev---python/dig_ytb/dig_ytb_channel/data/uploads/ScreenShot_2026-03-04_141337_417.png"

print("--- 开始运行竖版封面生成测试 ---")
try:
    output_path = thumbnail_service.generate_vertical_thumbnail(
        main_title=main_title,
        sub_title=sub_title,
        date_text=date_text,
        screenshot_path=screenshot_path,
        output_filename="test_vertical_case_0304.jpg"
    )
    print(f"✅ 生成成功！文件路径: {output_path}")
    print(f"📸 物理尺寸: 1080x1920 (9:16)")
except Exception as e:
    print(f"❌ 生成失败: {e}")
    import traceback
    traceback.print_exc()
