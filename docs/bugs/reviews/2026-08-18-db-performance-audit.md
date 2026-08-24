# 2026-08-18 数据库性能与错误巡检

## 结论

本轮没有达到“全部根因 CLOSED”。窗口流量显著下降，但 SQL 与 HTTP 尾延迟、HTTP 失败率均恶化；PgBouncer/远端网络仍是最高优先级 OPEN 根因。已落地并运行替换三项可安全修复的应用根因：视频编辑器隐藏页轮询、Watchdog 成功分支 Session 未释放、ZSXQ Insights 查询后跨 CPU/缓存持有 Session；同时修复了分析器把 scheduler SQL 失败误归为临时诊断的问题。它们均缺完整后续窗口，所以保持 OBSERVING，不写成已解决。

固定分析窗口为 `2026-08-17 06:02:19` 至 `2026-08-18 06:02:19`（Asia/Shanghai）。基线为 `2026-08-16-db-performance-audit.md`。窗口后运行态证据单独标明，不混入 24 小时统计。

## 24 小时量化基线与上轮对比

| 指标 | 本轮 | 上轮 | 判断 |
| --- | ---: | ---: | --- |
| 应用慢 SQL | 369 | 688 | 数量改善 46.4%，但受流量下降影响 |
| 应用慢 SQL 累计 | 981.741s | 1658.905s | 改善 40.8% |
| SQL P50 / P95 / 最大 | 1.693 / 7.648 / 21.707s | 1.578 / 7.079 / 18.179s | 分别恶化 7.3% / 8.0% / 19.4% |
| HTTP 请求 | 2124 | 7569 | 流量下降 71.9% |
| HTTP 累计 | 1639.689s | 4451.187s | 下降 63.2% |
| HTTP P50 / P95 / 最大 | 0.158 / 3.874 / 21.231s | — / 2.884 / 26.202s | P95 恶化 34.3%，最大改善 |
| HTTP 失败 | 45 / 2.12% | 24 / 0.32% | 次数与失败率均恶化；ZSXQ import 主要是 400 输入校验 |
| Connection hold | 70；P50/P95/max 7.777/20.315/23.889s | — | 37 web、17 scheduler、10 worker；SQL 509.678s，非 SQL 163.751s |
| statement/lock/idle-in-tx timeout | 0 / 0 / 0 | 0 / 0 / 0 | 未捕获不等于未发生 |
| PgBouncer query/connect timeout | 3 / 6 | 7 / 11 | 数量改善 57.1% / 45.5%，但仍复发 |

慢 SQL 的 P95、最大值和 HTTP P95 在更低流量下反而上升，因此不能把数量下降视为根治。

## 主要问题排序

排序综合累计耗时、P95、最大耗时、失败率、页面/任务影响与复发次数，而非只看次数。

1. **PgBouncer/远端 PostgreSQL 网络长尾（OPEN，反复）**：3 个 query timeout、6 个 connect timeout；两条托管 `publication_tasks` SQL 以 server closed 失败，最大约 20.3s。现场 executor 是 0.025–34.558ms 量级，无锁等待、无 idle-in-transaction，网络/连接往返证据远强于缺索引。
2. **Celery status + Publisher 成对后台轮询（CODE_CHANGED/OBSERVING，反复）**：两端点合计 644 次、931.740s；最大 21.231/16.827s，42+57 条慢 SQL。视频编辑器隐藏页无 Visibility 门禁是明确调用源。
3. **scheduler config 指纹 `60ff7467696400d3`（OPEN，反复）**：101 次、累计 228.478s、P95 4.733s、最大 21.707s，仍是最高累计 SQL 指纹；executor 0.028ms，根因不是索引。
4. **ZSXQ Insights 冷链路（RUNTIME_REPLACED/OBSERVING；网络部分 OPEN）**：修复前真实请求 18.392s、Session 13.790s，其中非 SQL 9.427s；修复后 CPU/缓存移出 Session，但最终冷请求仍因结果 fetch/rollback 网络长尾为 26.856s。
5. **Watchdog repair Session 生命周期（RUNTIME_REPLACED/OBSERVING，反复）**：成功 no-op 分支未 release，令后续 repair/文件/broker 路径交叉持有；已在 wrapper finally 修复。
6. **后台长任务**：231 个 Celery 成功、0 retry、0 fail，总计 8419.531s；ZSXQ publish 8/2307.395s、内容 PDF 3/2270.080s、Publisher 9/1588.886s、upload 8/967.275s。长任务本身不等价慢 SQL，窗口没有任务失败风暴。

