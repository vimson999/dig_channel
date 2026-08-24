# 2026-08-20 数据库性能与错误巡检

## 根因闭环结论

本轮没有达到“全部根因 CLOSED”。固定窗口内应用慢 SQL 数量和累计耗时分别恶化
25.4%/22.8%，HTTP P95 恶化 168.6%；主因不是 PostgreSQL executor 或缺索引，而是研报
基础文案物料页隐藏后仍轮询 Celery 状态、scheduler 稳定配置仍按分钟跨 PgBouncer 读取，
叠加远端网络长尾。

已落地两项根因修复：物料页改用共享 Page Visibility/single-flight controller；scheduler
配置成功快照从 60 秒改为 300 秒，保存仍立即失效、失败仍不缓存。安全门通过后标准整栈重启，
backend、17/17 Celery 服务和 ZSXQ scheduler 新进程已加载。新页面和真实 API 已验收，但旧浏览器
内存中的旧 JS 仍在分钟级请求；配置缓存尚缺完整后续窗口。因此两项都只能标记
`RUNTIME_REPLACED / OBSERVING`，不能写成已解决。

固定统计窗口：`2026-08-19 06:02:19` 至 `2026-08-20 06:02:19`（Asia/Shanghai）。
上轮基线为 `2026-08-19-db-performance-audit.md`；窗口后的测试、重启和运行态证据单列。

## 24 小时基线与变化

| 指标 | 本轮 | 上轮 | 新增 / 恶化 / 改善 / 反复 |
| --- | ---: | ---: | --- |
| 应用慢 SQL | 385（另有 adhoc 14） | 307 | 恶化 25.4% |
| 应用慢 SQL累计 | 856.984s | 697.666s | 恶化 22.8% |
| SQL P50 / P95 / 最大 | 1.547 / 5.451 / 15.516s | 1.486 / 7.299 / 15.761s | P50 恶化 4.1%；P95 改善 25.3%；最大改善 1.6% |
| HTTP 请求 | 2760 | 1383 | 流量增加 99.6% |
| HTTP 累计 | 2255.433s | 588.584s | 恶化 283.2% |
| HTTP P50 / P95 / 最大 | 0.00571 / 4.286 / 23.281s | 0.00350 / 1.596 / 35.463s | P50/P95 恶化 63.1%/168.6%；最大改善 34.4% |
| HTTP 失败 | 2 / 0.07% | 15 / 1.08% | 次数与失败率改善 |
| Connection hold | 28；P50/P95/max 8.603/20.520/62.183s | 43；7.032/21.033/21.879s | 次数、P95改善；raw max 被 adhoc 诊断拉高 |
| 应用 statement/lock/idle-in-tx error | 0 / 0 / 0 | 0 / 0 / 0 | 未捕获不等于未发生 |
| PgBouncer 去重 query/connect timeout | 1 / 5 | 3 / 7 | 改善，但仍反复 |

应用托管连接持有最大 16.869 秒；SQL 段累计 233.267 秒，非 SQL/result fetch/rollback/checkin
等段累计 81.618 秒。PgBouncer wait P50/P95/max 为 24.40/115.17/1809.20ms，query 为
57.57/199.49/2265.23ms；相对上轮 timeout 次数改善，但 wait/query P95 分别恶化
22.1%/32.4%。server source `100.96.3.12` 连接 1734 次，未捕获 proxy TUN、crash、TLS、
HBA 或 login error。

## 主要问题排序

排序综合累计耗时、P95、最大耗时、失败率、用户/页面影响与复发次数，而非只看出现次数。

1. **物料页 → Celery status 隐藏轮询（反复，RUNTIME_REPLACED/OBSERVING）**：809 次、
   累计 1750.319 秒、P95 9.352 秒、最大 21.372 秒；上轮仅 5 次。109 次匹配慢 SQL，
   累计 210.626 秒。旧页 hidden 后被 Chrome 节流为每小时恰好 60 次。
2. **scheduler config `60ff7467696400d3`（第三次反复，RUNTIME_REPLACED/OBSERVING）**：
   110 次、261.419 秒、P95 7.438 秒、最大 15.516 秒；累计/P95较上轮恶化 12.9%/53.5%。
   executor 0.049ms，根因是 30 秒循环配 60 秒成功缓存导致分钟级远端往返，不是索引。
3. **PgBouncer/网络长尾（OPEN，反复）**：timeout 下降，但 wait/query P95 恶化；数据库无
   waiting lock、idle-in-transaction，代表性 executor 0.037ms–34.222ms，不能用加索引解释。
4. **长后台任务与外部 I/O（OPEN/OBSERVING）**：ZSXQ publish 8 次/1962.100 秒、Publisher
   9 次/1697.746 秒、MP3 45 次/878.139 秒、PDF clean 3 次/782.280 秒。长任务本身不等于
   慢 SQL；必须按 task id 拆分 DB、broker、下载、AI 与上传阶段。
