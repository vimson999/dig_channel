# 2026-08-08 数据库性能与错误巡检

## 结论

本轮原始巡检没有达到“根因已闭环”的标准；2026-08-08 11:34 已完成
`route-exclude-address` 物理绕过 A/B，当前结论更新为 **ISOLATED / OBSERVING**。
短窗口已证明 PostgreSQL socket 脱离 TUN 且负载测试无超时，但仍需后续完整 24 小时窗口才能写成已解决。

最近 24 小时应用慢 SQL 数量和累计耗时明显下降，但 P95 反而恶化；12 次去重后的 PgBouncer `query_timeout` 与多个 60 秒 API/worker 黑洞同窗发生。真实 SQL 的 `EXPLAIN (ANALYZE, BUFFERS)` 仅需 0.154ms，而应用侧同一批查询最高 31.729s。原始巡检进一步确认 PostgreSQL 目标 `121.4.126.31` 的路由网关为 `198.18.0.1`、接口为 `utun8`，PgBouncer 后端连接的本地地址也全部为 `198.18.0.1`。因此可以排除 PostgreSQL executor、锁和缺索引作为共同主因，并把故障边界定位到 TUN/物理出口/远端传输路径；当时尚未完成绕过 TUN 的 A/B，不能把某个代理节点写成已证实根因。

该路由属于主机/代理外部状态，原始无人值守巡检没有修改。后续跟进证明普通 DIRECT 仍经 TUN；11:34 已在确认物理出口 HBA 后使用 Mihomo `tun.route-exclude-address` 完成真正的路由排除并重建 PgBouncer。现在只剩后续完整窗口复发验证。只降低超时、增加重试或新增索引都不能根治。

### 11:18 跟进验收修正

- 用户已把 `IP-CIDR,121.4.126.31/32,DIRECT,no-resolve` 写入 Clash Verge merge 配置，但 Mihomo controller 的 577 条运行规则没有该条；数据库新连接仍命中既有第 433 条 `GeoIP(cn) -> DIRECT`。
- 普通 DIRECT 只是 TUN 捕获后的出站策略。`route` 和新建 PgBouncer socket 仍为 `utun8` / `198.18.0.1`，所以本次调整没有完成原验收条件。
- 10:57:51–11:22:00 仍捕获 1 个去重 query timeout；11:05:17 的 ZSXQ Session 持有 63.134s，非 SQL 60.195s。另有 20 个 HBA 拒绝、20 个 server login failure、21 个 cached login failure，拒绝的直连出口为 `223.80.177.86`。
- 在 `sv_active=0` 时执行 `RECONNECT show_ytb_data`；六条 socket 于 11:18 重建但仍经 TUN。20 次 `SELECT 1` P50/P95/max 26.153/135.760/159.705ms，backend health 为 healthy。
- 11:18:13–11:23:00 的短窗口 102 个真实请求全部成功、timeout/HBA 暂为 0，但应用慢 SQL P95 仍为 9.184s。短窗口不构成不复发证据。

### 11:34 route-exclude A/B 结果

- 已把 `121.4.126.31/32` 写入当前订阅实际绑定的 Merge 和 Clash Verge GUI 管理的 TUN 配置，
  重新激活后 Mihomo 运行配置包含 `route-exclude-address`。
- 系统路由从 `198.18.0.1/utun8` 切换为 `192.168.12.1/en1`；项目 PgBouncer 重建后，
  六条服务端连接的 `local_addr` 为 `192.168.12.104`，不再是 `198.18.0.1`。
- PostgreSQL 端观测到物理出口 `223.80.177.86/32`；HBA 已精确更新并 reload。
- PostgreSQL 只读查询、Redis DB、Celery broker 全部通过。100 次、10 并发的正确事务归还
  `SELECT 1` 为 100/100 成功，P50/P95/max 72.63/232.39/240.92ms。
