# 2026-08-15 数据库性能与错误巡检

## 根因闭环结论

固定窗口为 **2026-08-14 06:02:19 至 2026-08-15 06:02:19（Asia/Shanghai）**。
本轮结论不是“已解决”：慢 SQL 和 HTTP 总体指标明显下降，但请求量也下降 40.6%；同时
13:07–13:10 真实发生 PostgreSQL 链路回退到代理 TUN `198.18.0.1`，触发 pool 等待、
server crash、TLS 错误和 16 条应用 SQL 失败。路由目前恢复到 Tailscale，但窗口后段仍有
query/connect timeout，因此网络根因继续 `OPEN`。

本轮落地的是诊断防线根因修复：分析器此前漏分 `query_wait_timeout`、server crash、TLS
receive error 和 server source address，现已由历史故障片段契约测试覆盖并回放验证。该修复
使复发能被自动识别，不等于网络本身已根治。没有执行 CREATE/DROP INDEX、ALTER TABLE、
数据回填、生产配置、路由修改或删除原始 JSON。

## 上一轮基线与本轮变化

| 指标 | 2026-08-14 基线 | 本轮 | 变化 | 判定 |
| --- | ---: | ---: | ---: | --- |
| 应用慢 SQL | 1,177 | 245 | -79.2% | 改善，但受流量下降影响 |
| 应用慢 SQL 累计耗时 | 2,895.935s | 555.812s | -80.8% | 改善 |
| 应用慢 SQL P50 | 1.571s | 1.542s | -1.8% | 基本持平 |
| 应用慢 SQL P95 | 6.934s | 4.924s | -29.0% | 改善但仍高 |
| 应用慢 SQL最大 | 26.175s | 15.285s | -41.6% | 改善但仍触顶网络等待 |
| HTTP 请求 | 3,085 | 1,833 | -40.6% | 流量下降，不能直接归因修复 |
| HTTP 失败 | 30 / 0.97% | 8 / 0.44% | 数量 -73.3%，失败率 -0.53pp | 改善；8 次为旧 backend 404 |
| HTTP 累计耗时 | 2,285.533s | 987.443s | -56.8% | 改善 |
| HTTP P95 / 最大 | 3.261s / 28.549s | 2.887s / 16.619s | -11.5% / -41.8% | 改善 |
| Session 长持有 | 195 | 30 | -84.6% | 改善；仍有托管 15.5s 长尾 |
| 托管 Session P95 / 最大 | 21.288s / 122.171s（业务） | 约 15.5s / 15.545s | 改善但网络等待复发 |
| PgBouncer 客户端 query timeout | 14（旧口径） | 5 个去重事件 / 15 原始行 | 口径已细分 | 仍复发 |
| PgBouncer connect timeout | 3 | 7 | +133.3% | 恶化 |
| 新增 pool query-wait / server crash / TLS error | 旧分析器未分栏 | 9 / 18 / 3 个去重事件 | 新增可见性 | 主要失败风暴 |
| proxy TUN source | 旧分析器未捕获 | 16 次 | 新增并复发 | P0 OPEN |
| statement/lock timeout、deadlock、idle-in-tx 错误 | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 | 持平 | 仅表示当前日志未捕获 |

目标表慢查询计数：`publication_tasks 5`、`zsxq_sync_runs 45`，`zsxq_topics`、
`zsxq_topic_comments`、`zsxq_attachments`、`external_documents` 均为 0。这里的 0 不能覆盖
没有真实目标流量或阈值以下请求；目标表仍通过只读统计和 EXPLAIN 复核。

## 主要问题排序

排序综合累计耗时、P95、最大耗时、失败率、页面/用户影响和历史复发，不按次数单排。

