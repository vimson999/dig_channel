# 2026-11-01 数据库性能与错误巡检

## 巡检窗口与根因闭环结论

- 固定窗口：2026-10-31 06:00:00 至 2026-11-01 06:00:00（Asia/Shanghai）。
- 基线：2026-10-31 巡检、既有 Bug 记录、Session 生命周期纪律与 recurrence registry。
- **本轮不能宣告全局完成。** 应用慢 SQL 数量和累计耗时显著下降，但固定窗只有 16 条完成 HTTP、没有新建
  `publication_tasks`，多数业务链路没有自然暴露；零计数不能替代运行态证据。
- B站情报 DNS/TUN 问题是本轮唯一达到根因闭环标准的主要问题：代理根因已修复，23 个自然小时周期、161/161
  competitor 请求成功且 DNS 错误为 0，状态更新为 `CLOSED/RUNTIME_ACCEPTED`。
- TTS 风暴保持 `RUNTIME_REPLACED/OBSERVING`；数据库 driver/Tailscale 接收尾延迟、YouTube 21/56、ZSXQ 1059、
  旧运行体物理隔离仍为 `OPEN`、`OBSERVING` 或等待人工动作。
- 本轮未执行生产 DDL、数据回填、破坏性操作，也未删除或裁剪原始 JSON 审计字段。

按累计耗时、P95/max、失败率、用户影响和复发次数综合排序，本轮优先级为：数据库网络接收尾延迟、YouTube
固定两源第 29 窗失败、TTS 后窗观察、远端旧运行体隔离、ZSXQ 1059 未暴露。B站 DNS 根因已完成闭环。

## 与上一轮的量化对比

| 指标 | 2026-10-31 | 本轮 | 变化/判断 |
|---|---:|---:|---|
| 慢 SQL 数量 | 39 | 10 | -74.4%，改善，但样本覆盖骤降 |
| 慢 SQL 累计 | 71.738s | 13.170s | -81.6%，改善 |
| SQL P50 | 1.349s | 1.144s | -15.2% |
| SQL P95 | 2.726s | 2.213s | -18.8% |
| SQL max | 9.885s | 2.213s | -77.6%；audio catalog 本窗未触发 |
| SQL/statement/lock/connection timeout | 0 | 0 | 无新增；不能外推到未暴露链路 |
| 完成 HTTP | 1,577 | 16 | -99.0%，自然业务覆盖不足 |
| HTTP 成功/失败 | 709/868 | 14/2 | 两个失败均为上一轮验收探针，不代表业务失败率改善 |
| HTTP P50/P95/max | 330ms/1.057s/62.643s | 1.67/311.37/972.23ms | 样本结构完全不同，不作同质趋势结论 |
| B站 competitor 成功 | 138/168 | 161/161 | 修复后自然周期 23/23 完成，DNS 错误归零 |
| YouTube channel/video errors | 2/2 | 2/2 | id 21/56 第 29 窗复发 |
| ZSXQ upload 1059 | 8 | 0 captured | 本窗无发布任务，属于未暴露，不是改善 |

## 慢 SQL、Session、PgBouncer 与 executor 分层

### 主要 SQL 指纹

| 排名 | 指纹/来源 | 次数 | 累计 | P95/max | 根因判断 |
|---|---|---:|---:|---:|---|
| 1 | `7c472aa43eb81ca0`，report audio recovery | 2 | 3.224s | 2.213/2.213s | 同形 executor 0.026ms；不是扫描或缺索引 |
| 2 | `c1e57cc097d3e051`，notification due | 2 | 2.361s | 1.217/1.217s | executor 0.243ms；应用/网络往返尾部 |
| 3 | `fe84b2c3255ffe39`，Publisher processing repair | 2 | 2.239s | 1.152/1.152s | executor 0.051ms；索引命中 |
| 4 | `674016d39deab3f5`，ZSXQ Insights | 1 | 2.021s | 2.021/2.021s | executor 32.663ms；远端结果接收仍主导 |
| 5 | `f9865691fae81ae9`，Publisher pending repair | 1 | 1.192s | 1.192/1.192s | executor 0.014ms |
| 6 | `59acf8fbd7ac9933`，report audio finalization | 1 | 1.082s | 1.082/1.082s | executor 0.025ms |
| 7 | `60ff7467696400d3`，all-tabs config | 1 | 1.050s | 1.050/1.050s | executor 0.024ms |