- 切换后的短窗口未出现 query/connect timeout、HBA 或 server login failure。网络问题进入
  `ISOLATED/OBSERVING`，保留完整 24 小时真实负载验收，不提前标记 CLOSED。

## 巡检窗口与基线

- 本轮窗口：2026-08-07 06:02:19 至 2026-08-08 06:02:19（Asia/Shanghai）。
- 上轮基线：2026-08-06 报告中的同口径 24 小时统计。
- 数据源：`logs/slow_query.log`、`logs/app.log`、`logs/backend.log`、`logs/scheduler.log`、`logs/celery/*.log` 及轮转压缩日志。
- 统计边界：慢 SQL 分离 application 与 adhoc diagnostics；PgBouncer 同一事件的三份镜像日志按时间/内容去重。

| 指标 | 上轮 | 本轮 | 变化 | 判断 |
| --- | ---: | ---: | ---: | --- |
| 应用慢 SQL 数 | 2,125 | 861 | -59.5% | 改善，但不能代表根因消失 |
| 应用慢 SQL 累计耗时 | 6,332.341s | 2,494.784s | -60.6% | 改善 |
| 应用慢 SQL P50 | 2.083s | 1.525s | -26.8% | 改善 |
| 应用慢 SQL P95 | 7.006s | 7.952s | **+13.5%** | 恶化 |
| 应用慢 SQL最大值 | 60.645s | 60.615s | -0.05% | 基本不变，60 秒黑洞仍在 |
| HTTP 请求数 | 6,236 | 3,315 | -46.8% | 流量下降，不能直接归因于优化 |
| HTTP 失败率 | 1.94% | 1.06% | -0.88pp | 改善 |
| HTTP P50 / P95 / 最大 | 0.239 / 4.029 / 63.788s | 0.192 / 2.506 / 61.932s | P95 -37.8% | 改善，但尾部仍受 60 秒黑洞支配 |
| HTTP 累计耗时 | 5,200.974s | 2,305.165s | -55.7% | 改善 |
| PgBouncer 去重 query timeout | 10 | 12 | **+20%** | 反复且恶化 |
| PgBouncer connect timeout | 1 | 2 | **+100%** | 反复且恶化 |
| statement / lock timeout | 0 / 0 | 0 / 0 | 持平 | 未捕获不等于绝对未发生 |
| idle in transaction | 0 | 0 | 持平 | 运行态观察中 |

慢 SQL 原始记录 875 条，其中 application 861 条、adhoc diagnostics 14 条。应用 SQL 错误 2 条，分别位于 report-audio `_live_child_tasks` 和 MP3 `load_page`；另有 3 条诊断查询错误已排除在业务错误统计外。

## 根因优先级

按累计耗时、P95、最大耗时、失败/用户影响和复发次数综合排序，而非只按出现次数：

| 优先级 | 问题指纹 | 次数 / 累计 | P95 / 最大 | 影响 | 判断 |
| --- | --- | ---: | ---: | --- | --- |
| P0 | `DB-NETWORK-TUN-QUERY-TIMEOUT` | 12 次去重 query timeout、2 次 connect timeout | 多条约 60s | API 500/503、worker 卡住、跨多个业务域 | 原始窗口新定位；现已隔离观察 |
| P0 | `DB-SESSION-LIFECYCLE` | 147 次长连接持有；SQL 1,555.206s、非 SQL 868.427s | 60.485 / 63.439s | 放大网络黑洞与并发占用 | 反复；根因簇回退为 located |
| P1 | ZSXQ snapshot 两个 SQL 形态 | 81 次 / 370.932s | 31.346 / 31.729s | 同步 worker；无前台端点 | executor 0.154ms，主因是网络等待 |
| P1 | report-audio asset reference | 16 次 / 107.459s | 16.071 / 60.167s | 状态页 500、用户轮询 | 网络黑洞；非查询形态问题 |
| P1 | ZSXQ existing topic/comment batch update | 105 次 / 190.212s | 最高 3.506 / 8.261s | 后台同步累计耗时 | 批处理已替代逐条旧 origin，继续观察网络尾部 |
| P1 | PDF claim / owned claim | 7 次 / 68.723s | 最大 60.615s | 图片接口 500 | 网络黑洞 |
| P1 | publisher repair | 4 次 / 62.541s | 最大 59.302s | 后台修复任务 | 网络黑洞/连接持有交叉观察 |
| P2 | MP3 load task/list | 14 次 / 59.692s | 7.463 / 18.315s；另有失败请求 60.347s | 列表页 503 | 同窗 PgBouncer timeout，下一请求 505.6ms |

