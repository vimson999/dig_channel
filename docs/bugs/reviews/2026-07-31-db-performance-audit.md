# 2026-07-31 数据库性能与错误巡检

## 结论

- 固定统计窗口：`2026-07-30 06:01:31 +08:00` 至
  `2026-07-31 06:01:31 +08:00`。
- 本轮仍不能把应用记录的秒级耗时归因于 PostgreSQL executor 或缺索引：
  代表性 SQL 的 `EXPLAIN (ANALYZE, BUFFERS)` 为 `0.025ms` 至 `4.026ms`，
  而 PgBouncer 等待和连接跨外部 I/O 持有仍然明显。
- 找到一个可复现的 Session 生命周期复发：Watchdog 发布修复消息后的 CAS
  竞争失败分支先执行 Celery revoke，再释放数据库 Session。现场连接因此持有
  `60.70s`，与 PgBouncer query timeout 同秒发生。
- 没有 statement timeout、lock timeout、deadlock、连接建立超时或长
  idle-in-transaction 事件；巡检时无等待锁。
- 未执行 CREATE/DROP INDEX、ALTER TABLE、数据回填、生产配置或其他生产 DDL。

## 近 24 小时日志量化

- 慢查询 `1,924` 条：P50 `1,544.47ms`、P95 `8,193.15ms`、最大
  `80,079.66ms`。
- 目标表命中：`publication_tasks=685`、`zsxq_sync_runs=44`、
  `zsxq_topics=162`、`zsxq_topic_comments=294`、
  `zsxq_attachments=57`、`external_documents=5`。
- 固定窗口内连接持有 `211` 条；为核对进程角色而延伸到 `06:14:45` 的诊断快照
  共 `213` 条，P50 `8.51s`、P95 `60.37s`、最大 `298.24s`。
  其中 `11` 条来自一次性诊断进程；受管进程中 `91` 条没有匹配慢 SQL，
  P95 `60.98s`、最大 `71.21s`。
- PgBouncer query-timeout 命中 `24` 行，按同秒三条 pool 日志去重后为
  `8` 次事件；无 connect timeout。1,453 个统计样本的等待
  P50/P95/最大为 `29.66/88.23/774.22ms`，查询为
  `75.29/224.27/2,028.90ms`。
- SQL 错误共 `6` 条：应用流量 `2` 条 `OperationalError`，一次性诊断 SQL
  `4` 条 `UndefinedColumn/UndefinedFunction`。诊断 SQL 已从应用错误基线分离。

## 主要 SQL 指纹与执行器证据

| 指纹 | 次数 | P95 | 调用点 |
| --- | ---: | ---: | --- |
| `b40d5814b820693e` | 223 | 3.89s | stick figure asset 进度窄更新 |
| `60ff7467696400d3` | 127 | 4.01s | scheduler system config |
| `be5b1f4bedc893e5` | 73 | 2.77s | report script child wait |
| `c28ef8b653a8ebba` | 66 | 3.32s | stick video 进度窄更新 |
| `1f91e2f6c30e317a` | 47 | 4.98s | ZSXQ attachment upsert |
| `d7450f2b201d61b1` | 45 | 15.03s | report audio asset ownership |
| `d232cde5c17514e8` | 39 | 21.22s | Bilibili child list |

代表性 `EXPLAIN (ANALYZE, BUFFERS)`：

- `publication_tasks` 主键宽字段点查 `0.028ms`，channel chart `0.980ms`。
- scheduler system config `0.025ms`。
- publisher/content JSON 列表查询分别 `1.682ms`、`4.026ms`。
- `zsxq_sync_runs` system summary 在 667 行表上的顺序扫描 `0.903ms`。
- 巡检时数据库为 PostgreSQL 16.13、非 recovery；角色/数据库的
  `statement_timeout=60s`、`lock_timeout=5s`、
  `idle_in_transaction_session_timeout=60s` 已生效。

这些证据说明本轮不能仅按应用总耗时新增索引。

## 表规模、扫描与写放大

| 表 | 总大小 | live/dead | 索引扫描占比 | HOT update |
| --- | ---: | ---: | ---: | ---: |
| `zsxq_topic_comments` | 117MB | 40,214 / 3,732 | 99.91% | 47.02% |
| `zsxq_topics` | 105MB | 10,126 / 1,878 | 99.65% | 1.54% |
| `publication_tasks` | 99MB | 7,888 / 579 | 99.91% | 1.72% |
| `zsxq_attachments` | 63MB | 27,531 / 2,781 | 99.95% | 0.14% |
| `zsxq_sync_runs` | 28MB | 667 / 0 | 99.94% | 0.57% |
| `external_documents` | 6.8MB | 1,612 / 26 | 99.18% | 0.00% |

