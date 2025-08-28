# YouTube Analytics 传统部署指南

## 🚀 部署概览

本指南适用于不使用Docker的传统Linux服务器部署方式。

## 📋 系统要求

### 操作系统
- Ubuntu 20.04 LTS 或更高版本
- CentOS 8 或更高版本
- Debian 11 或更高版本

### 硬件要求
- CPU: 2核心以上
- 内存: 4GB以上
- 存储: 20GB以上可用空间
- 网络: 稳定的互联网连接

### 软件要求
- Python 3.11+
- Nginx 1.18+
- Git
- curl

## 🔧 快速部署

### 1. 准备环境变量

```bash
# 设置必需的环境变量
export YOUTUBE_API_KEY="your_youtube_api_key_here"
export SECRET_KEY="your_secret_key_here"
export DOMAIN="your-domain.com"  # 可选，默认localhost
```

### 2. 一键部署

```bash
# 克隆项目
git clone <repository-url>
cd youtube-analytics

# 运行部署脚本
sudo ./deployment/scripts/deploy-traditional.sh
```

### 3. 配置Nginx

```bash
# 配置Nginx反向代理
sudo ./deployment/scripts/setup-nginx.sh /path/to/youtube-analytics your-domain.com
```

### 4. 配置SSL (可选但推荐)

```bash
# 配置SSL证书
sudo ./deployment/scripts/ssl-setup.sh your-domain.com admin@your-domain.com
```

## 📝 详细部署步骤

### 步骤1: 系统准备

```bash
# 更新系统
sudo apt update && sudo apt upgrade -y

# 安装基础工具
sudo apt install -y git curl wget nginx python3 python3-pip python3-venv

# 创建项目用户 (可选)
sudo useradd -m -s /bin/bash youtube-analytics
sudo usermod -aG www-data youtube-analytics
```

### 步骤2: 获取项目代码

```bash
# 克隆到生产目录
sudo git clone <repository-url> /var/www/youtube-analytics
sudo chown -R youtube-analytics:www-data /var/www/youtube-analytics
cd /var/www/youtube-analytics
```

### 步骤3: 环境配置

```bash
# 设置环境变量
export YOUTUBE_API_KEY="your_api_key"
export SECRET_KEY="$(openssl rand -hex 32)"
export DOMAIN="your-domain.com"

# 运行部署脚本
sudo -E ./deployment/scripts/deploy-traditional.sh
```

### 步骤4: Nginx配置

```bash
# 配置Nginx
sudo ./deployment/scripts/setup-nginx.sh /var/www/youtube-analytics $DOMAIN

# 测试Nginx配置
sudo nginx -t

# 重启Nginx
sudo systemctl restart nginx
```

### 步骤5: SSL配置 (生产环境必需)

```bash
# 确保域名已解析到服务器IP
dig +short $DOMAIN

# 配置SSL证书
sudo ./deployment/scripts/ssl-setup.sh $DOMAIN admin@$DOMAIN
```

## 🔧 服务管理

### 后端服务管理

```bash
# 查看服务状态
sudo systemctl status youtube-analytics

# 启动服务
sudo systemctl start youtube-analytics

# 停止服务
sudo systemctl stop youtube-analytics

# 重启服务
sudo systemctl restart youtube-analytics

# 查看日志
sudo journalctl -u youtube-analytics -f

# 查看应用日志
tail -f /var/www/youtube-analytics/logs/*.log
```

### Nginx管理

```bash
# 重新加载配置
sudo nginx -s reload

# 重启Nginx
sudo systemctl restart nginx

# 查看Nginx日志
sudo tail -f /var/log/nginx/youtube-analytics-*.log
```

## 🔍 故障排除

### 常见问题

**1. 服务启动失败**
```bash
# 查看详细错误
sudo journalctl -u youtube-analytics --no-pager

# 检查Python环境
/var/www/youtube-analytics/venv/bin/python --version

# 手动测试
cd /var/www/youtube-analytics
source venv/bin/activate
cd src/backend
python app.py
```

**2. Nginx 502错误**
```bash
# 检查后端服务是否运行
curl http://127.0.0.1:5000/health

# 检查Nginx配置
sudo nginx -t

# 查看Nginx错误日志
sudo tail -f /var/log/nginx/error.log
```

**3. SSL证书问题**
```bash
# 检查证书状态
sudo certbot certificates

# 手动续期
sudo certbot renew

# 测试SSL配置
openssl s_client -connect your-domain.com:443
```