## 页面、API、后台任务与 SQL 关联

### 典型 60 秒链路

| request / 来源 | 端点或任务 | 结果 | SQL / Session 证据 | 同窗基础设施证据 |
| --- | --- | --- | --- | --- |
| `3a767a2e` | report-audio status | 500 / 60.287s | 窄字段任务查询 60.167s；lifecycle 60.198s；随后 child query 失败 | 同秒 PgBouncer query timeout |
| `1730dbc0` | PDF evidence claim image | 500 / 61.044s | 主键查询 60.615s；lifecycle 60.632s | 同窗 query timeout |
| `da65bdfb` | report-audio pdf-assets | 500 / 61.204s | SQL 尝试存在；non-SQL hold 60.390s | 同窗 query timeout |
| `43f0462b` | platform credentials | 500 / 61.932s | lifecycle 61.595s，其中 non-SQL 60.033s | 同窗 query timeout |
| `4c3764ae` | MP3 jobs list | 503 / 60.347s | 前置 SQL 117ms；列表 SELECT 失败；non-SQL hold 60.160s | 03:54:54 query timeout，03:55:04 connect timeout；8 秒后同端点 200 / 505.6ms |
| scheduler watchdog | pending repair | 卡住 61.665s | non-SQL hold 60.098s | 同窗 query timeout |
| Celery `b7e…` | ZSXQ sync | 多轮长持有 | 03:55、04:00、04:12 出现约 60 秒 hold | 与同一 timeout 簇重合 |

这些请求跨 report-audio、PDF、凭据、MP3、scheduler 和 ZSXQ，不共享 SQL 形态，却共享 60 秒边界和 PgBouncer timeout，支持“网络路径共同故障”而不是“某一张表缺索引”。

### 主要前台/API 指标

| 端点族 | 请求 / 成功 / 失败 | P50 / P95 / 最大 | 累计 | 慢 SQL 关联 |
| --- | ---: | ---: | ---: | --- |
| PDF claim image | 364 / 363 / 1 | 0.523 / 2.500 / 61.044s | 329.360s | 失败请求对应 60.615s PK SELECT |
| platform credentials | 340 / 339 / 1 | 0.369 / 1.276 / 61.932s | 226.224s | 60 秒主要落在 lifecycle 非 SQL 段 |
| report-audio status | 115 / 114 / 1 | 0.652 / 3.984 / 60.287s | 183.248s | 窄查询 60.167s |
| report-audio detail | 70 / 70 / 0 | — / 10.727 / 16.904s | 142.416s | 多条慢 SQL，未见失败 |
| Celery status | 12 / 12 / 0 | — / 32.999 / 33.060s | 115.966s | 低频高延迟 |
| MP3 list | 41 / 40 / 1 | — / 3.346 / 60.347s | 109.039s | 失败落在连接黑洞 |
| MP3 detail | 71 / 71 / 0 | — / 4.730 / 8.502s | 88.670s | 有慢查询但无失败 |
| report-audio pdf-assets | 11 / 10 / 1 | — / 61.204 / 61.204s | — | 60 秒 lifecycle 非 SQL 段 |
| report-visual job detail | 26 / 26 / 0 | 0.412 / 2.888 / 2.902s | 16.099s | 仅 1 条窄字段 SQL 2.101s；未见宽 JSON origin |

