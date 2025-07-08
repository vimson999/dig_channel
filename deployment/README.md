# YouTube Analytics 部署指南

## 🚀 部署概览

YouTube Analytics 是一个专业的YouTube频道数据分析平台，支持Docker部署和传统部署方式。

## 📋 系统要求

### 硬件要求
- CPU: 2核心以上
- 内存: 4GB以上
- 存储: 20GB以上可用空间
- 网络: 稳定的互联网连接

### 软件要求
- Docker 20.10+
- Docker Compose 2.0+
- Python 3.11+ (如果使用传统部署)
- Nginx (推荐用于生产环境)

## 🔑 环境配置

### 必需的环境变量

```bash
# YouTube API密钥
export YOUTUBE_API_KEY="your_youtube_api_key_here"

# 应用密钥
export SECRET_KEY="your_secret_key_here"

# JWT密钥 (可选，默认使用SECRET_KEY)
export JWT_SECRET_KEY="your_jwt_secret_key_here"

# 允许的跨域来源 (可选)
export CORS_ORIGINS="https://yourdomain.com"
```

### 获取YouTube API密钥

1. 访问 [Google Cloud Console](https://console.cloud.google.com/)
2. 创建新项目或选择现有项目
3. 启用 YouTube Data API v3
4. 创建API密钥
5. 配置API密钥限制（推荐限制IP和API）

## 🐳 Docker部署 (推荐)

### 快速部署

```bash
# 1. 克隆项目
git clone <repository-url>
cd youtube-analytics

# 2. 设置环境变量
export YOUTUBE_API_KEY="your_api_key"
export SECRET_KEY="your_secret_key"

# 3. 运行部署脚本
./deployment/scripts/deploy.sh
```

### 手动部署

```bash
# 1. 配置环境变量
cp deployment/production.env.example deployment/production.env
# 编辑 deployment/production.env 文件

# 2. 构建和启动
docker-compose -f deployment/docker/docker-compose.yml up -d

# 3. 初始化数据库
docker-compose -f deployment/docker/docker-compose.yml exec youtube-analytics python backend/init_db.py
```

## 📊 SEO优化特性

### 已实现的SEO优化

✅ **基础SEO**
- 完整的meta标签（title, description, keywords）
- 语义化HTML结构
- 面包屑导航
- 规范化URL

✅ **社交媒体优化**
- Open Graph标签（Facebook, LinkedIn）
- Twitter Card标签
- 社交媒体图片优化

✅ **技术SEO**
- 结构化数据（JSON-LD）
- Sitemap.xml
- Robots.txt
- 移动端适配
- 页面加载速度优化

✅ **用户体验**
- 响应式设计
- 无障碍访问支持
- 快速加载
- 清晰的导航结构

### SEO文件位置

```
src/frontend/
├── sitemap.xml          # 网站地图
├── robots.txt           # 搜索引擎爬虫指令
├── site.webmanifest     # PWA清单文件
└── index.html           # 主页面（包含所有SEO标签）
```

## 🌐 生产环境配置

### Nginx配置特性

- SSL/TLS加密
- HTTP/2支持
- Gzip压缩
- 静态资源缓存
- 安全头设置
- 反向代理
- 负载均衡

### 安全特性

- HTTPS强制重定向
- 安全HTTP头
- HSTS启用
- CSP策略
- XSS防护
- 点击劫持防护

## 📈 监控和维护

### 健康检查

```bash
# 检查服务状态
curl http://localhost/health

# 查看日志
docker-compose -f deployment/docker/docker-compose.yml logs -f

# 查看容器状态
docker-compose -f deployment/docker/docker-compose.yml ps
```

### 数据备份

```bash
# 备份数据库
docker-compose -f deployment/docker/docker-compose.yml exec youtube-analytics cp /app/instance/youtube_tracker.db /app/backup/

# 备份日志
docker-compose -f deployment/docker/docker-compose.yml exec youtube-analytics tar -czf /app/backup/logs-$(date +%Y%m%d).tar.gz /app/logs/
```

### 更新部署

```bash
# 拉取最新代码
git pull origin main

# 重新部署
./deployment/scripts/deploy.sh
```

## 🔧 故障排除

### 常见问题

**1. API密钥错误**
```bash
# 检查API密钥配置
docker-compose -f deployment/docker/docker-compose.yml exec youtube-analytics env | grep YOUTUBE_API_KEY
```

**2. 数据库问题**
```bash
# 重新初始化数据库
docker-compose -f deployment/docker/docker-compose.yml exec youtube-analytics python backend/init_db.py
```

**3. 服务无法启动**
```bash
# 查看详细日志
docker-compose -f deployment/docker/docker-compose.yml logs youtube-analytics
```

**4. 网络连接问题**
```bash
# 检查端口占用
netstat -tulpn | grep :5000
netstat -tulpn | grep :80
```

### 性能优化

1. **数据库优化**
   - 定期清理旧数据
   - 添加适当的索引
   - 使用数据库连接池

2. **缓存配置**
   - 启用Redis缓存
   - 配置Nginx缓存
   - 使用CDN加速静态资源

3. **监控设置**
   - 设置服务监控
   - 配置告警通知
   - 定期性能评估

## 📞 技术支持

如需技术支持，请提供以下信息：

1. 错误日志
2. 系统配置
3. 部署环境
4. 复现步骤

---

**注意**: 请确保在生产环境中使用强密码和安全的API密钥配置。