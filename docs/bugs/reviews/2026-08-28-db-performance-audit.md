# 2026-08-28 每日数据库性能与错误巡检

## 根因闭环结论

- 固定窗口：`2026-08-27 06:02:19 +0800` 至 `2026-08-28 06:02:19 +0800`；对照窗口：前移 24 小时。统计覆盖 `slow_query.log`、`app.log`、`backend.log`、`scheduler.log`、`celery/*.log` 及轮转文件。
- 全链路总体改善但未闭环：应用慢 SQL 由 278 降至 220（-20.9%），累计耗时 772.543s 降至 567.679s（-26.5%）；HTTP 累计耗时 1079.468s 降至 664.611s（-38.4%）；Session 长持有由 60 降至 29（-51.7%）。这些下降不能替代后续窗口验收。
- 系统性主根因仍是本机 PgBouncer 到远端 PostgreSQL 的建连/往返/commit-checkin 长尾，而不是 PostgreSQL executor 或目标表缺索引。窗口内去重 server connect timeout 仍为 10 次，query timeout 由 6 降至 0；`SHOW POOLS` 采样无 client wait，但业务池只有 5 个 used server，低于配置 `min_pool_size=6`，窗口后 06:07 又出现一次 10 秒建连失败。该根因保持 **OPEN / REPEATED**。
- 新确认并修复 MP3 编排历史页轮询缺少 Page Visibility 和 single-flight：212 次 jobs 请求集中在两个操作窗口，旧 timer 在隐藏页和慢请求时缺少背压。已增加隐藏页暂停和 in-flight 门禁、补契约并由运行中 backend 提供新 JS；尚无真实隐藏页后续 request-id 窗口，状态为 **RUNTIME_REPLACED / OBSERVING**，不是 CLOSED。
- PDF canonical miss N+1 的旧单项补查指纹本窗为 0；Bilibili 宽读/逐行更新在新 scheduler 部署边界后为 0。前者自然样本不足 5 次，后者仅覆盖约 17 小时和 1 个计划周期，均保持 **OBSERVING**。
- 未执行生产 DDL、数据回填、生产配置或破坏性操作。现有 Bilibili 重复 bvid 索引脚本继续等待 DBA 维护窗口；本轮没有真实 SQL/统计/EXPLAIN 支持为重点表新增索引。

## 与上一轮量化对比

| 指标 | 上一窗口 | 本窗口 | 变化 | 判定 |
| --- | ---: | ---: | ---: | --- |
| 应用慢 SQL | 278 | 220 | -20.9% | 改善 |
| 应用慢 SQL 累计 | 772.543s | 567.679s | -26.5% | 改善 |
| SQL P50 / P95 / 最大 | 1.691 / 8.631 / 18.455s | 1.672 / 7.424 / 17.094s | P95 -14.0% | 改善但长尾仍高 |
| HTTP 请求 | 1073 | 1073 | 0 | 持平 |
| HTTP 累计 | 1079.468s | 664.611s | -38.4% | 改善 |
| HTTP P95 / 最大 | 5.334 / 32.365s | 2.960 / 19.380s | -44.5% / -40.1% | 改善 |
| HTTP 成功 / 失败 | 1072 / 1 | 1072 / 1 | 持平 | 本窗失败为预期 409 业务冲突 |
| Session 长持有 | 60 | 29 | -51.7% | 改善 |
| Session P95 / 最大 | 27.487 / 32.329s | 17.656 / 17.702s | -35.8% / -45.2% | 改善 |
| 持有期 cursor SQL / 非 cursor | 691.744 / 32.818s | 259.928 / 22.281s | -62.4% / -32.1% | 改善；后者包含 commit/checkin，不能等同应用 CPU |
| PgBouncer connect / query timeout（去重） | 10 / 6 | 10 / 0 | connect 持平、query 清零 | 建连根因反复 |
| PgBouncer server wait P95 / 最大 | 81.70 / 2021.05ms | 103.15 / 1945.67ms | +26.3% / -3.7% | P95 恶化 |
| Celery 完成 / retry / fail | 259 / 0 / 0 | 231 / 0 / 0 | 工作量 -10.8% | 无失败风暴 |
| Celery 累计 | 7389.626s | 6792.510s | -8.1% | 随任务构成变化 |
| 应用 SQL 错误 | 3 | 0 | 清零 | 改善；未捕获不代表未发生 |