低频高延迟还包括 bilibili intelligence sync（3 次，最大 20.400s）、report-audio vertical generate（5 次，最大 17.295s）、Celery restart render（3 次、2 次失败，最大 12.443s）和 finance cover regeneration（4 次，最大 12.086s）。这些样本不足以证明稳定性，下一轮仍按累计耗时和失败率复核。

### 后台任务

Celery 记录成功 572、retry 70、failed 9；上轮为 774 / 135 / 38。重试和失败绝对数下降，但负载也下降，不能标记 CLOSED。

| 任务 | 成功 / retry / failed | P50 / P95 / 最大 | 累计成功耗时 | 说明 |
| --- | ---: | ---: | ---: | --- |
| ZSXQ sync | 24 / 9 / 0 | 374.574 / 970.773 / 1207.323s | 7,894.559s | 最大后台累计耗时；跨网络和外部附件 I/O |
| report-audio process | 34 / — / — | — / 598.246 / 1920.313s | 4,670.763s | 长任务，需继续检查 DB Session 与外部 I/O 交叉持有 |
| publisher run | 30 / — / — | — | 3,883.490s | 有 59.302s repair SQL 指纹 |
| upload-only | 17 / — / — | — | 2,551.559s | 外部平台 I/O 主导 |
| MP3 process | 124 / — / — | — | 1,788.319s | 列表 API 另有网络黑洞 |
| workflow notify | 183 / 60 / 9 | — | 246.607s | 外部邮件失败风暴仍需单独治理 |

## 三类异常边界

1. **低频高延迟**：Celery status、bilibili sync、vertical generate、restart render 等均已单列；不能因样本少忽略最大值和失败率。
2. **页面/API 很慢但无匹配慢 SQL**：platform credentials、report-audio pdf-assets、report-visual create、stick creation、restart、文件下载等出现明显非 SQL lifecycle 时间；其中 60 秒样本已与 PgBouncer/网络同窗关联，其余保留为应用/外部 I/O 观察项。
3. **后台任务或连接持有很久但无前台端点**：ZSXQ sync、watchdog repair、report-audio process 和 publisher repair 均存在；本轮 147 次 connection hold 的 P50/P95/最大为 8.663/60.485/63.439s，25 次未匹配 request、20 次由 managed context 标记。

日志没有捕获某页面、某轮询或某事务，不等于它没有发生；本报告只对当前日志覆盖作结论。

## ZSXQ / 生财有术专项

- `POST topics/local`：2 次全部成功，P50 0.832s、最大 8.567s，对应最慢 SQL 7.867s。
- `POST sync/runs`：2 次全部成功，最大 0.871s。
- `POST topics/local/tabs`：2 次全部成功，最大 0.321s。
- scheduler system status：1 次，0.637s。
- 本窗口没有捕获 `zsxq-insights overview` 请求，因此不能宣称该页面未复发。
- 数据库中窗口内 13 个 sync run 均为 completed。`1824528822` 的 auto run 能处理 610 topics；`28888112822211` 的 sys run 能处理 78 topics / 533 attachments，但至少两个批次各有 1 个下载失败，仍需复核外部附件错误。
- 旧的逐 topic origin `_update_existing_topic`、`_update_attachment_download_state`、`_upsert_topic_comments` 已在全窗口和新进程窗口消失；新的 batch origins 正常出现。这说明上一轮批处理代码已被运行态替换，但网络尾部仍使批查询变慢，故保持 OBSERVING。

代表性真实 SQL 使用最新 group、tab 和 20 个真实 topic id 执行只读 `EXPLAIN (ANALYZE, BUFFERS)`：命中 `uq_zsxq_topics_group_tab_topic`，actual 0.038..0.134ms，execution 0.154ms，shared hit 80。应用日志相同查询族 P95 31.346s、最大 31.729s；差值位于 executor 之外。

## 程序模式复核

