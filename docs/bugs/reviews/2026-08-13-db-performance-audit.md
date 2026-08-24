# 2026-08-13 数据库性能与错误巡检

## 结论

本轮不能给出“数据库问题已解决”的结论。固定 24 小时窗口内，应用慢 SQL 数量、累计耗时、P95、HTTP P95/最大值、失败率以及 PgBouncer query/connect timeout 均恶化；PostgreSQL executor、锁和索引证据则继续显示数据库内核不是共同长尾根因。主要开放问题仍是 Tailscale/TLS 长连接链路失效、Celery 状态强制刷新控制面长尾、ZSXQ 批量写往返和 SMTP 通知失败风暴。

本轮落地并运行替换了 Celery 状态接口的并发强刷单飞修复：旧窗口重叠请求从 26.648 秒逐级堆到 88.103 秒；新 PID 下 8 个真实并发强刷全部返回同一 payload，P50/max `17.541/17.542s`，锁队列已消除。但单次刷新仍高于 10 秒，保持 `RUNTIME_REPLACED/OBSERVING`。

上一轮 ZSXQ 自动发布 count helper 跨七牛持有 Session 的问题，已通过 29 个真实规则批次、384 条七牛事件和下一完整窗口未复发证据闭环，仅该指纹标记 `CLOSED`。全局网络问题仍为 `RUNTIME_MITIGATED/OPEN`。

## 窗口与基线

- 当前窗口：`2026-08-12 06:02:19`（含）至 `2026-08-13 06:02:19`（不含），与上一轮截止点连续。
- 日志范围：`logs/slow_query.log`、`logs/app.log`、`logs/backend.log`、`logs/scheduler.log`、`logs/celery/*.log`、PgBouncer 当前及轮转日志。
- 已读取上一轮报告、automation memory、`docs/bugs`、Session 生命周期纪律、数据库性能验收纪律和 recurrence registry。
- 统计将应用业务 SQL 与 15 条巡检/临时诊断 SQL分开；未捕获不解释为未发生。

## 修复前后量化

| 指标 | 上一窗口 | 当前窗口 | 变化与判断 |
| --- | ---: | ---: | --- |
| 应用慢 SQL 数 | 408 | 872 | `+113.7%`，恶化 |
| 应用慢 SQL P50 | 1.650s | 1.620s | `-1.8%`，基本持平 |
| 应用慢 SQL P95 | 5.203s | 7.477s | `+43.7%`，恶化 |
| 应用慢 SQL 最大 | 18.691s | 19.577s | `+4.7%` |
| 应用慢 SQL累计 | 930.176s | 2180.829s | `+134.5%`，恶化 |
| HTTP 请求数 | 1922 | 2031 | `+5.7%` |
| HTTP 失败/失败率 | 13 / 0.68% | 30 / 1.48% | 失败 `+130.8%`，失败率 `+0.80pp` |
| HTTP P95 | 1.963s | 2.826s | `+43.9%` |
| HTTP 最大 | 24.369s | 88.103s | `+261.5%`，Celery 强刷锁队列 |
| HTTP累计 | 833.855s | 1697.577s | `+103.6%` |
| checkout 长持有数 | 65 | 143 | `+120%` |
| checkout P95 | 20.846s | 20.954s | 基本持平 |
| 业务 checkout 最大 | 88.557s | 23.006s | `-74.0%`，自动发布 Session 漏洞未复发 |
| PgBouncer 去重 query timeout | 6 | 8 | `+33.3%` |
| PgBouncer connect timeout | 0 | 7 | 新增并恶化 |
| PgBouncer server crash | 上轮短窗 4 | 132 | 300 秒轮换未根治 |
| statement/lock timeout | 0 / 0 | 0 / 0 | 无新增 |
| idle in transaction | 0 | 0 | 无新增 |

当前 143 个 checkout 的 P50/P95/max 为 `9.144/20.954/94.034s`；94.034 秒来自只读诊断进程，不应冒充业务最大值。worker 103 个最大 23.006 秒、web 27 个最大 19.430 秒、scheduler 1 个 16.230 秒。131 个可关联 SQL；12 个无完整 SQL 配对，其中 11 个属于托管进程，主要仍是连接/网络和外部等待。

## 主要 SQL 指纹排序