慢 SQL 总数 222 条，其中托管应用 220、adhoc 诊断 2。`statement timeout`、`lock timeout`、deadlock、query timeout、connection timeout 与超过门槛的 `idle in transaction` 在托管应用日志中均为 0。

## 页面 / API 全链路

按累计耗时优先、再结合 P95/最大/失败率排序：

| 端点 | 请求 | 成功/失败 | 累计 | P95 | 最大 | 匹配慢 SQL | 结论 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| GET MP3 jobs | 212 | 212/0 | 95.265s | 2.352s | 7.908s | 16 / 29.194s | 高频轮询；已补 Visibility + single-flight |
| GET MP3 audio-assets | 35 | 35/0 | 79.665s | 7.531s | 14.639s | 9 / 32.848s | 较上窗 P95 21.950s 改善 65.7% |
| POST MP3 bilibili-upload | 11 | 11/0 | 61.928s | 15.909s | 15.909s | 5 / 27.313s | 尾延迟仍受数据库链路与上传编排影响 |
| GET MP3 detail | 26 | 26/0 | 44.408s | 10.733s | 14.816s | 3 / 20.853s | 流量下降但 P95 较上窗 4.371s 恶化 |
| GET Celery status | 12 | 12/0 | 41.083s | 13.472s | 14.900s | 2 / 3.533s | 较上窗 P95 23.261s 改善 42.1%，冷控制面仍 OPEN |
| POST 小军军背景重生成 | 5 | 5/0 | 36.040s | 19.380s | 19.380s | 3 / 15.177s | 低频高延迟 |
| POST ZSXQ browser-import/history | 4 | 4/0 | 35.611s | 17.691s | 17.691s | 4 / 32.687s | 新增严重长尾；executor 0.254ms，非索引根因 |
| POST 小军军封面重生成 | 5 | 5/0 | 35.362s | 19.197s | 19.197s | 2 / 14.269s | 低频高延迟 |
| GET Bilibili uploads | 25 | 25/0 | 28.148s | 3.883s | 4.443s | 少量 | 可接受但继续观察网络尾延迟 |
| POST MP3 jobs | 5 | 5/0 | 28.114s | 16.033s | 16.033s | 3 / 10.859s | 低频高延迟 |
| POST continue-full-video | 5 | 5/0 | 25.431s | 14.184s | 14.184s | 2 / 11.891s | 低频高延迟 |
| GET report-audio PDF assets | 3 | 3/0 | 21.592s | 18.223s | 18.223s | 3 / 16.834s | N+1 未复发；样本不足且网络尾延迟仍在 |

唯一 HTTP 失败是 `request_id=3a86535a` 的 Bilibili upload cancel 409：任务已越过可取消阶段，随后继续上传；无异常 traceback、无 SQL 失败，按预期业务冲突处理，不计失败风暴。

### 特别复核链路

- `zsxq-insights overview`：2 次，冷 9.603s、热 0.91ms，2 条慢 SQL 合计 7.501s；均是上一轮 06:12 验收探针，本窗口无后续自然流量，不能据此确认后续未复发。
- `topics/local` 1 次 0.601s，`topics/local/tabs` 1 次 1.457s，`sync/status` 1 次 0.204s；同样属于上一轮探针。
- PDF 三入口：report-audio 3 次；report-script-material 与 Top10 各 1 次 1.141/1.933s。旧 canonical 单项指纹为 0，但样本不足。
- MP3 隐藏发布面板：抖音、视频号上传历史均为 0；平台凭据 26 次、P95 1.574s（上窗 28 次、P95 1.948s）。说明惰性挂载继续有效，但首次超时后的 single-owner 仍缺真实专门复现样本，保持 OBSERVING。

