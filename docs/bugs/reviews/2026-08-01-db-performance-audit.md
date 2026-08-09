# 2026-08-01 数据库性能与错误巡检

## 结论

- 固定统计窗口：`2026-07-31 06:01:31 +08:00` 至
  `2026-08-01 06:02:01 +08:00`。
- 应用记录的秒级耗时仍不能归因于 PostgreSQL executor 或缺索引：代表性
  `EXPLAIN (ANALYZE, BUFFERS)` 为 `0.023ms` 至 `4.530ms`，PgBouncer
  自身 query P95 为 `240.47ms`；主要尖峰是公网链路/PgBouncer query timeout
  与 Session 跨 Celery control I/O 持有。
- 上一轮只修复了消息发布后的 CAS/revoke 分支。本轮从新 Watchdog PID `32805`
  的真实日志确认两条横向遗漏：publisher processing 的 worker/queue 可见性检查
  和上传收口，以及终态父任务清理中的 revoke，仍把事务带过外部等待。
- 已把两条路径改成“DTO 快照 -> 释放 Session -> inspect/queue/revoke -> 按 ID
  重载并校验 -> 新短事务写入”，并增加运行态合同测试。
- 当前没有 statement timeout、lock timeout、deadlock、连接建立超时或
  idle-in-transaction；巡检时没有等待锁。
- 未执行 CREATE/DROP INDEX、ALTER TABLE、数据回填、生产配置或数据删除；原始
  JSON 审计数据完整保留。

## 最近 24 小时日志量化

- 慢查询 `2,275` 条：P50 `1,480.26ms`、P95 `8,818.24ms`、最大
  `60,396.32ms`。
- 目标表命中：`publication_tasks=1,212`、`zsxq_sync_runs=14`、
  `zsxq_topics=128`、`zsxq_topic_comments=291`、
  `zsxq_attachments=48`、`external_documents=5`。
- 连接持有 `204` 次：P50 `8.94s`、P95 `40.17s`、最大 `80.21s`。
  角色分布为 web `142`、worker `42`、scheduler `16`、一次性诊断 `4`；
  受管进程中 `92` 次没有匹配慢 SQL，P95 `40.37s`、最大 `80.21s`。
- PgBouncer query timeout 命中 `42` 行，按同秒去重后 `14` 次事件；connect
  timeout 为 `0`。1,440 个统计样本的 wait P50/P95/最大为
  `28.61/86.18/708.40ms`，query 为 `76.67/240.47/1,434.61ms`。
- SQL 错误 `8` 条、7 个签名：应用流量 `4` 条均为
  `server closed the connection unexpectedly`，分别落在 report-audio jobs/
  child list 和 MP3 jobs/detail；一次性诊断 `4` 条为 3 条断连及 1 条
  `UndefinedColumn`。诊断错误未计入应用缺陷基线。
- event 聚合：server-closed `7`，statement timeout `0`，lock timeout `0`，
  deadlock `0`，connection timeout `0`，idle-in-transaction `0`。

## 主要 SQL 指纹与归因

| 指纹 | 次数 | P95 | 归因 |
| --- | ---: | ---: | --- |
| `d7450f2b201d61b1` | 181 | 14.54s | report-audio 资产归属窄点查 |
| `60ff7467696400d3` | 105 | 3.18s | scheduler system config |
| `4a107631d71dc10f` | 104 | 15.82s | report-audio 列表投影 |
| `40b27e0226933f0c` | 103 | 3.16s | report-audio child 窄点查 |
| `b40d5814b820693e` | 86 | 3.84s | stick figure progress 窄更新 |
| `19cc392c5b5cbe76` | 76 | 4.02s | comment 审计 payload/hash 更新 |
| `d232cde5c17514e8` | 73 | 9.66s | Bilibili child list 投影 |

归类证据：

- PostgreSQL executor：publication_tasks 主键点查 `0.023ms`，channel chart
  `0.942ms`，system config `0.024ms`，publisher/content JSON 列表
  `2.629/4.530ms`，ZSXQ system summary `1.348ms`。
- PgBouncer/网络：14 次去重 query-timeout 与 4 条应用断连；同类 SQL 下一请求可
  恢复到毫秒/亚秒级，符合链路/连接关闭而非稳定执行计划退化。
- 连接持有：Watchdog terminal cleanup `67.36s/42.21s`、publisher upload
  processing 收口 `37.46s`，processing 可见性检查最大 `80.21s`。期间 SQL
  只有约 `0.1–0.8s`，长尾落在 Celery inspect/queue scan/revoke。
- N+1：去重重复日志 sink 后，`d745...`、`4a10...`、`d232...` 等每个已知
  request ID 均只有 1 次实际 SQL，没有发现单请求内重复点查。高次数来自 24 小时
  多次页面/轮询请求，不按 N+1 加索引。
- 大字段：MP3 detail 指纹 `590220bb0dd49238` 读取 params/result，24 小时 36 次，
  其中一次 60 秒断连；但最近 20 行带大 JSON 与不带大 JSON 的 executor 分别为
  `0.027ms/0.023ms`。ZSXQ topic snapshot 不读取 raw_payload；comment 更新只在
  payload hash 变化时保留新的原始审计 JSON。本轮不牺牲字段完整性。

## 表规模、扫描与写放大