排序同时考虑累计耗时、P95、最大耗时、失败与复发次数，不按次数单排。

| 指纹 / origin | 次数 | 累计 | P95 / 最大 | 根因判断 | 状态 |
| --- | ---: | ---: | ---: | --- | --- |
| `60ff7467696400d3` scheduler `_db_config_rows` | 113 | 209.439s | 4.107 / 7.034s | 极小 `system_configs` 查询；EXPLAIN 0.023ms，网络往返/频繁调度读取 | OPEN，复发 |
| `0b4ccc892361a813` topic 批量 update | 58 | 198.261s | 10.055 / 14.246s | ZSXQ 同步写往返与网络放大 | OPEN，复发 |
| `19cc392c5b5cbe76` comment update | 40 | 95.800s | 8.650 / 10.463s | 评论同步高写量；索引写放大治理尚有人工维护 | OPEN，复发 |
| `40b27e0226933f0c` report-audio `_wait_child` | 41 | 92.641s | 5.260 / 7.555s | 频繁子任务轮询叠加网络 | OBSERVING |
| `1f91e2f6c30e317a` comment update | 40 | 84.437s | 4.204 / 5.007s | 同评论写路径 | OPEN，复发 |
| comment snapshot 多个 IN 形状 | 低频多指纹 | 未合并 | 最大 19.577s | 参数数量改变导致指纹分裂；executor 小，网络/批量快照长尾 | OPEN |
| `fd910014d5877aa5` `_wait_child` 失败 SQL | 1 | 20.153s | 20.153 / 20.153s | 02:31:38 与 PgBouncer query timeout 同秒，server socket 失败 | OPEN，网络 |

重点表慢日志次数：`publication_tasks=233`、`zsxq_sync_runs=20`、`zsxq_topics=133`、`zsxq_topic_comments=142`、`zsxq_attachments=57`、`external_documents=3`，相对上一轮分别约 `+174.1%/+150%/+66.3%/+100%/+147.8%/持平`。

## 页面、API、后台任务与 SQL 关联

### 页面/API

| 端点 | 请求 / 失败 | P95 / 最大 /累计 | SQL/调用链证据 | 判断 |
| --- | ---: | ---: | --- | --- |
| `/api/v1/celery/status` | 74 / 0 | 69.753 / 88.103 / 631.004s | 21 条慢 SQL累计 33.528s；最慢 request `71e90ffd` 前 76.5s 无 SQL | 并发强刷锁队列为主，已修单飞；单次 broker/网络长尾未闭环 |
| MP3 job detail | 95 | 3.535 / 5.680 / 96.291s | publication_tasks 状态投影 | 频繁轮询+网络，继续观察 |
| platform credentials | 146 / 1 | 2.314 / 15.531 / 93.181s | 15.531s 请求为数据库断连 | 网络失效 |
| data summary | 32 | 11.322 / 13.969 / 87.520s | 指纹 `d957...` | 多 SQL聚合+网络 |
| audio assets | 42 | 8.208 / 15.049 / 67.367s | executor 既有证据约 55.6ms | 非 executor 长尾 |
| Bilibili sync POST | 3 | 30.546 / 30.546 / 62.320s | SQL累计 22.757s | 低频高延迟，数据库往返占主导 |
| auth login | 3 | 18.791 / 18.791 | 无匹配慢 SQL | 外部/auth 等待，属于“页面慢但无慢 SQL” |
| retention preview | 1 | 19.532 | SQL 8.127s | 低频高延迟，网络+非 SQL 约 11.4s |
| cover regenerate | 1 | 16.855 | SQL 10.757s | 低频高延迟 |
| Celery restart | 1 | 12.314 | 无匹配慢 SQL | 进程控制等待，不是 DB |

知识星球专项：

- `zsxq-insights/overview` 2 次，`2.772/5.515s`，慢 SQL 3 条累计 4.666s；样本低于 5，不能宣称稳定。
- `POST /zsxq/topics/local` 1 次 `0.768s`；`POST /zsxq/topics/local/tabs` 1 次 `0.175s`，均为低样本。
- sync/status 类仅 4 个样本，最大 `8.178s`、慢 SQL `3.598s`；仍是上一轮冷样本形态。
- Insights 代表性 canonical selection 当前 executor `34.606ms`，但真实页面最大 5.515s，差异位于往返/应用链路；未证明需要新索引。

