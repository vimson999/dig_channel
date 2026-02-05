
import os
import requests
from dotenv import load_dotenv

def check_youtube_quota():
    load_dotenv(override=True)
    api_key = os.getenv("YOUTUBE_API_KEY")
    
    if not api_key:
        print("❌ 错误: .env 中未配置 YOUTUBE_API_KEY")
        return

    print(f"🔍 正在测试 Key: {api_key[:8]}...")
    
    # 使用最小消耗 (1 point) 的接口测试
    url = "https://www.googleapis.com/youtube/v3/channels"
    params = {
        "part": "snippet",
        "id": "UCBR8-60-B28hp2BmDPdntcQ", # YouTube 官方频道
        "key": api_key
    }
    
    try:
        response = requests.get(url, params=params)
        if response.status_code == 200:
            print("✅ API 连接正常！该 Key 目前有效。")
            print("数据返回成功。")
        elif response.status_code == 403:
            error_data = response.json()
            reason = error_data.get('error', {}).get('errors', [{}])[0].get('reason', '')
            if reason == 'quotaExceeded':
                print("❌ 错误: 配额已耗尽 (Quota Exceeded)！今天无法再使用了。")
            else:
                print(f"❌ 错误: 403 被拒绝。原因: {reason}")
                print(f"详细信息: {error_data}")
        elif response.status_code == 400:
            print("❌ 错误: 400 Bad Request。通常是 API Key 无效或未在该项目启用 YouTube Data API v3。")
            print(f"详细信息: {response.text}")
        else:
            print(f"❓ 未知状态码 {response.status_code}: {response.text}")
            
    except Exception as e:
        print(f"💥 请求发生异常: {e}")

if __name__ == "__main__":
    check_youtube_quota()
