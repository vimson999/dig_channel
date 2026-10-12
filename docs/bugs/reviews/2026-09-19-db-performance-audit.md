# 2026-09-19 每日数据库性能与错误巡检

## 窗口与结论

固定窗为 2026-09-18 06:00–2026-09-19 06:00（Asia/Shanghai），先对齐 09-18 报告、自动化记忆、Bug、Session 纪律与复发登记。扫描 `slow_query.log`、顶层 `app/backend/scheduler` 及独立 scheduler、轮转日志、`celery/*.log` 和 PgBouncer；另以只读数据库快照和 `EXPLAIN (ANALYZE, BUFFERS)` 对账。**没有任何网络、队列或端到端根因达到 CLOSED**：完成慢 SQL 改善且 PgBouncer 本窗六类故障为零，但新进程 06:18 后到窗口末尚差约 18 分钟才满 24 小时，且链路瞬断断点未定位。更重要的是，持久化任务对账发现五条 Publisher 被修复器反复换 ID 后失败，Celery 的五次“成功”是 stale no-op。

| 指标 | 09-18 前窗 | 本窗 | 变化/口径 |
| --- | ---: | ---: | --- |
| 完成慢 SQL/累计 | 16/30.106s | 11/15.149s | -31.3%/-49.7%；前窗另有失败 SQL 15.208s，不在完成慢日志 |
| P50/P95/max | 1.188/4.772/6.118s | 1.154/2.289/2.289s | P95 -52.0%、max -62.6%，不等于网络根治 |
| HTTP 完成/成功/失败 | 749/745/4 | 176/175/1 | 流量 -76.5%；本窗唯一失败是 favicon 404，业务 5xx=0 |
| HTTP P50/P95/max/累计 | 6.52/1099.46/5244.33ms/226.034s | 247.14/741.27/5251.91ms/62.006s | 业务结构改变，P50 不可直接视为相同负载恶化 |
| Celery 完成/失败/retry | 208/1/0 | 137/2/0 | 2 个媒体合成失败；5 个 stale skip 计在完成内，数据库另有 5 个修复上限失败 |
| PgBouncer query-wait/TLS/crash | 1/1/1 | 0/0/0 | 288/288 新后端连接来自 100.96.3.12；无代理 TUN 回退 |
| >5s Session hold / idle offender | 2/28 | 0/10 | 本窗短 offender 最大事务/idle 0.409/0.381s，defer=0；未捕获不等于无短跨 I/O |

## 按影响排序的调用链