- **N+1 / 逐条更新**：ZSXQ 旧 origin 未再出现，batch origin 已替换运行态；继续观察，不标记 CLOSED。
- **大字段/宽轮询**：report-visual job detail 26 次均成功，只有一次窄字段慢查询；没有捕获 `params/result` 宽 JSON SQL。相较上轮 1,759 次请求大幅下降，但流量变化很大且没有隐藏页签端到端证据，保持 OBSERVING。
- **超大分页**：未找到新的高累计超大分页指纹。不能以未捕获代替不存在；下一轮继续按 SQL origin 与返回行数复核。
- **重复去重/聚合**：insights canonical 查询仍使用既有索引；本轮 EXPLAIN 33.111ms，没有新增索引依据。
- **缓存失效/频繁轮询**：status、claim image 和 credentials 仍是高请求量端点，累计耗时高；60 秒失败主因是网络黑洞，但轮询放大影响，需在根因修复后再量化是否需要退避/缓存调整。
- **Session 与外部 I/O 交叉持有**：147 次长持有、non-SQL 868.427s，仍是放大器。运行态 guard 未捕获 idle-in-transaction/idle pressure，但不满足闭环。
- **失败重试/重复投递**：workflow notify 60 retry / 9 failed；属于外部邮件链路，本轮未实施掩盖式重试改动。

## PgBouncer、网络与 executor 证据

- PgBouncer：`query_timeout=60`，`min_pool_size=6`，`cl_waiting=0`，server check delay 5s，TCP keepalive 开启；stats wait P50/P95/最大 28.89/108.57/843.93ms，query 63.30/198.27/2575.66ms。
- 当前活动：仅 7 个 idle session，无 waiting lock、无 idle-in-transaction。
- `SHOW SERVERS` 的 6 个后端连接本地端点均为 `198.18.0.1`，远端为 `121.4.126.31`。
- `route -n get 121.4.126.31` 显示 gateway `198.18.0.1`、interface `utun8`、MTU 9000；系统 HTTP/HTTPS/SOCKS 代理为 `127.0.0.1:7890`，`utun8` 地址为 `198.18.0.1`。
- timeout 既发生在连接年龄仅数分钟时，也发生在数小时连接上，因此“只缩短 server lifetime”证据不足。
- 多个真实 SQL 在数据库端为毫秒级，而应用/PgBouncer 端达到 30/60 秒，排除“executor 慢”作为主要共同根因。

## 表规模、扫描与索引

| 表 | heap / toast / index / total | live / dead | seq scan / idx scan | idx 使用率 | HOT update |
| --- | --- | ---: | ---: | ---: | ---: |
| `publication_tasks` | 17 / 87 / 11 / 115MB | 11,735 / 2,111 | 3,746 / 2,532,473 | 99.85% | 1.42% |
| `zsxq_topics` | 13 / 75 / 32 / 120MB | 11,304 / 1,335 | 2,302 / 691,455 | 99.67% | 0.92% |
| `zsxq_topic_comments` | 91 / 4.24 / 23 / 118MB | 40,991 / 4,040 | 181 / 260,210 | 99.93% | 64.73% |
| `zsxq_attachments` | 30 / 0.04 / 37 / 67MB | 31,303 / 5,993 | 448 / 981,304 | 99.95% | 0.13% |
| `zsxq_sync_runs` | 1.096 / 31 / 0.904 / 33MB | 860 / 172 | — | 99.94% | 0.34% |
| `external_documents` | 0.832 / 4.880 / 1.208 / 6.920MB | 1,745 / 146 | 513 / 73,328 | 99.31% | 0% |

- 未发现定义完全重复的索引。
- `zsxq-insights` canonical 索引累计扫描从 1,064 增至 1,180（+116），应保留。
- 代表性 EXPLAIN：publication PK 0.024ms、channel 1.643ms、config 0.032ms、publisher list 0.070ms、content 0.453ms、recent JSON 0.024ms、without JSON 0.026ms、ZSXQ system runs 1.192ms、insights canonical 33.111ms。
- `pg_stat_statements` 未安装，跨时间累计 executor 排名仍受限；现有 SQL 日志 + EXPLAIN 已足以拒绝盲目加索引，但不足以支持删低使用索引。