| 排名 | 问题/指纹 | 次数与累计 | P95 / 最大 | 失败与影响 | 根因 / 状态 |
| ---: | --- | ---: | ---: | --- | --- |
| 1 | PgBouncer proxy TUN 回退 | 16 次回退连接；9 query-wait、18 crash、3 TLS error | 失败 SQL 约 15.3s | 16 条 scheduler repair SQL 错误，8 个 repair origin | 路由错误进入 `198.18.0.1`，P0 OPEN |
| 2 | scheduler config `60ff7467696400d3` | 90 / 212.614s | 6.012s / 15.285s | scheduler 80、Web 10；尾部较上轮恶化 | 高频轮询 + 进程内缓存边界 + 网络长尾，OPEN |
| 3 | 四个系统同步页面轮询端点 | 906 请求 / 809.831s | 最高 5.762s / 16.619s | 同一操作台用户高频轮询 | UI 轮询、重复配置/摘要 SQL 与网络共同放大，OPEN |
| 4 | credential check `5d42e…` | 48 / 99.554s | 4.295s / 7.772s | 配置/鉴权页面 | 远端往返，不是 executor，OBSERVING |
| 5 | sync run summaries `ba0ef…` | 27 / 78.213s | 5.225s / 8.972s | system-sync-test batches | 摘要轮询 + PgBouncer 长尾，OPEN |
| 6 | auto publish account count `ab3ccc…` | 20 / 47.452s | 4.029s / 15.193s | 自动发布 scheduler status | 周期查询 + 路由风暴，OPEN |
| 7 | Celery status | 19 / 30.826s | 3.077s / 15.197s | 页面慢但无匹配慢 SQL | broker inspect/Redis 与连接等待，OBSERVING |
| 8 | Bilibili intelligence sync | 1 / 16.616s | 16.616s | 单次低频高延迟 | SQL 仅 3.214s，其余为应用/外部 I/O，OPEN |

`60ff…` 较上一轮次数下降 15.1%、累计下降 4.3%，但 P95 增长 27.2%、最大增长
53.2%，属于“量改善、尾部恶化”，不能关闭。旧 finalization 指纹 `af15f9b3d2324c80`
和 report-audio wait 指纹本轮为 0；前者有后续窗口正向证据，后者因没有真实 child completion
仍只能 OBSERVING。

## 真实 SQL、请求与调用链关联

### 1. 路由回退 → pool 等待 → scheduler 失败风暴

13:06:24 PgBouncer 首次出现 `query_wait_timeout`。13:07:08 新 server connection 的
source 从 `100.96.3.12` 变为 `198.18.0.1`，至 13:10:56 共 16 次；同期出现 9 个
去重 query-wait、18 个 server crash、3 个 TLS receive error，其中包括
`Can't assign requested address`。应用侧 8 个 scheduler repair origin 各产生“一次约 15.3s
失败 + 一次缓存后即时失败”，共 16 条 SQL 错误：report-audio recovery、publisher child
cleanup、publisher pending/processing/publication/business processing、stick-video ready、
pending finalization。

15:16:33 PgBouncer 重启后 source 恢复为 `100.96.3.12`。当前只读 `route -n get`
显示目标地址经 `utun5`，数据库 live snapshot 的 `client_addr` 也是 `100.96.3.12`。
但 18:10、20:13、00:12 仍出现 query timeout，21:01、21:17、01:28、02:54 仍有
connect timeout，说明 TUN 回退解释主失败风暴但不是全部网络不稳的唯一根因。

### 2. executor 与端到端耗时分离

同形 scheduler config 查询 executor 仅 0.024ms；其应用 P95/最大却为 6.012s/15.285s。
PostgreSQL 现场无 waiting lock、无 idle-in-transaction，设置为 statement timeout 60s、
lock timeout 5s、idle-in-transaction timeout 60s。代表性只读 EXPLAIN：publication point
0.039ms、channel chart 2.228ms、publisher 0.055ms、content 0.156ms、recent JSON 0.024ms、
narrow 0.025ms、sync runs 1.161ms、Insights canonical 35.767ms。故秒级问题主要在
PgBouncer/网络、连接持有、轮询和外部 I/O，不应加索引掩盖。

### 3. Session/事务持有

窗口捕获 30 次长持有：Web 13 次（P95/最大 15.384/15.545s）、scheduler 10 次
（P95/最大 15.328s）、ad-hoc 诊断 7 次（最大 64.935s）。27 次可关联 SQL，3 次没有
匹配慢 SQL，属于 rollback/checkin/网络等待。托管连接 SQL 累计约 400.162s，non-SQL
约 34.951s；本轮不再出现 DeepSeek 80–122s hold，但没有新真实 DeepSeek 任务，不能确认
其闭环。

