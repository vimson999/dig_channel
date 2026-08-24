# 2026-08-19 数据库性能与错误巡检

## 根因闭环结论

本轮没有达到“全部根因 CLOSED”。固定窗口内慢 SQL 数量、累计耗时、HTTP P95 与失败率均改善，但 HTTP 最大耗时恶化，PgBouncer connect timeout 由 6 次升至 7 次，连接持有 P95 仍超过 21 秒；因此不能把流量下降或慢查询暂时减少当作根治。

本轮新增的最高价值根因是 **Bilibili intelligence 双调度实例 + 未变化原始 JSON 全量回写**。单实例 OS 锁、按真实变化字段更新和模型唯一约束去重已落地；安全门后只替换两个调度器进程，真实 `scheduler_once` 6/6 成功，旧全列 UPDATE P95 9.905 秒，替换后 UPDATE 最大 889.54ms，且没有新的 1 秒慢全列 UPDATE。由于尚无完整后续 24 小时，只标记 `RUNTIME_REPLACED / OBSERVING`，不标记已解决。

固定统计窗口：`2026-08-18 06:02:19` 至 `2026-08-19 06:02:19`（Asia/Shanghai）。上轮基线为 `2026-08-18-db-performance-audit.md`。窗口后的测试与运行态证据单独列出，不混入 24 小时统计。

## 24 小时基线与变化

| 指标 | 本轮 | 上轮 | 新增 / 恶化 / 改善 / 反复 |
| --- | ---: | ---: | --- |
| 应用慢 SQL | 307 | 369 | 改善 16.8% |
| 应用慢 SQL 累计 | 697.666s | 981.741s | 改善 28.9% |
| SQL P50 / P95 / 最大 | 1.486 / 7.299 / 15.761s | 1.693 / 7.648 / 21.707s | 改善 12.2% / 4.6% / 27.4% |
| HTTP 请求 | 1383 | 2124 | 流量下降 34.9%，不能单独证明优化 |
| HTTP 累计 | 588.584s | 1639.689s | 改善 64.1% |
| HTTP P50 / P95 / 最大 | 0.0035 / 1.596 / 35.463s | 0.158 / 3.874 / 21.231s | P95 改善 58.8%，最大恶化 67.0% |
| HTTP 失败 | 15 / 1.08% | 45 / 2.12% | 次数改善 66.7%，失败率改善 1.04pp |
| Connection hold | 43；P50/P95/max 7.032/21.033/21.879s | 70；7.777/20.315/23.889s | 次数改善；P95 恶化 3.5%，反复 |
| 应用 statement/lock/idle-in-tx error | 0 / 0 / 0 | 0 / 0 / 0 | 未捕获不等于未发生 |
| PgBouncer 去重 query/connect timeout | 3 / 7 | 3 / 6 | query 持平，connect 恶化 16.7%，反复 |

连接持有按角色为 web 15、scheduler 13、worker 2、unknown 13；SQL 段累计 279.811s，非 SQL/结果 fetch/rollback/checkin 等段累计 133.214s。PgBouncer 等待 P50/P95/max 为 24.98/94.35/5267.70ms，query 为 56.22/150.68/1648.43ms；本机 server source `100.96.3.12` 连接 1735 次，未捕获代理 TUN、query-wait、crash、TLS 或 HBA 错误。

## 主要问题排序

排序综合累计耗时、P95、最大耗时、失败率、用户/页面影响与复发次数，而非出现次数。