**索引结论：本轮不新增、不删除、不改索引，不生成生产 DDL。** 低使用索引的统计窗口短，且存在低频业务，不能据此删索引。上一轮仅注释整理的 DBeaver 脚本仍保持人工执行属性，不是本轮阻塞项。

## 本轮已落地变更

1. 新增 Bug 记录 `2026-08-08-postgres-tunneled-route-query-timeout.md`，记录真实请求、路由、PgBouncer 与 EXPLAIN 证据。
2. recurrence registry 更新至 2026-08-08，将 `DB-SESSION-LIFECYCLE` 根因簇由 `runtime_replaced` 回退到 `located`，并把代理/TUN 直连校验加入防复发契约。
3. Bug 索引登记新问题；严格检查仍为 154/154 已分类。

没有修改应用代码、数据库 schema 或生产配置。根因修复需要外部网络状态变更，因此本轮没有运行 restart-safety，也没有重启 backend/Celery/scheduler；应用重启不能修复当前路由。

## 定向回归、完整测试与运行态验证

- 定向单测：20 项通过（DB performance analyzer、local PgBouncer config、database connection lifecycle）。
- 完整 unit tests：1055 项通过，37.919s。
- `compileall`：通过。
- `git diff --check`：通过。
- recurrence registry `jq empty`：通过。
- Bug 严格检查：154 条 Bug、154 条已归类、最近正式回顾 2026-08-08，通过。
- 运行态：后端、worker 和 scheduler 日志可读取，真实 API/worker 链路已用于定位；但根因路由尚未修改，因此没有“修复后真实运行态”证据，状态必须保持 OPEN。

## 人工动作、风险与验收顺序

本轮没有待执行 SQL，也不应执行 CREATE/DROP INDEX、ALTER TABLE 或数据回填。

需要人工完成的非 SQL 动作：

1. 先由数据库管理员为稳定物理出口配置 HBA/防火墙；当前已观测到出口 `223.80.177.86` 被拒绝。
2. 在 Mihomo 实际加载的 `tun` 配置中加入 `route-exclude-address: [121.4.126.31/32]`。普通 `IP-CIDR -> DIRECT` 不等价于绕过 TUN。
3. 用 controller、`route -n get 121.4.126.31` 验证配置已加载且 gateway/interface 不再是 `198.18.0.1` / `utun8`。
4. 在安全窗口用 PgBouncer admin `RECONNECT show_ytb_data`，或安全重启本地 PgBouncer，确保旧后端 socket 被重建；随后以 `SHOW SERVERS` 验证 local address 不再是 `198.18.0.1`。
5. 先做短只读 SQL 和真实 API 冒烟，再恢复 worker 调度；不要用提高 `query_timeout` 或无限重试掩盖失败。

风险：绕过代理后若直连出口、防火墙或数据库白名单不允许，会立即连接失败；因此必须保留原代理规则的可回滚方式，并在改变前记录配置。若直连不可用，应回滚网络规则并将出口/白名单问题作为新的明确阻塞，不能切回代理后宣称已修复。