## 页面 / API / 后台任务与 SQL 关联

| 页面或链路 | 请求/任务 | 总耗时 / P95 / 最大 | SQL、Session 与调用源 |
| --- | ---: | --- | --- |
| 视频编辑器任务列表 | Celery status 323；Publisher 321 | 635.286s / 8.457 / 21.231s；296.454s / 4.751 / 16.827s | `main.js` 4s timer 扇出两端点；42 条 Celery 慢 SQL 76.9s，57 条 Publisher 慢 SQL 186.914s |
| MP3 工作流列表 | 361 | 138.404s / 2.053 / 6.886s | 可见页活动期约 3s；未捕获 hidden 证据，保持观察 |
| MP3 详情 | 21 | 76.685s / 17.488 / 18.745s | 低频高延迟；与远端 SQL/连接尾延迟同窗 |
| ZSXQ browser import | 21，失败 4 | 34.887s；失败率 19.05% | 主要为 HTTP 400 业务输入校验，不是数据库 5xx |
| Insights overview | 窗口内低频；窗口后强制冷验收 | 修复前 18.392s；修复后冷 26.856s、热 3.70ms | executor 34.558ms；修复前跨 CPU 持有，修复后剩余 fetch/rollback 网络持有 |
| Watchdog/scheduler repair | lifecycle 样本 | Publisher child cleanup 非 SQL 52.690s；report video stats 非 SQL 33.984s | 成功 repair wrapper 未 release，后续文件/broker 路径继续占 checkout |
| workflow notification | 123 | 178.791s / 1.859 / 6.669s | 0 retry/0 fail；SMTP 上轮修复改善但没有故障注入，继续 OBSERVING |

三类强制异常：

- **低频高延迟**：MP3 详情、continue full video（4 次/35.435s，P50 15.247s）、Bilibili upload（8 次/34.704s，P95 18.686s）、data summary（4 次/30.283s，P95 21.189s）。
- **页面慢但没有匹配慢 SQL**：Celery status 总 635.286s，仅 76.9s 匹配慢 SQL；broker inspect、进程扫描、普通 SQL 和网络等待占其余预算。
- **后台/连接长持有但无前台端点**：scheduler repair、启动配置读取和 worker 任务；必须按 PID/role/origin 而不是 request id 关联。

## 根因定位与已落地修复

### 视频编辑器隐藏轮询

修复前：`main.js` 无条件 4s `setInterval`，隐藏 Chrome 被节流成 60s但继续成对调用 Celery/Publisher。修复：接入共享 visibility controller 和 cache-bust。定向回归验证 hidden pause、visible immediate、single-flight、stop cleanup；真实 HTML/helper 200。状态 OBSERVING，等待完整窗口。

### Watchdog repair Session

修复前：`_isolated_watchdog_repair` 仅异常时 release，成功 no-op 读事务跨下一个 repair、文件和 broker 工作。修复：`finally` 无条件 release。定向 37 个 watchdog 测试通过；安全门后标准重启，新 Watchdog 若干轮未出现旧型 >5s 跨 repair hold。状态 OBSERVING。

### ZSXQ Insights Session 与远端 fetch

修复前 request `577298a3`：18.392s；checkout 13.790s，SQL 4.363s、非 SQL 9.427s。代码改为四个显式只读 scope，物化后 release，CPU/排序/缓存仅使用 dict/Row；保留 SQL origin 和字段完整性。最终新进程 request `b24437ad`：SQL origin 正确，冷 MISS 26.856s、热 local HIT 3.70ms、health 0.51ms。详情 scope 仍持有 13.253s，其中 SQL hook 3.684s、fetch/rollback/checkin 9.569s；executor 同形 0.137ms。应用跨 CPU 持有已替换，但远端网络仍 OPEN。

### 分析器托管来源误分类

修复前两条 scheduler server-closed SQL 因 role/process/origin 缺失被误算 ad-hoc，应用 SQL error 错报 0。分类器现以 app/backend/scheduler/celery `source_path` 为托管证据，并在去重时升级来源。重跑后 application SQL error=2、ad-hoc=0、托管生命周期 ad-hoc=0；分析器 20 个测试通过。

## PostgreSQL 与索引复核

PostgreSQL 16.13；read-only 诊断，无 recovery。无 waiting lock、无 idle in transaction；`statement_timeout=1min`、`lock_timeout=5s`、`idle_in_transaction_session_timeout=1min`；`pg_stat_statements` 未安装。