1. **scheduler config 指纹 `60ff7467696400d3`（OPEN，反复）**：109 次、累计 231.458s、P95 4.847s、最大 15.761s；次数较上轮增加 7.9%，累计增加 1.3%。现场 executor 0.026ms，根因是重复远端往返和网络长尾，不是缺索引。
2. **Bilibili 全列 UPDATE 指纹 `f28c58ebb852d69c`（新增，RUNTIME_REPLACED / OBSERVING）**：70 次、累计 174.367s、P95 9.905s、最大 14.778s；双实例每小时重复同步，且未变化行仍重写 `raw_payload` 等 15 列。
3. **PgBouncer/远端网络长尾（OPEN，反复）**：query/connect 3/7，connect 恶化；PostgreSQL 无锁等待、无 idle-in-transaction，代表性 executor 0.026–35.423ms，不能用加索引解释 21 秒 Session 持有。
4. **Workflow notification SMTP 失败风暴（OPEN，反复）**：142 成功、51 retry、13 fail，累计 198.716s。服务内协议回退与 Celery 共享重试预算已生效，但大量 milestone event 各自拥有独立预算；根因上移到 provider 级熔断/背压和 durable pending/最终送达。
5. **Insights / Watchdog Session 修复（OBSERVING，反复键）**：旧跨 CPU/文件/broker 持有修复已在运行，但本窗口仍覆盖修复切点前后，且网络长尾未闭环；不能据单次测试关闭。
6. **隐藏页轮询（改善，OBSERVING）**：Celery status 从 323 降到 5，Publisher 从 321 降到 0，分别改善 98.5%/100%；5 次 status 是人工/运行探针，未出现旧成对轮询，但仍需完整真实 hidden 页窗口确认。

## 页面、API、后台任务与 SQL 关联

| 页面 / 链路 | 请求或任务 | 累计 / P95 / 最大 | SQL、Session、调用源与结论 |
| --- | ---: | --- | --- |
| ZSXQ Insights overview | 6 | 50.264s / 26.856 / 26.856s | request `b24437ad`；6 条慢 SQL 合计 16.563s。现场 canonical executor 35.423ms，剩余主要是远端 fetch/事务回收；应用 read scope 继续 OBSERVING |
| MP3 jobs GET | 295 | 101.482s / 1.475 / 7.593s | 高频列表；无单一慢 SQL覆盖全部耗时，需继续监测轮询与网络 |
| Platform credentials | 106 | 45.230s / 1.543s / — | 低单次、高累计；未捕获匹配慢 SQL，不误报为 executor 慢 |
| ZSXQ browser import | 14，失败 4 | 44.314s / 9.186 / 14.257s | 失败率 28.57%，主要为 400 输入/业务校验；影响用户成功率，但不是 DB 5xx |
| MP3 job POST | 6，失败 2 | 38.806s / 19.055s / — | 失败率 33.3%，低频高延迟；下一轮需按 request id 继续区分外部 I/O 与 DB |
| Bilibili intelligence sync POST | 1 | 35.463s | 7 条慢 SQL 18.013s；进程树与 origin `bilibili_intelligence_service.py:224` 证实双实例和全列回写根因 |
| MP3 detail | 19 | 33.011s / 5.718 / 12.472s | 低频高延迟，未被单一慢 SQL解释 |
| Bilibili upload | 8 | 32.661s / 14.659s / — | 外部上传链路，不能把全耗时归因 DB |
| Celery status / Publisher | 5 / 0 | status 14.315s / 6.331s / — | 上轮为 323/321；旧 hidden pair 未复发，但观察窗和真实页面覆盖仍不足 |

重点 ZSXQ 链路中，`topics/local` 有效 POST 为 740.57ms，`topics/local/tabs` 有效 POST 为 140.02ms；错误 GET 探针返回 404，不作为业务性能证据。`sync/status` 的有效 POST 未在本窗捕获，故结论是“未观测”，不是“没有问题”。

三类强制异常：

- **低频高延迟**：Bilibili sync 1 次/35.463s；continue-full-video 4 次、P95 12.178s；MP3 job POST 和 detail 同属低频尾延迟。
- **页面/API 很慢但无匹配慢 SQL**：seasons 33 次/16.179s、WeChat 29/14.642s、Douyin 29/12.852s、finance regenerate 3/10.704s、audio-assets 13/8.261s；需继续追外部 I/O、连接等待和普通 SQL。
- **后台/Session 长持有但无前台端点**：scheduler config、Bilibili 周期同步、watchdog repair 与 Celery 长任务；按 PID/role/origin 关联，不能依赖 request id。

Celery 成功 240、retry 51、failed 13，总成功耗时 4089.168s。主要任务为 Publisher 9/1136.089s（P95 184.973s）、MP3 process 43/791.286s（最大 231.506s）、ZSXQ publish 7/676.190s、Bilibili upload 8/643.141s、PDF clean 4/510.888s。长任务本身不等价慢 SQL；通知任务的 51 retry/13 fail 才构成明确失败风暴。