应用 SQL error、statement timeout、lock timeout、connection timeout、deadlock、server closed 均为 0；没有
`idle in transaction` episode，也没有连接持有记录。PgBouncer 1,440 个样本八类错误均为 0，288/288 个连接均
来自当前节点 `100.96.3.12`，没有 proxy/TUN fallback；wait/query P50 为 21.09/55.38ms、P95 为
37.54/63.94ms、max 为 134.23/157.93ms。

真实 SQL 的 `EXPLAIN (ANALYZE, BUFFERS)` 均显示 executor 为 0.014–32.663ms，而应用记录为
1.050–2.213s。证据继续指向 PostgreSQL executor 之后的 driver/Tailscale 结果接收和远端往返边界，不支持新增
索引、增加超时或重试。上一窗 `be891...` audio catalog 的 9.885 秒问题本窗请求为 0，因此保持
`OPEN/REPEATED/WAITING_INFRASTRUCTURE`，不能按“未捕获”关闭。

## 页面/API/后台任务与 SQL 关联

固定窗 16 条 HTTP 主要是上一轮 06:08 重启后的验收请求，backend heartbeat 则持续一整天；这证明服务存活，
但不构成全部业务页面的自然绿窗。

| 页面/API/任务 | 请求/结果 | 耗时 | SQL/调用链关联 | 状态 |
|---|---|---|---|---|
| TTS history | 1 次、成功 | 972.23ms | 同次 SQL 821.61ms，未达到 1s 慢 SQL 阈值 | 后续无周期轮询，仍缺自然任务/长开页 |
| TTS segment audio | 0 | — | 上一窗 890 请求/862 个 404 的扇出未在已捕获流量出现 | `RUNTIME_REPLACED/OBSERVING` |
| ZSXQ Insights overview | 无自然页面请求；1 条后台/缓存 SQL | SQL 2.021s | executor 32.663ms | 网络尾延迟仍在 |
| B站 intelligence | 23/23 周期完成；161/161 请求成功 | P50/P95/max 11.657/14.646/14.725s | 外部 HTTP，无慢 SQL/Session 交叉持有 | `CLOSED/RUNTIME_ACCEPTED` |
| YouTube channel sync | 49/51 | 约 17.76s max | 外部 API；id 21/56 失败 | `OPEN/REPEATED` |
| YouTube video sync | processed 49、errors=2 | 67.32s | 外部 API；同两源失败 | `OPEN/REPEATED` |
| workflow notification | 35 completed、0 retry/failed | 累计 82.212s | notification due SQL executor 0.243ms | 后台任务，无前台端点 |
| publication tasks / ZSXQ publish | 0 | — | 数据库快照确认本窗没有新任务 | 未暴露 |

三类必须单列的异常如下：

1. **低频高延迟**：Insights SQL 2.021 秒；YouTube channel/video 单轮约 17.76/67.32 秒。
2. **页面/API 很慢但无匹配慢 SQL**：固定窗没有足够自然页面样本，不能判定为“无”。窗后真实 API 烟测中
   overview 3.332 秒，明显高于其他端点，仍符合 executor 后尾延迟特征。
3. **后台任务或连接持有很久但无前台端点**：workflow notification 35 次累计 82.212 秒；YouTube、B站均为
   scheduler 外部调用链。没有长 Session 持有证据，但无 publication task 也意味着 Publisher/ZSXQ 链未暴露。

## 程序模式复核与反复问题

- **TTS 轮询/N+1/失效媒体扇出**：上一窗已落地 active-only、Page Visibility、single-flight、终态停止、
  `preload=none`、资源存在性投影和 Session 提前释放。本窗只有 1 次验收 history，之后 history、segment audio、
  404 均为 0；但没有自然 TTS 任务与长时间打开页面，故不能标记 CLOSED。
- **大字段/超大分页**：固定窗没有新证据显示恢复 TTS 宽 JSON 或 Insights 全量正文读取；publication_tasks 的
  TOAST 仍为 120MB，继续要求投影和缓存纪律。低流量不足以证明所有页面无超大分页。
- **重复聚合/缓存**：Insights 真实 SQL 应用 2.021 秒、executor 32.663ms，仍以网络接收为主；不以重复索引
  掩盖。audio catalog 冷 miss 本窗未触发，缓存有效性无法重新验收。