**4. 权限问题**
```bash
# 修复文件权限
sudo chown -R youtube-analytics:www-data /var/www/youtube-analytics
sudo chmod -R 755 /var/www/youtube-analytics
sudo chmod -R 644 /var/www/youtube-analytics/src/frontend/*
```

### 性能优化

**1. 数据库优化**
```bash
# 定期备份数据库
cp /var/www/youtube-analytics/instance/youtube_tracker.db /backup/

# 数据库vacuum (SQLite)
sqlite3 /var/www/youtube-analytics/instance/youtube_tracker.db "VACUUM;"
```

**2. 日志管理**
```bash
# 设置日志轮转
sudo tee /etc/logrotate.d/youtube-analytics << EOF
/var/www/youtube-analytics/logs/*.log {
    daily
    missingok
    rotate 7
    compress
    delaycompress
    notifempty
    postrotate
        systemctl reload youtube-analytics
    endscript
}
EOF
```

**3. 监控设置**
```bash
# 安装htop监控
sudo apt install htop

# 监控系统资源
htop

# 监控磁盘使用
df -h

# 监控网络连接
netstat -tulpn | grep :5000
```

## 🔄 更新部署

### 常规更新

```bash
cd /var/www/youtube-analytics

# 备份当前版本
sudo cp -r . ../youtube-analytics-backup-$(date +%Y%m%d)

# 拉取最新代码
sudo git pull origin main

# 更新依赖
source venv/bin/activate
pip install -r requirements/production.txt

# 重启服务
sudo systemctl restart youtube-analytics
sudo systemctl reload nginx
```

### 数据库迁移

```bash
# 备份数据库
cp instance/youtube_tracker.db instance/youtube_tracker.db.backup

# 运行迁移 (如果有)
cd src/backend
python migrate.py  # 如果存在迁移脚本
```

## 📊 监控和维护

### 健康检查

```bash
# 创建健康检查脚本
sudo tee /usr/local/bin/youtube-analytics-healthcheck.sh << 'EOF'
#!/bin/bash
if curl -f http://127.0.0.1:5000/health > /dev/null 2>&1; then
    echo "$(date): Service is healthy"
else
    echo "$(date): Service is unhealthy, restarting..."
    systemctl restart youtube-analytics
fi
EOF

sudo chmod +x /usr/local/bin/youtube-analytics-healthcheck.sh

# 添加到crontab (每5分钟检查一次)
echo "*/5 * * * * /usr/local/bin/youtube-analytics-healthcheck.sh >> /var/log/youtube-analytics-health.log 2>&1" | sudo crontab -
```

### 备份策略

```bash
# 创建备份脚本
sudo tee /usr/local/bin/youtube-analytics-backup.sh << 'EOF'
#!/bin/bash
BACKUP_DIR="/backup/youtube-analytics"
DATE=$(date +%Y%m%d_%H%M%S)

mkdir -p $BACKUP_DIR

# 备份数据库
cp /var/www/youtube-analytics/instance/youtube_tracker.db $BACKUP_DIR/db_$DATE.db

# 备份配置
cp /var/www/youtube-analytics/.env $BACKUP_DIR/env_$DATE

# 压缩旧备份 (保留7天)
find $BACKUP_DIR -name "*.db" -mtime +7 -exec gzip {} \;
find $BACKUP_DIR -name "*.gz" -mtime +30 -delete

echo "Backup completed: $DATE"
EOF

sudo chmod +x /usr/local/bin/youtube-analytics-backup.sh

# 每天凌晨2点备份
echo "0 2 * * * /usr/local/bin/youtube-analytics-backup.sh >> /var/log/youtube-analytics-backup.log 2>&1" | sudo crontab -
```

## 🔒 安全配置

### 防火墙配置

```bash
# UFW配置
sudo ufw enable
sudo ufw allow ssh
sudo ufw allow 'Nginx Full'
sudo ufw status

# 或者 firewalld配置
sudo firewall-cmd --permanent --add-service=ssh
sudo firewall-cmd --permanent --add-service=http
sudo firewall-cmd --permanent --add-service=https
sudo firewall-cmd --reload
```

### 系统安全

```bash
# 禁用root SSH登录
sudo sed -i 's/PermitRootLogin yes/PermitRootLogin no/' /etc/ssh/sshd_config
sudo systemctl restart ssh

# 配置fail2ban
sudo apt install fail2ban
sudo systemctl enable fail2ban
sudo systemctl start fail2ban
```

---

部署完成后，你的YouTube Analytics工具将在 https://your-domain.com 运行！