## 新增根因与已落地修复

### Bilibili 双实例与写放大

修复前证据：PID 11364 与 16231 同时由 PID 1 持有，日志每小时分别在约 `:10`、`:52` 运行；每轮 6 个监控源 × 30 条，旧代码只加载 `id/bvid` 后给所有字段重新赋值，形成包含完整 `raw_payload` 的 executemany UPDATE。表仅 839 live rows，却已有 10,905 updates、216 dead rows。

代码变更：`start` 模式持有 OS 级非阻塞文件锁，PID 文件只作展示；同步查询加载可比较投影，未变化行仅更新 `last_seen_at/updated_at`，变化时仍完整保留全部字段和原始 JSON；模型删除列级重复 `unique/index` 声明，保留命名表级唯一约束。

验证链：三个新契约先红后绿；定向文件 12 passed；安全门确认无不可中断任务。标准整栈重启在停止任何服务前被 Redis 预检超时安全中止，随后只终止两个已核验 scheduler PID 并启动新 PID 77876。新进程唯一运行，受控第二实例退出码 2。真实 once 运行 6/6、抓取 180、新增 6、更新 174、失败 0、13.346s；旧全列 UPDATE P95 9.905s，替换后 UPDATE 最大 889.54ms，未出现新的 1 秒慢全列指纹。仍需下一完整窗口确认不复发。

### SQL 错误来源分类器

上一轮 source-aware 修复又暴露优先级问题：共享 `scheduler.log` 中一条显式 `process=-`、`role/origin=unknown` 的 stdin 诊断 SQL 被误算应用错误。本轮让显式 shell/python 元数据优先于文件路径，保留真正缺元数据的托管来源规则。重跑后应用 SQL error 0、ad-hoc server-closed 1；analyzer 20 passed。该变更修复的是巡检准确性，不掩盖应用错误。

### 反复但未闭环

- **SMTP**：单 event 内双层固定重试已被替换，但 08:52 多个 milestone event 同时失败，51 次 retry、13 次终态失败。需要跨 event 的 provider circuit breaker/backpressure、durable pending 和最终送达核对；保持 OPEN。
- **PgBouncer/网络**：executor 毫秒级、无锁/idle-in-tx，但 connect timeout 恶化且 Session P95 21.033s；保持 OPEN。
- **Insights/Watchdog**：代码和运行 PID 已替换，本窗不足以排除跨 CPU/外部 I/O 持有；保持 OBSERVING。
- **scheduler config**：次数和累计均小幅恶化，继续追跨进程缓存和失效写入；保持 OPEN。

## PostgreSQL、表规模与索引复核

PostgreSQL 16.13，read-only 诊断；无 recovery、waiting lock、idle-in-transaction；`statement_timeout=45s`（诊断）、`lock_timeout=5s`、`idle_in_transaction_session_timeout=1min`。`pg_stat_statements` 未安装。

| 表 | 总大小 | live/dead | seq/idx scan | idx 使用率 | 风险 |
| --- | ---: | ---: | ---: | ---: | --- |
| zsxq_topics | 128MB | 12452/1584 | 3302/871223 | 99.62% | TOAST 79MB；HOT 0.71% |
| publication_tasks | 123MB | 12588/995 | 6868/2854512 | 99.76% | TOAST 94MB；HOT 1.32% |
| zsxq_topic_comments | 118MB | 41333/5644 | 263/356927 | 99.93% | table 91MB；HOT 70.46% |
| zsxq_attachments | 77MB | 34278/6667 | 579/1132464 | 99.95% | 更新 264296、HOT 0.12%，写放大风险 |
| zsxq_sync_runs | 37MB | 937/1 | 219/334269 | 99.93% | index 35MB、更新 111665、HOT 0.33% |
| external_documents | 7MB | 1817/146 | 1019/84933 | 98.81% | 小表 seq scan 可接受 |

