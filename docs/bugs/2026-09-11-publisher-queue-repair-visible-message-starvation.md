# Publisher 队列修复替换仍可见消息导致任务自我饿死

## 基本信息

- 日期：2026-09-11
- 状态：RUNTIME_REPLACED / OBSERVING
- 严重级别：P0
- 影响模块：Publisher pending repair / render queue / Redis unacked / MP3 视频编排
- 首次发现：2026-09-11 每日数据库性能巡检
- 修复提交：当前工作区
- 根因分类：队列可见性状态机错误、消息身份非单调
- 复发键：`ORCH-PARENT-CHILD-STATE` / `RUNTIME-TOPOLOGY-RELOAD`
- 是否复发：是；既有防线已经要求普通队列、worker 与 Redis unacked 联合判断，但 pending repair 仍在消息可见时替换 ID
- 关联记录：`2026-07-07-platform-upload-worker-restart-orphan.md`、`2026-07-24-auto-image-replay-downgraded-completed-video.md`、`2026-07-27-celery-solo-inspect-false-stuck-watchdog.md`
- 防线层级：shared_guardrail / contract_test / observability

## 现象

固定窗口 `2026-09-10 06:00:00` 至 `2026-09-11 06:00:00`（Asia/Shanghai）中，MP3 父任务
`c6247250…` 的 Publisher 子任务 `d588a3db…` 最终被 queue repair 写成 `failed/failed`，错误为
“任务入队修复已达到上限，Celery 中也找不到对应消息”。Celery 业务日志却显示被替换的消息随后陆续到达：

- 16:54：当前消息 `aaa3…` 仍 queued/unacked，但 repair 改投 `51e1…`。
- 16:56：`aaa3…` 到达 worker，因数据库已经记录 `51e1…` 而被 stale guard 跳过。
- 16:57：repair 再把 `51e1…` 替换为 `0caf…`；16:59 到达的 `51e1…` 再次被跳过。
- 17:00：repair 把 `0caf…` 替换为 `9703…`；17:04 达到三次上限并关闭任务。
- 17:04:39/17:04:46：`0caf…` 与 `9703…` 先后到达，但任务已终态，均无法继续。

## 影响

- 有效排队消息被人为作废，Publisher 永远得不到一次可执行 delivery。
- 修复器自身制造 stale delivery，并最终把没有业务错误的任务误收口为失败。
- Celery delivery 可以记录为正常跳过，单看 Celery 成败数会漏掉数据库中的业务终态失败。

## 根因

`maybe_requeue_stale_publisher_child` 的注释与总体设计要求 worker inspect 和 queue scan 都成功、且消息在两侧
都不可见后才能判定 orphan。但旧实现只在消息 active 时返回；当 `_message_visible_in_queue` 返回
`(True, True)`，即普通队列或 Redis unacked 中仍存在当前消息时，它仍发布新消息并把数据库
`celery_task_id` 改成新 ID。原消息到达后必然被 current-id guard 判 stale，周期 repair 又对新 ID 重复同一动作，
形成确定性的自我饿死。

## 解决思路

保留 current-id guard 与真正消息丢失时的自动修复，只修正可见性状态迁移：只要当前 ID 在 worker、普通队列
或 Redis unacked 任一处可见，就等待原消息，不发布替代消息，也不增加 repair count。消费者积压或 worker 健康
由现有 watchdog 负责，不能由 lost-message repair 通过换 ID 处理。

## 实际方案

- queue scan 成功且 `queue_visible=True` 时立即返回原快照与 `requeued=False`。
- 保留原 `celery_task_id`、步骤、状态与 repair 计数，不产生 broker 副作用。
- 删除可见消息分支中已经不可达的“仍重排”原因与告警，避免后续误读。
- 真正 worker/queue 都不可见时仍沿用既有发布、CAS 和竞态撤销流程。

## 验证