### 后台任务

| 任务族 | 成功 / retry / 失败 | P95 / 最大 /累计 | 判断 |
| --- | ---: | ---: | --- |
| ZSXQ sync | 24 / 8 / 0 | 822 / 1029 / 7034s | 长任务正常占比高；批量写 SQL/网络长尾仍复发 |
| publisher | 26 / 0 / 0 | — / — / 3049s | 真实自动发布完成，count-helper Session 已闭环 |
| PDF clean | 6 / 0 / 0 | — / — / 2726s | 文件处理为主，非前台端点 |
| ZSXQ publish | 11 / 0 / 0 | — / — / 2686s | 外部上传/发帖；账号 Session 未跨持有 |
| report audio | 52 / 1 / 0 | — / 1115 / 2664s | 出现 2 条共享导入错误传播，已完整重启，待真任务复验 |
| MP3 workflow | 121 / 0 / 1 | — / — / 1840s | 1 次 `public_browser...` ImportError，旧 worker 混版 |
| workflow_notify | 172 / 71 / 18 | — / — / 216s | SMTP disconnect 双层固定重试风暴，OPEN |

SMTP 失败集中在 07:40–07:55，18 个唯一终态失败；服务内 3×2 秒重试与 Celery 3×30 秒固定重投同步叠加。重启后 3 个真实通知在 `1.000–1.202s` 成功，只证明 provider 已恢复，不证明根因已修。

## 三类特殊异常

1. 低频高延迟：Bilibili sync 30.546s、auth login 18.791s、retention preview 19.532s、cover regenerate 16.855s、Celery restart 12.314s。
2. 页面/API 很慢但无匹配慢 SQL：auth login 与 Celery restart；Celery 最慢强刷在首条 SQL 前等待约 76.5 秒。
3. 后台任务/连接持有很久但无前台端点：ZSXQ sync 最大 1029s、report audio 最大 1115s；11 个托管 checkout 无完整 SQL配对，最大 19.430s。

## PostgreSQL、索引与表规模证据

只读事务于 06:11 执行，`transaction_read_only=on`，`statement_timeout=60s`、`lock_timeout=5s`、`idle_in_transaction_session_timeout=60s`。

| 表 | live / dead | table / toast / index / total | idx scan 占比 | 关注点 |
| --- | ---: | --- | ---: | --- |
| zsxq_topics | 12043 / 2124 | 13 / 79 / 35 / 127MB | 99.65% | TOAST 大但查询 executor 34.6ms；不盲目加索引 |
| publication_tasks | 12285 / 663 | 17 / 93 / 12 / 122MB | 99.81% | JSON TOAST 与高更新；热查询需继续投影 |
| zsxq_topic_comments | 41322 / 5885 | 91 / 4.2 / 23 / 118MB | 99.94% | 233108 updates，HOT 69.97%；人工清理重复索引仍待执行 |
| zsxq_attachments | 33746 / 4691 | 32 / 0.05 / 41 / 73MB | 99.95% | 更新 262315，HOT 0.12%，索引写放大高 |
| zsxq_sync_runs | 882 / 109 | 1.2 / 34 / 0.9 / 37MB | 99.94% | result TOAST 大；系统列表 executor 1.659ms |
| external_documents | 1810 / 146 | 0.8 / 4.8 / 1.2 / 7MB | 98.87% | 小表，低频索引需长期观察 |

无 waiting lock，PostgreSQL 端仅 7 个 idle/ClientRead，会话无 idle-in-transaction。`pg_stat_statements` 未安装，因此跨重启历史 executor 统计边界仍存在。

代表性 EXPLAIN execution：主键含 result `0.048ms`、chart `2.265ms`、scheduler config `0.023ms`、publisher list `0.071ms`、content pipeline list `0.168ms`、publication_tasks 大 JSON/窄投影 `0.059/0.054ms`、system sync runs `1.659ms`、Insights canonical `34.606ms`。应用 P95 7.477 秒与 executor 量级不一致，根因优先级仍在网络/连接与应用往返。

### 索引建议与人工 SQL

