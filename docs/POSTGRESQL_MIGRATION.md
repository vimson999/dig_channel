# PostgreSQL迁移指南

本指南详细说明如何将YouTube Analytics项目从SQLite迁移到PostgreSQL数据库。

## 📋 前提条件

### 1. 安装PostgreSQL
```bash
# Ubuntu/Debian
sudo apt update
sudo apt install postgresql postgresql-contrib

# CentOS/RHEL
sudo yum install postgresql-server postgresql-contrib

# macOS (使用Homebrew)
brew install postgresql
brew services start postgresql

# Docker方式运行
docker run --name postgres-youtube \
  -e POSTGRES_DB=youtube_analytics \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=your_password \
  -p 5432:5432 \
  -d postgres:15
```

### 2. 创建数据库和用户
```sql
-- 连接PostgreSQL
psql -U postgres

-- 创建数据库
CREATE DATABASE youtube_analytics;

-- 创建用户（可选）
CREATE USER youtube_user WITH PASSWORD 'your_password';
GRANT ALL PRIVILEGES ON DATABASE youtube_analytics TO youtube_user;

-- 退出
\q
```

## 🔧 配置PostgreSQL连接

### 1. 设置环境变量
复制并配置环境变量文件：
```bash
# 复制配置模板
cp .env.postgresql .env

# 编辑配置文件
nano .env
```

### 2. 配置示例
```bash
# 启用PostgreSQL
USE_POSTGRESQL=true

# PostgreSQL连接配置
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=youtube_analytics
POSTGRES_USER=postgres
POSTGRES_PASSWORD=your_password_here

# 应用配置
SECRET_KEY=your-super-secret-key-here
JWT_SECRET_KEY=your-jwt-secret-key-here
YOUTUBE_API_KEY=your_youtube_api_key_here

# 默认管理员用户配置 (用于数据库初始化)
ADMIN_USERNAME=admin
ADMIN_EMAIL=admin@yourdomain.com
ADMIN_PASSWORD=your_secure_admin_password
ADMIN_DISPLAY_NAME=系统管理员
```

## 🚀 迁移方案

### 方案一：全新PostgreSQL安装
如果你是新用户或者不需要保留现有数据：

```bash
# 1. 复制并编辑配置文件
cp .env.postgresql .env
nano .env  # 修改PostgreSQL连接参数

# 2. 初始化PostgreSQL数据库
python scripts/init_postgresql.py

# 3. 启动应用
cd src/backend
python app.py
```

**或者使用环境变量方式：**
```bash
# 1. 设置环境变量
export USE_POSTGRESQL=true
export POSTGRES_HOST=localhost
export POSTGRES_DB=youtube_analytics
export POSTGRES_USER=postgres
export POSTGRES_PASSWORD=your_password

# 2. 初始化PostgreSQL数据库
python scripts/init_postgresql.py

# 3. 启动应用
cd src/backend
python app.py
```

### 方案二：从SQLite迁移数据
如果你有现有的SQLite数据需要迁移：

```bash
# 1. 确保SQLite数据库存在
ls -la instance/youtube_tracker.db

# 2. 设置PostgreSQL环境变量
export USE_POSTGRESQL=true
export POSTGRES_HOST=localhost
export POSTGRES_DB=youtube_analytics
export POSTGRES_USER=postgres
export POSTGRES_PASSWORD=your_password

# 3. 执行数据迁移
python scripts/migrate_to_postgresql.py

# 4. 启动应用
cd src/backend
python app.py
```

## 📊 迁移脚本功能

### `migrate_to_postgresql.py`
- ✅ 自动创建PostgreSQL表结构
- ✅ 读取SQLite中的所有数据
- ✅ 转换数据格式适配PostgreSQL
- ✅ 批量插入数据（高性能）
- ✅ 处理外键关系和序列
- ✅ 验证迁移完整性
- ✅ 错误处理和回滚

### `init_postgresql.py`  
- ✅ 创建全新数据库表结构
- ✅ 创建默认管理员用户
- ✅ 设置基础配置

## 🔍 验证迁移

### 1. 检查表结构
```sql
-- 连接数据库
psql -U postgres -d youtube_analytics

-- 查看所有表
\dt

-- 查看表结构
\d users
\d channels
\d channel_data
\d videos
\d favorite_videos
```