- TDD RED：将旧测试改为“queued/unacked 时必须等待”，旧实现得到 `requeued=1`，断言失败。
- GREEN：最小实现后目标测试通过；完整 `test_publication_task_queue_repair` 套件 50/50 通过。
- 重构后目标测试与完整套件联合运行 51/51 通过。
- 完整 unit 在受限沙箱运行 1,666 项，34 项仅因禁止绑定 `127.0.0.1` 报 `PermissionError`；允许
  loopback 后原命令复跑为 `Ran 1666 tests in 83.477s / OK`。
- `compileall`、worker app factory 27 文件、Celery revoke 317 文件门禁、registry JSON、Bug strict
  197/197 与 `git diff --check` 均通过。
- 真实时间线证明四个 ID 不是 broker 丢失，而是 repair 更新 current ID 后被消费者正确拒绝。

## 复发预防

- pending/queued repair 的状态表必须明确区分 active、reserved/scheduled、普通队列、Redis unacked 与所有来源不可见。
- “消息可见但未 active”只能等待或触发消费者健康检查，禁止替换消息身份。
- 队列修复回归必须断言 broker 调用次数、`celery_task_id`、repair count 与业务状态同时不变。
- 每日巡检必须把 Celery delivery 结果与 `publication_tasks` durable 终态对账，不能用 worker success/skip 替代业务成功。

## 后续事项

- 2026-09-12 06:04，安全门读取 47 条近期任务与 0 条活动同步并放行；标准双门禁整栈重启完成，
  未使用 `FORCE_RESTART_ACTIVE_TASKS`。新 backend PID `7204` healthy，bootstrap 17/17、`errors=[]`，
  Watchdog 与目标 worker 均 ready。
- 新 Watchdog 首轮 Publisher 扫描约 `102–122ms`，无长 hold 或 SQL 错误；但没有自然 queued/unacked
  候选，当前只能确认代码已加载，不能提前写成业务验收完成。
- 下一完整窗口确认不再出现 queued/unacked 消息被换 ID、连续 stale skip 或同类 repair-limit 失败。

### 2026-09-13 固定窗复核：发现旧语义运行态证据，重新替换

- Publisher child `91b5f0f4…` 在 20:25 的 `queue_repair` 审计仍记录
  `pending_message_not_active_but_queue_visible`，并把 `celery_task_id` 从 `ac45…` 改为 `039e…`；随后投出的
  CAS loser `8973…` 被正确撤销，最终 `039e…` 成功完成。这次没有形成连续自我饿死，但“消息可见仍换 ID”
  指纹证明上一轮运行验收是假绿。
- 09-11 最终修复文件写入时间晚于当时进程启动时间，而旧报告只核对了 healthy/ready，没有核对所有调用者
  的代码版本与自然业务审计。状态重新按 `RUNTIME-TOPOLOGY-RELOAD` 和
  `REGRESSION-ACCEPTANCE-INTEGRITY` 复发处理。
- 2026-09-13 06:09 双安全门读取 46 条近期任务、0 条活动同步并放行；标准脚本冻结 17 个队列后完成整栈
  重启，未使用 `FORCE_RESTART_ACTIVE_TASKS`。新 backend PID `46428` healthy，bootstrap 17/17、
  `errors=[]`，关键 worker 均 running、queue 0。
- 当前代码的 queued/unacked 合同与 queue-repair 完整套件继续通过；重启后尚无自然 queued/unacked 候选，
  因而保持 `RUNTIME_REPLACED/OBSERVING`，不能提前关闭。

### 2026-09-15 固定窗复核：CAS 竞态防线生效，原分支仍待自然覆盖

两次 pending repair 在判定旧消息不可见并发布替代 delivery 后遇到并发 owner，CAS loser 被撤销或由
current-id guard 跳过；对应业务任务最终由数据库当前 ID 完成，projection 的 repair count/reason 保持干净。
这证明 CAS/current-id 防线没有把竞态放大为业务失败，但并未直接覆盖“原消息仍在普通队列或 Redis unacked 时
必须等待”的目标分支。状态继续 `RUNTIME_REPLACED / OBSERVING`。
