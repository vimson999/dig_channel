#!/bin/bash

# Nginx配置脚本
set -e

echo "🌐 配置Nginx反向代理..."

# 检查是否为root用户
if [ "$EUID" -ne 0 ]; then
    echo "❌ 请使用sudo运行此脚本"
    exit 1
fi

# 获取项目路径
if [ -z "$1" ]; then
    echo "❌ 用法: $0 <项目绝对路径> [域名]"
    echo "   例如: $0 /var/www/youtube-analytics youtube-analytics.example.com"
    exit 1
fi

PROJECT_PATH="$1"
DOMAIN="${2:-localhost}"

echo "📂 项目路径: $PROJECT_PATH"
echo "🌍 域名: $DOMAIN"

# 检查项目路径是否存在
if [ ! -d "$PROJECT_PATH" ]; then
    echo "❌ 项目路径不存在: $PROJECT_PATH"
    exit 1
fi

# 安装Nginx (如果未安装)
echo "📦 检查Nginx安装..."
if ! command -v nginx &> /dev/null; then
    echo "🔽 安装Nginx..."
    apt update
    apt install -y nginx
fi

# 检查Nginx配置目录
if [ ! -d "/etc/nginx/sites-available" ]; then
    mkdir -p /etc/nginx/sites-available
    mkdir -p /etc/nginx/sites-enabled
fi

# 创建Nginx配置文件
echo "📝 创建Nginx配置..."
cat > /etc/nginx/sites-available/youtube-analytics << EOF
# YouTube Analytics Nginx配置

# 上游后端服务
upstream youtube_analytics_backend {
    server 127.0.0.1:8084;
    keepalive 32;
}

# HTTP服务器
server {
    listen 80;
    server_name $DOMAIN www.$DOMAIN;

    # 前端静态文件目录
    root $PROJECT_PATH/src/frontend;
    index index.html;

    # 客户端最大上传大小
    client_max_body_size 10M;

    # Gzip压缩
    gzip on;
    gzip_vary on;
    gzip_min_length 1024;
    gzip_types
        text/plain
        text/css
        text/xml
        text/javascript
        text/html
        application/javascript
        application/xml+rss
        application/json
        image/svg+xml;

    # 安全头
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "no-referrer-when-downgrade" always;

    # 静态文件缓存
    location ~* \.(css|js|png|jpg|jpeg|gif|ico|svg|woff|woff2|ttf|eot)$ {
        expires 1y;
        add_header Cache-Control "public, immutable";
        access_log off;
    }

    # 前端路由处理
    location / {
        try_files \$uri \$uri/ @fallback;
    }

    # 前端fallback到index.html (SPA路由)
    location @fallback {
        try_files /index.html =404;
    }

    # API代理到后端
    location = /api/audio-transcriptions/jobs {
        client_max_body_size 2050M;
        proxy_request_buffering off;
        proxy_pass http://youtube_analytics_backend;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_send_timeout 3600s;
        proxy_read_timeout 3600s;
    }

    location /api/ {
        proxy_pass http://youtube_analytics_backend;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_cache_bypass \$http_upgrade;
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }

    # 健康检查
    location /health {
        proxy_pass http://youtube_analytics_backend/health;
        access_log off;
    }

    # robots.txt
    location = /robots.txt {
        try_files \$uri =404;
        access_log off;
    }

    # sitemap.xml
    location = /sitemap.xml {
        try_files \$uri =404;
        access_log off;
    }

    # 隐藏文件和目录
    location ~ /\. {
        deny all;
        access_log off;
        log_not_found off;
    }

    # 禁止访问敏感文件
    location ~* \.(env|log|ini|conf|bak|old|tmp|py|pyc)$ {
        deny all;
        access_log off;
        log_not_found off;
    }

    # 日志配置
    access_log /var/log/nginx/youtube-analytics-access.log;
    error_log /var/log/nginx/youtube-analytics-error.log;
}
EOF

# 创建软链接启用站点
echo "🔗 启用Nginx站点..."
ln -sf /etc/nginx/sites-available/youtube-analytics /etc/nginx/sites-enabled/

# 删除默认站点配置（如果存在）
if [ -f "/etc/nginx/sites-enabled/default" ]; then
    echo "🗑️ 删除默认站点配置..."
    rm /etc/nginx/sites-enabled/default
fi

# 测试Nginx配置
echo "🧪 测试Nginx配置..."
if nginx -t; then
    echo "✅ Nginx配置测试通过"
else
    echo "❌ Nginx配置测试失败"
    exit 1
fi

# 重启Nginx
echo "🔄 重启Nginx..."
systemctl restart nginx
systemctl enable nginx

# 检查Nginx状态
if systemctl is-active --quiet nginx; then
    echo "✅ Nginx启动成功!"
else
    echo "❌ Nginx启动失败!"
    systemctl status nginx
    exit 1
fi

# 设置防火墙规则
echo "🛡️ 配置防火墙..."
if command -v ufw &> /dev/null; then
    ufw allow 'Nginx Full'
    ufw allow 22/tcp  # SSH
elif command -v firewall-cmd &> /dev/null; then
    firewall-cmd --permanent --add-service=http
    firewall-cmd --permanent --add-service=https
    firewall-cmd --reload
fi

echo "🎉 Nginx配置完成!"
echo ""
echo "📝 访问信息:"
echo "  - 网站地址: http://$DOMAIN"
if [ "$DOMAIN" != "localhost" ]; then
    echo "  - 本地测试: http://localhost"
fi
echo "  - API地址: http://$DOMAIN/api"
echo "  - 健康检查: http://$DOMAIN/health"
echo ""
echo "📊 管理命令:"
echo "  重启Nginx: sudo systemctl restart nginx"
echo "  查看状态: sudo systemctl status nginx"
echo "  查看日志: sudo tail -f /var/log/nginx/youtube-analytics-*.log"
echo ""
if [ "$DOMAIN" != "localhost" ]; then
    echo "🔒 配置SSL证书 (推荐):"
    echo "  sudo apt install certbot python3-certbot-nginx"
    echo "  sudo certbot --nginx -d $DOMAIN -d www.$DOMAIN"
fi