### 三类必须单列的异常

1. **低频高延迟**：两类小军军重生成、ZSXQ browser history、MP3 create/continue、report-audio PDF assets，均见上表；不能被低请求量掩盖。
2. **页面/API 很慢但没有匹配慢 SQL**：本窗没有 `>10s` 且完全无慢 SQL 匹配的端点；无 SQL 匹配的最高代表是 finance cover regenerate 最大 4.637s。这里只能说明当前日志关联未捕获到，不代表外部 I/O 或更细 SQL 不存在。
3. **后台任务/连接持有很久但没有前台端点**：29 次长持有中 worker 7、unknown 3；MP3 worker 一次 8.624s 中 cursor SQL 0.702s、其余 7.923s，publisher worker 一次 6.038s 中 cursor SQL 1.081s、其余 4.957s。代码在这些点立即 commit/release，故剩余时间可能是 commit/checkin/网络而非业务外部 I/O；现有 telemetry 尚未拆开这些阶段，保持 OPEN。

## 后台任务

| Celery 任务族 | 完成 | 累计 | P95 | 最大 | 失败/重试 | 判定 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| ZSXQ publish clean | 8 | 1508.254s | 435.594s | 435.594s | 0/0 | 外部处理主导，非数据库单点结论 |
| Publisher | 10 | 1418.879s | 194.035s | 194.035s | 0/0 | 需继续监控 commit/release 长尾 |
| PDF clean | 6 | 1379.252s | 447.621s | 447.621s | 0/0 | 媒体/外部处理主导 |
| Bilibili upload | 10 | 1025.124s | 133.255s | 133.255s | 0/0 | 无失败风暴 |
| MP3 workflow | 40 | 872.523s | 119.127s | 141.754s | 0/0 | 较上窗累计 +55.2%、P95 +137.8%；任务构成加重，不等同 DB 退化 |
| Audio clean | 2 | 367.215s | 230.910s | 230.910s | 0/0 | 样本少 |
| Workflow notify | 155 | 221.264s | 1.945s | 3.091s | 0/0 | 无通知失败风暴 |

共 231 条完成、0 retry、0 fail。未观察到任务重复投递/失败重试风暴；但“无日志捕获”不等于全局未发生。

## 真实 SQL、request id 与 executor 归因

| 排名/指纹 | 调用来源 | 次数 | 累计 | P95 / 最大 | executor/载荷证据 | 根因 |
| --- | --- | ---: | ---: | ---: | --- | --- |
| `590220bb0dd49238` | `api/mp3_video_workflow.py:1265:load_task` | 13 | 83.707s | 10.903 / 12.432s | 三条真实任务 params+result 17.1–17.9KB；主键点查 execution 0.025ms | 远端往返/连接，非缺索引；累计较上窗 +114.5% |
| `60ff7467696400d3` | 全 tabs scheduler config | 27 | 61.134s | 7.991 / 8.522s | `system_configs` 代表执行 0.027ms | 反复的远端网络/配置轮询尾延迟 |
| `3e7b5bb592aa0562` | Celery report-video stage stats | 19 | 31.518s | 3.440 / 3.840s | 有界统计查询 | 网络放大，继续观察 |
| `5d0c7947861207fe` | ZSXQ browser history | 3 | 31.264s | 17.094 / 17.094s | `content_pipeline_assets` 542 行，189 个 audio 结果、约 49.9KB；Seq Scan + sort execution 0.254ms | 新增链路长尾，明确排除索引 |
| `e73fec8af8fe216a` | content pipeline field update | 13 | 24.303s | 2.782 / 2.965s | 窄字段更新 | 网络/写事务尾延迟 |

两条最差 web Session `request_id=f76cd06f/f68877fc` 都来自 MP3 `load_task`，分别持有 13.597/11.711s，其中 cursor SQL 10.569/9.614s。两条 worker 非 cursor 长尾位于 commit/release 邻近；不能在 telemetry 未分阶段前误报为“Session 跨外部 I/O”。

