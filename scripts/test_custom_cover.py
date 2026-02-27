
import sys
import os
import logging

# Ensure backend modules can be found
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src', 'backend'))

from services.thumbnail_service import ThumbnailService

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def test_final_design():
    project_root = "/Users/v9/Documents/3---work/06---dev---python/dig_ytb/dig_ytb_channel"
    asset_dir = os.path.join(project_root, 'assets', 'cover')
    service = ThumbnailService(asset_dir=asset_dir)
    
    # THE SPECIFIC TARGET IMAGE
    screenshot_bg = "/Users/v9/Documents/3---work/06---dev---python/dig_ytb/dig_ytb_channel/data/uploads/ScreenShot_2026-02-11_101151_048_1770775933.png"
    
    if not os.path.exists(screenshot_bg):
        print(f"❌ Error: Missing test screenshot at {screenshot_bg}")
        return

    test_data = {
        "main_title": """高盛重磅研报：
2026 中国半导体全景图""",
        "sub_title": "详解 14nm 以下制程突破与封测产业链投资机会",
        "tag_text": "应被隐藏", 
        "date_text": "Feb 2026 | 胶囊播客",
        "custom_bg_path": screenshot_bg,
        "output_filename": "test_FINAL_YELLOW_WHITE_LAYOUT.jpg"
    }

    print(f"🎨 正在生成最终版后端渲染封面...")
    try:
        path = service.generate_thumbnail(
            main_title=test_data['main_title'],
            sub_title=test_data['sub_title'],
            tag_text=test_data['tag_text'],
            date_text=test_data['date_text'],
            custom_bg_path=test_data['custom_bg_path'],
            output_filename=test_data['output_filename']
        )
        print(f"✅ 后端渲染成功！")
        print(f"📍 图片路径: {path}")
        print(f"🔍 请检查：1. 蒙层高度(48%) 2. 黄主白副配色 3. 标签是否隐藏")
    except Exception as e:
        print(f"❌ 生成失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_final_design()