- 本轮不创建或删除索引；没有“真实慢 SQL + stats + EXPLAIN”支持新增索引。
- exact duplicate 检测当前无完全相同定义；但 schema 门禁仍确认 `ix_zsxq_topics_group_tab_type_create_desc` 与 `ix_zsxq_topics_group_type_create_desc` 属于已有替代定义覆盖的低价值索引，Alembic `20260724_comment_hash_indexes` 未记录应用。
- 现有人工脚本：`docs/sql/comment_hash_index_cleanup_dbeaver_20260724.sql`。它包含 `lock_timeout=30s`、`statement_timeout=10min`、comment hash 回填、指定 DROP、ANALYZE、版本记录和验证查询。
- 风险：非并发 DROP 与 41322 行 hash 回填会拿锁并产生写 I/O；必须先停 backend/Celery/scheduler，在 DBeaver 由管理员逐条执行。失败时停止，不手工推进 alembic version；回滚需按脚本前 `pg_indexes` 定义重建被删索引。本轮无人值守未执行。

## 已落地修复与验证

### Celery status 强刷单飞

- 修复前：8 个重叠强刷逐级 `26.648→40.566→48.372→70.946→80.083→88.103→82.171→69.753s`；每个等待者拿锁后重复刷新。
- 代码：缓存增加 `refreshed_at`；等待者只复用其请求开始后完成的同 key 结果。
- TDD：新增并发契约在旧实现上因 revision 1/2 不同先失败；修复后通过。
- 定向：Celery monitor 35/35 通过。
- 完整：unit 1154/1154 通过。
- 重启：两次安全门均为“无不可中断任务”；backend PID `64827` health healthy，17 worker、watchdog、调度器 bootstrap errors=[]。
- E2E：8 并发真实强刷 8/8 HTTP 200、一个 payload hash，P50/max `17.541/17.542s`；只有 request `470017de` 执行快照 SQL。
- 状态：并发锁队列层 `RUNTIME_REPLACED`；单次 17.4 秒未达标，整体 `OBSERVING`。

### 运行时共享导入

- 修复前：当前窗口 3 次 `public_browser_human_intervention_notification` ImportError；磁盘函数存在，worker 启动时间早于共享服务变更。
- 处理：完整安全重启，不修改或伪造业务结果。
- 运行态：MP3/report-audio/workflow_notify 新 worker ready，通知 worker完成 3 个真实任务。
- 状态：尚无新 MP3/report-audio 走到同调用链，`RUNTIME_REPLACED/OBSERVING`。

### ZSXQ 自动发布 count Session

- 真实 29 个规则批次、26 个 publisher 完成、384 条七牛事件；长持有中无 count helper origin。
- 业务 checkout 最大从 88.557s 降至 23.006s，且下一完整窗口未复发。
- 状态：该指纹 `CLOSED`；不包含网络总问题。

## 根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险 / 责任动作 | 下一轮验收条件 |
| --- | --- | --- | --- | --- | --- | --- |
| `CELERY-STATUS-FORCE-REFRESH-CONVOY` | 强刷等待者拿锁后重复完整刷新 | `refreshed_at` 单飞 + 并发契约 | 8/8 200、同 hash、max 17.542s | 本窗口复发后已替换 | 单次 broker/网络仍 17.4s；应用侧补分段打点 | P95 <10s、无逐级排队、timeout/server closed=0，下一 24h不复发 |
| `DB-TUNNEL-SERVER-SOCKET-CRASH` | Tailscale/TLS 长连接在 300s 内仍黑洞/断连 | 仅既有 300s 轮换缓解 | 132 crash、8 query、7 connect timeout | 是，恶化 | 运维/DBA 做稳定链路 A/B；禁止加索引掩盖 | 完整 24h query/connect/crash=0，真实 Web/worker 均覆盖 |
| `ZSXQ-AUTO-PUBLISH-PREVIEW-SESSION` | count helper 跨七牛持有 Session | finally remove + 契约 | 29 真实批次、下一窗口 origin 长持有=0 | 否 | 仅全局网络风险 | 已满足，单指纹 CLOSED |
| `RUNTIME-TOPOLOGY-RELOAD` | 共享模块变更后旧 worker 缓存混版 | 完整安全重启 | 新 worker ready；3 通知成功 | 是，3 ImportError | 缺 worker 版本指纹；等待真 MP3/report-audio | 同 ImportError=0，至少 1 条真实相关任务成功，下一 24h不复发 |
| `WORKFLOW-NOTIFY-SMTP-RETRY-STORM` | SMTP 断连 + 服务内/任务层固定重试叠加 | 本轮仅定位/登记 | 172 success、71 retry、18 fail；重启后 3 success | 新增 | 应用团队实现单一预算、抖动、背压、durable pending/补发 | 故障注入通过、最终送达、失败=0、下一窗口无风暴 |
| `ZSXQ-BATCH-WRITE-ROUNDTRIP` | topic/comment/attachment 高频更新被网络放大 | 既有批量/指纹防线 | 指纹累计仍居前，重点表慢日志显著增加 | 是 | 继续追上游不变数据重写与事务数 | 页级事务 O(1)、相关指纹累计/P95下降且真实同步字段完整 |
| `COMMENT-INDEX-MAINTENANCE` | 历史重叠索引与 hash 迁移未由管理员执行 | DBeaver 脚本已存在 | schema 门禁仍提示两条等价覆盖索引 | 是/未执行 | DBA 安全窗口，锁与回填风险 | 执行后 schema 门禁 clean、missing hash=0、写 P95下降 |