1. **Publisher 队列复发（P0，五名业务任务受影响）**：数据库只读对账显示本窗新建 `publisher` 19 completed、7 failed、2 canceled；7 failed 中五条为 queue-repair limit，两条为合成。14:12–14:32 watchdog 对 `a6069ada` 等五条判定当前 ID 不可见，发布替代消息、CAS 换 ID、撤销 superseded 消息；render 随即收到旧 ID 并按 current-id guard 跳过，最终五条 `failed/failed`。这不是数据库 SQL 失败；`_message_visible_in_queue` 只检查目标 list 和 unacked 各前 1000 项，是否截断或存在 broker→worker 竞态，缺当时的队列长度、位置、consumer 快照，**上游可见性误判尚未证实到单一断点**。与 09-11 同键反复，不能再通过调大 grace/次数假收口。另两条 `487a9218`、`30e8faa0` 的任务载荷有一个 audio segment，但调用视频编辑器前日志为 `Audio Segments: 0`，分别抛 `No valid audio clips` 与空集合 `max()`；需追输入投影到旧单文件分支的转换，非 DB/索引故障。
2. **内容 Web 提交与历史轮询（用户可见）**：4 次提交 4/4 返回 202、累计 20.663s、P95/max 5.252s；request `18a8e56f` 的 15 资产只做一次 canonical SELECT、一次批量 INSERT，单次 5.252s 中有任务入库、broker、两次串行通知 outbox 的小 SQL（最高 277ms），没有 >=1s 同 request SQL。缺接收/哈希/commit/broker/outbox 阶段 marker，不能精确分摊剩余时间；前窗 6 次 P95 5.244s，按请求未改善。历史 GET 70 次/27.170s，前窗 105/33.781s；量降 33.3%、累计降 19.6%，但 P95 435→570ms（+31.2%）、每请求平均 322→388ms。轮询 visible/active/single-flight 的真实浏览器状态未捕获，保持 OPEN。
3. **后台 SQL 长尾/链路**：11 条慢 SQL 分散于通知 due-provider 2 条/2.518s、内容任务状态 UPDATE 2.289s、ZSXQ 发布 UPDATE 2.007s、Insights 聚合 1.767s、canonical register 1.198s 等；4 条涉及 `publication_tasks`、1 条 `zsxq_topics`、其他目标表 0。`system_configs` all-tabs 读取 1.062s；无同窗失败 SQL、statement/lock/query/connect timeout。现场同形 executor 是 0.025–35.158ms，无 waiting lock 与长 idle 事务；秒级 SQL 更像驱动/远端往返尾延迟，不支持盲加索引。一次 PgBouncer 分钟平均不能分解单个 SQL 的 pool/network 比例，根因仍 OPEN。
4. **B站情报（后台外部 I/O）**：前窗 24/24 整点外部连接失败；09-18 07:18–09-19 05:24 的 23 个真实整点均 6/6 completed，单轮 7.558–19.822s。改善有真实任务证据，但 DNS/代理/TUN 断点未采到故障同秒现场，不能 CLOSED。独立日志 06:09–06:16 的 15 条 server-closed 文本、3 条 SQL traceback 与多条 `partial total=2` 来自全量单测，非真实故障/同步；根因是导入 runner 时全局 root logger 挂生产 FileHandler。本轮用 TDD 把 handler 初始化移到 `main()`，不改调度频率、外部连接或生产配置；下一次正常启动验收日志文件。
5. **低频、无慢 SQL 前台**：`POST /api/v1/channels` 唯一请求 3.560s/201，request `7dfa4741` 记录多次 28–260ms 小 SQL 和无标记间隙，无匹配 >=1s SQL；Web submit 4 次亦同。窗内没有 <=5 次且 >=10s 的 HTTP。PDF clean 4 次成功累计 1792.467s、ZSXQ publish 4 次成功累计 1392.898s，是无对应前台端点的长后台任务，不可把外部处理累计秒数当作 Session 持有。

`slow_query.log` 只收完成查询；本窗结构化 SQL error 0、托管应用 traceback SQL 0，独立测试日志模拟 traceback 3、server-closed 15（诊断/测试流）；PgBouncer connect/query/query-wait、TLS、server crash、HBA/login 全 0。短暂 idle offender 10 episode、最大 3 连接，当前只读 `pg_stat_activity` 仅 15 个 idle/ClientRead、无 lock wait 或 idle-in-transaction。PgBouncer 分钟 wait/query P50 15.38/47.63ms、P95 46.97/79.02ms、max 165.84/170.72ms，建连 288/24h=12/h。Tailscale 当前直连远端 `100.92.99.39`，本地 route `utun6`，不是代理 TUN。

## ZSXQ 与数据访问模式

固定窗没有自然 Insights overview、topics/local、tabs 或 system-sync scheduler status 请求；不能据此判未复发。窗后 06:05 对真实存量组做只读 API 验收：overview 200/0.350s/64,919B（request `5ce63ffe`，服务端 281.61ms，缓存命中路径无慢 SQL）；topics/local 200/0.447s/49,335B（`a80008ac`，服务端 390.60ms）；tabs 200/0.269s/1,192B（`24d2861d`，213.16ms）；scheduler status 200/0.471s/6,361B（`e8615958`，441.20ms）。这些是主动 API 样本，不是浏览器页面或固定窗自然负载。新调度器 09-18 06:18 启动读到两条 `status=0` 配置，`targets=[] queued=False`；窗口内没有真实 DB 配置异常，因此 fail-closed 修复只到 RUNTIME_REPLACED/OBSERVING。06:07/06:15 的 DB 故障注入、`queued=True`、恢复日志均为上一轮单测污染，不是生产意外投递。

有界批量 canonical 15 行仍是 1 查 1 插，未见逐文件 N+1；本窗没有音频素材目录自然请求，所以前窗 2 条/8.933s 的同指纹不能以“零”宣称改善。`publication_tasks` JSON/TOAST 107MB、topics TOAST 79MB，Web 历史用两阶段窄列读取；不以删原始 JSON 或缺字段换性能。Insights 真实聚合 SQL 1.767s（06:18 bootstrap，request unknown），现场 8,229 组内行、6,707 canonical 行 executor 35.158ms；原始全文/去重聚合还需结合冷 key、传输字节、缓存命中与 Page Visibility 复核。Web 提交/页面上本窗未发现已完成慢 SQL，但 <1s SQL、broker、文件和外部 I/O 不在慢阈值内，不能误报无瓶颈。

