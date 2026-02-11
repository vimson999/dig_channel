import os
import sys
import logging
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 设置路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(project_root, 'src', 'backend'))

# 配置日志到控制台
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)

def test_upload():
    from services.youtube.uploader import YouTubeUploader
    
    print("\n" + "="*50)
    print("🚀 开始 YouTube 上传逻辑独立测试")
    print("="*50)
    
    # 打印当前代理情况
    proxy = os.environ.get('HTTPS_PROXY') or os.environ.get('HTTP_PROXY')
    print(f"当前环境代理: {proxy}")
    
    try:
        print("\n1. 正在初始化 Uploader...")
        uploader = YouTubeUploader()
        print("✅ 初始化成功！")
        
        print("\n2. 正在测试 API 调用（获取频道信息）...")
        # 直接调用 service 测试连通性
        channels_response = uploader.service.channels().list(
            part='snippet',
            mine=True
        ).execute()
        
        if 'items' in channels_response and len(channels_response['items']) > 0:
            print(f"✅ API 调用成功！当前频道名称: {channels_response['items'][0]['snippet']['title']}")
        else:
            print("⚠️ API 调用成功，但未找到频道信息。")

    except Exception as e:
        print(f"\n❌ 测试失败！")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_upload()