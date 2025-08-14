{
"SELECT * from channel_data cd \nlimit 100": [
	{
		"id" : 1,
		"channel_id" : 1,
		"date" : "2025-07-07",
		"subscribers" : 31200,
		"total_views" : 1542838,
		"video_count" : 190,
		"subscriber_change" : 0,
		"view_change" : 0,
		"video_change" : 0,
		"estimated_revenue" : 0,
		"created_at" : "2025-07-07 02:23:49.470045"
	},
	{
		"id" : 2,
		"channel_id" : 2,
		"date" : "2025-07-07",
		"subscribers" : 68700,
		"total_views" : 2772749,
		"video_count" : 210,
		"subscriber_change" : 0,
		"view_change" : 0,
		"video_change" : 0,
		"estimated_revenue" : 0,
		"created_at" : "2025-07-07 02:25:08.398790"
	},
	{
		"id" : 3,
		"channel_id" : 3,
		"date" : "2025-07-07",
		"subscribers" : 4130,
		"total_views" : 75677,
		"video_count" : 6,
		"subscriber_change" : 0,
		"view_change" : 0,
		"video_change" : 0,
		"estimated_revenue" : 0,
		"created_at" : "2025-07-07 02:26:41.322830"
	},
	{
		"id" : 4,
		"channel_id" : 4,
		"date" : "2025-07-07",
		"subscribers" : 29100,
		"total_views" : 926599,
		"video_count" : 116,
		"subscriber_change" : 0,
		"view_change" : 0,
		"video_change" : 0,
		"estimated_revenue" : 0,
		"created_at" : "2025-07-07 02:27:25.347532"
	},
	{
		"id" : 5,
		"channel_id" : 5,
		"date" : "2025-07-07",
		"subscribers" : 47200,
		"total_views" : 20970094,
		"video_count" : 1895,
		"subscriber_change" : 0,
		"view_change" : 0,
		"video_change" : 0,
		"estimated_revenue" : 0,
		"created_at" : "2025-07-07 02:45:50.743782"
	},
	{
		"id" : 6,
		"channel_id" : 6,
		"date" : "2025-07-07",
		"subscribers" : 78000,
		"total_views" : 12261919,
		"video_count" : 691,
		"subscriber_change" : 0,
		"view_change" : 0,
		"video_change" : 0,
		"estimated_revenue" : 0,
		"created_at" : "2025-07-07 02:46:18.664841"
	}
]}



{
"\nSELECT * from channels c \nlimit 100": [
	{
		"id" : 1,
		"name" : "AI超元域",
		"channel_id" : "UCIomFkAj4Vq_rGX2Jot7D8A",
		"url" : "https:\/\/www.youtube.com\/@AIsuperdomain",
		"cpm" : 9,
		"category" : "科技",
		"is_active" : 1,
		"created_at" : "2025-07-07 02:23:49.307045",
		"updated_at" : "2025-07-07 02:23:49.307059"
	},
	{
		"id" : 2,
		"name" : "技术爬爬虾  TechShrimp",
		"channel_id" : "UCa6D2k5qhpOI9I-WT8fpd6g",
		"url" : "https:\/\/www.youtube.com\/@tech-shrimp",
		"cpm" : 9,
		"category" : "科技",
		"is_active" : 1,
		"created_at" : "2025-07-07 02:25:08.227180",
		"updated_at" : "2025-07-07 02:25:08.227187"
	},
	{
		"id" : 3,
		"name" : "海拉鲁编程客",
		"channel_id" : "UCPrN2OUsJHOha26GyKOm5HA",
		"url" : "https:\/\/www.youtube.com\/@hylarucoder",
		"cpm" : 9,
		"category" : "科技",
		"is_active" : 1,
		"created_at" : "2025-07-07 02:26:41.158854",
		"updated_at" : "2025-07-07 02:26:41.158863"
	},
	{
		"id" : 4,
		"name" : "huangyihe",
		"channel_id" : "UCPpdGTNbIKdiWgxCrbka4Zw",
		"url" : "https:\/\/www.youtube.com\/@huanyihe777",
		"cpm" : 9,
		"category" : "科技",
		"is_active" : 1,
		"created_at" : "2025-07-07 02:27:25.193534",
		"updated_at" : "2025-07-07 02:27:25.193544"
	},
	{
		"id" : 5,
		"name" : "蛋小黄",
		"channel_id" : "UCwDZlUQvik6mSR1f62a0Q9w",
		"url" : "https:\/\/www.youtube.com\/@蛋小黄",
		"cpm" : 6,
		"category" : "转发",
		"is_active" : 1,
		"created_at" : "2025-07-07 02:45:50.577352",
		"updated_at" : "2025-07-07 02:45:50.577362"
	},
	{
		"id" : 6,
		"name" : "硬核刘大",
		"channel_id" : "UCCeMdcZw1LJsAGKeoYXZw6A",
		"url" : "https:\/\/www.youtube.com\/@硬核刘大",
		"cpm" : 6,
		"category" : "转发",
		"is_active" : 1,
		"created_at" : "2025-07-07 02:46:18.492955",
		"updated_at" : "2025-07-07 02:46:18.492961"
	}
]}






