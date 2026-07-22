# 2026-07-03 知识星球工作流入队失败与同步 orphan running

## 基本信息

- 状态：已修复；2026-07-19 复发审计补齐状态投影、终态收口和回归测试
- 严重级别：高
- 影响模块：知识星球系统同步、`zsxq_workflow`、`zsxq_sync`、自动调度器、SQLAlchemy session
- 首次发现：2026-07-03 20:43 邮件告警
- 修复提交：待提交

## 现象

邮件告警显示工作流 `002ec0e8-4d40-4f1c-9818-8266b02d2f6c`、批次 `20260703-170611` 失败：

```text
工作流启动任务长时间停留在 queued/init（超过 20 分钟），已自动标记失败，避免阻塞后续调度。
```

排查后发现两层问题：

- `17:06` 轮次在创建 `zsxq_workflow_runs` 记录后，入队阶段数据库连接被服务端断开。
- `20:43` 重启后新建工作流 `3c4103e0-ea18-462a-96aa-6fdcb4be13b5`，同步任务 `8d486c5d-b50f-4150-989e-ba9ec7e750ca` 已开始执行，但 `20:49` 重启时 `zsxq_sync` worker 被杀，Celery 任务丢失，DB 中仍停留在 `running/sync`。

现场日志：

```text
2026-07-03 17:06:14 INSERT ZSXQ_WORKFLOW_RUNS 成功
2026-07-03 17:08:14 workflow queue failed ... server closed the connection unexpectedly
2026-07-03 17:08:14 PendingRollbackError: Can't reconnect until invalid transaction is rolled back
2026-07-03 20:49:39 [ZSXQ_SYNC] pacing wait 11.4s
worker: Warm shutdown (MainProcess)
run-celery-zsxq.sh: ... Killed: 9 ... -Q zsxq_sync
```

## 影响

- `20260703-170611` 没有 `sync_run_id`，同步阶段根本没有启动。
- `20260703-204344` 同步到一半后被 worker 重启打断，状态残留 `running`。
- 调度器看到“上一轮仍在执行”，后续启动会跳过，表现为“半天没动静”。
- 已下载的部分文件可复用，但本轮未进入清洗和发布阶段。

## 根因

直接根因：

- 入队阶段发生可重试数据库断连后，异常处理没有先 `rollback/remove` 当前 SQLAlchemy session。
- 代码继续用已处于 `PendingRollback` 的 session 调用 `mark_failed_by_id()`，导致失败状态也无法写入，只留下 `queued/init` 孤儿记录。
- `zsxq_sync` worker 被重启杀掉后，Celery 任务不会自动恢复；业务表缺少 `running/sync` 阶段 stale 回收。

架构层根因：

- 只做了 `queued/init` 和 `clean` 阶段 stale 回收，没有覆盖最核心的 `sync` 阶段。
- `sync_run.updated_at` 会被非 worker 的读/投影路径刷新，不能作为可靠 worker 心跳。
- 同步任务的 `result.partial` 没有显式进度心跳字段，无法区分“真实运行中”和“worker 已死但 DB 被读路径刷新”。

## 解决思路

- 入队失败后必须先释放坏 session，再用新 session 收口失败状态。
- `sync` 阶段也必须纳入 workflow stale recovery。
- 运行中的同步任务必须写显式 `heartbeat_at`，stale 判断优先看心跳，而不是看易被污染的 `updated_at`。
- 对没有 `heartbeat_at` 的历史 running sync，用 workflow 自身最后推进时间兜底。
- 当前 orphan run 先标失败释放调度，再让 scheduler_start 自动补跑。

## 实际方案

- `services.zsxq_system_sync_test.queue_configured_jobs()`：
  - 捕获入队异常后调用 `_reset_db_session("workflow_queue_failed")`。
  - 再调用 `zsxq_workflow_service.mark_failed_by_id()`，避免 `PendingRollbackError` 阻断失败收口。
- `services.zsxq_system_sync_test._pending_system_sync_work()`：
  - pending 检查前同时执行 `recover_stale_initial_workflows()`、`recover_stale_running_sync_workflows()`、`recover_stale_running_clean_workflows()`。
