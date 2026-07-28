# 数据库 Session 跨 Celery Broker 与远程 I/O

## 基本信息

- 状态：共享防线、业务修复、完整回归和全量重启已完成，持续观察真实任务
- 严重级别：P0
- 影响模块：runtime guard / 外链文档抓取与总结 / 外链 TTS 与补抓任务
- 首次发现：2026-07-24
- 修复提交：未提交
- 根因分类：未提交事务跨 broker、提交后 ORM 重新借连接、外部 I/O 边界缺少统一约束
- 复发键：`DB-SESSION-LIFECYCLE`
- 是否复发：是
- 关联记录：`2026-06-24-zsxq-sync-download-held-db-session.md`、`2026-07-18-zsxq-credential-session-held-across-remote-publish.md`
- 防线层级：shared_guardrail / transaction_boundary / contract_test / runtime_acceptance

## 现象

代码审计发现两类会反复放大连接占用的路径：

- 任务记录执行 `flush()` 后，在 `commit()` 前调用 Celery `apply_async()`。Redis 延迟会把未提交事务、数据库连接和行锁一起拖长。
- 外链抓取和 AI 总结虽然先 `commit()`，但随后继续把 ORM 对象传入 HTTP、浏览器或 LLM 函数。SQLAlchemy 默认提交后会过期属性，首次读取字段可能重新借出连接并跨远程等待持有。

共享任务准入还会在投递前执行数据库压力和任务计数查询，然后直接访问 broker，留下一个只读事务跨 Redis 调用。

## 影响

- Redis、HTTP、浏览器或 AI 响应变慢时，数据库连接占用时间同步增长。
- 未提交任务事务会延长锁持有时间，并增加 PgBouncer 排队和应用查询超时概率。
- 单点修补某个任务无法阻止新入口再次写出相同模式。

## 根因

项目过去主要依赖函数结束、Flask teardown 或 Celery app context 退出清理 Session，没有把“数据库阶段”和“外部 I/O 阶段”定义为强制边界。

另外，`commit()` 被误认为等同于“后续代码不会再借连接”。实际上 ORM 属性过期后，提交后的字段访问可以透明触发 SELECT 和新事务。

## 实际方案

- runtime guard 新增数据库边界检查：外部 I/O 前若 `session.new/dirty/deleted` 非空，立即拒绝投递并暴露编程错误。
- 任务准入查询完成后、Celery broker 或 retry publish 前统一结束干净事务，先归还连接再访问 Redis；为避免 SQLAlchemy `commit()` 过期已加载投影并透明重查，边界提交期间临时关闭 `expire_on_commit`。
- 外链补抓与 TTS 改为“任务先落库提交 -> broker 投递 -> 短事务回写 celery_task_id”；投递失败保留任务并写明确失败状态。
- 外链抓取和 DeepSeek 总结在事务内生成 `SimpleNamespace` 快照，释放 Session 后只使用快照执行远程调用，完成后按 ID 重新加载并写回。
- ZSXQ 外链详情在本地缓存和凭据查询完成后释放 Session，再请求远端 topic API。
- 新增 AST 契约测试，阻止任何 Python 函数再次出现数据库 mutation/flush 后未 commit 就直接 `apply_async()`。
- ZSXQ 已存在主题批量快照只查询身份、同步元数据和 `raw_payload_hash`；哈希未变时不再读取正文、问答和摘要大列，哈希变化时由新 payload 完整重建结构化列和原始 JSON。
- ZSXQ 外链登记从每条链接两次点查改为每个 topic 固定两次集合查询：一次批量加载文档 shell，一次批量加载 topic 关系；新增文档集中一次 flush，完整关系字段仍照常写入。
- 运行时发现 PgBouncer 生成默认值回退为 `min_pool_size=2 / server_idle_timeout=180 / server_lifetime=900`，修复生成源与测试下限为 `6 / 600 / 21600`，并完成在线 HUP。
- 全量重启门禁补查 `zsxq_sync_runs` 的近期 `running/retrying` 心跳，不再只检查 `publication_tasks`。
- SQLAlchemy Pool 增加 checkout/checkin 持有时长监控；连接持有超过默认 5 秒时写入 `db_connection_lifecycle` 告警，即使期间没有执行 SQL，也能暴露跨外部等待的连接。
- ZSXQ 每次主题列表、评论补抓、附件流式下载和节流等待前统一调用数据库边界；存在未提交 ORM 变更时直接失败，干净只读事务则先提交归还连接。
- ZSXQ 页面级主题/附件快照加载和投影刷新完成后立即提交，不再把页面事务带入逐 topic 的 CPU、文件和远程处理。

## 验证

- runtime guard、外链服务和数据库纪律定向回归通过。
- 新测试现场检查 broker 回调执行时 SQLAlchemy `in_transaction()` 为 `False`。
- 新反例测试确认未提交 ORM 变更会阻止 broker 调用，且 Celery callable 未被执行。
- 完整单元回归：`877 passed, 3 skipped, 46 subtests passed`；新增 ZSXQ 远程边界后定向回归 `43 passed, 4 subtests passed`。
- 全项目语法编译、Bug 记录严格审查和 `git diff --check` 通过。
- 重启后 `pg_stat_activity` 首次采到一个 `0.25s` 的正常提交瞬态，八秒后复查 `idle in transaction=0`。
- `publication_tasks` 安全检查应用侧曾记录 `15883.78ms`，相同 SQL 的 `EXPLAIN (ANALYZE, BUFFERS)` 使用 `idx_publication_tasks_status`，executor 仅 `0.040ms`，排除缺索引。
- PgBouncer 热重载后管理视图确认 `min_pool_size=6`、`server_idle_timeout=600`、`server_lifetime=21600`、`cl_waiting=0`，业务后端连接合计 6。
- 热重载后的真实同步仍捕获外链关系点查 `9244.06ms`；同参数 executor 为 `0.030ms`，研报音频列表同形 executor 为 `0.058ms`，两者都排除执行计划问题。外链 N+1 已改为集合查询，以降低公网往返次数和抖动放大倍数。
- 全量重启门禁现场识别两个近期 ZSXQ 同步心跳并返回退出码 `2`，证明不会再次强杀活动同步。
- 旧 worker 现场曾出现 `idle in transaction` 且事务年龄 `59.64s`，最后语句为评论批量查询；同一语句的应用日志随后记录 `59752.63ms`。由于当时没有同时采集 `state_change`，不能把全部 59 秒武断归因为 SQL 后的空闲等待，但它暴露了远程边界和诊断字段均不完整的问题。
- 2026-07-24 15:16 安全门禁确认无活动任务后完成全量重启；新后端 PID `27271`、ZSXQ worker PID `27668`，健康接口正常，重启后复查 `idle in transaction=0`，暂未出现超过 5 秒的连接持有告警。

## 复发预防

- 所有外部耗时操作固定使用“短事务快照 -> 释放 -> 外部 I/O -> 按 ID 新短事务写回”三阶段。
- 禁止 `flush -> apply_async/HTTP/浏览器 -> commit`。
- 禁止提交后把 ORM 对象传入网络函数；外部阶段只允许纯标量、字典、不可变 Row 或 DTO。
- 数据库事务和 broker 消息通过幂等 task ID、失败状态和 watchdog 实现最终一致性，不跨系统持有数据库事务。

## 后续事项

- 继续观察下一次真实 ZSXQ 同步的 `db_connection_lifecycle`、`pg_stat_activity.state_change` 和慢查询日志。
- 若应用超过 500ms 而 executor 小于 10ms，直接进入连接池/网络诊断，不再堆索引。