目标六表没有 exact duplicate index。代表性 `EXPLAIN (ANALYZE, BUFFERS)`：watchdog 0.065ms、chart 2.698ms、scheduler config 0.026ms、Publisher 0.085ms、content 0.188ms、JSON recent/narrow 0.070/0.043ms、sync 1.617ms、Insights 35.423ms。证据不支持新增索引。

Bilibili 辅助表发现两个等价唯一 btree `(bvid)`：独立索引 `ix_bilibili_competitor_videos_bvid` scan 10980，约束所有者 `bilibili_competitor_videos_bvid_key` scan 0；各约 56kB。代表点查 executor 0.035ms，故只建议去重，不建议再加索引。已生成 DBeaver 人工脚本 `docs/sql/bilibili_competitor_bvid_index_cleanup_dbeaver_20260819.sql`，包含 duplicate precheck、约束归属、`lock_timeout=5s`、`statement_timeout=15min`、`DROP INDEX CONCURRENTLY`、验证与回滚。**本轮未执行任何 CREATE/DROP/ALTER/backfill。** 既有 comments hash 索引人工脚本也仍未自动执行。

## 测试与运行态验收

- TDD/定向：Bilibili scheduler 12 passed；日志分析器 20 passed；视频隐藏轮询 Node 1 passed。
- 完整 unit：沙箱全跑 `1301 passed, 3 skipped, 33 failed`，33 项全部是两个本地 HTTP 测试文件 bind `127.0.0.1` 被沙箱拒绝，无业务断言失败；两文件在允许 loopback 后 `31 passed, 8 subtests passed`。合并结果为收集项全部业务断言通过、3 项预期 skip。
- `compileall`：`src/backend`、`scripts/diagnostics`、`scripts/dev` 通过。
- Bug strict：169/169 已归类，9 个复发簇，回顾周期未过期；recurrence JSON 和 `git diff --check` 通过。
- backend `/health` 返回 `healthy` / `1.0.0`。整栈没有因 Redis 预检失败被强行停止；只做了通过安全门的窄 scheduler 替换。
- 新 scheduler PID 77876 唯一运行；第二实例拒绝与真实 once 已验收。通知 Celery 收到 once 结果。完整后续 24 小时仍缺失，因此状态保持 OBSERVING。

## 根因闭环状态表

| 问题指纹 / 复发键 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险 / 责任动作 | 下一轮验收条件 |
| --- | --- | --- | --- | --- | --- | --- |
| `f28c58ebb852d69c` / `RUNTIME-TOPOLOGY-RELOAD` / `HOT-PATH-PAYLOAD-POLLING` | 双 scheduler；未变化 JSON 全列回写 | OS 单实例锁、可比较投影、仅变更字段、模型唯一声明去重 | 新 PID 唯一；第二实例 exit 2；真实 once 6/6；UPDATE max 889.54ms | 新增且同类复发 | `OBSERVING`；缺完整窗，重复物理索引待 DBA | 24h 仅一个调度序列；旧全列指纹 0；窄 UPDATE P95<500ms |
| `60ff7467696400d3` | 重复配置远端往返 + 网络长尾 | 既有 TTL；本轮无掩盖性改动 | 109/231.458s/P95 4.847/max15.761；executor0.026ms | 是 | `OPEN`；应用 owner 追 cache hit/miss/失效 | 次数和累计显著下降，P95<500ms，并按 origin 证明命中 |
| `PGBOUNCER-NETWORK-QUERY-CONNECT` | 连接/传输长尾，非 executor/锁/索引 | 既有网络诊断；本轮保留证据 | query/connect 3/7；hold P95 21.033s；无 lock/idle-tx | 是且 connect 恶化 | `OPEN`；运维关联 source/socket/path | 连续 24h timeout/crash/TLS/server-closed=0，fetch/checkin P95<500ms |
| `SMTP-CROSS-EVENT-PROVIDER-STORM` | 每个 milestone event 各自拥有重试预算，provider 故障时同步风暴 | 既有单 event 预算/指数退避；Bug/registry 已更新 | 142 success、51 retry、13 fail | 是 | `OPEN`；通知 owner 实施 provider breaker + durable pending | 故障窗口无同步风暴；最终送达/明确终态可审计 |
| `VIDEO-EDITOR-HIDDEN-PAIR-POLL-20260818` | hidden 页无 visibility/single-flight | 上轮共享 controller | status 323→5、Publisher 321→0 | 本窗未捕获旧模式 | `OBSERVING`；缺完整真实 hidden 页 | hidden 计数归零、visible 单次恢复、无 overlap，完整 24h |
| `WATCHDOG-SUCCESS-REPAIR-SESSION-20260818` | 成功 repair wrapper 未 release | 上轮 finally release | 新代码运行；本窗 hold 仍含切点前样本 | 同类历史复发 | `OBSERVING` | 完整窗无跨 repair/file/broker checkout、无 idle-tx |
| `ZSXQ-INSIGHTS-READ-SCOPE-20260818` | 查询后 CPU/缓存曾持有 Session；当前剩余网络 fetch/rollback | 上轮有界 read scope | overview 仍有 26.856s 尾延迟；executor35.423ms | 网络部分是 | 应用 `OBSERVING`、网络 `OPEN` | CPU阶段 checkout=0；fetch/checkin P95<500ms；完整窗无 >5s hold |
| `MANAGED-SQL-SOURCE-CLASSIFIER-20260818` | 显式 shell 元数据被共享日志路径覆盖 | 来源优先级 + 回放契约 | 重跑 application error0、ad-hoc1；20 tests | 是，验收纪律复发 | `OBSERVING`；继续三源对账 | 显式 shell 保持 adhoc，真正托管错误不落入 adhoc |
| `BILIBILI-BVID-DUPLICATE-INDEX` | 模型列级 unique/index 与表级约束重叠 | 模型只保留表级约束；人工 SQL 已生成 | 两索引定义/stats/owner/EXPLAIN 已取证 | 新增 | `OPEN/人工阻塞`；自动化禁止 DDL | DBA 执行脚本，保存锁、定义、EXPLAIN、验证和回滚证据 |