`publication_tasks`、`zsxq_topics`、`zsxq_attachments`、
`zsxq_sync_runs` 的低 HOT 比例继续显示索引列/宽行更新的写放大压力；
本轮没有删除业务字段，也没有改动或删除原始 JSON 审计数据。

## 反复问题与根因

- `05:26:01`，publisher Watchdog 已向 Celery 发布新消息，随后任务与投影的
  CAS 更新返回 0 行。
- 竞争失败分支在数据库事务仍打开时调用 `celery_app.control.revoke`；
  Session 到 `05:27:03` 才归还，连接持有 `60,695.53ms`。
- 同一秒 PgBouncer 记录 query timeout，期间没有匹配慢 SQL。这把问题明确归类为
  “连接跨 broker I/O 持有”，而不是 executor 慢、N+1 或大字段读取。
- 通用 pending repair 的 CAS 失败与 processing repair 的状态竞争失败也存在
  相同顺序问题，属于 `DB-SESSION-LIFECYCLE` 根因簇第三次打开后的横向遗漏。

## 已落地修复

- 新增统一 `_release_then_revoke_published_repair_message` 边界：三个竞争失败
  分支都先 `rollback/remove`，再执行 Celery revoke。
- 新增运行态回归测试，在 revoke 回调内断言
  `db.session().in_transaction() is False`；结构契约拒绝旧调用顺序。
- 日志分析器新增 role/process 归因、一次性诊断与应用 SQL 错误分流、
  受管进程无匹配 SQL 的连接持有统计，以及 PgBouncer 同秒事件去重。
- Bug 记录、README 和 recurrence registry 已更新；根因簇验收阶段回退到
  `code_changed`，等待新进程真实窗口验收。

## 索引复核

- 六张目标表没有完全重复索引。
- `ix_zsxq_attachments_group_tab_status_file` 为 `4.2MB`，自
  `2026-07-17` 统计启动以来扫描 `0` 次，是当前最强的低价值候选。
  但真实查询包含 `file_id/attachment_key/name` 的 OR 条件，尚缺实际参数的
  EXPLAIN 证明 DROP 不会伤害回退路径，因此本轮不生成删除脚本。
- `ix_zsxq_topic_comments_owner_created` 为 `4.1MB`、扫描 `3` 次；
  `ix_zsxq_topics_group_type_create_desc` 为 `2.8MB`、扫描 `1` 次；
  这些查询维度并不与相似前缀索引完全等价，继续观察。
- `external_documents` 的多个状态索引扫描低，但表仅 `1,612` 行且任务具有周期性，
  目前删除收益不足。

## 验证、人工 SQL与下一步

- 本轮相关测试：`151 passed, 7 subtests passed`。
- 完整 unit suite：`986 passed, 3 skipped, 53 subtests passed, 1 failed`。
  唯一失败是既有前端缓存版本断言期望 `lightweight-status-poll-v2`，
  实际页面为 `20260729-sticky-sidebar-status-v3`，与本次数据库改动无关。
- `compileall`、`git diff --check`、Bug 记录严格检查均通过。
- 首次重启安全门禁发现 1 个不可中断的 `zsxq_sync_runs` 任务
  `e7bf7b7d-2856-456a-8941-a02328614455` 正处于
  `sys199_workflow_2210`，当时没有重启。
- `08:17` 复查门禁已无不可中断任务；`08:18–08:19` 完整开发栈重启成功。
  新后端 PID `32247` 的 `/health` 返回 `healthy`，新 Watchdog PID `32805`；
  17 类 Celery 服务和 5 个 scheduler 均已启动，bootstrap `errors=[]`。
- 本轮没有达到证据门槛的人工 DDL，所以没有生成 DBeaver SQL 脚本。
- 本轮修复已由新运行时加载；跨过真实 Watchdog CAS 竞争窗口后，验证无匹配 SQL
  的约 60 秒连接持有是否消失。低命中索引至少继续覆盖一个完整业务
  周期，并用实际参数 EXPLAIN 后再决定是否生成带超时、验证和回滚说明的 DROP 脚本。