5. **SMTP provider 级闭环（OPEN）**：本窗 147 success、0 retry/failed，较上轮 51 retry/
   13 fail 改善；但未捕获 provider 故障和 durable pending/最终送达，不能关闭。

## 页面、API、后台任务与 SQL 关联

| 页面 / 链路 | 请求/任务 | 累计 / P95 / 最大 | SQL、Session、调用源与结论 |
| --- | ---: | --- | --- |
| 研报基础文案物料台 → Celery status | 809 | 1750.319 / 9.352 / 21.372s | 109 慢 SQL/210.626s；request `777c1124` 21.372s 中鉴权慢 SQL 约 1.480s，其余是控制面、普通 SQL/网络等待；hidden 轮询是累计主因 |
| MP3 jobs | 146 | 62.350 / 2.124 / — | 高频列表；需继续监测轮询和普通 SQL/网络，不把无匹配慢 SQL解释为无问题 |
| ZSXQ browser import | 11，失败2 | 58.154 / 23.281 / 23.281s | 失败率18.18%，慢 SQL 29.031s；输入/业务失败与 DB 长尾均影响用户 |
| Platform credentials | 140 | 56.663 / — / — | 高频控制面；部分时间没有慢 SQL对应，继续追普通查询和连接等待 |
| report audio assets | 19 | 31.122 / 4.242 / — | 页面慢但缺单一慢 SQL；属于未完全捕获的普通 SQL/网络链路 |
| continue-full-video | 4 | 30.488 / 19.377 / 19.377s | 慢 SQL 16.080s；低频高延迟，需按 child task 与外部编排继续观察 |
| Bilibili upload | 6 | 30.299 / 14.546 / — | 外部上传链路，不把全耗时归因数据库 |
| cover regenerate | 4 | 28.310 / 19.318 / — | 低频高延迟，未被单一慢 SQL完整解释 |
| MP3 detail | 23 | 26.180 / 4.476 / 8.963s | 当前无单一 executor 根因；保留为观察项 |

三类强制异常：

- **低频高延迟**：continue-full-video、cover regenerate、browser import、Bilibili upload。
- **页面/API 很慢但没有匹配慢 SQL**：WeChat 38 次/18.470 秒、Douyin 38/16.277、
  background regenerate 4/12.356、finance 3/10.239、create jobs 4/8.271；需追外部 I/O、
  普通 SQL、PgBouncer 与缓存，而非宣称没有数据库问题。
- **后台/连接持有很久但无前台端点**：scheduler config、ZSXQ publish、Publisher、PDF clean、
  Bilibili hourly sync；按 PID/role/task id/origin 关联。

四个托管但未匹配前台慢 SQL 的代表连接：MP3 load request `930c7182` 16.869 秒（SQL
14.434、非 SQL 2.435），credentials `77c58fe8` 10.695 秒，browser import `367ca669`
10.288 秒，Celery status `6604d2c6` 8.603 秒。

Celery 本窗 248 success、0 retry、0 failed，总成功耗时 6541.379 秒。主要任务 P95：ZSXQ
publish 561.539 秒、Publisher 308.920 秒、MP3 122.777 秒；需保留真实业务完整性，不能通过
删除字段、审计 JSON 或假数据来降载。

重点 ZSXQ 运行态验收：`topics/local` POST 200/0.960 秒，`topics/local/tabs` POST
200/0.189 秒，`sync/status` 200/0.160 秒；Insights overview 冷请求 200/2.956 秒，现场
canonical executor 34.222ms。固定窗口没有覆盖足够的 overview/tabs/status 样本，故只能说
“窗口未充分捕获”，不能说没有问题。

## 已落地根因修复

### 1. 物料页隐藏轮询

修复前：`resetPollTimer()` 无条件 `setInterval(12s)`；17:27 后持续轮询，隐藏节流后仍每小时
60 次。修复：页面加载共享 `visibility-polling.js`，统一 hidden pause、visible immediate、
single-flight、stop/unload cleanup；只有 active job 才刷新 history，worker status 与 history
共享同一 poll。

验证：真实 HTML/业务脚本契约先红后绿；新 backend 返回页面/helper/业务脚本均 200，页面
渲染“物料队列在线”；Celery 17/17 服务在线。旧浏览器标签不可由四个可控 Chrome 配置发现，
仍执行旧内存 JS，所以保持 OBSERVING，下一轮必须确认旧每小时 60 次序列消失。

### 2. scheduler 配置分钟级远端往返