PgBouncer 事件在 10:44、12:23、14:02、14:57、15:50、16:35、20:13、20:36、00:13、03:40 各出现 10s server connect timeout；来源本机 `100.96.3.12`，目标远端 `100.92.99.39:5432`，TLS 1.3，未见 proxy TUN、HBA reject 或 pool client wait。06:07 的窗口后 timeout 说明根因仍活跃。

## 程序模式复核与本轮修复

### 已修：MP3 jobs 轮询背压

- 212 次请求只出现在 12:07–12:23（66 次）、14:13（1 次）、17:08–17:39（145 次），没有全天均匀背景流量；但 3 秒可调 timer 缺少 `document.hidden` 和 in-flight 门禁，慢网络时可并发重叠。
- TDD 先新增失败契约，再在 `Mp3VideoWorkflowPage` 加 `pollInFlight`；页面隐藏、上一轮未完成或当前页无活动任务时均不发请求。
- 静态资源版本更新为 `20260828-cleaned-audio-assets-v2-visibility-singleflight`，保留用户正在开发的 cleaned asset 版本语义。
- 运行中 backend `/health` 为 healthy，真实 HTML 与 JS 已返回版本号、`document.hidden` 和 `pollInFlight`。未重启 backend/Celery/scheduler，因为只变更静态文件；没有触发不必要的任务中断风险。
- 仍缺真实隐藏浏览器标签页的后续请求序列，状态为 `RUNTIME_REPLACED / OBSERVING`。

### 已替换但继续观察

- **PDF canonical miss N+1**：旧单项指纹 0；report-audio 每请求保持 O(1) canonical 查询。但三个入口自然样本不足 5 次，且 report-audio 最大仍 18.223s，不能 CLOSED。
- **Bilibili competitor 宽读**：部署前旧 PID 有宽读慢指纹 2 条、逐行 UPDATE 4 条；12:46 部署后新 PID 70795 约 17 小时内旧指纹均为 0，只有 1 个计划周期。没有把部署前证据误算成复发，也没有用短窗口宣告闭环。
- **MP3 隐藏发布面板**：抖音/视频号历史 0、凭据尾延迟改善；首次凭据超时后的 single-owner 缺自然复现样本。

### 未实施的伪修复

- 没有为 0.025ms 主键点查、0.027ms 配置查询或 0.254ms 小表历史查询增加索引。
- 没有通过提高 timeout、盲目 retry、删字段、删除 raw JSON 或缓存假数据掩盖远端链路问题。
- 没有将 commit/checkin 时间误命名为应用非 SQL CPU，也没有在缺少调用链证据时改动 Session 边界。

## 重点表规模、顺扫、索引与写放大

| 表 | 总大小 | live/dead | 累计 update / HOT 比例 | 索引扫描占比 | 结论 |
| --- | ---: | ---: | ---: | ---: | --- |
| `publication_tasks` | 127MB | 12,914 / 2,184 | 240,134 / 1.29% | 99.77%（seq 6,899 / idx 3,011,493） | 写放大风险最高；较上轮 update +717，继续以窄投影/JSON 条件写治理 |
| `zsxq_sync_runs` | 37MB | 1,062 / 46 | 111,710 / 低 | 99.93% | 高频状态写，未见新缺索引证据 |
| `zsxq_topics` | 129MB | 12,637 / 1,584 | 174,041 / 0.71% | 99.58%（seq 3,759 / idx 889,629） | 原始 JSON 写放大继续由 payload hash/条件更新治理 |
| `zsxq_topic_comments` | 118MB | 41,333 / 5,644 | 237,197 / 70.46% | 99.91%（seq 313 / idx 357,020） | HOT 比例良好；dead rows 继续观察 vacuum |
| `zsxq_attachments` | 78MB | 33,906 / 37 | 264,485 / 低 | 99.95% | 写多但 dead 极低；不盲目改索引 |
| `external_documents` | 7MB | 1,817 / 146 | 6,265 / 未突出 | 98.80% | 当前无主要慢指纹 |