| 表 | 总大小 | live/dead | seq/idx scan | idx 使用率 | 重点风险 |
| --- | ---: | ---: | ---: | ---: | --- |
| zsxq_topics | 128MB | 12415/1584 | 3271/870289 | 99.63% | TOAST 79MB；更新 HOT 0.71% |
| publication_tasks | 123MB | 12678/458 | 6866/2837244 | 99.76% | TOAST 94MB；更新 HOT 1.33% |
| zsxq_topic_comments | 118MB | 41333/5644 | 252/356834 | 99.93% | table 91MB；HOT 70.46% |
| zsxq_attachments | 77MB | 34241/6630 | 573/1132180 | 99.95% | 更新 264259、HOT 0.12%，写放大 |
| zsxq_sync_runs | 37MB | 1025/235 | 217/334162 | 99.94% | index 35MB、频繁更新 |
| external_documents | 7MB | 1817/146 | 1018/84931 | 98.82% | 小表 seq scan 可接受 |

没有 exact duplicate index。代表性 `EXPLAIN (ANALYZE, BUFFERS)`：watchdog point 0.037ms、chart 2.590ms、scheduler config 0.028ms、Publisher list 0.066ms、content 0.137ms、JSON 0.076ms、narrow 0.025ms、sync 1.563ms、Insights canonical 34.558ms。证据不支持新增索引。

低使用索引包括 attachments PDF group tab（约 5MB、scan 1）、topics group type（约 3.7MB、scan 1）等，但仅凭低 scan 不足以删除。没有执行任何 CREATE/DROP/ALTER/backfill。

既有人工维护仍阻塞：`docs/sql/comment_hash_index_cleanup_dbeaver_20260724.sql`。启动门禁确认迁移未记录、两个等价重叠索引仍在；必须由管理员在 DBeaver 按脚本逐条执行 lock/statement timeout、验证和回滚，自动化不得无人值守运行。

## 测试与运行态门禁

- TDD/定向：analyzer 20、watchdog 37、Insights service 10、visibility controller 与视频编辑器入口测试通过。
- 完整 unit 采用确定性拆分：沙箱内排除两组 loopback 文件后 `1260 passed, 3 skipped`；两组 loopback 文件在允许本机 socket 后 `30 passed`，合计覆盖收集到的 1293 项（1290 passed、3 skipped）。首次沙箱整跑的 32 个失败均为 loopback bind 权限；一次全提权运行因放开真实外网触发无关 SSL 路径并被中止，不作为通过证据。
- 重启：首次沙箱门禁因网络权限 fail-closed，没有据此绕过；提权只读门禁两次确认无不可中断任务后才运行标准 `restart-dev-stack.sh`。新 backend PID 42761 监听，bootstrap errors 为空，workers/schedulers/watchdog 已补齐。
- 运行态：backend health 200；Celery status 200/1.724s，broker ok、17 个服务均 running 且 active/reserved/scheduled 为 0；Watchdog status 200/0.126s，PID 43291 running；Insights 冷/热 200；视频编辑器/helper 200。冷链路网络尾延迟仍复发，故不标 CLOSED。
- `compileall` 覆盖 `src/backend`、`scripts/diagnostics`、`scripts/dev`；Node 三个 frontend 脚本和 JavaScript syntax 通过；Bug strict 为 168/168、9 个复发簇；recurrence JSON 与 `git diff --check` 通过。