- **Session 与外部 I/O 交叉持有**：日志中连接 hold、idle-in-transaction 均为 0；现有提前释放门禁未见复发，
  但 Publisher、ZSXQ 发布零任务，保持观察。
- **B站 DNS/TUN**：上一窗定位到 `bilibili_api` 内部空 proxy 未继承进程环境代理；显式注入库级 proxy 后，
  23 个自然周期、161/161 competitor 成功且 DNS/nodename 错误为 0。根因、修复、回归和完整自然后窗均具备，
  本问题闭环；通用外部 provider 可用性继续常规监控。
- **YouTube 21/56**：永久不可枚举源连续第 29 窗失败，不能用全任务最终成功掩盖；等待人工 DBeaver 停用。
- **ZSXQ 1059**：本窗无发布任务，上一窗 8 次失败没有得到反证，继续 `OPEN/UNOBSERVED`。
- **旧运行体**：当前 `pg_stat_activity` 只见 `100.96.3.12` 与本地 gunicorn，旧 Mac 快照为 0；但没有自然任务、
  也没有卸载/凭据轮换/broker namespace 隔离证据，保持 `WAITING_DISTRIBUTED_ISOLATION`。

## 表规模、扫描与索引复核

| 表 | 总尺寸 | live/dead | index usage | 判断 |
|---|---:|---:|---:|---|
| publication_tasks | 153MB（table 18MB、TOAST 120MB、index 14MB） | 14,751/5 | 99.84% | 宽 JSON 继续投影；无新增索引依据 |
| zsxq_topics | 133MB | 13,981/1,665 | 99.51% | Insights executor 32.663ms，不支持新索引 |
| zsxq_topic_comments | 119MB | 45,884/6,093 | 99.84% | 观察 autovacuum，不人工 DDL |
| zsxq_attachments | 79MB | 35,988/75 | 99.94% | 无热点顺扫证据 |
| zsxq_sync_runs | 37MB | 1,077/61 | 99.90% | 代表查询 executor 1.561ms |
| external_documents | 约 7MB | 1,817/146 | 98.73% | 无新热点 |

exact duplicate index 为 0、waiting lock 为 0；`pg_stat_statements` 未安装，表/索引统计自 2026-08-08 server
start 累计且未 reset。代表 EXPLAIN：点查 0.043ms、chart 3.191ms、all-tabs 0.024ms、Publisher list
0.064ms、content list 0.247ms、wide/narrow JSON 0.025/0.024ms、sync runs 1.561ms、Insights 32.663ms。
**本轮没有 CREATE/DROP INDEX 建议，也没有待执行索引 SQL。** 在获得真实高成本 executor、reset 后统计和
`ANALYZE, BUFFERS` 三重证据前，禁止按应用壁钟或累计低使用盲目增删索引。

## 运行态验收与验证

- 固定窗内 B站修复后的 23 个自然小时周期全部 completed、161/161 competitor 请求成功，DNS 错误为 0。
- 窗后真实 backend API 烟测：health 200/0.052s、ZSXQ overview 200/3.332s、topics/local 200/0.961s、
  topics/local/tabs 200/0.328s、sync/runs 200/0.290s、scheduler status 200/0.646s、frontend 真实参数的
  runtime-status 200/0.354s。
- 一次缺少 `services` 参数的 runtime-status 诊断探针返回 400；它发生在固定窗后，属于巡检自测参数错误，随后
  用前端真实参数验证为 200，不计作产品根因。
- 本轮没有新增源代码修复，不需要重启 backend/Celery/scheduler；因此没有绕过重启安全门禁。
- 定向 Python 回归 53/53 通过；完整前端合同测试 90/90 通过。
- 完整 unit tests 首次在沙箱内因 loopback bind 权限产生 35 个环境错误；授权环境补上项目约定的
  `PYTHONPATH=src/backend` 后新鲜重跑为 **1,749/1,749 通过**，无失败或跳过。
- `compileall`、`git diff --check`、recurrence JSON 与 Bug 记录 strict 均通过；strict 共检查 218 条 Bug，
  218 条全部归类、9 个复发根因簇、回顾日期为 2026-11-01。

## 待人工动作、风险与未捕获边界