## 未捕获边界

- 请求日志只有完成事件和部分 request id；没有所有内部 broker/进程/外部 I/O 的分段 span。Celery status 的剩余约 14.5 秒只能定位到非 SQL 控制面，尚不能精确分到 Redis、inspect、进程扫描或文件系统。
- `pg_stat_statements` 未安装，无法用服务端累计 executor 历史直接覆盖所有真实 SQL；本轮以现场 EXPLAIN 和应用/PgBouncer 同时证据替代，仍存在样本边界。
- Insights/topics/tabs/sync status 样本仅 1–4 次，不能以低样本宣布稳定。
- 日志重复 ANSI/plain 行和轮转会放大朴素 grep；正式数量使用分析器去重事件，不把原始行数当事件数。
- HTTP 成功不代表返回内容无降级；Celery status 需继续检查 inspect_error、字段完整性和业务任务快照。
- 重启后真实窗口只有数分钟；除自动发布的上一完整窗口外，其余本轮修复均不得以短 smoke 标记 CLOSED。

## 下一轮明确验收计划

1. 首先复核本轮五个开放复发键：网络 socket crash、Celery 单次强刷、共享导入、SMTP 风暴、ZSXQ 批量写；不从零开始。
2. 对 Celery status 增加 broker ping、三类并行 inspect、queue length、process scan、business snapshot、report stats 的分段耗时，并在真实并发/单请求下要求 P95 <10 秒。
3. 统计 PgBouncer 每个 query/connect timeout 对应 server socket 创建时间、Tailscale path 与应用 request/task id；要求完整 24h crash/query/connect timeout 全为 0。
4. 捕获至少一条新 MP3 或 report-audio 任务经过 `public_browser_human_intervention_notification` 导入链，确认新 worker 无 ImportError并完成业务终态。
5. 对 SMTP 做受控断连故障注入，先写失败测试，再实现共享重试预算、指数抖动、provider 背压/熔断和 durable 补发；验证用户最终送达。
6. 若管理员执行 comment hash/index 脚本，运行 schema invariants、索引 stats、EXPLAIN 与真实同步写 P95；未执行则继续保持阻塞，不重复建议新增索引。
7. 继续要求 full unit、compileall、git diff --check、Bug strict、真实 health/worker/API/任务证据；测试通过不能替代下一窗口不复发。

## 2026-08-13 10:02 私网链路 follow-up：keepalive 根因修复已落地，整体仍开放

这不是新的 24 小时巡检窗口，而是针对用户报告的 127 次 TLS socket crash、8 次 query
timeout、7 次 connect timeout 的修复与运行态复核。上一轮基线和本轮新增证据如下：