## 根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险 / 责任动作 | 下一轮验收条件 |
| --- | --- | --- | --- | --- | --- | --- |
| `VIDEO-EDITOR-HIDDEN-PAIR-POLL-20260818` | 4s timer 无 visibility/single-flight，扇出两端点 | 共享 controller、HTML 版本、契约测试 | 静态资源 200 | 是，同类页面纪律复发 | 缺后续 hidden 窗口；前端/巡检负责 | hidden 计数归零，visible 单次恢复，无 overlap，完整 24h |
| `WATCHDOG-SUCCESS-REPAIR-SESSION-20260818` | wrapper 仅异常 release | finally 无条件 release | 新 watchdog 若干轮无旧型跨 repair >5s hold | 是 | 观察窗短；backend/watchdog 负责 | 完整 24h repair round 无跨文件/broker checkout、无 idle transaction |
| `ZSXQ-INSIGHTS-READ-SCOPE-20260818` | SQL 后 CPU/缓存仍占 scoped Session | 四个有界只读 scope | 新 PID、冷/热/health 200，origin 保留 | 应用根因未再捕获；网络长尾仍复发 | fetch/rollback 9.569s；DB/网络链路负责 | CPU 阶段 checkout=0；fetch/checkin P95 <500ms；完整窗无 >5s hold |
| `PGBOUNCER-NETWORK-QUERY-CONNECT` | 远端连接/传输不稳定；非 executor/锁/索引 | 既有 route/keepalive/诊断；本轮无掩盖修复 | query/connect 3/6；server closed 2；executor 毫秒级 | 是 | OPEN；运维关联 socket/source/path | 24h query/connect/query-wait/crash/TLS/server-closed=0，fetch/checkin 无长尾 |
| `60ff7467696400d3` | scheduler/Web 稳定配置反复远端读取叠加网络长尾 | 既有 TTL；本轮保持监测 | 101/228.478s/P95 4.733/max21.707；executor0.028ms | 是 | 跨进程 cache/失效链仍待查 | 次数/累计显著下降且 P95<500ms，按 origin 证明 cache hit |
| `MANAGED-SQL-SOURCE-CLASSIFIER-20260818` | 缺 role/process 时忽略日志来源 | source-aware classifier + replay | 重跑 application error2/ad-hoc0 | 是，巡检完整性复发 | slow_query 单源仍可能未知；诊断负责 | 三源一致，托管错误不再落入 ad-hoc |
| `SMTP-DOUBLE-FIXED-RETRY` | 上轮双层固定重试 | 既有单预算+任务抖动 | 本轮通知123，0 retry/0 fail | 未捕获失败样本 | 缺故障注入/最终送达 | 新失败单预算、durable pending、最终送达闭环 |
| `INDEX-GOVERNANCE-20260818` | 网络主导；低使用索引删除证据不足 | 无 DDL；保留人工脚本 | catalog+EXPLAIN+无锁/idle | 无新增指纹 | 人工维护未执行 | 有真实 SQL/stats/EXPLAIN/写成本/回滚后才变更 |

## 未捕获边界

- `pg_stat_statements` 不可用，无法取得服务器端跨应用累计执行统计；用应用 SQL、PgBouncer 和现场 EXPLAIN 三角验证。
- SQLAlchemy `after_cursor_execute` 不含全部结果 fetch 与 rollback/checkin，lifecycle 的“non_sql”可能包含网络取数/事务回收；下一轮必须进一步拆点，不能一律解释为 Python CPU。
- `slow_query.log` 若 role/process/origin 全缺且脱离原始 scheduler/backend 路径，无法可靠重建托管来源。
- 窗口没有 comments、external_documents 慢 SQL，不代表这些表无问题；没有真实 DeepSeek report-audio 新任务，也不能关闭其观察项。
- 浏览器 hidden 行为有确定性 Node 证据，但尚无完整真实后台标签时间窗。
- ZSXQ import 的 400 被计入 HTTP 失败；它影响用户成功率，但不能误报为 DB 错误。

## 下一轮明确验收计划

1. 优先读取本报告和 registry，先复核 `PGBOUNCER-NETWORK-QUERY-CONNECT`、视频编辑器成对轮询、Insights read scope、Watchdog wrapper 四个复发键。
2. 以 PID 42761 和新静态版本为切点跑完整 24 小时：hidden 页两端点停止；Watchdog 无跨 repair hold；Insights CPU/缓存阶段 checkout 为 0。
3. 为 DB lifecycle 增加 execute/fetch/rollback/checkin 分段证据，逐个 timeout 关联 PgBouncer server source、连接年龄、Tailscale path、request id、SQL origin。
4. scheduler config 按 Web/scheduler origin 分拆 cache hit/miss；网络稳定后仍达百次量级时继续追跨进程缓存与失效写入。
5. 捕获下一条真实 report-audio DeepSeek 和通知失败样本；没有样本继续 OPEN/OBSERVING。
6. 人工 DDL 仍只执行 `comment_hash_index_cleanup_dbeaver_20260724.sql`；未满足真实 SQL + stats + EXPLAIN + 锁/回滚证据时不提出新 DDL。

## 报告后门禁补记

- Python unit：拆分后 `1260 passed, 3 skipped` + loopback `30 passed`，无业务断言失败。
- Frontend Node：3/3 scripts 通过；JavaScript syntax 通过。
- `compileall`：`src/backend`、`scripts/diagnostics`、`scripts/dev` 通过。
- Bug strict：168/168 已归类、9 个复发簇、回顾周期未过期；`jq empty` 通过。
- `git diff --check`：通过。