补充 `content_pipeline_assets`：总计 600KB（table 336KB/index 224KB）、542 live/11 dead；历史查询现场 0.254ms。现有 `media_type,content_hash` 唯一扫描 486 次、canonical task 索引 243 次、created 索引 29 次、status 单列 0 次、task_status 28 次。短统计期不能据此自动删除 0 次索引。

目标表未发现完全重复索引。低频索引包括 attachment group-tab partial（1）、topics group-type（1）、publication status（5）等，但统计窗口短且可能服务恢复/后台入口；本轮不生成盲删脚本。生产未安装 `pg_stat_statements`，因此索引与 SQL 结论仅覆盖当前 PostgreSQL stats 与应用日志。

## 索引整改建议与待人工 SQL

1. 本轮重点表 **无新增索引建议**：代表 SQL executor 已经为 0.025–35.391ms，应用端秒级耗时与 PgBouncer timeout 不一致，新增索引不会根治网络/建连。
2. Bilibili `bvid` 重复唯一索引仍待人工执行 [`docs/sql/bilibili_competitor_bvid_index_cleanup_dbeaver_20260819.sql`](../../sql/bilibili_competitor_bvid_index_cleanup_dbeaver_20260819.sql)。脚本包含重复数据门禁、约束 owner 确认、`lock_timeout=5s`、`statement_timeout=15min`、`DROP INDEX CONCURRENTLY`、EXPLAIN 验证和回滚 `CREATE UNIQUE INDEX CONCURRENTLY`。风险是误删约束 owner 或长时间并发索引操作；必须逐段核对，不得自动化执行。
3. `publication_tasks`、ZSXQ 六表的低使用索引只进入观察清单；需至少覆盖业务周期的 stats、真实 SQL、owner/约束、写成本与 EXPLAIN 后，才可生成新的 DBeaver 脚本。

## 测试与运行态验证

- TDD 红灯：新增 MP3 polling 契约在旧代码下因缺 `pollInFlight` 明确失败。
- 定向 Python：98 passed、3 subtests passed（DB projection、PDF canonical、Bilibili scheduler）。
- 前端：`node --check` 通过；cleaned asset 与 lazy mount 两个行为契约通过。期间静态版本契约先暴露版本语义丢失，修正为组合版本后通过。
- 完整 unit：沙箱内首次 34 failures 均为 `socket.bind(127.0.0.1, 0) -> PermissionError`；相同命令在允许本地测试 socket 的环境复跑为 **1412 passed、3 skipped、305 subtests passed**。
- `compileall -q src/backend`、`git diff --check`、`jq empty recurrence-registry.json` 全部通过。
- Bug 严格门禁：174 条全部归类，9 个复发根因簇，最近回顾 2026-08-28，结果 OK。
- 运行态：backend health healthy；真实 HTML/JS 已返回最终静态版本与轮询门禁。未重启任何服务；新行为仍需真实浏览器后续窗口。