## 表与索引只读证据

09-19 06:02 只读快照：`publication_tasks/zsxq_topics/zsxq_topic_comments/zsxq_attachments/zsxq_sync_runs/external_documents` 总大小分别 137/129/118/78/37/约 7MB（前窗 135/129/118/78/37/7MB）。索引扫描累计占比 99.80/99.54/99.87/99.95/99.92/98.77%，自较早统计周期累计而非日增量；dead tuples 2273/1584/5644/37/46/146。HOT 比例 1.24/0.71/70.46/0.12/0.33/0%，前两类与 sync_runs 的高非 HOT 更新仍需按具体写列/索引维护成本研究。精确重复索引 0，`pg_stat_statements` 未安装。

代表 `EXPLAIN (ANALYZE, BUFFERS)`：all-tabs 18 行配置顺扫取 2 行 0.044ms（合理），Publisher feature 列表走 `ix_publication_tasks_feature_created_desc_v2` 0.087ms/20 行，内容任务 0.164ms/20 行，ZSXQ run 1.723ms/120 行，chart 3.213ms/1365 行，Insights 35.158ms。低使用候选 `ix_zsxq_attachments_pdf_assets_group_tab` 5.0MB/scan1、`ix_zsxq_topics_group_type_create_desc` 3.7MB/scan1 仍缺真实参数 SQL 与该形状 EXPLAIN，且统计为累计值。**不执行、不建议立即执行任何 CREATE/DROP INDEX/ALTER/回填/生产配置；本轮无待人工 DBeaver SQL 脚本。**如后续证实需 DDL，再单独给逐条 SQL、lock/statement timeout、验证与回滚，不能用应用长尾替代执行器证据。

## 根因闭环状态表

| 指纹/复发键 | 根因与已落地变更 | 运行态/是否复发 | 剩余风险、责任动作及下一轮验收 |
| --- | --- | --- | --- |
| PgBouncer query-wait/TLS；`DB-SESSION-LIFECYCLE` | 上窗私网链路瞬断；本窗未修改网络，executor/route/池三源已复核 | 本窗六类 0、288/288 正确 source；**OPEN/OBSERVING**，不能以单窗零事件关闭 | 网络维护者在下次尖峰同步采 Tailscale/代理、SHOW POOLS/SERVERS、socket 与 PG wait；完整后窗零 timeout/crash、真实 API/任务连续成功且断点定位 |
| 五条 Publisher 修复上限；`ORCH-PARENT-CHILD-STATE` | 反复判消息缺席→换 ID→旧 delivery stale skip→终态失败；本轮只读诊断与 Bug 复发登记，未改业务代码 | **OPEN/REPEATED**，五条真实失败，Celery “成功”是 no-op | 编排维护者下次首次判不可见前记录 queue/unacked 长度、扫描是否截断、worker 快照、PID/心跳、delivery；据现场复现后修上游、回归、全量、安全重启、自然业务验收；不可自动重投历史失败 |
| `web-submit`/`web-submissions`；`HOT-PATH-PAYLOAD-POLLING` | 既有批量 1 查 1 插保留；本轮没有性能代码修复 | 4/4 submit 202 但 P95 5.252s；history P95 +31%；**OPEN** | 内容维护者加六阶段/Session marker，浏览器 visible/active/single-flight 对账；下一窗按每请求与每资产比较，验收 outbox/asset 唯一和终态 |
| 音频目录 `be891b5c1af362da` | 既有 canonical 缓存/有界读取；本窗无请求 | **未观测，OPEN/OBSERVING**；不是零复发证据 | 下一自然冷/暖请求采 pool/driver/fetch bytes、行数和 SQL 数（冷<=2、暖0），完整窗口无 >5s 长尾 |
| ZSXQ DB fail-open；`RUNTIME-TOPOLOGY-RELOAD` | 09-18 改为读库失败 rollback/remove 并返回空计划 | 06:18 新 PID 启动停用计划 targets=[]，无生产故障样本；**RUNTIME_REPLACED/OBSERVING** | 调度器负责人在真实或受控故障后证明 0 job/0 投递、恢复仍 status=0，并验收完整后窗 |
| B站情报外部连接；`EXTERNAL-CALL-RESILIENCE` | 断点未定位、未修改网络 | 23 个真实整点 6/6 成功（前窗 24 失败）；**OPEN/OBSERVING** | 网络维护者保留下次失败同周期 DNS/proxy/TUN/连接目标和 HTTP 分类，后续完整窗持续 6/6 |
| runner 导入挂 FileHandler；`REGRESSION-ACCEPTANCE-INTEGRITY` | TDD 将 handler 从 import 移到 `main()`，新增导入/显式启动合同、纪律 | 15 项定向通过；测试未再写假 partial，旧运行 PID 仍正常；**CODE_VERIFIED/OBSERVING** | 下次正常重启后验证独立文件继续收到真实整点、无单测模拟错误；巡检坚持三源分栏 |
| Publisher 空音频输入 | 两个任务载荷有 segment、编辑器入口却见 0；本轮仅归因到分支交接，未修 | 2 Celery failed 与 2 durable failed，**OPEN** | 媒体维护者追 `audio_segments` 到 legacy `audio_file` 投影，补合成前校验/契约，真实产物和后窗验收 |

