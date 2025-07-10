# 🔧 故障排除指南

## 🚨 常见错误及解决方案

### 1. 依赖包缺失错误

#### 错误信息
```
ModuleNotFoundError: No module named 'jwt'
ModuleNotFoundError: No module named 'flask_sqlalchemy'
```

#### 解决方案
```bash
# 安装所有必需依赖
pip install PyJWT flask flask-sqlalchemy werkzeug python-dotenv

# 或使用requirements.txt
pip install -r requirements.txt

# 如果使用虚拟环境，确保已激活
source venv/bin/activate  # Linux/Mac
# 或
venv\Scripts\activate  # Windows
```

### 2. 数据库相关错误

#### 错误信息
```
sqlite3.OperationalError: no such table: users
sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) no such column: channels.user_id
```

#### 解决方案
```bash
# 运行数据库迁移脚本
python scripts/migrate_to_multiuser.py

# 如果迁移失败，备份并重新创建数据库
cp instance/youtube_tracker.db instance/youtube_tracker.db.backup
rm instance/youtube_tracker.db
python scripts/migrate_to_multiuser.py
```

### 3. 认证相关错误

#### 错误信息
```
401 Unauthorized
jwt.exceptions.DecodeError: Invalid token
```

#### 解决方案
```bash
# 1. 检查环境变量设置
echo $SECRET_KEY
echo $JWT_SECRET_KEY

# 2. 重新设置环境变量
export SECRET_KEY="your-secure-secret-key-here"
export JWT_SECRET_KEY="your-jwt-secret-key-here"

# 3. 清除浏览器缓存和localStorage
# 在浏览器开发者工具中运行:
localStorage.clear()
sessionStorage.clear()
```

### 4. API连接错误

#### 错误信息
```
ERR_CONNECTION_REFUSED
Network Error
CORS error
```

#### 解决方案
```bash
# 1. 检查后端服务是否运行
curl http://localhost:5000/api

# 2. 检查前端服务是否运行
curl http://localhost:8000

# 3. 检查端口是否被占用
netstat -tulpn | grep :5000
netstat -tulpn | grep :8000

# 4. 重启服务
# 后端
cd src/backend && python app.py

# 前端
cd src/frontend && python server.py
```

### 5. YouTube API错误

#### 错误信息
```
API key not valid
Quota exceeded
YouTube API未初始化
```

#### 解决方案
```bash
# 1. 检查API密钥设置
echo $YOUTUBE_API_KEY

# 2. 验证API密钥有效性
curl "https://www.googleapis.com/youtube/v3/channels?part=snippet&forUsername=YouTubeCreators&key=$YOUTUBE_API_KEY"

# 3. 设置正确的API密钥
export YOUTUBE_API_KEY="your_valid_youtube_api_key"

# 4. 检查API配额
# 访问 Google Cloud Console 查看API使用情况
```

## 🔍 调试技巧

### 1. 启用详细日志

```python
# 在 src/backend/app.py 中设置调试模式
app.run(debug=True, host='0.0.0.0', port=5000)
```

### 2. 检查数据库状态

```bash
# 连接到SQLite数据库
sqlite3 instance/youtube_tracker.db

# 查看表结构
.schema users
.schema channels
.schema videos

# 查看数据
SELECT * FROM users;
SELECT * FROM channels LIMIT 5;

# 退出
.quit
```

### 3. 浏览器开发者工具

1. **Network标签**: 检查API请求和响应
2. **Console标签**: 查看JavaScript错误
3. **Application标签**: 检查localStorage中的Token
4. **Sources标签**: 设置断点调试

### 4. 后端日志检查

```bash
# 查看Flask应用日志
tail -f logs/flask.log

# 查看调度器日志
tail -f logs/scheduler.log

# 查看前端服务器日志
tail -f logs/frontend.log
```

## 🛠️ 系统重置

### 完全重置系统

```bash
# 1. 停止所有服务
pkill -f "python.*app.py"
pkill -f "python.*server.py"

# 2. 备份重要数据
cp instance/youtube_tracker.db backup/youtube_tracker_$(date +%Y%m%d).db

# 3. 清除数据库
rm instance/youtube_tracker.db

# 4. 重新初始化
python scripts/migrate_to_multiuser.py

# 5. 重启服务
cd src/backend && python app.py &
cd src/frontend && python server.py &
```

### 重置用户认证

```bash
# 在浏览器控制台中清除认证信息
localStorage.removeItem('access_token');
localStorage.removeItem('user_info');

# 重新访问应用
window.location.href = '/login.html';
```

## 📞 获取帮助

如果上述解决方案都无法解决问题，请提供以下信息寻求帮助：

### 错误报告模板

```
**环境信息**
- 操作系统: [Windows/Mac/Linux]
- Python版本: [python --version]
- 浏览器: [Chrome/Firefox/Safari/Edge]

**错误描述**
[详细描述遇到的问题]

**错误日志**
[粘贴完整的错误信息]

**复现步骤**
1. [第一步]
2. [第二步]
3. [错误出现]

**已尝试的解决方案**
[列出已经尝试过的解决方法]
```

### 联系方式

- 📧 **邮箱**: support@youtube-analytics.com
- 🐛 **问题反馈**: GitHub Issues
- 📖 **文档**: [项目文档](docs/)

---

**💡 提示**: 大多数问题都是由于依赖包缺失或环境变量未正确设置导致的。请首先检查这两个方面！