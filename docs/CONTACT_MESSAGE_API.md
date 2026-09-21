# 网站留言异步邮件接口

同一台服务器上的其他项目可通过本接口提交访客留言。接口先将留言写入
`workflow_notification_deliveries` 持久化 Outbox，再投递到 `workflow_notify`
Celery 队列；实际邮件继续使用现有 SMTP 配置发送。

## 配置

```env
# 至少 32 个字符，建议用 secrets.token_urlsafe(48) 独立生成
CONTACT_API_KEY=replace-with-a-random-secret
CONTACT_API_ALLOWED_IPS=127.0.0.1,::1
CONTACT_TO_EMAIL=27518653@qq.com
```

发件账号沿用 `WORKFLOW_EMAIL_*`。访客邮箱不会被伪装成发件人，而是写入
`Reply-To`，便于直接回复且不会破坏 SPF/DMARC 校验。

## 请求

```http
POST /api/v1/contact-messages
Authorization: Bearer <CONTACT_API_KEY>
Content-Type: application/json
```

```json
{
  "name": "张三",
  "email": "visitor@example.com",
  "message": "你好，我想咨询一下……",
  "source": "example.com",
  "idempotency_key": "contact-20260921-001"
}
```

`email`、`message` 和 `idempotency_key` 必填。调用方重试时必须复用同一个
`idempotency_key`；服务端会返回原有投递 ID，不会创建第二封邮件。

成功响应为 HTTP `202`：

```json
{
  "success": true,
  "data": {
    "queued": true,
    "deduplicated": false,
    "delivery_id": "..."
  }
}
```

## 调用示例

```bash
curl -X POST http://127.0.0.1:5001/api/v1/contact-messages \
  -H "Authorization: Bearer $CONTACT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "张三",
    "email": "visitor@example.com",
    "message": "你好，我想咨询一下……",
    "source": "example.com",
    "idempotency_key": "contact-20260921-001"
  }'
```

接口默认只接受来自 `127.0.0.1` 和 `::1` 的请求。若两个项目位于不同容器，
应将调用方的固定容器网络地址加入 `CONTACT_API_ALLOWED_IPS`，不要使用通配符。

## 静态网站通过 Nginx 接入

静态网站不能持有 `CONTACT_API_KEY`。可由同机 Nginx 提供公开的
`/api/contact` 路径，在反向代理时注入密钥：

```nginx
limit_req_zone $binary_remote_addr zone=contact_form:10m rate=5r/m;

location = /api/contact {
    limit_except POST {
        deny all;
    }

    limit_req zone=contact_form burst=3 nodelay;
    client_max_body_size 32k;

    include /etc/nginx/secrets/xiaoshanming-contact.conf;

    proxy_set_header Content-Type "application/json";
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;

    proxy_connect_timeout 3s;
    proxy_read_timeout 12s;
    proxy_pass http://127.0.0.1:5001/api/v1/contact-messages;
}
```

密钥文件内容：

```nginx
proxy_set_header Authorization "Bearer 至少32字符的随机密钥";
```

```bash
sudo chown root:root /etc/nginx/secrets/xiaoshanming-contact.conf
sudo chmod 600 /etc/nginx/secrets/xiaoshanming-contact.conf
```

服务端来源鉴权使用 `ProxyFix` 改写前保存的实际 TCP 对端地址，不使用
访客可控的 `X-Forwarded-For`。因此本机 Nginx 可通过校验，远程客户端不能
通过伪造 `X-Forwarded-For: 127.0.0.1` 绕过限制。