## 验证、风险与下一轮

本轮 TDD：导入 runner 的生产 handler 合同修复前 FAIL，显式配置合同修复前 AttributeError，修复后调度器定向 15/15；09-18 的模拟 15+3 条已按来源单列。全量 unit 首跑沙箱网络权限拒绝；授权本地依赖后 1693 项中 1 项失败，为既有排行榜测试把运行当天硬编码为 09-15。已只改该测试冻结夹具日期、定向通过；最终全量复跑 **1693/1693 OK（82.154s）**。`compileall`、worker app factory AST、Celery revoke 边界合同（321 文件）、Bug strict **204/204**、registry JSON 校验与 `git diff --check` 均通过。无应用业务运行时变更需即时重启；现有调度器已在正常记录整点，贸然整栈重启可能打断任务。本轮没有执行重启脚本、DDL、回填、数据补投或网络配置修改。

下一轮先查五条 Publisher 根因现场及待验收任务、前次 ZSXQ fail-closed 完整后窗，再查链路瞬断与整点 B站情报；采自然 ZSXQ 四入口与音频目录，补 Web submit 六阶段/轮询触发因子，对失败任务终态和 outbox/资产完整性只读对账。无自然样本继续 OPEN，不从零开始扫描。日志只有 request 完成行与 >=1s 慢 SQL，PgBouncer 平均及 PG 当前快照不能反推单 request pool wait；测试污染过滤依赖业务键，需下一次真实故障检验。当前没有足够依据将任何缺样本项判 CLOSED。

## 固定窗全部已捕获端点

以下按累计耗时降序列出 55 个 method/path；`n` 为完成数，`成/败` 为 HTTP 状态 <400/>=400，耗时单位 ms，P95 为分析器的最近秩分位数。业务表之外的静态资源也保留，以免把“未列出”误说成“没有请求”。

