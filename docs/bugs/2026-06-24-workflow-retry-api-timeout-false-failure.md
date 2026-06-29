# 2026-06-24 编排续跑接口超时但实际成功导致前端误报失败

## 基本信息

- 状态：已修复，后端已重启生效
- 严重级别：中
- 影响模块：`report_top10_workflow`、Celery broker 发布、前端历史列表操作
- 首次发现：用户在今日研报 Top10 编排页点击失败续跑后，页面提示 `续跑失败：请求超时（20s）`
- 修复提交：待提交

## 现象

页面点击 Top10 失败续跑后提示：

```text
续跑失败：请求超时（20s）
```

但后端日志显示同一个请求最终返回 200：

```text
POST /api/report-top10-workflows/jobs/95abe64a-9560-4ecc-9567-d5c47ad3b70e/retry -> 200 (31113ms)
```

数据库确认任务已经进入续跑：

```text
status=processing
current_step=single_gemini_02_waiting
progress=15
retry_requested_at=2026-06-24T05:29:14Z
```

因此前端“续跑失败”是误报，真实状态是续跑已提交并开始执行。

## 影响

- 用户看到失败提示，会误以为续跑没有发生，可能重复点击。
- 重复点击可能制造重复父任务入队请求或状态混乱。
- 任务实际已经运行，但 UI 反馈相反，削弱对系统状态的信任。

## 根因

直接根因：

- 前端 retry 请求超时时间为 20 秒。
- 后端 retry 接口在 `apply_async()` 发布 Celery 消息时阻塞约 30 秒，最终成功。
- 前端超时后直接展示失败，没有回查任务状态。

底层原因：

- Flask Web 进程和 Celery worker 共用 broker 配置。
- Web 进程发布任务时使用 Redis broker 连接池，旧连接可能卡到 `CELERY_BROKER_SOCKET_TIMEOUT=30` 才恢复。
- 对 Web API 来说，发布任务应该快速失败或快速重连，不能沿用 worker 的长连接容忍策略。

## 解决思路

分两层修：

1. 后端降低 Web/Scheduler 进程发布 Celery 消息时的阻塞上限，避免接口卡 30 秒。
2. 前端遇到续跑请求超时时，不立刻判失败，而是回查任务详情；如果任务已进入 `queued/running`，按成功提交处理。

## 实际方案

后端 `src/backend/celery_config.py`：

- 按 `DB_PROCESS_ROLE` 区分 Celery broker 策略。
- `web` / `scheduler` 默认：
  - `broker_pool_limit=0`
  - `broker socket_timeout=5`
- `worker` 默认保持：
  - `broker_pool_limit=10`
  - `broker socket_timeout=30`

这样 Web API 发布任务不会长期卡在陈旧 Redis 连接上，同时不改变 worker 的长运行消费策略。

前端 `src/frontend/js/report-top10-workflow-test.js`：

- retry 超时后调用任务详情接口回查。
- 如果任务状态是 `queued` 或 `running`，显示：

```text
续跑请求已提交，接口确认稍慢；任务已进入运行队列。
```

- 只有回查也无法确认时，才展示真正的续跑失败。

前端 HTML 已 bump JS cache：

```text
20260624-retry-timeout-confirm
```

## 验证

语法检查：

```bash
venv/bin/python -m py_compile src/backend/celery_config.py src/backend/api/report_top10_workflow.py src/backend/tasks/report_top10_workflow.py
node --check src/frontend/js/report-top10-workflow-test.js
```

配置验证：

```text
web_pool 0
web_socket_timeout 5
worker_pool 10
worker_socket_timeout 30
```

运行态验证：

- 已只重启 Flask 后端，未重启正在执行的 Celery workers。
- 后端健康检查正常：

```json
{"status": "healthy", "version": "1.0.0"}
```

- `report_top10_workflow` worker 在线。
- `report_pdf_gemini` worker 在线。

## 复发预防

- Web API 中触发 Celery 入队必须使用 API 进程友好的 broker 发布策略，不能照搬 worker 长连接策略。
- 前端对“触发异步任务”的接口超时，必须先回查业务状态，再决定是否提示失败。
- 所有编排续跑按钮应逐步统一这一交互：超时不等于失败，业务表状态才是最终判断依据。

## 后续事项

- 将同样的超时回查逻辑推广到 `report_audio_workflow` 和 `mp3_video_workflow` 的失败续跑按钮。
- 可抽一个共享前端 helper，统一处理“异步触发接口超时后的状态确认”。