## 根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险 / 责任动作 | 下一轮验收条件 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| PgBouncer server connect timeout | 本机到远端 PostgreSQL/Tailscale 建连长尾，热池未稳定维持 6 | 既有路由、pool 与诊断防线；本轮无配置盲改 | 10 次 timeout；池 0 wait、5 used；06:07 再发 | 是 | 运维复核远端可达性、server 建连与热池补槽 | connect timeout 归零；6 个热 server 稳定；应用/exec 差距收敛 | OPEN / REPEATED |
| MP3 jobs 212 次轮询 | 缺 Visibility 与 single-flight 背压 | 已加 `document.hidden`、`pollInFlight`、契约和 cache bust | 运行 backend 返回新 HTML/JS | 新入口已替换，未完成后窗 | 真实隐藏页/慢请求未验收 | 隐藏态 0 请求；可见活动态同时在途 ≤1；完整窗请求/活跃分钟下降 | RUNTIME_REPLACED / OBSERVING |
| `590220bb0dd49238` MP3 load_task | 远端往返/连接/事务释放；非主键或 18KB payload executor | 既有有界 load/Session 纪律；本轮不伪改查询 | executor 0.025ms vs app max 12.432s | 是，累计恶化 | 网络根因与 commit/checkin telemetry 未闭环 | P95 <500ms 或取得明确 pool/network分段证据并根治 | OPEN |
| `5d0c7947861207fe` browser history | 小表顺扫被远端往返放大 | 不加索引；保留完整历史字段 | 542 行、49.9KB、executor 0.254ms vs app 17.094s | 新增 | 自然流量少，依赖链路修复 | ≥5 次真实请求；P95 <1s；无 connect timeout 重合 | OPEN / NEW |
| `60ff7467696400d3` scheduler config | 配置轮询遇远端链路长尾 | 既有稳定配置快照防线 | executor 0.027ms，27 次累计 61.134s | 是 | 仍受 connect timeout 影响 | 一个完整日 P95 <500ms，且无建连失败 | OPEN / REPEATED |
| PDF canonical 单项补查 | 已知缺失态被误当未查询 | 三 helper sentinel + O(1) SQL 契约 | 本窗旧指纹 0，三个真实 API 均成功 | 未捕获复发 | 样本 <5、report-audio max 18.223s | 三入口各 ≥5 次、旧指纹 0、字段完整、每页 O(1) | RUNTIME_REPLACED / OBSERVING |
| Bilibili 宽 identity read/逐行 UPDATE | 为比较变化读取历史大 JSON，逐行写回 | `id,bvid` identity + 批量条件标量/raw JSONB 更新 | 部署后约 17h、1 个周期旧指纹 0 | 部署前旧 PID 有，部署后无 | 未满 24h；重复索引待 DBA | 完整日全部周期旧指纹 0，更新 O(源数)，raw JSON 完整 | RUNTIME_REPLACED / OBSERVING |
| Session 非 cursor 7.923/4.957s | commit/checkin/网络阶段未细分 | 既有 I/O 前 release；本轮保留证据不误修 | 29 holds、无 >60s idle-in-tx | 长尾仍在 | telemetry 无法区分 commit/checkin/fetch | 分阶段 telemetry 落地；代表任务无 >5s 归还延迟 | OPEN / OBSERVING |
| Celery status cold tail | broker inspect/控制面冷启动与少量 SQL | 既有 single-flight/短事务 | P95 23.261→13.472s，仍 max14.900s | 改善但仍在 | 自然冷样本不足 | ≥5 次冷请求 P95 <5s、无 Session 跨 inspect | OPEN / IMPROVING |

## 未捕获边界

- `pg_stat_statements` 未安装，无法取得服务器全历史语句；日志没有捕获不代表未执行。
- 应用 SQL timer 包含客户端等待但 PostgreSQL executor 只对代表查询现场采样；两者不能逐条一一等价。
- Session lifecycle 的“非 cursor”包含 result fetch、commit、rollback、checkin 和应用阶段，尚不能精确分离。
- Page Visibility 状态、浏览器标签数量和同时在途请求没有写入服务端日志；MP3 修复后的真实 hidden/single-flight 仍待后窗。
- ZSXQ 特别页面和 PDF 三入口自然流量不足；探针成功不能代替后续业务流量。
- 日志窗口之外 06:07 的 PgBouncer timeout 仅作为根因仍活跃的旁证，不混入固定窗口数量。

## 下一轮明确验收计划

1. 先按复发键检查 PgBouncer connect timeout、业务池 server 数、source/remote 地址、TLS、client wait，并把 app SQL、executor、commit/checkin 分段关联到同一 request/task id。
2. 对 MP3 jobs 按浏览器可见/隐藏时段、活跃分钟和 request id 聚类；验证隐藏态 0 请求、可见态同时在途 ≤1，并比较 212 次/95.265s 基线。
3. PDF 三入口各补足至少 5 次自然样本：旧 canonical 单项指纹必须为 0，每页查询 O(1)、结果字段完整；否则继续搜索其他缺失态回退入口。
4. Bilibili 覆盖完整 24 小时及全部计划周期：部署后宽读和逐行 UPDATE 都为 0，批量更新保持 O(监控源数)，抽检 raw JSON 字段完整。
5. 优先复查 browser history、MP3 load_task、scheduler config 三个网络放大指纹；若仍连续出现，继续追远端路由、PgBouncer server 建连和 commit/checkin，而不是添加索引或提高 timeout。
6. DBA 若安排维护窗口，逐段执行 bvid 重复索引脚本并保存定义、scan、EXPLAIN、锁等待和回滚证据；未执行时保持 OPEN 风险。
7. 继续报告低频高延迟、慢 API 无慢 SQL、后台长任务无前台三类边界；任何 OBSERVING 项只有在后续窗口达成验收条件后才可 CLOSED/RUNTIME_ACCEPTED。

