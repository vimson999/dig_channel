import sys
import os

# 确保能找到 backend 目录
backend_path = os.path.join(os.path.dirname(__file__), '..', 'src', 'backend')
sys.path.append(backend_path)

from services.config_service import ConfigService
from app import create_app

def seed():
    # 创建 Flask app context 以后台运行
    app = create_app()
    with app.app_context():
        print("🌱 开始从 .env 初始化配置到数据库...")
        
        # 定义需要同步的配置项 (Env Key, DB Key, Type, Description)
        sync_list = [
            # Bilibili
            ('BILI_SESSDATA', 'sessdata', 'bilibili_auth', 'B站会话数据'),
            ('BILI_BILI_JCT', 'bili_jct', 'bilibili_auth', 'B站CSRF Token'),
            ('BILI_BUVID3', 'buvid3', 'bilibili_auth', 'B站浏览器标识'),
            ('BILI_DEDEUSERID', 'dedeuserid', 'bilibili_auth', 'B站用户ID'),
            
            # YouTube / Google
            ('YOUTUBE_API_KEY', 'api_key', 'youtube_auth', 'YouTube Data API Key (Primary)'),
            ('GOOGLE_API_KEYS', 'api_key_pool', 'youtube_auth', 'Google API Key Pool (Comma Separated)'),
            ('GOOGLE_CLIENT_ID', 'client_id', 'youtube_auth', 'Google OAuth Client ID'),
            ('GOOGLE_CLIENT_SECRET', 'client_secret', 'youtube_auth', 'Google OAuth Client Secret'),
            ('GOOGLE_OAUTH_CONFIG_FILE', 'oauth_config_file', 'youtube_auth', 'OAuth配置文件路径'),

            # Reddit (商机发现)
            ('REDDIT_CLIENT_ID', 'client_id', 'reddit_auth', 'Reddit Client ID'),
            ('REDDIT_CLIENT_SECRET', 'client_secret', 'reddit_auth', 'Reddit Client Secret'),
            ('REDDIT_USER_AGENT', 'user_agent', 'reddit_auth', 'Reddit User Agent'),
            ('REDDIT_ENABLED', 'enabled', 'reddit_config', 'Reddit 功能开关'),
            
            # 业务参数
            ('DEFAULT_CPM', 'default_cpm', 'business_rule', '默认CPM值'),
            ('CRAWL_INTERVAL_HOURS', 'crawl_interval', 'business_rule', '抓取间隔(小时)'),
        ]
        
        count = 0
        updated = 0
        
        for env_key, db_key, b_type, desc in sync_list:
            # 尝试从环境变量读取
            val = os.environ.get(env_key)
            
            # 特殊处理：有些值可能在脚本里硬编码传入（为了方便您这次操作）
            # 比如您刚才贴的 B 站和 Google Pool 数据，如果 .env 还没更新，这里可以做一个兜底逻辑
            # 但为了规范，我们还是只读 os.environ，请确保 .env 文件是最新的
            
            if val:
                # 检查是否已存在（避免重复 update 刷屏，虽然 set 方法有处理）
                current_val = ConfigService.get(db_key, type_group=b_type)
                
                if current_val != val:
                    success = ConfigService.set(
                        key=db_key, 
                        value=val, 
                        type_group=b_type, 
                        description=desc
                    )
                    if success:
                        print(f"✅ 已同步/更新: {env_key} -> {db_key}")
                        updated += 1
                else:
                    print(f"⏭️  已存在且一致: {env_key}")
                count += 1
            else:
                print(f"⚠️ 跳过: {env_key} (.env 中未设置)")
                
        print(f"\n✨ 初始化完成！处理了 {count} 项，更新了 {updated} 项。")

if __name__ == "__main__":
    seed()