## 页面/API 全链路

HTTP 总计 1,833 请求，成功 1,825、失败 8，P50/P95/最大为
`123.92ms / 2.887s / 16.619s`，累计 987.443s。8 个失败均为 06:15–06:16 旧 backend
路径的 404；后续同类真实请求成功，但历史失败仍保留。

| 端点 | 请求 | P95 / 最大 | 累计 | 慢 SQL 关联 |
| --- | ---: | ---: | ---: | --- |
| GET system-sync scheduler status | 228 | 4.929s / 16.004s | 267.045s | 34 条 / 61.695s |
| GET auto-publish scheduler status | 226 | 3.337s / 16.619s | 191.402s | 20 条 / 47.452s |
| POST system-sync-test/batches | 226 | 5.762s / 9.736s | 176.064s | 45 条 / 107.743s |
| GET system-sync-test/config | 226 | 2.904s / 16.525s | 175.320s | 24 条 / 43.577s |
| GET Celery status | 19 | 3.077s / 15.197s | 30.826s | 无匹配慢 SQL |
| system-sync run-once | 2 | 最大 14.259s | — | 网络/任务启动链 |
| Bilibili intelligence sync | 1 | 16.616s | 16.616s | SQL 3.214s |
| PDF evidence detail | 1 | 7.608s | 7.608s | SQL 4.255s |
| report audio Douyin/WeChat upload | 各 1 | 约 6.35s | — | 无匹配慢 SQL，外部上传 |

三类异常单列：

1. **低频高延迟**：Bilibili intelligence sync、PDF evidence detail、run-once、两个上传端点。
2. **页面/API 慢但无匹配慢 SQL**：Celery status、Douyin/WeChat 上传；优先排 broker、Redis、
   文件和外部网络。
3. **后台/连接持有久但无前台端点**：scheduler 的 10 次约 15.3s hold，以及 3 次
   rollback/checkin/网络等待无慢 SQL；ad-hoc 诊断单独分栏，不冒充用户流量。

## 特别链路运行态验收

固定窗口内没有捕获 Insights、topics/local 与 tabs 的真实页面流量，不能写“没有问题”。
本轮在当前新鲜运行态用真实 API 验收：

- health：HTTP 200，`healthy`。
- `zsxq-insights/overview` 合法真实参数：冷请求 HTTP 200 / 2.885s / 46,297 bytes；
  热请求 HTTP 200 / 0.030s / 46,297 bytes，证明缓存生效但冷链仍 OBSERVING。
- `zsxq/topics/local`：HTTP 200 / 0.663s / 132,951 bytes，`include_raw_payload=false`。
- `zsxq/topics/local/tabs`：HTTP 200 / 0.222s / 4,638 bytes。
- `system-sync-scheduler/status`：HTTP 200 / 0.289s / 6,362 bytes。
- `celery/status`：HTTP 200 / 1.554s / 32,838 bytes。

首次 Insights smoke 使用了 UI 不支持的参数并得到预期 400，属于诊断参数错误而非业务缺陷；
合法参数已重新验收成功。当前 backend PID 9261、PgBouncer PID 9297 监听正常。本轮只修改
诊断脚本、测试和文档，不需要重启业务进程，因此没有运行重启门禁或强制打断任务。

## 后台任务

固定窗口 Celery 结构化日志只完整捕获 `workflow_notify`：8 success、0 retry、0 failed，
累计 10.065s，P95/最大 1.373s。样本量远低于上一轮 133/53/12，且没有 SMTP 断连故障
注入，所以只能 OBSERVING，不能关闭 retry storm。其他长任务未形成可比的结构化 complete
样本；未捕获不等于未执行或无问题。

## 程序模式复核

- **频繁轮询/重复聚合**：系统同步测试页四个端点合计 906 请求，scheduler config、run
  summaries、account count 反复读取；这是本轮主要累计耗时来源。
- **缓存边界**：config 使用 60 秒进程内缓存，但 scheduler/Web 各进程独立，且 30 秒轮询
  仍形成远端查询；Insights 热请求 30ms 证明其缓存有效，冷请求仍 2.885s。