## 09:21 授权执行跟进：代码已加载，DDL 未获破坏性门禁放行

- **维护工具与 DDL 状态**：新增默认只读、显式 `--apply` 的
  `scripts/maintenance/apply_bilibili_competitor_bvid_index_cleanup.py`，初版 5 项 TDD 通过；续审又以
  2 个先红后绿用例补齐保留索引 invalid 与 EXPLAIN 验收失败时的统一回滚，现为 7 项。live dry-run
  和同步后复核均为 1,106 行、重复组 0、等待锁 0；约束索引与独立索引均 valid/ready/unique、
  64kB，且只有前者拥有唯一约束。直接 DDL 请求被破坏性操作审查在进程创建前拒绝，故数据库没有
  发生变更；独立索引仍存在，scan 48,782→48,962，约束索引 scan 0。该动作保持 OPEN，不能写成
  已完成。
- **安全重启**：门禁先后保护两条 PDF 清理及其发布子任务，知识星球 1059 有界重试最终成功；两项
  发布自然完成后，09:18:43 门禁返回 OK。标准重启将 backend PID 70203 替换为 21133；health
  healthy、broker OK、17/17 服务 running、无 paused，watchdog PID 21685，Bilibili 常驻调度器
  唯一 PID 21707。全程未使用 FORCE。
- **真实负载**：09:21 run `bilibili-intelligence-20260828T012100Z` 为 6/6 源、180 条、0 失败、
  created 0 / updated 180、9.749 秒。目标表为 6 SELECT + 12 UPDATE + 0 INSERT；SELECT/UPDATE
  最大 177.00/116.85ms，无新慢 SQL，旧宽读和逐行 UPDATE 在新边界内为 0。
- **字段与查询正确性**：两条最新真实行各保留 38 个 raw 字段，七个核心审计键全部存在；代表点查
  返回 2/2、executor 0.037ms。因为 DDL 未执行，planner 仍使用独立索引；这既证明没有查询退化，
  也不能冒充保留索引的 post-drop 验收。
- **新增运行证据**：等待安全窗期间捕获 `resolve_zsxq_cookie_accounts` SELECT 6.190 秒、连接持有
  6.462 秒而非 SQL 仅 0.272 秒；重启门禁本身一次窄 `publication_tasks` SELECT 也达 2.308 秒。
  两者继续支持远端 PostgreSQL/PgBouncer 链路长尾 OPEN，不支持为这些路径盲加索引。
- **验证**：索引维护工具定向 7 passed；Bilibili scheduler + DB projection + 工具合计 101 passed、
  3 subtests；完整 unit 1,419 passed、3 skipped、305 subtests；compileall、Node 契约、
  `git diff --check`、registry JSON 与 Bug strict 均通过。最终文档更新后再次执行的门禁结果见本节后续
  交付记录。

跟进后的 Bilibili 代码状态仍为 `RUNTIME_REPLACED / OBSERVING`，重复索引维护为 `OPEN /
AWAITING_EXPLICIT_DDL_CONFIRMATION`，PgBouncer/网络为 `OPEN / REPEATED`。下一轮必须优先复核本次
09:19 新进程边界后的完整 24 小时；DDL 若获确认，则用同一维护工具重做即时门禁、执行精确并发 DROP、
保存 post-drop 目录/行数/重复组/EXPLAIN/回滚证据。