- `services.zsxq_workflow_service`：
  - 新增 `ZSXQ_WORKFLOW_SYNC_STALE_MINUTES`，默认 30 分钟。
  - 新增 `recover_stale_running_sync_workflows()`。
  - 对 stale `running/sync` 同时标记 `ZsxqSyncRun` 和 `ZsxqWorkflowRun` 为 `failed`，并写入明确错误。
  - stale 判断优先读取 `result.heartbeat_at`。
- `services.zsxq_sync_service`：
  - `_commit_running_progress()` 和 `_commit_manual_running_progress()` 写入 `heartbeat_at`。

## 现场恢复

- 已将 orphan workflow `3c4103e0-ea18-462a-96aa-6fdcb4be13b5` 标记为 `failed`。
- 已将 orphan sync run `8d486c5d-b50f-4150-989e-ba9ec7e750ca` 标记为 `failed`。
- pending 锁已解除：

```text
pending=False
queue_length=0
run_id=None
workflow_run_id=None
```

- 已执行 `scripts/dev/restart-dev-stack.sh`，后端、所有 Celery worker、watchdog、`zsxq_auto_scheduler` 已重新拉起。
- 重启后自动补跑新一轮：
  - workflow：`df812a90-e09d-4eda-9f65-b3adac91c527`
  - 批次：`20260703-215043`
  - sync run：`2471324e-d97f-4df1-a719-bf878baa26f4`
  - 状态：`running/sync`
  - 已确认新 worker 写入 `heartbeat_at=2026-07-03T13:51:39.194605Z`

## 验证

2026-07-19 复发审计发现旧恢复器会先读取 `zsxq_sync_runs.result` 大 JSON，再判断同步是否已经终态；
终态失败还可能被恢复逻辑覆盖。现改为先只读状态列，只有仍处于非终态的 stale 候选才点读
`id/result` 心跳；失败终态保留原错误，完成终态推进后续阶段。新增测试覆盖 failed、completed 和
running/fresh 三条路径。真实自动轮次 117 从同步、双清洗到双发布全部完成，活动任务归零。

- `py_compile` 通过：

```bash
venv/bin/python -m py_compile \
  src/backend/services/zsxq_workflow_service.py \
  src/backend/services/zsxq_system_sync_test.py \
  src/backend/services/zsxq_sync_service.py
```

- 数据库确认：
  - `002ec0e8-4d40-4f1c-9818-8266b02d2f6c`：`failed/failed`，无 `sync_run_id`。
  - `3c4103e0-ea18-462a-96aa-6fdcb4be13b5`：`failed/failed`，错误为 stale sync recovery。
  - `8d486c5d-b50f-4150-989e-ba9ec7e750ca`：`failed`。
  - `df812a90-e09d-4eda-9f65-b3adac91c527`：`running/sync`。
  - `2471324e-d97f-4df1-a719-bf878baa26f4`：`running`，且 `result.heartbeat_at` 正常更新。
- 进程确认：
  - `zsxq_sync`、`zsxq_workflow`、`zsxq_publish`、watchdog、`zsxq_auto_scheduler` 均在线。
- 2026-07-19 定向回归覆盖状态投影、心跳读取和终态推进；完整已知问题回归另由
  `scripts/dev/run-known-issue-regression.sh` 统一执行。

## 复发预防

- workflow 每个长阶段都必须有 stale recovery：`queued/init`、`sync`、`clean`、`publish` 不能只覆盖部分阶段。
- 不能把 `updated_at` 当 worker 心跳，除非确认没有读路径或投影路径会刷新它。
- 长任务必须写显式 `heartbeat_at` 或等价心跳字段。
- 所有数据库异常收口逻辑必须先 `rollback/remove session`，再进入失败标记或重试逻辑。
- 重启前必须确认业务 pending/active 为空；重启后必须确认 worker、scheduler 和补跑 workflow 状态。

## 后续事项

- 将 `sync` 阶段 heartbeat 展示到 celery monitor 或知识星球编排详情页。