1. 人工在 DBeaver 复核并逐条执行 `docs/sql/disable_unavailable_youtube_channels_20260920.sql`；脚本已有
   `lock_timeout`/`statement_timeout`、验证和回滚说明。禁止无人值守执行。
2. audio catalog 需在下一次自然冷 miss 捕获 connect/acquire、execute/fetch、bytes、socket/route 与 executor
   同秒证据；不得用新索引、删字段、增加超时或重试掩盖。
3. 旧 Mac 栈需取得停用/卸载证据；否则轮换 PostgreSQL/Redis 凭据并隔离 broker namespace。单次会话为 0
   不能替代物理隔离。
4. `pg_stat_statements` 不可用、统计未 reset；本轮不得生成或执行索引 DDL。
5. 未被日志捕获不等于未发生：固定窗没有自然 publication task、ZSXQ publish、audio catalog 或完整 TTS 用户
   会话；request id 只能关联已记录请求，provider 内部阶段和网络 receive 边界仍可能不可见。

## 根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险/责任动作 | 下一轮验收条件 |
|---|---|---|---|---|---|---|
| `BILIBILI-DNS-TUN` | `bilibili_api` 空 proxy 未继承环境代理，落入 TUN fake-IP DNS | 显式设置库级 proxy；既有回归合同 | 23/23 自然周期、161/161、DNS=0 | 否 | 通用 provider 可用性常规监控 | 继续确认后续自然周期无 proxy/DNS 回归；本根因已 `CLOSED/RUNTIME_ACCEPTED` |
| `TTS-HISTORY-POLLING-AUDIO-FANOUT` | 终态无界轮询、宽 JSON、媒体预加载、失效 URL | active/visible/single-flight、preload none、存在性投影、Session 提前释放 | 后窗 1 次验收 history，后续 history/audio/404 均 0 | 已捕获流量未复发 | 无自然任务与长开页 | 自然 TTS 任务 + 24h 长开页无空闲轮询、404=0、指纹不放大 |
| `DB-DRIVER-TAILSCALE-RESULT-TAIL` / `be891...` | executor 后 driver/Tailscale 结果接收尾部 | 既有投影/TTL/single-flight；本轮无掩盖变更 | 本窗通用 SQL executor 0.014–32.663ms，应用 1.050–2.213s | 既有指纹未暴露 | 基础设施 owner 补同秒 socket/route A/B | audio catalog 自然冷 P95 收敛且物理断点闭合 |
| `YOUTUBE-21-56` | 两源永久不可枚举 | 受保护停用 SQL 待人工 | channel 49/51、video errors=2 | 是，第 29 窗 | 人工 DBeaver | 连续两周期 49/49、errors=0 |
| `ZSXQ-UPLOAD-1059` | provider 上传 1059，物理原因未知 | 既有有界 retry/幂等/verify | 本窗任务 0，未验收 | 未暴露 | 补 provider 阶段与 A/B | 完整窗 upload/retry/pending 全 0 且真实发布成功 |
| `REMOTE-OLD-RUNTIME` | 共享 DB/Redis/broker 未物理隔离 | 诊断与 actor 门禁已有 | 当前旧 Mac DB 会话 0，但自然任务 0 | 未暴露 | 停旧栈或轮换凭据/namespace | 完整 24h remote/old/foreign=0 + 当前根自然链成功 |

## 下一轮明确验收计划

1. 先按自然请求量归一复核 TTS history、`4ff19...`、segment audio 404，并主动覆盖一个真实任务和长开页面。
2. 继续确认 B站自然周期的 competitor 7/7 与 DNS/proxy 错误为 0；只有新指纹出现才重新打开已闭环根因。
3. 在 audio catalog 下一次冷 miss 同秒记录 socket/route、fetch bytes、driver fetch 与 executor，定位物理断点。
4. 优先复核 YouTube 21/56、ZSXQ 1059/retry/pending、旧运行体会话/actor；不用最终成功或零快照代替闭环。
5. 保持全站端点统计；若自然 HTTP 仍过低，明确标记 UNOBSERVED，不得生成“所有页面正常”的结论。
6. 复跑表规模、索引和 EXPLAIN；只有真实 SQL、reset 后统计与 `ANALYZE, BUFFERS` 同时支持时才生成 DDL 草案。
