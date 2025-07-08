#!/bin/bash

# SSL证书配置脚本 (Let's Encrypt)
set -e

echo "🔒 配置SSL证书..."

# 检查是否为root用户
if [ "$EUID" -ne 0 ]; then
    echo "❌ 请使用sudo运行此脚本"
    exit 1
fi

# 检查参数
if [ -z "$1" ]; then
    echo "❌ 用法: $0 <域名> [邮箱]"
    echo "   例如: $0 youtube-analytics.example.com admin@example.com"
    exit 1
fi

DOMAIN="$1"
EMAIL="${2:-admin@${DOMAIN}}"

echo "🌍 域名: $DOMAIN"
echo "📧 邮箱: $EMAIL"

# 检查域名是否解析到当前服务器
echo "🔍 检查域名解析..."
DOMAIN_IP=$(dig +short $DOMAIN)
SERVER_IP=$(curl -s ifconfig.me || curl -s ipinfo.io/ip)

if [ "$DOMAIN_IP" != "$SERVER_IP" ]; then
    echo "⚠️ 警告: 域名 $DOMAIN 未解析到当前服务器"
    echo "   域名IP: $DOMAIN_IP"
    echo "   服务器IP: $SERVER_IP"
    read -p "是否继续? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# 安装certbot
echo "📦 安装certbot..."
if ! command -v certbot &> /dev/null; then
    apt update
    apt install -y certbot python3-certbot-nginx
fi

# 检查Nginx配置
echo "🔧 检查Nginx配置..."
if ! nginx -t; then
    echo "❌ Nginx配置有误，请先修复"
    exit 1
fi

# 获取SSL证书
echo "🔐 获取SSL证书..."
certbot --nginx \
    --non-interactive \
    --agree-tos \
    --email "$EMAIL" \
    --domains "$DOMAIN,www.$DOMAIN" \
    --redirect

# 检查证书是否成功获取
if [ $? -eq 0 ]; then
    echo "✅ SSL证书获取成功!"
else
    echo "❌ SSL证书获取失败!"
    exit 1
fi

# 设置自动续期
echo "🔄 设置SSL证书自动续期..."
(crontab -l 2>/dev/null; echo "0 12 * * * /usr/bin/certbot renew --quiet") | crontab -

# 测试自动续期
echo "🧪 测试SSL证书续期..."
certbot renew --dry-run

# 重启Nginx
echo "🔄 重启Nginx..."
systemctl restart nginx

# 验证SSL配置
echo "🔍 验证SSL配置..."
if command -v openssl &> /dev/null; then
    echo "📋 SSL证书信息:"
    echo | openssl s_client -servername "$DOMAIN" -connect "$DOMAIN:443" 2>/dev/null | openssl x509 -noout -dates
fi

echo "🎉 SSL配置完成!"
echo ""
echo "🔒 SSL信息:"
echo "  - HTTPS地址: https://$DOMAIN"
echo "  - 证书路径: /etc/letsencrypt/live/$DOMAIN/"
echo "  - 自动续期: 已配置 (每天12:00检查)"
echo ""
echo "🔧 管理命令:"
echo "  手动续期: sudo certbot renew"
echo "  查看证书: sudo certbot certificates"
echo "  撤销证书: sudo certbot revoke --cert-path /etc/letsencrypt/live/$DOMAIN/cert.pem"
echo ""
echo "🧪 SSL测试工具:"
echo "  https://www.ssllabs.com/ssltest/analyze.html?d=$DOMAIN"