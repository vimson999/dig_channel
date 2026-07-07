# 2026-07-07 MP3 详情页 B站上传记录停留在排队中

## 基本信息

- 状态：已修复
- 严重级别：中
- 影响模块：`mp3-video-workflow-detail` / B站上传子任务 / `publisher`
- 首次发现：用户在 MP3 视频编排详情页投递 B站上传后，上传记录一直显示 `排队中`

## 现象

详情页的“上传记录”里显示：

```text
此刻此声竖屏
排队中
```

但数据库和日志确认对应子任务已经执行并失败：

```text
task_id=82f4d1c2-3abb-4219-8a80-21a23c98d948
status=failed
current_step=failed
progress=10
error=网络错误，状态码：403 - 。
```

`logs/bilibili.log` 中也记录了真实上传尝试和 B站 403 返回。

## 影响

- 用户会以为上传任务还在排队，而不是已经失败。
- 失败原因不会及时暴露，用户无法判断是账号凭据、B站风控、网络还是程序问题。
- 如果用户重复投递，可能制造更多失败任务和无效请求。

## 根因

详情页投递 B站上传后会立刻刷新一次父 MP3 编排详情。

但父 MP3 编排任务此时通常已经是 `completed`，旧页面没有针对 B站上传子任务继续轮询。于是页面停留在刚创建子任务时拿到的 `queued` 状态；后端子任务后续进入 `failed` 或 `completed` 后，前端不会自动更新。

## 解决方案

- 在 `src/frontend/js/mp3-video-workflow-detail.js` 增加 B站上传子任务自动刷新。
- 只要 `bilibili_uploads` 或刚投递的 `lastBiliUpload` 中存在 `queued/pending/running/processing/submitting` 状态，就每 5 秒轻量刷新详情。
- 用户手动点击“刷新详情”时会先清理旧定时器，避免重复轮询。
- 页面关闭时清理定时器。

## 验证

```bash
node --check src/frontend/js/mp3-video-workflow-detail.js
git diff --check -- src/frontend/js/mp3-video-workflow-detail.js docs/bugs/README.md docs/bugs/2026-07-07-mp3-detail-bilibili-upload-status-stale.md
```

## 复发预防

- 父编排 completed 不代表详情页内所有派生子流程都已经终态。
- 给 completed 父任务新增异步子能力时，页面必须以子任务状态作为刷新条件。
- 上传、发布、审核这类外部平台任务要把失败状态和错误原因优先暴露出来，不能只展示初始排队态。