## 根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险 | 责任动作 | 下一轮验收条件 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `DB-NETWORK-TUN-QUERY-TIMEOUT` | PostgreSQL 长连接被 TUN 捕获；普通 DIRECT 不等于物理绕过 | `route-exclude-address` 已进入运行配置；PgBouncer 已重建 | route 为 `en1`；server local_addr 为 `192.168.12.104`；100/100 并发只读测试成功 | 原窗口复发；修复后短窗未复发 | 订阅/TUN 配置漂移、动态公网出口 | 24 小时真实负载观察并推进 Tailscale | 完整窗口 0 timeout/60s 黑洞，真实 API/worker 全部成功 | **ISOLATED / OBSERVING** |
| `DB-SESSION-LIFECYCLE` | 长事务/连接持有放大网络和外部 I/O 阻塞 | 既有 lifecycle instrumentation/guard 仍在 | 147 holds；P95 60.485s；non-SQL 868.427s | **是** | 未匹配 request、外部 I/O 交叉持有 | 路由修复后再按 origin 排除残余持有 | 连续后续窗口无 60s hold；外部 I/O 不跨 Session | **OPEN / LOCATED** |
| `ZSXQ-BATCH-DATA-ACCESS` | 旧逐条数据访问已改 batch；本轮尾部由网络主导 | batch origins 已运行态替换 | 旧 origin 0；真实 EXPLAIN 0.154ms；run 全部 completed | 旧形态未复发，尾部仍在 | 附件外部 I/O、网络尾部 | 下一轮优先复核两个 snapshot 指纹 | 路由修复后 P95/最大回归且旧 origin 仍为 0 | **OBSERVING** |
| `HOT-PATH-PAYLOAD-POLLING` | 宽 JSON 高频轮询 | 既有窄字段 helper/guard | 26 次 detail 均成功；仅 1 条窄 SQL；无宽 origin | 本窗口未捕获 | 请求量大幅下降、隐藏页签未验收 | 真实页面含隐藏页签验收 | 足量真实流量下宽 origin=0、无请求风暴 | **OBSERVING** |
| `AMBIGUOUS-COMMIT-SCENE-ASSETS` | commit 成功后连接断开导致误报失败 | 上轮 probe-after-rollback 已落地 | 本轮所有 409 在修复前；修复后未再捕获 reconnect 文本 | 未见修复后复发 | 缺同一 workflow 的完整重复提交验收 | 安全窗口真实重复提交 | commit 后断链场景返回幂等成功且后续窗口无复发 | **OBSERVING** |
| `HBA-AUTH-REJECTION` | 动态物理出口未及时进入精确 HBA | 已放行 `223.80.177.86/32` 并 reload | PostgreSQL 观测地址匹配；修复后短窗 HBA/login failure=0 | 原窗口复发；修复后短窗未复发 | 家庭公网 IP 仍可能变化 | 迁移到 Tailscale 私网后移除公网依赖 | 完整窗口 HBA/login failure=0 | **OBSERVING** |
| `IDLE-IN-TRANSACTION-PRESSURE` | 历史 Session 生命周期违规 | 既有 guard/纪律 | log、pg_stat_activity 均 0 | 未捕获 | 低流量漏检 | 持续全窗口监控 | 连续窗口为 0 且 guard 真实触发测试通过 | **OBSERVING** |

## 下一轮明确验收计划

1. 第一优先读取本报告、Bug 与 registry，检查 `tun.route-exclude-address`、物理出口 HBA 和防火墙是否仍保持本次修复状态，不重复从零定位。
2. 记录 `route -n get` 的 gateway/interface，与 PgBouncer `SHOW SERVERS` local address 配对；任何一侧回到 `198.18.0.1`/`utun8` 都视为配置回退并重新打开隔离问题。
3. 路由生效并重建连接后，运行短只读 SQL、report-audio status、PDF claim image、platform credentials、MP3 list、ZSXQ topics/local 和一次真实 ZSXQ sync；记录 request id、SQL origin、Session hold 和 worker task id。
4. 对修复后窗口统计去重 query/connect timeout、60 秒 HTTP/worker、应用 SQL P95/最大、connection hold P95/非 SQL 总耗时。验收至少要求 query/connect timeout=0、60 秒黑洞=0，且不能靠减少请求量或拉高超时实现。
5. 继续复核 ZSXQ 两个 snapshot 指纹、旧逐条 origin、report-visual 宽轮询、scene-assets 幂等提交、HBA 与 idle-in-transaction；未捕获只保留 OBSERVING。
6. 只有真实 API/页面/worker 验收通过并在后续完整窗口确认未复发，才允许把网络根因标记 CLOSED/RUNTIME_REPLACED。