- **大字段/超大分页**：topics/local 明确 `include_raw_payload=false`；未发现新 raw JSON 全页
  搬运。原始 JSON 存储没有删除或裁剪。
- **N+1/重复去重**：本轮没有捕获旧 finalization `af15…` 和 report-audio child-wait 指纹；
  缺少真实 child completion，父唤醒 CAS JSON 路径仍 OPEN。
- **外部 I/O 交叉持有**：未捕获新 DeepSeek hold，但无目标任务；上传/Celery status 的慢耗时
  与慢 SQL不匹配，继续按外部 I/O/broker 观察。
- **失败风暴**：13:07 scheduler repair 是网络路由失败风暴；SMTP 本轮低流量未复现，不代表
  已有背压/幂等补发。

## 表规模、顺序扫描与索引证据

只读现场（PostgreSQL 16.13）：

| 表 | 总大小 | live / dead | updates / HOT% | 索引扫描占比 |
| --- | ---: | ---: | ---: | ---: |
| zsxq_topics | 128MB（toast 79MB，index 36MB） | 12,111 / 1,419 | 173,876 / 0.71% | 99.63% |
| publication_tasks | 123MB（toast 94MB，index 12MB） | 12,465 / 1,331 | 231,500 / 1.34% | 99.81% |
| zsxq_topic_comments | 118MB（table 91MB，index 23MB） | 41,333 / 5,644 | 237,197 / 70.46% | 99.93% |
| zsxq_attachments | 74MB（table 33MB，index 41MB） | 33,970 / 5,824 | 263,453 / 0.12% | 99.95% |
| zsxq_sync_runs | 37MB（index 35MB） | 996 / 191 | 111,605 / 0.33% | 99.94% |
| external_documents | 7.016MB | 1,817 / 146 | 6,265 / 0% | 98.82% |

表规模与上一轮基本不变，无 waiting lock、无 exact duplicate index。低扫描索引仍包括
attachment PDF group-tab 4.872MB/scan 1、topics group-type 3.648MB/scan 1、publish status
480KB/scan 5 等；低扫描不等于可直接删除。两个 topics 等价重叠定义仍是 schema warning，
但没有新证据支持无人值守 DDL。

唯一保留的人工候选仍为 `docs/sql/comment_hash_index_cleanup_dbeaver_20260724.sql`。必须在停栈
维护窗口由人工在 DBeaver 逐条执行脚本内 `lock_timeout`、`statement_timeout`、验证和回滚；
风险包括索引 DDL 锁等待、所有者权限和写放大变化。本轮未执行。

`pg_stat_statements` 未安装，无法提供跨重启服务端累计 SQL 画像；本轮用应用 SQL origin、
request id、Session checkout、PgBouncer 事件、表统计和 EXPLAIN 交叉归因。

## 已落地修复与验证

已落地：

- 分析器新增 pool query-wait、server crash、TLS receive error 分类。
- 解析 PgBouncer server source address，使用 `ipaddress` 标识 `198.18.0.0/15` proxy TUN。
- 新增四个历史故障片段测试，覆盖分类、地址判定和主程序输出；先 RED 后 GREEN。
- 新 Bug、既有 TUN/scheduler/SMTP/DeepSeek/finalization 记录、README 和 recurrence registry
  已更新。

验证结果：

- 分析器完整测试模块：19 tests / OK；其中 4 个新增历史故障片段测试已先 RED 后 GREEN。
- 完整 unit suite：1,170 tests / OK，40.508s。
- `compileall src/backend scripts/diagnostics`：通过。
- `git diff --check`：通过。
- recurrence registry `jq empty`：通过。
- Bug 严格检查：163 条全部归类、9 个复发根因簇，OK。

业务代码未改变，故无需新 worker 重载；现有真实 API 验收只证明当前运行链路，不替代下一
完整窗口不复发证据。

