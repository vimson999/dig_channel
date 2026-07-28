# Celery solo Worker 被误判卡死且 Watchdog 误报重启

## 基本信息

- 状态：已修复并完成真实运行时验收
- 严重级别：P1
- 影响模块：Celery Monitor / Watchdog / `report_pdf_gemini` 浏览器 Worker 池
- 首次发现：2026-07-27
- 修复提交：当前工作区
- 根因分类：Celery 控制面可见性与业务执行态混淆
- 复发键：`RUNTIME-TOPOLOGY-RELOAD`
- 是否复发：第 N 次；再次出现“inspect 空响应覆盖业务心跳”的同类问题
- 关联记录：`2026-07-24-zsxq-watchdog-killed-active-all-tabs-sync.md`
- 防线层级：shared_guardrail / contract_test / observability

## 现象

Celery Monitor 显示 `report_pdf_gemini` 队列积压 44、业务进行中 8，但
`active/reserved/scheduled` 都为 0，并提示“消费连接可能卡住，建议重启”。
Watchdog 连续三轮后写入“自动重启成功”，页面看起来像全部任务停在
`prepare_browser · 3%`。

## 影响

- 用户被错误引导去重启仍在工作的 5 个浏览器 Worker。
- Watchdog 持续发送消费异常邮件并累计卡死次数。
- 安全门虽然阻止了本次停止，但日志仍记录“restarted”，掩盖真实执行状态。
- 如果同类队列缺少停止门禁，错误判定可能再次中断活动任务。

## 根因

5 个浏览器 Worker 使用 Celery `solo` pool。执行长任务时，主进程无法并行处理
`inspect active` 控制命令；控制命令在任务间隙返回时，活动列表又可能已经为空。
Monitor 把控制面瞬时空响应直接等同于没有消费。

页面已经拿到近期业务心跳，却仍无条件设置 `consumer_warning`。Watchdog 为减少
数据库轮询完全不加载业务快照，也没有在疑似卡死时做一次窄心跳复核。此外，
Watchdog 未检查 `_stop_service()` 的 `skipped` 结果，安全门拒绝停止后仍调用启动
函数并记录“restarted”。

## 解决思路

Celery inspect 只作为控制面证据，不能覆盖结构化业务心跳。日常轮询继续避免全表
查询；只有队列满足“有积压、进程在线、inspect 无活动任务”的疑似条件时，才按该
服务 feature 做一次轻量业务心跳查询。查询失败时 fail-closed。

## 实际方案

- Monitor 检测到近期 `processing/running` 心跳时，显示 solo Worker 忙碌说明，
  不再输出卡死警告。
- Watchdog 只对疑似卡死的单个服务调用现有不可中断任务门禁做窄查询。
- 心跳存在或心跳查询失败时均禁止告警、累计卡死次数和自动重启。
- `_stop_service()` 返回 `skipped` 后停止重启流程，记录
  `restart_skipped`，不再伪造 `restarted`。

## 验证

- 定向回归：`61 passed`，覆盖近期心跳、心跳查询失败、solo inspect 假空、
  Watchdog 窄复核及停止安全门跳过语义。
- 真实现场 5 个 Worker PID 在后端与 Watchdog 重载前后保持
  `27437/27466/27513/27542/27581` 不变。
- 现场队列从 44 持续下降到 21，最终归零；8 条业务任务全部退出进行中。
- 新后端 PID `89441`、新 Watchdog PID `90211` 均加载修复代码。

## 复发预防

- `solo`、浏览器和长任务 Worker 不得只用 Celery inspect 判定空闲或卡死。
- 所有自动重启必须在动作前做服务范围的结构化业务心跳复核，查询失败 fail-closed。
- stop 返回 `skipped`、`blocked` 或 `guard_check_failed` 时，编排器不得继续 start，
  更不得记录重启成功。
- 契约测试必须同时覆盖页面展示、Watchdog 判定和最终动作语义。

## 后续事项

- 继续观察下一批浏览器图片任务，确认页面只显示忙碌说明且不再发送假卡死邮件。