| 证据 | 修复前 | 修复后短窗（10:03:54–10:06:08） | 判断 |
| --- | ---: | ---: | --- |
| `server conn crashed?` | 132（上一完整窗口） | 0（观察至 10:20:08） | keepalive 行为改善；不足以证明 24h 不复发 |
| TLS `Operation timed out` | 125（与 crash 一一对应） | 0（观察至 10:20:08） | 空闲后端 socket 误判暂未再现 |
| PgBouncer `query timeout` | 8 去重事件 | 1 | 仍复发，单独保持 OPEN |
| PgBouncer `connect timeout` | 7 去重事件 | 0（观察至 10:20:08） | 短窗改善，需完整托管流量确认 |
| 90 秒空闲池验收 | 无 | 6/6 客户端无错误 | 只证明当前运行态可用 |

### 根因与变更

- 真实 PgBouncer 日志的每次 crash 都是 `tls_sbufio_recv: read failed: Operation timed out`
  后的服务端连接 crash，年龄分布 25–299 秒，没有业务客户端附着；PostgreSQL
  `idle_session_timeout=0`、直连 TLS 空闲 40 秒稳定，现场 `EXPLAIN`/锁检查仍为毫秒级、无
  waiting lock。因此共同根因边界在 PgBouncer 空闲后端 socket 的平台 keepalive 探测与
  Tailscale/TLS 路径，而不是索引或 executor。
- `scripts/dev/setup-local-pgbouncer.py` 不再默认硬编码 `tcp_keepcnt=3`、
  `tcp_keepidle=10`、`tcp_keepintvl=5`，只保留 `tcp_keepalive=1` 的 OS 默认行为；三项值
  仍可通过 `LOCAL_PGBOUNCER_TCP_*` 显式注入作受控 A/B。相关默认/显式配置契约写入
  `tests/unit/test_local_pgbouncer_config.py`。
- 同一 query timeout 复核还定位到 report-audio 的大字段放大：
  `_resolve_scheme3_visual_inputs` 原来按主键加载 `PublicationTask` 全行，现改为只读
  `id/user_id/report_audio_pdf_attachment_id/report_audio_pdf_filename/report_audio_pdf_bytes`
  五列，避免 `params/result` 通过私网传输。新增契约测试在旧代码上先红，修复后 report-audio
  与 MP3 定向共 `160` 个用例通过。
- 这是兼容业务且无需生产 DDL 的代码/本地代理配置修复。已生成配置、HUP 重载本机
  PgBouncer 并现场确认 `tcp_keepcnt/idle/intvl=0`、6 条后端连接、`cl_waiting=0`；没有
  重启 backend/Celery/scheduler，也没有无人值守改路由、HBA、生产 PgBouncer 或数据库参数。

### 验证与仍未闭环项

- TDD 证据：旧代码运行新增默认配置测试为 RED；修复后配置类 4/4、`compileall`、
  `git diff --check` 均通过。窄投影改动后使用仓库根 top-level 的完整命令运行 `1165` 个
  unit，结果 `OK (skipped=2)`。
- 90 秒真实空闲池验收 6 个客户端全部成功；但同一重载窗口另有 1 次独立业务 query
  timeout（PgBouncer 客户端端口 `60374`，不是验收客户端），所以 query timeout 复发键仍
  开放，不能写成已解决。该事件对应的应用慢 SQL/ZSXQ 同步仍需继续按 request/task id、
  socket 创建时间和 Tailscale path 关联。
- 重启安全门禁已运行并读取远端任务，明确阻止当前 worker 替换：仍有 2 个不可中断任务。
  未强制重启，因此窄投影尚未获得新 worker 的运行态证据；下一安全窗口需先复核 ready/health，
  再用真实 report-audio/ZSXQ 任务验收 SQL 不再加载全行。
- 隔离的一次性只读进程以真实任务 `d859b7d7-866f-485e-9a9e-d8871ad12295` 调用新函数，
  首条窄投影查询 228.18ms、总耗时 905.96ms、无工作流返回且无写入/投递；它只能证明
  新代码路径可运行，不能替代新 worker 的运行态验收。
- 当前根因闭环状态为 `RUNTIME_MITIGATED/OPEN`。下一轮必须从重载时间点开始复核完整
  24 小时：托管 Web/worker/scheduler 的 `server crash=0`、TLS receive timeout=0、
  query/connect timeout=0，HTTP/任务失败率不恶化、`cl_waiting=0`、无 60 秒连接持有，并
  至少验收一个真实 ZSXQ 页面/API 与后台任务；否则保持 OPEN。