| method/path | n | 成/败 | 累计 | P50/P95/max |
| --- | ---: | ---: | ---: | ---: |
| GET `/api/v1/content-pipeline/web-submissions` | 70 | 70/0 | 27170 | 334/570/1557 |
| POST `/api/v1/content-pipeline/web-submit` | 4 | 4/0 | 20663 | 5215/5252/5252 |
| POST `/api/v1/channels` | 1 | 1/0 | 3560 | 3560/3560/3560 |
| GET `/api/v1/channels` | 5 | 5/0 | 1924 | 392/409/409 |
| GET `/api/v1/rankings/all` | 2 | 2/0 | 1480 | 572/908/908 |
| GET `/api/v1/data/chart-data` | 2 | 2/0 | 1211 | 404/808/808 |
| GET `/api/v1/data/summary` | 1 | 1/0 | 940 | 940/940/940 |
| GET `/api/v1/rankings/monthly-revenue` | 2 | 2/0 | 756 | 349/407/407 |
| GET `/api/v1/data/table` | 1 | 1/0 | 741 | 741/741/741 |
| GET `/api/v1/bilibili-intelligence/charging-dashboard` | 1 | 1/0 | 671 | 671/671/671 |
| GET `/api/v1/rankings/monthly-subscriber` | 1 | 1/0 | 457 | 457/457/457 |
| GET `/api/v1/rankings/daily-revenue` | 1 | 1/0 | 424 | 424/424/424 |
| GET `/api/v1/rankings/daily-subscriber` | 1 | 1/0 | 424 | 424/424/424 |
| GET `/api/v1/channels/categories` | 2 | 2/0 | 345 | 128/217/217 |
| GET `/content-pipeline-submit.html` | 2 | 2/0 | 284 | 139/145/145 |
| GET `/api/v1/rankings/yearly-subscriber` | 1 | 1/0 | 260 | 260/260/260 |
| GET `/api/v1/rankings/yearly-revenue` | 1 | 1/0 | 212 | 212/212/212 |
| GET `/index.html` | 1 | 1/0 | 159 | 159/159/159 |
| GET `/js/shared-navigation.js` | 5 | 5/0 | 44 | 9/19/19 |
| GET `/js/api.js` | 3 | 3/0 | 27 | 7/17/17 |
| GET `/js/env.js` | 5 | 5/0 | 26 | 3/11/11 |
| OPTIONS `/api/v1/channels` | 6 | 6/0 | 25 | 2/14/14 |
| GET `/js/channel-management.js` | 2 | 2/0 | 22 | 7/15/15 |
| GET `/js/config.js` | 3 | 3/0 | 21 | 5/12/12 |
| GET `/js/bilibili-charging-dashboard.js` | 1 | 1/0 | 18 | 18/18/18 |
| GET `/js/utils.js` | 3 | 3/0 | 15 | 4/10/10 |
| GET `/api/v1/auth/verify` | 6 | 6/0 | 14 | 1/8/8 |
| OPTIONS `/api/v1/channels/defaults` | 2 | 2/0 | 12 | 1/11/11 |
| OPTIONS `/api/v1/auth/verify` | 6 | 6/0 | 11 | 2/3/3 |
| GET `/health` | 4 | 4/0 | 9 | 2/4/4 |
| GET `/css/main.css` | 1 | 1/0 | 8 | 8/8/8 |
| GET `/js/dashboard.js` | 1 | 1/0 | 8 | 8/8/8 |
| GET `/js/new-main.js` | 1 | 1/0 | 7 | 7/7/7 |
| GET `/js/data-table.js` | 1 | 1/0 | 6 | 6/6/6 |
| GET `/js/rankings-trends.js` | 1 | 1/0 | 6 | 6/6/6 |
| GET `/assets/images/apple-touch-icon.png` | 1 | 1/0 | 4 | 4/4/4 |
| GET `/js/content-pipeline-submit.js` | 2 | 2/0 | 4 | 2/3/3 |
| OPTIONS `/api/v1/rankings/monthly-revenue` | 2 | 2/0 | 4 | 1/2/2 |
| GET `/channels.html` | 1 | 1/0 | 3 | 3/3/3 |
| GET `/api/v1/channels/defaults` | 2 | 2/0 | 3 | 1/2/2 |
| GET `/favicon.ico` | 1 | 0/1 | 3 | 3/3/3 |
| GET `/js/rankings-page.js` | 1 | 1/0 | 3 | 3/3/3 |
| OPTIONS `/api/v1/channels/categories` | 2 | 2/0 | 3 | 1/2/2 |
| OPTIONS `/api/v1/bilibili-intelligence/charging-dashboard` | 1 | 1/0 | 2 | 2/2/2 |
| OPTIONS `/api/v1/data/chart-data` | 2 | 2/0 | 2 | 1/1/1 |
| GET `/rankings.html` | 1 | 1/0 | 2 | 2/2/2 |
| OPTIONS `/api/v1/rankings/all` | 2 | 2/0 | 2 | 1/1/1 |
| OPTIONS `/api/v1/rankings/yearly-subscriber` | 1 | 1/0 | 2 | 2/2/2 |
| OPTIONS `/api/v1/rankings/daily-subscriber` | 1 | 1/0 | 2 | 2/2/2 |
| OPTIONS `/api/v1/rankings/monthly-subscriber` | 1 | 1/0 | 2 | 2/2/2 |
| GET `/site.webmanifest` | 1 | 1/0 | 1 | 1/1/1 |
| OPTIONS `/api/v1/rankings/yearly-revenue` | 1 | 1/0 | 1 | 1/1/1 |
| OPTIONS `/api/v1/data/summary` | 1 | 1/0 | 1 | 1/1/1 |
| OPTIONS `/api/v1/rankings/daily-revenue` | 1 | 1/0 | 1 | 1/1/1 |
| OPTIONS `/api/v1/data/table` | 1 | 1/0 | 1 | 1/1/1 |