## 根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险/责任动作 | 下一轮验收条件 |
| --- | --- | --- | --- | --- | --- | --- |
| proxy TUN `198.18.0.1` | PostgreSQL server connection 错入代理 TUN | 诊断分类/source 防线；未改系统路由 | 当前 `utun5` / `100.96.3.12` | 是 | 网络 owner 查代理规则/Tailscale path；OPEN | 完整 24h TUN source 与所有 timeout/crash/失败 SQL 为 0 |
| 巡检漏分 query-wait/crash/TLS/source | 分析器分类不完整 | TDD 补齐并历史回放 | 精确重建 13:07 失败风暴 | 是（验收假绿簇） | OBSERVING | 下一轮自动报告全部类别且无漏报 |
| `60ff7467696400d3` | 30s 轮询、进程内缓存边界、网络长尾 | 既有 TTL；本轮无掩盖性改动 | executor 0.024ms；90/212.614s | 是，尾部恶化 | scheduler owner 评估共享快照/事件失效 | 无 TUN 窗口 P95 <500ms、查询按变化频率收敛 |
| system-sync UI 高频端点 | 多端点同步轮询 + 重复摘要/config | 未改业务；证据已量化 | 906 请求/809.831s | 反复 | frontend/service owner 合并或单飞轮询 | 请求/累计/P95下降且字段完整 |
| finalization `af15…` | 单任务完整 JSON（上轮已修） | 窄投影已加载 | 本窗口 0 | 未捕获复发 | OBSERVING；parent wakeup 仍 OPEN | 真实 child completion，旧指纹与 full JSON 为 0 |
| DeepSeek Session hold | 外部 I/O 前未释放 Session（上轮已修） | release-before-I/O | 本窗口无样本 | 未知，非“无复发” | 等真实新任务 | 同 origin non-SQL hold <5s，完整 24h 无长持有 |
| SMTP retry storm | 双层固定重试、无背压/持久补发 | 本轮无代码修复 | 8/0/0 低流量 | 未复现但未闭环 | OPEN；故障注入与 durable pending | 20+ 并发故障注入、最终送达、完整窗口 fail=0 |
| Bilibili/Celery/upload 无慢 SQL长尾 | 外部 I/O/broker/Redis/文件等待 | 本轮仅归因 | 真实 API 与单次请求证据 | 仍出现 | 分来源追踪 | 端到端 P95/最大收敛，匹配外部 span |

## 未捕获边界与下一轮验收计划

- 日志范围覆盖 `logs/slow_query.log`、`app.log`、`backend.log`、`scheduler.log`、
  `logs/celery/*.log` 及轮转文件；结构化字段缺失或没有流量的路径仍可能漏报。
- request id 只能关联同一托管进程记录；PgBouncer 与 PostgreSQL 无统一 trace id 时依赖时间、
  SQL fingerprint、origin 和 source address，需明确为关联推断。
- API 400 smoke、ad-hoc 诊断与托管流量已分栏，不能计入用户失败或业务最大耗时。
- 没有 `pg_stat_statements`，没有生产路由控制面变更权限，也没有真实 SMTP 故障注入；这些是
  当前闭环边界，不得用测试通过替代。

下一轮按顺序执行：

1. 首先复核 proxy TUN source、query/connect/query-wait、server crash、TLS error 和应用 SQL
   error；任何一项非 0 都保持网络簇 OPEN，并按恢复前后分窗。
2. 复核 `60ff…`、四个 system-sync 页面端点、credentials、sync summary 的累计/P95/最大，
   不能只比次数；在无路由回退的稳定窗判断是否需要共享缓存/事件失效。
3. 要求一条真实 report-audio DeepSeek 任务和一条 publisher child completion，分别验收
   Session hold 与 parent wakeup JSON 契约；无样本继续 OPEN/OBSERVING。
4. 继续真实覆盖 Insights 冷/热、topics/local、tabs、sync/status 和 Celery status；保留字段
   完整性、payload 大小和端到端耗时。
5. SMTP 若再断连，按 event id、attempt/deadline、队列等待和最终送达取证；没有共享预算、
   抖动、背压和 durable pending 的故障注入前不得关闭。
6. 人工若执行索引脚本，必须保存执行前后 `pg_stat_user_indexes/tables`、EXPLAIN、锁等待、
   逐条结果和回滚证据；自动巡检不执行 DDL。