修复前：30 秒循环 + 60 秒缓存，稳定成功路径至少每分钟一次远端 DB；指纹连续三轮位居累计
耗时前列，executor 始终小于 0.05ms。修复：成功规范化快照缓存 300 秒；配置保存/默认值写入
立即失效；失败读取不缓存，数据库恢复仍在下一轮重试；返回深拷贝。

验证：可控 monotonic 契约先红后绿，300 秒内一查、过期重查；15 个定向单元测试通过。
标准整栈重启后 ZSXQ scheduler PID 93383 运行，status 返回 config source=database；真实日志只在
06:12:51、06:17:51、06:22:51 查询配置，已跨两个完整周期证明由分钟级变为 5 分钟级。仍缺
完整 24 小时，状态保持 OBSERVING。

### 3. 上轮 Bilibili 写放大复核

当前固定窗口单一旧 PID 77876 共 24 个整点轮次，均 6/6 成功、抓取 180；10 轮完全未变化。
旧全列 UPDATE 指纹 `f28c58ebb852d69c` 为 0，新日志只出现窄 `last_seen_at/updated_at` 或
真实变化列更新，慢样本 1.0–2.013 秒。标准重启已终止旧 PID；新 scheduler PID 93402 于
06:13:09 启动，进程核对仅一个实例。
这证明本窗改善，但仍需下一完整新 PID 窗口，保持 OBSERVING。

## PostgreSQL、表规模与索引复核

PostgreSQL 16.13，只读诊断；无 recovery、waiting lock、idle-in-transaction；
`pg_stat_statements` 未安装。

| 表 | 总大小 | live/dead | seq/idx scan | idx 使用率 | 风险 |
| --- | ---: | ---: | ---: | ---: | --- |
| zsxq_topics | 128MB | 12548/1584 | 3330/871368 | 99.62% | TOAST 79MB；HOT 0.71% |
| publication_tasks | 123MB | 12622/1577 | 6870/2872372 | 99.76% | TOAST 94MB；HOT 1.32% |
| zsxq_topic_comments | 118MB | 41333/5644 | 266/356927 | 99.93% | table 91MB；HOT 70.46% |
| zsxq_attachments | 77MB | 34374/6763 | 584/1133015 | 99.95% | 264392 updates；HOT 0.12% |
| zsxq_sync_runs | 37MB | 1043/18 | 223/336353 | 99.93% | index 35MB；HOT 0.33% |
| external_documents | 7MB | 1817/146 | — | 98.81% | 小表 seq scan 可接受 |

目标六表无 exact duplicate index。现场 `EXPLAIN (ANALYZE, BUFFERS)`：watchdog point
0.037ms、channel chart 2.792ms、scheduler config 0.049ms、Publisher 0.079ms、content
0.202ms、recent JSON/narrow 0.073/0.044ms、sync runs 1.540ms、Insights canonical 34.222ms。
证据不支持新增索引。

待人工 SQL：

- `docs/sql/bilibili_competitor_bvid_index_cleanup_dbeaver_20260819.sql`：删除等价 Bilibili
  bvid 唯一索引前需 DBA 逐段确认定义、owner、scan、锁与回滚。
- `docs/sql/comment_hash_index_cleanup_dbeaver_20260724.sql`：comments hash 等价索引维护仍未执行。

两份脚本都带 timeout、验证与回滚；本轮未执行 CREATE/DROP INDEX、ALTER、backfill 或生产配置。

## 测试与运行态验收

- TDD/定向：物料页 Visibility Node 契约通过；ZSXQ config 15 passed。
- 安全重启：`check-restart-safety.py` 确认无不可中断任务；标准 `restart-dev-stack.sh` 完成。
- 运行态：backend `/health` healthy/1.0.0；Celery broker ok、17/17 running；页面/helper/
  业务脚本 200；ZSXQ scheduler status 200，PID 93383、config source database。
- 完整 unit 首轮为 `1309 passed, 3 skipped, 34 failed`：33 项来自沙箱禁止 bind loopback，
  1 项是旧源码结构契约；更新为共享 controller 契约后该项通过，两个 loopback 文件在允许本地
  回环后 `31 passed, 8 subtests passed`。随后单进程全量在 728 项后被一个既有 oversized-body
  socket `BrokenPipe` 波动和长时网络用例阻塞，已停止该测试进程；因此报告采用完整收集加定向
  补跑证据，不伪称单命令全绿。
- 窗口后捕获一次临时 Celery pidbox reply 恢复 Redis read timeout；消息 delivery_mode=1，非持久
  任务。后续 broker ok、17/17 服务运行，但 status 探针 22.876 秒，Redis/control-plane 保持 OPEN。
- `compileall`（backend/diagnostics/dev）、三个前端 Visibility Node 契约、JavaScript syntax、
  recurrence JSON、`git diff --check` 均通过；Bug strict 为 170/170 已归类、9 个复发簇、
  回顾日期 2026-08-20，未过期。