## 未捕获边界

- `pg_stat_statements` 不可用，不能获得服务器端跨应用完整累计；本报告用应用日志、PgBouncer、catalog 与现场 EXPLAIN 三角验证。
- SQLAlchemy execute hook 不覆盖全部 result fetch、rollback 与 checkin；lifecycle 的非 SQL 段不能一律解释为 Python CPU。
- `sync/status` 有效 POST、真实 hidden 浏览器完整窗口、下一次 SMTP provider 故障和修复后整整 24 小时 scheduler 窗口均未捕获。
- 目标表在本窗没有慢 SQL不表示不存在全表读、大字段或 N+1；只表示当前日志没有样本。
- HTTP 400/404 影响请求失败率，但不能误报为数据库错误；窗口后一次人工 stdin SQL 引号错误也属于 ad-hoc，不计入固定窗口应用基线。
- 标准整栈重启因 Redis 控制面预检超时而安全中止；backend/Celery 未重载本轮无关代码。Bilibili scheduler 已独立完成运行替换，其他观察项仍使用既有新 PID 证据。

## 下一轮明确验收计划

1. 先以新 PID 77876 和 06:07:58 切点复核完整 24 小时：只允许一个调度序列；`f28c58...` 全列 UPDATE 为 0；窄 UPDATE P95<500ms；抓取/新增/更新语义完整。
2. 将每个 PgBouncer timeout 与 request/task id、SQL origin、server source、连接年龄、execute/fetch/rollback/checkin 分段关联；connect 仍大于 0 时继续追网络和池配置，不加掩盖性重试。
3. 对 `60ff...` 按 web/scheduler origin 统计 cache hit/miss 和失效来源；若 executor 仍毫秒级但累计居首，继续根治跨进程缓存/轮询。
4. 捕获下一次 SMTP 故障，验证 provider 级背压和 durable pending 方案；未落地前保持 OPEN，不把单次成功视为关闭。
5. 复核视频 hidden、Watchdog、Insights 三项后续窗口；没有真实运行样本继续 OBSERVING。
6. DBA 仅在维护窗口逐段执行 Bilibili bvid 与既有 comments hash 索引脚本；执行前后必须保存 stats、EXPLAIN、锁等待、验证和回滚证据，自动化不得代执行。
