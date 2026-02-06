import redis
import os
from pathlib import Path
from dotenv import load_dotenv

# 强制加载项目根目录的 .env，并覆盖现有变量
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(dotenv_path=env_path, override=True)

def test_redis_connection():
    urls = {
        "REDIS_URL (DB 14)": os.getenv("REDIS_URL"),
        "CELERY_BROKER_URL (DB 15)": os.getenv("CELERY_BROKER_URL")
    }
    
    for name, url in urls.items():
        print(f"🔍 正在测试 {name}: {url.split('@')[-1]} ...")
        try:
            # 建立连接
            r = redis.from_url(url, socket_connect_timeout=5)
            # 测试 PING
            if r.ping():
                print(f"✅ {name} 连接成功！")
                
                # 测试 写入/读取
                test_key = "test_connectivity_key"
                r.set(test_key, "success")
                val = r.get(test_key)
                if val and val.decode() == "success":
                    print(f"✅ {name} 读写测试通过！")
                    r.delete(test_key)
                else:
                    print(f"❌ {name} 读写测试失败。")
            else:
                print(f"❌ {name} PING 无响应。")
        except Exception as e:
            print(f"💥 {name} 发生错误: {e}")
        print("-" * 30)

if __name__ == "__main__":
    test_redis_connection()