## 根因闭环状态表

| 问题指纹 / 复发键 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险 / 责任动作 | 下一轮验收条件 |
| --- | --- | --- | --- | --- | --- | --- |
| `/api/v1/celery/status` hidden 60/h / `HOT-PATH-PAYLOAD-POLLING` | 物料页直接 setInterval，无 visibility/single-flight | 共享 controller、实际 HTML/脚本契约 | 新资源/页面/API已加载；旧 JS 实例仍请求 | 是 | `OBSERVING`；页面 owner/下一轮先识别调用页版本 | hidden 计数 0、visible 单次恢复、无 overlap；旧 60/h 序列消失 |
| `60ff7467696400d3` | 30s loop + 60s cache + PgBouncer 网络长尾 | 成功快照 300s、保存失效、失败不缓存、深拷贝契约 | 新 PID；06:12:51/06:17:51/06:22:51 严格 5 分钟 | 连续第三次 | `OBSERVING`；缺完整24h | 单进程稳态约≤288次/日；P95显著下降；按 PID/origin 证明命中 |
| `reply.celery.pidbox` restore timeout | 临时 control reply 无消费者，恢复时 Redis read timeout | 未用重试掩盖；保留原始证据 | delivery_mode=1；broker ok、17/17 running；status 22.876s | 同类 control-plane 长尾 | `OPEN`；监控 Redis/pidbox，不误报业务任务丢失 | 24h restore timeout=0，status P95<10s，持久队列无丢失 |
| `PGBOUNCER-NETWORK-QUERY-CONNECT` | 连接/传输长尾，非 executor/锁/索引 | 既有分段观测；本轮不加掩盖性重试 | query/connect 1/5；wait/query P95恶化；无锁/idle-tx | 是 | `OPEN`；运维追 source/socket/path | 连续24h timeout/crash/TLS/HBA=0，fetch/checkin P95<500ms |
| `f28c58ebb852d69c` | 旧双实例 + 未变化 JSON 重写 | 上轮单实例锁、变化投影、窄更新 | 24轮6/6；旧指纹0；新窄 UPDATE | 本窗未复发 | `OBSERVING`；缺新 PID 完整窗，重复索引待人工 | 24h单一序列、旧全列0、窄 UPDATE P95<500ms |
| SMTP provider storm | provider 故障时跨 event 各自重试 | 既有单 event 预算/退避 | 147 success、0 retry/fail | 本窗未捕获 | `OPEN`；无故障/durable pending证据 | 故障窗口无同步风暴且最终送达可审计 |
| Insights read/network split | 查询后 CPU边界已修，剩余 fetch/网络 | 既有有界 read scope | cold 2.956s，executor34.222ms | 网络同类反复 | 应用 `OBSERVING`、网络 `OPEN` | CPU阶段 checkout=0；fetch/checkin P95<500ms；完整窗 |

## 未捕获边界

- `pg_stat_statements` 不可用，无法取得服务器端全应用累计；以应用日志、PgBouncer、catalog、
  request/task id 和现场 EXPLAIN 三角验证。
- execute hook 不覆盖全部 result fetch、rollback/checkin，非 SQL段不能一律解释为 Python CPU。
- 固定窗未充分捕获 Insights、topics/local/tabs、sync/status；窗口后探针不回写 24 小时基线。
- 旧浏览器页面不可控，不能用新页面加载替代真实 hidden 后续窗口；“未捕获”不是“未发生”。
- 当前无慢 SQL 的表/端点不代表无大字段、N+1、缓存失效或频繁轮询；只代表日志没有样本。
- SMTP 本窗无失败不证明 provider 熔断、durable pending 和最终送达已闭环。

## 下一轮明确验收计划

1. 首先复核 `/api/v1/celery/status`：按分钟序列和页面静态资源版本确认旧 60/h 消失；hidden
   必须为 0，visible 恢复不得重叠。若仍出现，继续追实际调用页，不调大 API 超时。
2. 按新 scheduler PID 与 300 秒窗口统计 `60ff...` 的 hit/miss/失效；稳态仍接近每分钟时，
   继续追多进程拓扑和 cache invalidation，不增加索引。
3. 逐一关联 PgBouncer timeout 到 request/task id、SQL origin、execute/fetch/rollback/checkin、
   server source 与连接年龄；timeout 未归零保持 OPEN。
4. 复核 Bilibili 新 PID 24 小时：单一调度序列、旧全列 UPDATE 0、窄 UPDATE P95<500ms。
5. 捕获下一次 SMTP provider 故障，验证跨 event 背压、durable pending 和最终送达；无样本不关闭。
6. DBA 只在维护窗逐条执行现有两份索引脚本，保留执行前后 stats、EXPLAIN、锁与回滚证据。
