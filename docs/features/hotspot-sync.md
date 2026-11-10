# 热点二创接口

本机接收地址为 `http://localhost:5001/api/v1/sync/hotspots-with-posts`。后台页面为 `http://localhost:5001/hotspots.html`，导航入口在「选题情报 → 热点二创」。

上游 Agent 使用 `Authorization: Bearer <HOTSPOT_SYNC_TOKEN>` 和 `Content-Type: application/json`。开发环境的令牌保存在工作区 `.env` 的同名变量中。请通过上游的密钥设置传递实际值。跨机器调用时使用部署环境的 HTTPS 域名替换 localhost。

```sh
curl --fail-with-body 'http://localhost:5001/api/v1/sync/hotspots-with-posts' \
  -H "Authorization: Bearer ${HOTSPOT_SYNC_TOKEN:?Set the sync token first}" \
  -H 'Content-Type: application/json' \
  --data-binary @hotspot-bundle.json
```

`hotspot-bundle.json` 示例：

```json
{
  "hotspot": {
    "id": "HOT-20261105-001",
    "source_platform": "github",
    "category": "opensource",
    "title": "开源项目发布新版本",
    "scraped_at": "2026-11-05T08:50:00+08:00"
  },
  "posts": [
    {
      "id": "POST-20261105-001-A",
      "persona": "playful_snarky_v9",
      "post_type": "short_tweet",
      "content": "更新日志说修复了小问题。我看了一眼迁移指南，决定先泡杯茶。",
      "suggested_tags": ["#开源"]
    }
  ]
}
```

每次接受 1–100 条候选，总请求上限 5 MiB。时间字段需要 ISO 8601 时区。候选初始状态为 `draft`，热点初始状态为 `pending`。同一热点可追加新候选。相同 ID 重试保留已入库的正文和审核状态，跨热点复用候选 ID 返回 409 并回滚整批。成功时 `data.posts_saved_count` 是本次新增数，`data.posts_existing_count` 是已存在数。

HTTP 响应中的 `X-Request-ID` 与 JSON 的 `request_id` 可用于检索服务日志事件 `hotspot_sync`。事件记录调用方、热点 ID、候选提交数、保存数、HTTP 状态、结果和耗时。本机日志在 `logs/x-content-backend.log`。

部署时应用迁移至 `20261105_hotspot_images`，并在后台进程环境中设置至少 32 字符的独立随机 `HOTSPOT_SYNC_TOKEN`。工作台的读写采用现有管理员鉴权。页面的「已发布」用于记录运营状态。


在 `hotspot` 中可选填 `evidence_image_base64`（PNG/JPEG 的 base64 data URL，最多 2500 万像素）和 `cover_image_url`（HTTP/HTTPS 图片直链）。同 ID 重推时，提供的图片字段会更新，省略的图片字段保留原值。证据截图和候选随同一事务写入。列表和详情返回 `evidence_image_url`，通过管理员鉴权后可读取原始图片。

页面优先预览证据图，未提供时显示外链封面。证据 JPEG 复制时转换为 PNG，下载保留原格式。外链封面若限制跨域读取，可点击缩略图打开原图后保存。
