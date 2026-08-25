# 知识星球侧边栏历史接口

## 接口

```text
POST /api/v1/zsxq/browser-import/history
```

同一条接口支持三种访问方式：

- 本机侧边栏通过 `http://127.0.0.1:5001` 直接调用，不需要额外登录；
- 公网机器调用使用数据库分配的 `app_id` + `secret`，通过 `X-App-Id` + `X-Timestamp` + `X-Signature` 签名，不需要登录；
- 已登录的管理员 Session/JWT 仍可作为后台人工调用的兼容方式。

公网部署时，假设域名为 `https://xiaoshanqing.tech`，完整地址为：

```text
https://xiaoshanqing.tech/api/v1/zsxq/browser-import/history
```

接口只接受 `POST`，不能把地址直接粘贴到浏览器地址栏当作 `GET` 访问。公网签名请求不需要交互式登录；管理调用方凭据的接口仍只允许管理员。

## 多租户调用方凭据

调用方凭据不是环境变量配置，而是数据库中的租户记录。管理员通过以下接口分配和管理：

```text
POST /api/v1/zsxq/history-api-clients
GET  /api/v1/zsxq/history-api-clients?tenant_id=<tenant>
POST /api/v1/zsxq/history-api-clients/<client_id>/rotate
POST /api/v1/zsxq/history-api-clients/<client_id>/revoke
```

创建请求示例（需要管理员 Session/JWT）：

```json
{
  "tenant_id": "tenant-a",
  "name": "知识星球侧边栏"
}
```

创建或轮换响应只返回一次明文 `secret`：

```json
{
  "success": true,
  "client": {
    "id": "...",
    "tenant_id": "tenant-a",
    "app_id": "zsxq_...",
    "name": "知识星球侧边栏",
    "scopes": ["zsxq.history.read"],
    "status": "active"
  },
  "credentials": {
    "app_id": "zsxq_...",
    "secret": "只显示这一次"
  }
}
```

数据库只保存加密后的 secret；列表、详情和吊销响应不会再次返回 secret。不同租户的 `app_id` 独立分配，吊销或轮换立即使旧 secret 失效。

### 管理页面

管理员登录后可在以下页面管理调用方：

```text
本机：http://localhost:8888/zsxq-history-api-clients.html
公网：https://xiaoshanqing.tech/zsxq-history-api-clients.html
```

页面支持创建、按租户/状态筛选、复制 AppID、轮换 Secret 和吊销调用方。创建或轮换后，明文 Secret 只在该页面显示一次；页面不会把 Secret 写入列表、浏览器存储或日志。页面本身及其管理接口均要求管理员 Session/JWT，公网调用方仍只使用签名，不使用网页登录。

## 请求体

MP3：

```json
{
  "kind": "mp3",
  "days": 3,
  "group_id": "28888112822211",
  "tab_id": "88844545452542"
}
```

PDF 只需改为：

```json
{
  "kind": "pdf",
  "days": 3,
  "group_id": "28888112822211",
  "tab_id": "51184248544214"
}
```

`days` 允许 1 到 7，侧边栏固定使用 3；`group_id` 和 `tab_id` 必须与服务端固定范围匹配。

## 公网机器调用

创建调用方后，将响应中的 `app_id` 和 `secret` 安全交给对应租户。算法固定为 `HMAC-SHA256`；secret 不放入代码仓库、前端代码或 URL。

调用方对下面这段字符串签名：

```text
app_id + "\n" + timestamp + "\n" + method + "\n" + path + "\n" + sha256(raw_body).hexdigest()
```

请求头为：

```text
X-App-Id: <app_id>
X-Timestamp: <Unix seconds>
X-Signature: <lowercase hex HMAC-SHA256>
```

Python 调用示例：

```python
import hashlib
import hmac
import json
import os
import time
import requests

body = json.dumps({
    "kind": "mp3",
    "days": 3,
    "group_id": "28888112822211",
    "tab_id": "88844545452542",
}, separators=(",", ":")).encode("utf-8")
timestamp = str(int(time.time()))
app_id = os.environ["ZSXQ_APP_ID"]
api_secret = os.environ["ZSXQ_APP_SECRET"]
path = "/api/v1/zsxq/browser-import/history"
signing_string = "\n".join([
    app_id,
    timestamp,
    "POST",
    path,
    hashlib.sha256(body).hexdigest(),
])
signature = hmac.new(
    api_secret.encode(), signing_string.encode(), hashlib.sha256
).hexdigest()

response = requests.post(
    "https://xiaoshanqing.tech" + path,
    data=body,
    headers={
        "Content-Type": "application/json",
        "X-App-Id": app_id,
        "X-Timestamp": timestamp,
        "X-Signature": signature,
    },
    timeout=15,
)
response.raise_for_status()
print(response.json())
```

服务端默认只接受时间戳前后 300 秒的请求。跨域浏览器调用还需要把调用方来源加入生产 `CORS_ORIGINS`；服务端不会开放通配符来源。

成功响应为：

```json
{
  "success": true,
  "data": {
    "kind": "mp3",
    "days": 3,
    "complete": true,
    "files": [],
    "summary": {}
  }
}
```

历史结果带 `Cache-Control: no-store`，不会被浏览器或反向代理缓存。无有效签名且未登录的请求不会返回历史数据；签名调用方不要求交互式登录。
