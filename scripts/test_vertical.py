import os
import sys
import logging
logging.basicConfig(level=logging.INFO)

backend_dir = os.path.join(os.getcwd(), 'src', 'backend')
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from services.video_editor import video_editor_service

def test():
    image = "assets/cover/Gemini_Generated_Image_ifcvkkifcvkkifcv.png"
    audio = "data/uploads/t1_1773741301070.mp3"
    subs = [[
        {"start": 0.0, "end": 2.0, "text": "测试竖屏字幕一"},
        {"start": 2.0, "end": 4.0, "text": "测试竖屏字幕二"}
    ]]
    
    out = video_editor_service.process_vertical_video(image, [audio], subs, "test_v_ffmpeg.mp4")
    print("OUTPUT:", out)

if __name__ == "__main__":
    test()