### 2. 检查数据
```sql
-- 统计各表记录数
SELECT 'users' as table_name, COUNT(*) as count FROM users
UNION ALL
SELECT 'channels', COUNT(*) FROM channels
UNION ALL
SELECT 'channel_data', COUNT(*) FROM channel_data
UNION ALL
SELECT 'videos', COUNT(*) FROM videos
UNION ALL
SELECT 'favorite_videos', COUNT(*) FROM favorite_videos;

-- 检查用户和频道关系
SELECT u.username, COUNT(c.id) as channel_count
FROM users u
LEFT JOIN channels c ON u.id = c.user_id
GROUP BY u.id, u.username;
```

### 3. 测试应用功能
```bash
# 启动应用
cd src/backend
python app.py

# 访问应用
curl http://localhost:5001/health

# 测试用户登录
curl -X POST http://localhost:5001/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'
```

## 🔧 性能优化

### 1. 创建索引
迁移脚本会自动创建必要的索引，但可以根据需要添加更多：

```sql
-- 为频繁查询的列创建索引
CREATE INDEX CONCURRENTLY idx_channel_data_date_desc ON channel_data (date DESC);
CREATE INDEX CONCURRENTLY idx_videos_published_at ON videos (published_at DESC);
CREATE INDEX CONCURRENTLY idx_favorite_videos_user_favorited ON favorite_videos (user_id, favorited_at DESC);
```

### 2. 配置PostgreSQL
编辑 `postgresql.conf`：
```ini
# 内存配置
shared_buffers = 256MB
work_mem = 4MB
maintenance_work_mem = 64MB

# 连接配置  
max_connections = 100

# 日志配置
log_statement = 'mod'
log_duration = on
log_min_duration_statement = 1000
```

## 🛠️ 故障排除

### 1. 连接问题
```bash
# 测试PostgreSQL连接
psql -U postgres -h localhost -d youtube_analytics

# 检查PostgreSQL状态
sudo systemctl status postgresql

# 查看PostgreSQL日志
sudo tail -f /var/log/postgresql/postgresql-*.log
```

### 2. 权限问题
```sql
-- 检查用户权限
\du

-- 赋予用户权限
GRANT ALL PRIVILEGES ON DATABASE youtube_analytics TO your_user;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO your_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO your_user;
```

### 3. 迁移失败
```bash
# 查看详细错误
python scripts/migrate_to_postgresql.py --verbose

# 重新初始化（清空数据）
python scripts/init_postgresql.py

# 检查SQLite数据
sqlite3 instance/youtube_tracker.db ".schema"
sqlite3 instance/youtube_tracker.db "SELECT name FROM sqlite_master WHERE type='table';"
```

### 4. 应用启动问题
```bash
# 检查环境变量
env | grep POSTGRES

# 检查依赖
pip install psycopg2-binary

# 查看应用日志
tail -f logs/*.log
```

## 🔄 回滚到SQLite

如果需要回滚到SQLite：

```bash
# 1. 修改环境变量
export USE_POSTGRESQL=false
# 或者删除 USE_POSTGRESQL 环境变量

# 2. 重启应用
cd src/backend
python app.py
```

## 📈 性能对比

| 特性 | SQLite | PostgreSQL |
|------|--------|------------|
| 并发写入 | 串行 | 真正的并发 |
| 数据库大小限制 | 281TB | 无限制 |
| 索引类型 | B-tree | B-tree, Hash, GiST, SP-GiST, GIN, BRIN |
| JSON支持 | 基础 | 原生支持 |
| 全文搜索 | FTS5 | 内置全文搜索 |
| 备份 | 文件复制 | pg_dump, 连续归档 |
| 监控 | 有限 | 丰富的统计信息 |

## 📝 注意事项

1. **⚠️ 备份数据**: 迁移前务必备份SQLite数据库文件
2. **🔒 密码安全**: 迁移后立即修改默认管理员密码
3. **🌐 网络配置**: 确保PostgreSQL允许应用连接
4. **💾 存储空间**: PostgreSQL可能占用更多磁盘空间
5. **🔧 维护**: 定期执行VACUUM和ANALYZE操作

## 🆘 获取帮助

如果遇到问题：
1. 检查 `logs/` 目录下的日志文件
2. 查看PostgreSQL错误日志
3. 在项目Issue页面提交问题
4. 参考PostgreSQL官方文档