| 表 | 总大小 | live/dead | 索引扫描占比 | HOT update |
| --- | ---: | ---: | ---: | ---: |
| `zsxq_topic_comments` | 118MB | 40,300 / 5,695 | 99.91% | 51.37% |
| `zsxq_topics` | 107MB | 11,330 / 352 | 99.64% | 1.42% |
| `publication_tasks` | 102MB | 8,531 / 455 | 99.91% | 1.66% |
| `zsxq_attachments` | 65MB | 27,124 / 895 | 99.95% | 0.14% |
| `zsxq_sync_runs` | 28MB | 692 / 80 | 99.94% | 0.51% |
| `external_documents` | 6.8MB | 1,640 / 26 | 99.27% | 0.00% |

- 六张重点表没有完全重复索引。
- publication_tasks、zsxq_topics、zsxq_attachments、zsxq_sync_runs 的低 HOT
  比例继续显示宽行及索引列更新带来的写放大；其中 attachments 已有 233,682 次
  update、仅 316 次 HOT update，是最值得削减无效索引写成本的表。
- 顺序扫描的绝对次数在增长，但索引扫描占比均为 `99.27%–99.95%`；小表
  system_configs 和 692 行 sync_runs 的顺序扫描执行器仍在 `0.024–1.348ms`，
  无需为这些计划新增索引。

## 反复问题、根因与已落地修复

- `DB-SESSION-LIFECYCLE` 根因簇第四次打开。上一轮 release-then-revoke 只覆盖
  消息发布后的 CAS 竞争失败，未枚举发布前的 inspect/queue/revoke 分支。
- `maybe_repair_stale_publisher_processing_task` 现在先冻结任务 DTO 并释放 Session，
  worker 和 queue 可见性检查均在无事务状态执行；检查后重载并核对
  status/current_step/celery_task_id，状态变化则安全退出。
- upload/unknown/requeue-limit 收口先释放后 revoke，再重载收口；重新发布后也再次
  重载校验，竞争失败使用已统一的 release-then-revoke 边界撤销新消息。
- terminal-parent cleanup 先冻结父子标量、释放 Session、revoke，再按 ID 重载
  子任务并校验后收口；返回 DTO，不再把旧 ORM 对象跨等待传回扫描循环。
- 三条新增运行态合同覆盖 processing worker/queue 检查、upload revoke、
  terminal cleanup revoke，回调内均断言没有打开事务。
- Bug 记录、README 和 recurrence registry 已更新；验收阶段回退为
  `code_changed`，等待安全重启与真实扫描窗口。

## 索引整改建议与待人工 SQL

- `ix_zsxq_attachments_group_tab_status_file` 为 `4,360kB`，自
  `2026-07-17` 统计启动以来 `idx_scan=0`。真实 attachment-ref OR 查询使用 sibling
  `ix_zsxq_attachments_group_tab_status_key`，过滤 5,368 行，executor
  `4.182ms`；没有使用 file-id 复合索引。
- 结合 0.14% HOT update 和 233,682 次 update，该索引是当前最明确的写放大清理
  候选。已生成
  `docs/sql/zsxq_attachment_file_index_cleanup_dbeaver_20260801.sql`，包含统计重置/
  定义预检、真实参数 EXPLAIN、5 秒 lock timeout、10 分钟 statement timeout、
  `DROP INDEX CONCURRENTLY`、验证及 `CREATE INDEX CONCURRENTLY` 回滚。
- 风险：未来仅按 file_id 的同 tab 回退查询可能重新需要它。因此脚本要求人工确认
  idx_scan 仍为 0、保存真实 EXPLAIN，并在变更后监控 attachment-status buffers/
  延迟；本轮未执行该脚本。
- `ix_zsxq_topic_comments_owner_created` 为 4,144kB、scan=7；
  `ix_zsxq_topics_group_type_create_desc` 为 2,912kB、scan=1 但读取 3,380 个索引项；
  它们仍有真实查询维度，继续观察，不按前缀相似度删除。
- external_documents 的 0-scan 状态索引仅约 176–216kB，表仅 1,640 行且任务有
  周期性；当前收益不足。新增/待部署索引也不因短统计窗口删除。

## 验证、风险与下一步

- 新增修复定向测试：`45 passed, 4 subtests passed`。
- 完整 unit suite：`997 passed, 3 skipped, 53 subtests passed, 1 failed`。
  唯一失败是既有前端缓存版本断言期望 `lightweight-status-poll-v2`，当前 HTML 为
  `20260729-sticky-sidebar-status-v3`，与数据库修复无关。
- `compileall`、`git diff --check`、Bug 记录严格检查均通过。
- 重启安全门禁发现不可中断 ZSXQ 同步
  `33df42a5-25eb-4887-b019-cc6846d4cebe` 正在 `auto_all_tabs_sc`，未使用
  `FORCE_RESTART_ACTIVE_TASKS`，未执行重启。因此代码已落盘但当前 Watchdog
  运行时尚未加载本轮修复。
- 下一安全窗口重新运行 `scripts/dev/check-restart-safety.py`；仅在门禁通过后执行
  `scripts/dev/restart-dev-stack.sh`。新 PID 跨过 terminal cleanup 与 publisher
  processing 扫描后，复查无匹配慢 SQL 的 scheduler 持有是否归零，再把根因簇
  从 `code_changed` 推进到 `runtime_accepted`。