# YouTube频道数据追踪工具 - 环境变量配置示例
# 复制此文件为 .env 并填入实际的配置值

# ===========================================
# YouTube API配置 (必填)
# ===========================================
# YouTube Data API v3 密钥
# 获取方式: https://console.developers.google.com/
# 1. 创建项目或选择现有项目
# 2. 启用 YouTube Data API v3
# 3. 创建凭据 > API密钥
# 4. 复制API密钥到下方
YOUTUBE_API_KEY=AIzaSyCz1pMP7uzrNse6eim6JgFafIGYOmzyBA0

# ===========================================
# 数据库配置 (可选，默认使用SQLite)
# ===========================================
# 数据库类型 (sqlite/mysql/postgresql)
DATABASE_TYPE=sqlite

# SQLite数据库文件路径 (相对于项目根目录)
SQLITE_DATABASE_PATH=instance/youtube_tracker.db

# MySQL/PostgreSQL数据库连接配置 (如果使用)
# DATABASE_URL=mysql://username:password@localhost:3306/youtube_tracker
# DATABASE_URL=postgresql://username:password@localhost:5432/youtube_tracker

# ===========================================
# Flask应用配置 (可选)
# ===========================================
# Flask应用密钥 (用于会话加密等)
SECRET_KEY=dev_secret_key_for_testing

# 运行环境 (development/production)
FLASK_ENV=development

# 调试模式 (True/False)
DEBUG=True

# 服务器配置
HOST=0.0.0.0
PORT=5001

# ===========================================
# 数据抓取配置 (可选)
# ===========================================
# 默认CPM值 (每千次展示成本)
DEFAULT_CPM=1.00

# 数据抓取间隔 (小时)
CRAWL_INTERVAL_HOURS=8

# 最大重试次数
MAX_RETRY_ATTEMPTS=3

# 请求超时时间 (秒)
REQUEST_TIMEOUT=30

# ===========================================
# 日志配置 (可选)
# ===========================================
# 日志级别 (DEBUG/INFO/WARNING/ERROR)
LOG_LEVEL=INFO

# 日志文件路径
LOG_FILE=logs/app.log

# 是否输出到控制台
LOG_TO_CONSOLE=True

# ===========================================
# 缓存配置 (可选)
# ===========================================
# Redis连接URL (如果使用Redis缓存)
# REDIS_URL=redis://localhost:6379/0

# 缓存过期时间 (秒)
CACHE_TIMEOUT=3600

# ===========================================
# 安全配置 (生产环境必填)
# ===========================================
# CORS允许的域名 (逗号分隔)
CORS_ORIGINS=http://localhost:3000,http://localhost:5001

# API速率限制 (每分钟请求数)
API_RATE_LIMIT=100

# JWT令牌过期时间 (小时)
JWT_EXPIRATION_HOURS=24

# ===========================================
# 监控和报警配置 (可选)
# ===========================================
# 邮件通知配置
# EMAIL_SMTP_SERVER=smtp.gmail.com
# EMAIL_SMTP_PORT=587
# EMAIL_USERNAME=your_email@gmail.com
# EMAIL_PASSWORD=your_app_password
# EMAIL_FROM=your_email@gmail.com
# EMAIL_TO=admin@example.com

# Webhook通知URL
# WEBHOOK_URL=https://hooks.slack.com/services/...

# ===========================================
# 性能配置 (可选)
# ===========================================
# 数据库连接池大小
DB_POOL_SIZE=10

# 并发工作进程数
WORKER_PROCESSES=2

# 每个进程的线程数
WORKER_THREADS=2

# ===========================================
# 开发配置 (仅开发环境)
# ===========================================
# 是否启用SQL查询日志
SQL_ECHO=False

# 是否启用Flask工具栏
FLASK_TOOLBAR_ENABLED=False

# 是否启用热重载
FLASK_RELOAD=True

# ===========================================
# 配置说明
# ===========================================
# 1. 必填项目: YOUTUBE_API_KEY
# 2. 建议修改: SECRET_KEY (生产环境)
# 3. 可选配置: 其他所有配置都有默认值
# 4. 配置文件位置: 项目根目录/.env
# 5. 重启应用: 修改配置后需要重启Flask应用

# ===========================================
# 常见问题
# ===========================================
# Q: YouTube API密钥在哪里获取?
# A: https://console.developers.google.com/ -> 创建项目 -> 启用YouTube Data API v3 -> 创建API密钥

# Q: 如何知道配置是否正确?
# A: 启动应用后访问 http://localhost:5001/health 检查健康状态

# Q: 数据库文件在哪里?
# A: SQLite数据库文件默认在 instance/youtube_tracker.db

# Q: 如何查看日志?
# A: 日志默认输出到控制台，也可以配置LOG_FILE输出到文件

# Q: 如何修改端口?
# A: 修改PORT配置，同时需要修改前端js/config.js中的API_BASE_URL