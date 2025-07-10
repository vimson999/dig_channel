# 🎯 YouTube Analytics - 多用户数据分析平台

<div align="center">

![YouTube Analytics](https://img.shields.io/badge/YouTube-Analytics-red?style=for-the-badge&logo=youtube)
![Multi-User](https://img.shields.io/badge/Multi--User-System-blue?style=for-the-badge&logo=users)
![Flask](https://img.shields.io/badge/Flask-Backend-green?style=for-the-badge&logo=flask)
![JWT](https://img.shields.io/badge/JWT-Authentication-orange?style=for-the-badge&logo=jsonwebtokens)

**专业的多用户YouTube频道数据分析平台**

*实时追踪频道收入、订阅增长、播放量趋势，支持用户注册、数据隔离和权限管理*

</div>

---

## ✨ 主要特性

### 🔐 多用户系统
- **用户注册与登录**: 完整的用户认证系统
- **数据完全隔离**: 每个用户只能访问自己的频道数据
- **JWT Token认证**: 安全的会话管理
- **权限分级**: 管理员和普通用户权限区分
- **自动重定向**: 未登录用户自动跳转到登录页面

### 📊 数据分析功能
- **实时数据同步**: 自动获取YouTube频道最新数据
- **多维度分析**: 收入、订阅、播放量全方位分析
- **趋势图表**: 交互式ECharts图表展示数据趋势
- **排行榜系统**: 多种排行榜对比分析
- **数据导出**: 支持CSV格式数据导出

### 🎨 用户界面
- **现代化设计**: 使用Tailwind CSS的美观界面
- **响应式布局**: 完美适配桌面和移动设备
- **实时更新**: 无需刷新页面的数据更新
- **用户友好**: 直观的操作流程和清晰的导航

### 🛡️ 安全特性
- **密码哈希**: 使用Werkzeug安全存储密码
- **Token管理**: JWT令牌自动过期和刷新
- **API保护**: 所有API端点都需要认证
- **数据隔离**: 严格的用户数据访问控制
- **错误处理**: 完善的错误处理和用户提示

---

## 🚀 快速开始

### 📋 系统要求

- **Python**: 3.11 或更高版本
- **数据库**: SQLite (默认) 或 PostgreSQL/MySQL
- **浏览器**: 现代浏览器 (Chrome, Firefox, Safari, Edge)

### ⚡ 快速安装

```bash
# 1. 克隆项目
git clone <repository-url>
cd youtube-analytics

# 2. 安装依赖
pip install -r requirements.txt

# 3. 设置环境变量
export YOUTUBE_API_KEY="your_youtube_api_key_here"
export SECRET_KEY="your_secret_key_here"

# 4. 运行数据库迁移 (设置多用户系统)
python scripts/migrate_to_multiuser.py

# 5. 启动后端服务
cd src/backend
python app.py

# 6. 启动前端服务 (新终端)
cd src/frontend
python server.py
```

### 🌐 访问应用

- **应用地址**: http://localhost:8000
- **默认管理员**: 用户名 `admin`, 密码 `admin123`
- **API文档**: http://localhost:5000/api

**⚠️ 重要**: 首次登录后请立即修改默认密码！

---

## 📁 项目结构

```
youtube-analytics/
├── 📁 src/
│   ├── 📁 backend/           # Flask后端应用
│   │   ├── 📁 apis/          # RESTful API接口
│   │   ├── 📁 models/        # 数据库模型
│   │   ├── 📁 services/      # 业务逻辑服务
│   │   ├── 📁 utils/         # 工具函数
│   │   └── 📄 app.py         # 应用入口
│   └── 📁 frontend/          # 前端应用
│       ├── 📁 js/            # JavaScript文件
│       ├── 📁 css/           # 样式文件
│       ├── 📄 index.html     # 主页面
│       ├── 📄 login.html     # 登录页面
│       └── 📄 register.html  # 注册页面
├── 📁 scripts/               # 工具脚本
├── 📁 deployment/            # 部署配置
├── 📁 docs/                  # 项目文档
└── 📄 requirements.txt       # Python依赖
```

---

## 🔧 配置说明

### 环境变量

| 变量名 | 必需 | 说明 | 默认值 |
|--------|------|------|--------|
| `YOUTUBE_API_KEY` | ✅ | YouTube Data API v3 密钥 | - |
| `SECRET_KEY` | ✅ | Flask应用密钥 | - |
| `JWT_SECRET_KEY` | ❌ | JWT令牌密钥 | 使用SECRET_KEY |
| `DATABASE_URL` | ❌ | 数据库连接URL | SQLite数据库 |

### 获取YouTube API密钥

1. 访问 [Google Cloud Console](https://console.cloud.google.com/)
2. 创建新项目或选择现有项目
3. 启用 **YouTube Data API v3**
4. 创建凭据 → API密钥
5. 设置API密钥限制（推荐）

---

## 👥 多用户系统说明

### 用户类型

- **👤 普通用户**: 
  - 注册个人账户
  - 管理自己的YouTube频道
  - 查看个人数据分析
  - 导出个人数据

- **👨‍💼 管理员用户**:
  - 访问所有用户数据（用于系统管理）
  - 查看系统整体统计
  - 用户管理功能

### 数据隔离

每个用户拥有完全独立的数据空间：
- ✅ 频道数据按用户隔离
- ✅ 视频数据按用户隔离  
- ✅ 分析报告按用户隔离
- ✅ API接口权限控制

### 安全保障

- 🔐 密码哈希存储
- 🎫 JWT令牌认证
- 🛡️ API权限验证
- 🔒 数据访问控制

---

## 📊 功能特性

### 数据分析功能

| 功能 | 描述 | 状态 |
|------|------|------|
| **频道管理** | 添加、编辑、删除YouTube频道 | ✅ |
| **数据同步** | 自动同步YouTube频道数据 | ✅ |
| **趋势分析** | 收入、订阅、播放量趋势图表 | ✅ |
| **排行榜** | 多维度排行榜展示 | ✅ |
| **数据导出** | CSV格式数据导出 | ✅ |
| **实时更新** | 无刷新数据更新 | ✅ |

### 用户界面特性

| 功能 | 描述 | 状态 |
|------|------|------|
| **响应式设计** | 适配各种屏幕尺寸 | ✅ |
| **交互式图表** | ECharts可视化图表 | ✅ |
| **用户认证界面** | 登录注册页面 | ✅ |
| **导航系统** | 清晰的页面导航 | ✅ |
| **错误提示** | 友好的错误信息显示 | ✅ |
| **加载状态** | 数据加载进度指示 | ✅ |

---

## 🐳 Docker部署

详细的Docker部署说明请参考 [部署文档](deployment/README.md)

```bash
# 快速Docker部署
docker-compose up -d

# 初始化多用户系统
docker-compose exec app python scripts/migrate_to_multiuser.py
```

---

## 📈 API文档

### 认证接口

- `POST /api/v1/auth/register` - 用户注册
- `POST /api/v1/auth/login` - 用户登录
- `POST /api/v1/auth/logout` - 用户登出
- `GET /api/v1/auth/verify` - 验证令牌

### 频道管理

- `GET /api/v1/channels` - 获取用户频道列表
- `POST /api/v1/channels` - 添加新频道
- `PUT /api/v1/channels/{id}` - 更新频道信息
- `DELETE /api/v1/channels/{id}` - 删除频道

### 数据查询

- `GET /api/v1/data/table` - 获取表格数据
- `GET /api/v1/data/summary` - 获取统计摘要
- `GET /api/v1/rankings/daily-revenue` - 日收入排行榜

所有API都需要JWT认证，数据按用户自动过滤。

---

## 🤝 贡献指南

我们欢迎社区贡献！请遵循以下步骤：

1. **Fork** 本项目
2. 创建功能分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 开启 **Pull Request**

---

## 📄 开源协议

本项目采用 MIT 协议 - 查看 [LICENSE](LICENSE) 文件了解详情

---

## 📞 技术支持

- 📧 **邮箱**: support@youtube-analytics.com
- 📖 **文档**: [完整文档](docs/)
- 🐛 **问题反馈**: [GitHub Issues](https://github.com/your-repo/issues)

---

<div align="center">

**⭐ 如果这个项目对你有帮助，请给我们一个星标！**

*让每个人都能轻松分析YouTube频道数据* 🎯

</div>