# 2026-07-30 数据库性能与错误巡检

## 结论

- 统计窗口：`2026-07-29 06:14:32 +08:00` 至 `2026-07-30 06:14:32 +08:00`。
- 本轮慢耗时的主因不是 PostgreSQL executor 缺索引，而是 PgBouncer/网络等待和应用跨外部 I/O 持有连接。
- 同一 14 分钟窗口内多个独立 web、worker、watchdog 连接发生短时只读事务写失败，publisher 入口把可重试数据库故障送入了永久失败收口。
- 没有 statement timeout、lock timeout、deadlock、连接建立超时或 idle-in-transaction 日志事件；巡检时无等待锁、无只读事务，数据库不在 recovery。
- 未执行任何生产 DDL、数据回填或配置修改。

## 24 小时日志量化

- 慢查询 `1,225` 条：P50 `1,678.03ms`、P95 `14,234.64ms`、最大 `60,481.69ms`。
- 目标表命中：`publication_tasks=399`、`zsxq_sync_runs=111`、`zsxq_topics=93`、`zsxq_topic_comments=177`、`zsxq_attachments=38`、`external_documents=4`。
- 连接持有 `305` 条：P50 `15.54s`、P95 `61.49s`、最大 `173.85s`；其中 `161` 条没有匹配慢 SQL。
- PgBouncer query-timeout 日志命中 `51` 行；1440 个统计样本的等待 P50/P95/最大为 `18.64/67.17/694.67ms`，查询 P50/P95/最大为 `67.77/232.87/4,073.90ms`。
- SQL 错误 `87` 条、20 个指纹：`77` 条 `ReadOnlySqlTransaction`，其余 `10` 条为连接关闭类 `OperationalError`。
- 只读错误集中于 `04:14:38` 至 `04:28:29`；publisher 初始状态写和失败状态写各 `18` 条。受影响的 6 个 publisher 任务均已由 watchdog 恢复为 `completed/done/100`。

## 主要 SQL 指纹与执行器证据

应用日志中最频繁的指纹：

| 指纹 | 次数 | P95 | 调用点 |
| --- | ---: | ---: | --- |
| `60ff7467696400d3` | 115 | 3.85s | scheduler system config |
| `ba0ef8e45dfe5c37` | 84 | 16.61s | ZSXQ sync run summary |
| `5d42e2fe8577eed7` | 66 | 3.23s | platform credential |
| `4a107631d71dc10f` | 60 | 15.44s | report audio page |
| `1f91e2f6c30e317a` | 48 | 4.10s | attachment tab update |

代表性 `EXPLAIN (ANALYZE, BUFFERS)`：

- `publication_tasks` 主键宽字段点查 `0.030ms`，channel chart `1.065ms`。
- scheduler system config `0.023ms`。
- `zsxq_sync_runs` summary 在 646 行表上顺序扫描 `0.837ms`。
- 旧 JSON feature 列表查询为 `46.34ms` 至 `110.10ms`，明显慢于结构化列，但仍远低于应用记录的数秒至数十秒。

因此不能用应用端总耗时作为新增索引依据。

## 表规模、扫描与写放大

| 表 | 总大小 | live/dead | 索引扫描占比 | HOT update |
| --- | ---: | ---: | ---: | ---: |
| `zsxq_topic_comments` | 117MB | 40,131 / 3,085 | 99.90% | 42.07% |
| `zsxq_topics` | 104MB | 9,950 / 1,020 | 99.65% | 1.69% |
| `publication_tasks` | 98MB | 7,325 / 912 | 99.91% | 1.79% |
| `zsxq_attachments` | 62MB | 25,944 / 112 | 99.95% | 0.14% |
| `zsxq_sync_runs` | 27MB | 646 / 103 | 99.94% | 0.64% |
| `external_documents` | 6.8MB | 1,600 / 26 | 99.06% | 0.00% |

`publication_tasks`、`zsxq_topics`、`zsxq_attachments` 和 `zsxq_sync_runs` 的低 HOT 比例继续表明宽行/索引列更新存在写放大，但本轮没有发现可安全删除业务字段或原始 JSON 审计载荷的空间。

## 索引复核

- 六张目标表没有完全重复索引。
- 上轮单列 `ix_zsxq_attachments_publish_status` 已不存在，保留的复合索引 `ix_zsxq_attachments_publish_status_updated_at` 已有扫描命中。
- `ix_zsxq_attachments_group_tab_status_file` 为 4.2MB、统计期内扫描 0 次；`ix_zsxq_topic_comments_owner_created` 为 4.1MB、扫描 1 次。统计自 `2026-07-17` 起，且尚未用对应真实 SQL 和 EXPLAIN 证明可删除，暂不生成 DROP INDEX。
- `ix_zsxq_topics_group_type_create_desc` 扫描 1 次、`ix_zsxq_topics_group_tab_type_create_desc` 已有明显命中；二者查询维度不同，不按前缀相似度判重。

## 已落地修复

- 日志分析器纳入 `publication_tasks_query.log`，兼容 Celery 方括号时间戳，并把 query timeout 与 connection timeout 分开统计。
- publisher 在媒体/平台副作用前的初始数据库写入遇到可重试错误时先清理 Session，再由 Celery 延迟重试；非数据库业务错误不受影响。
- publisher pending watchdog 在 Celery/Redis 检查前释放数据库 Session，发布后用窄字段 CAS 回写；竞态失败会撤销新消息。
- 数据库诊断报告新增 recovery 和事务只读状态，避免把只读切换误判成业务 SQL 错误。
- 两个历史复发记录和 recurrence registry 已再次打开。

## 验证与运行边界

- 定向测试：`43 passed, 4 subtests passed`。
- 完整 unit suite：`977 passed, 3 skipped, 53 subtests passed, 1 failed`。唯一失败为既有前端静态资源版本标记不一致，与本轮数据库改动无关。
- `compileall`、`git diff --check`、Bug 记录严格检查均通过。
- 重启安全门禁识别到 12 个不可中断任务（11 个视觉视频任务和 1 个 ZSXQ 同步轮次），因此没有重启后端、Celery 或 scheduler。

## 人工 SQL 与下一步

- 本轮无满足证据门槛的 CREATE/DROP INDEX、ALTER TABLE、回填或配置 SQL，因此没有生成可执行 DDL。
- 当前角色/数据库已配置 `statement_timeout=60s`、`lock_timeout=5s`、`idle_in_transaction_session_timeout=60s`，无需重复执行上轮脚本。
- 等安全窗口重启后，应跨过至少一个 publisher watchdog 扫描周期，验证外部检查期间不再出现无匹配 SQL 的长连接持有，并观察上述低命中索引至少一周后再决定是否生成人工 DROP INDEX 脚本。
