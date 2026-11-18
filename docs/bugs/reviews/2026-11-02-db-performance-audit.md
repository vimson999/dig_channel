# 2026-11-02 数据库性能与错误巡检

## 巡检窗口与根因闭环结论

- 固定窗口：2026-11-01 06:00:00 至 2026-11-02 06:00:00（Asia/Shanghai）；对比窗使用同一分析器重放
  2026-10-31 06:00:00 至 2026-11-01 06:00:00。
- **本轮不能宣告全局完成。** 慢 SQL 从 10 条/13.170 秒恶化为 22 条/59.248 秒，首要指纹 audio catalog
  四次累计 38.203 秒；远端旧 Mac 数据库会话与日志外 writer 也再次出现。
- B站定时投稿三次 21173 已定位到“5 分钟入口校验没有覆盖约 8.5 分钟上传 P95”。本轮按 TDD 落地
  15 分钟准入、worker 外部调用前复核、SDK 提交前 5 分钟硬门槛并标准运行替换；由于尚无修复后自然投稿和
  后续完整零复发窗，状态仅为 `RUNTIME_REPLACED/OBSERVING`，不是“已解决”。
- audio catalog 的 executor 外 Tailscale/driver 结果接收、远端旧运行体、ZSXQ 1059、YouTube 21/56 均继续
  `OPEN/REPEATED`。TTS 本窗无自然任务暴露，保持 `OBSERVING/UNOBSERVED`。
- 本轮没有执行生产 DDL、数据回填、凭据轮换、生产配置或破坏性操作；没有裁剪原始 JSON 审计数据。

按累计耗时、P95/max、失败率、页面/用户影响和复发次数综合排序：audio catalog 网络接收尾部、远端旧运行体、
B站 21173、ZSXQ 1059、YouTube 21/56、TTS 后窗观察。没有新增根因达到 CLOSED 标准。

## 与上一轮的量化对比

| 指标 | 上一窗 | 本轮 | 变化/判断 |
|---|---:|---:|---|
| 慢 SQL 数量 | 10 | 22 | +120.0%，恶化 |
| 慢 SQL 累计 | 13.170s | 59.248s | +349.9%，恶化 |
| SQL P50/P95/max | 1.144/2.213/2.213s | 1.070/9.667/9.902s | P50 -6.5%，P95/max 约 +337%/+348% |
| SQL/statement/lock/connect timeout | 0 | 0 | 无新增；不覆盖未捕获链路 |
| 完成 HTTP | 16 | 1,241 | 覆盖显著恢复，106 个端点 |
| HTTP 成功/失败 | 14/2 | 1,233/8 | 8 个为 1 次巡检参数 400 + 7 次 favicon 404；自然业务 5xx=0 |
| HTTP P50/P95/max | 1.67/311.37/972.23ms | 3.37/818.33ms/57.217s | 长尾主要是同步/上传外部链路 |
| Celery 成功/失败/retry | 35/0/0 | 246/3/0 | 上一窗只捕获 1 类任务，本轮 246 成功覆盖多类；三次失败均 B站 21173 |
| ZSXQ 1059 upload/retry | 未暴露 | 7/1 | 自然复发，最终成功不能覆盖中间失败 |
| YouTube channel/video errors | 2/2 | 2/2 | id 21/56 连续第 29 窗复发 |
| 远端旧 Mac DB 会话 | 0 快照 | 6 | 复发；地址 `100.119.238.48`，空 application_name |

**覆盖异常：**上一窗仅捕获 16 个 HTTP 和 35 个 Celery success（1 个 task type），本轮分别为 1,241 个 HTTP、
246 个 Celery success 和 3 个 failure。两窗自然暴露量/任务类型不对称，因此 HTTP/Celery 的上一窗 0 failure
只能记作“当前统计未捕获”，不能解释为业务零事故，也不能用成功率作同口径趋势结论；慢 SQL 使用同一固定窗分析器，
仍可作量化对比。

## 慢 SQL、Session、PgBouncer 与 executor 分层

### 主要 SQL 指纹

| 排名 | 指纹/来源 | 次数 | 累计 | P95/max | 根因判断 |
|---|---|---:|---:|---:|---|
| 1 | `be891b5c1af362da`，`mp3_video_workflow:_build_audio_asset_catalog_snapshot` | 4 | 38.203s | 9.902/9.902s | executor 外 driver/Tailscale 结果接收，占慢 SQL 累计 64.5% |
| 2 | `f9865691fae81ae9`，Publisher pending repair | 3 | 3.222s | 1.123/1.123s | 代表 executor 毫秒级，网络往返尾部 |
| 3 | `c1e57cc097d3e051`，notification due | 3 | 3.212s | 1.119/1.119s | executor 毫秒级 |
| 4 | `7c472aa43eb81ca0`，report audio recovery | 2 | 2.119s | 1.104/1.104s | executor 毫秒级 |
| 5 | `abbd...`，B站 intelligence | 2 | 2.066s | 1.050/1.050s | 未造成外部周期失败 |
| 6 | cleanup / Insights | 各 1 | 1.973/1.949s | 同左 | Insights 生产 executor 41.248ms，仍非缺索引 |

四个长连接持有 P50/P95/max 约 9.972/10.025/10.025 秒；匹配 SQL 合计 38.312 秒，hold 内非 SQL 仅
0.716 秒。3 个 origin 明确为 audio catalog；另 1 个 request origin 虽显示 auth checkout，但匹配 SQL 仍为同一
audio 查询。未见 Session 跨文件 I/O、旧附件 N+1 或 deferred lazy-load 复发。

既有同形生产 A/B 返回 1,558 行/891,354 bytes：PgBouncer 与直连 execute/fetch 分别 9.105/9.067 秒，executor
6.029/5.139ms，Python materialize 4.61/6.52ms。本窗自然 9.7–9.9 秒与该断点一致，继续排除 PostgreSQL
executor、缺索引、PgBouncer 排队和 Python 物化；共同边界是 psycopg2 经 Tailscale 的结果接收链路。

本窗没有 SQL error、statement/lock/connect/query timeout、deadlock、server closed 或持久
`idle in transaction`。25 个瞬时 idle 观测最大事务龄 0.570 秒、idle 0.540 秒且没有压力延迟。
PgBouncer 1,439 个样本八类错误均为 0，287/287 个连接来自当前节点 `100.96.3.12`，无 proxy/TUN fallback；
wait P50/P95/max 17.90/44.09/169.06ms，query 46.68/71.39/325.81ms。

## 页面/API/后台任务与 SQL 关联

固定窗 1,241 条 HTTP、106 个端点，成功率 99.36%；1 次 400 是巡检对 runtime-status 漏传必填 services，7 次
404 均为 `/favicon.ico`，自然业务 5xx 为 0。按累计耗时与长尾排序：

| 页面/API | 请求/结果 | 累计；P95/max | SQL/调用链关联 | 判断 |
|---|---|---:|---|---|
| `POST /videos/sync` | 1/1 成功 | 57.217s | 无匹配慢 SQL，外部同步 | 低频高延迟 |
| B站 uploads 列表 | 148/148 | 57.154s；0.650/1.429s | 仅 1 条 1.025s SQL | 高频累计，不是 executor 单点 |
| MP3 `audio-assets` | 13/13 | 41.642s；10.611/10.648s | 4 条 audio SQL 38.203s | 本轮首要性能根因 |
| `channels/sync` | 1/1 | 21.798s | 无慢 SQL，外部 YouTube | 低频高延迟 |
| B站 upload POST | 14；业务有 3 durable 失败 | 20.116s；1.587/2.094s HTTP | 真正媒体任务在 Celery，3 条 21173 | 定时窗口根因 |
| content web-submit | 2/2 | 18.095s；9.520/9.520s | 外部/文件链，无同耗时 SQL | 页面慢但无慢 SQL |
| ZSXQ Insights overview | 1/1 | 3.301s | Insights SQL 1.949s；executor 41.248ms | driver/网络接收尾部 |
| topics/local / tabs / sync status | 均有自然请求 | SQL 指纹最多约 1.1s | 无 lock/timeout | 可用但仍受远端往返尾部影响 |

Celery 固定窗 246 次成功、3 次失败、0 retry，累计 7,543 秒。主要任务：Publisher pipeline 8 次成功、累计
2,863.7 秒、P95/max 445.6 秒；upload-only 11 成功/3 失败、累计 2,505.5 秒、P95/max 510.7 秒；MP3 process
117 次成功、累计 939 秒；ZSXQ publish 2 次成功、累计 660.9 秒；audio clean 2 次成功、累计 339.7 秒；
notification 106 次成功、累计 234.4 秒。

三类异常单列：

1. **低频高延迟**：videos sync 57.217 秒、channels sync 21.798 秒、content web-submit 9.520 秒。
2. **页面/API 很慢但无匹配慢 SQL**：上述三个端点以及多个 8–13 秒 artifact regeneration；瓶颈位于外部
   I/O、媒体/文件处理或任务等待，不能归因数据库 executor。
3. **后台任务/连接持有很久但无前台端点**：Publisher/MP3/ZSXQ 任务为分钟级；旧 Mac 6 个 idle DB 会话最长
   状态龄约 27 分钟。应用长 hold 则全部与 audio SQL 相关，不是跨外部 I/O。

## 反复问题、根因与已落地修复

### B站定时上传 21173

3 个真实 durable task 创建时仅有约 7.75、8.0、10.95 分钟提前量；媒体上传后提交时分别剩 -15、168、299 秒，
全部落在 B站“至少 5 分钟”硬门槛内。根因不是偶发 provider 错误，而是入口只验证 5 分钟、没有预算本窗
510.7 秒 upload P95，也没有执行、每次媒体重试及提交阶段再校验。

修复证据链：真实 21173 与任务时间线 → RED 测试 → API/服务/前端 15 分钟准入（默认 16 分钟防分钟截断） +
worker 在充电档位远程查询前复核 + 每次媒体重试前重新准入 + SDK 提交前 5 分钟硬门槛 → 定向/全量测试 GREEN →
安全门放行后标准重启 →
新 worker/runtime/health/API 验收。余量不足保持用户原始发布时间，持久化
`failed/waiting_user_reselect + schedule_margin_expired`，禁止静默改时或重复上传。缺自然修复后投稿与完整后窗，
所以只记 `RUNTIME_REPLACED/OBSERVING`。

### audio catalog 接收尾部

自然四次复发且主导累计慢 SQL；缓存/single-flight 使 13 请求只有 4 次目录 SQL，现有 O(1) 投影、2,500 行上限
和 Session 外文件校验仍有效。字段均服务完整目录合同，删除字段或服务端分页会破坏完整性/全局搜索语义。
本轮不改代码、不加 timeout/TTL/重试/索引，保持 `OPEN/REPEATED/WAITING_INFRASTRUCTURE`。

### 远端旧运行体

固定窗 30/30 个目标 durable task 路径均 current-root，但 Publisher 22 条中 3 条“文件不存在”失败的素材在当前
节点实际存在、终态 actor 缺失；快照又见 `100.119.238.48` 的 6 个空 application_name 会话。相同绝对路径
不能证明主机相同，说明共享 DB/Redis/broker 的物理隔离仍缺。不得通过杀当前连接止血，因为旧进程可重连。

### ZSXQ 1059、YouTube、TTS 与 B站情报

- ZSXQ 两任务最终 completed，但有 7 次 upload failure、1 次 publish retry；比上一暴露窗 8 次略降仍属复发。
- YouTube 00:03 channel 49/51（10.75 秒），01:00 video 处理 49 源、errors=2（32.55 秒）；仍是 id 21/56，
  连续第 29 窗。
- TTS history/audio/status 本窗没有自然请求，不能用 0 指纹升级上一轮修复状态。
- B站情报 24/24 自然周期无 DNS/nodename 失败；已闭环的显式代理根因没有复发，保持 CLOSED，不把其他外部
  问题并入该指纹。

## 表规模、扫描与索引复核

| 表 | 总尺寸 | live/dead | index usage | 判断 |
|---|---:|---:|---:|---|
| publication_tasks | 153MB（table 18MB、TOAST 121MB、index 14MB） | 14,868/568 | 99.84% | 宽 JSON 继续投影；无新增索引依据 |
| zsxq_topics | 133MB | 13,981/1,665 | 99.51% | Insights executor 41.248ms，不支持新索引 |
| zsxq_topic_comments | 119MB | 45,884/6,093 | 99.84% | 观察 autovacuum，不人工 DDL |
| zsxq_attachments | 79MB | 35,988/75 | 99.94% | 无热点顺扫证据 |
| zsxq_sync_runs | 37MB | 1,077/61 | 99.90% | 代表 Seq Scan executor 1.536ms |
| external_documents | 约 7MB | 1,817/146 | 98.72% | 无新热点 |

exact duplicate index 为 0、waiting lock 为 0；`pg_stat_statements` 未安装，统计自 2026-08-08 server start 累计
且未 reset。代表 EXPLAIN：PK 0.044ms、chart 3.357ms、all-tabs 0.023ms、Publisher list 0.067ms、content list
0.286ms、wide/narrow recent 0.056/0.034ms、sync runs 1.536ms、Insights 41.248ms/7,799 行。

**本轮没有 CREATE/DROP INDEX 建议，也没有新增待执行索引 SQL。** 在真实高成本 executor、reset 后
pg_stat_user_* 和 `EXPLAIN (ANALYZE, BUFFERS)` 三者同时支持前，禁止按应用壁钟或累计低使用盲目增删索引。

## 测试与运行态验证

- TDD：最初 3 项行为测试以断言失败确认 RED；独立终审新增的“重试前再校验、充电查询前校验、前端 15 分钟
  合同”也逐项先 RED 后 GREEN。
- 完整 unit 首跑发现 2 个旧测试夹具仍使用 10 分钟提前量/缺 FakeUploader 新接口；只更新夹具后定向 2/2、
  终审修复后沙箱首跑只有 34 个回环 bind PermissionError，允许本机回环后新鲜全量 **1,754/1,754** 通过。
- 前端合同 **106/106**；`compileall`、`git diff --check`、recurrence JSON 与 Bug strict 均通过；strict 为
  218/218 条已归类、9 个复发簇、回顾日期 2026-11-02。
- 重启安全门读取 46 条近期 publication task、0 个活动 sync，确认无不可中断任务；标准脚本冻结 17 队列、关闭
  API、二次门禁后重启，未使用 FORCE。
- 独立终审修复后再次通过安全门并标准重启；最终 backend PID 94384 healthy，bootstrap 17/17 Celery、
  Watchdog、6/6 scheduler，`errors=[]`。关键 platform-upload（94589）、MP3（95297）、ZSXQ publish（95536）、
  notification（95393）worker 均 running、queue=0。
- 窗后真实 API：health 200；runtime-status 200/0.493s；overview 200/1.013s；topics/local 200/0.864s；tabs
  200/0.248s；sync/runs 200/0.336s；scheduler status 200/0.197s。两次 `{}` 参数 400 是巡检自测遗漏 group_id，
  随后以真实前端参数全部 200，不是产品故障。
- 没有创建真实 B站投稿以免产生外部副作用；因此代码已运行替换，但自然业务验收与下一完整窗仍待后续。

## 待人工动作、风险与未捕获边界

1. 人工停止并卸载旧 Mac 的 backend/Celery/Watchdog/scheduler；若不能证明全停，轮换 PostgreSQL/Redis 凭据、
   隔离 broker namespace，并强制 deployment identity/application_name。不得以杀 6 个连接代替隔离。
2. 人工在 DBeaver 复核并逐条执行
   `docs/sql/disable_unavailable_youtube_channels_20260920.sql`；脚本已有 lock/statement timeout、验证和回滚。
3. audio catalog 下一自然 cold miss 做同机房/非 Tailscale A/B 或 client/server socket 同秒分段；不删字段、不分页
   改语义、不加 timeout/TTL/索引掩盖。
4. `pg_stat_statements` 不可用且统计未 reset；本轮不生成或执行索引 DDL。
5. 未捕获不等于未发生：TTS 无自然任务；B站新定时合同无自然投稿；provider 内部排队、socket receive 和日志外
   actor 只能在有样本时继续验证。

## 根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险/责任动作 | 下一轮验收条件 |
|---|---|---|---|---|---|---|
| `be891b5c1af362da` / `DB-DRIVER-TAILSCALE-RESULT-TAIL` | psycopg2 经 Tailscale 的结果接收尾部 | 既有投影、上限、缓存、single-flight、Session 外文件检查 | 4 次/38.203s；既有 A/B executor 5–6ms、两路径 fetch≈9.1s | 是 | 基础设施 owner 做 socket/同机房 A/B | 自然 cold P95 收敛、物理断点修复并有后续完整窗 |
| `REMOTE-OLD-RUNTIME` | 共享 DB/Redis/broker 未物理隔离、actor 标识缺失 | 诊断/actor/fail-closed 门禁已有 | 远端会话 6；3 条 actor_missing 缺文件失败 | 是 | 人工停旧栈或轮换凭据/namespace | 完整 24h remote/old/foreign/actor_missing=0 + 当前节点自然链成功 |
| `SCHEDULED-UPLOAD-TIME-MARGIN` | 5 分钟入口校验未覆盖排队/上传 P95 | 15 分钟准入、worker 再检、submit 前 5 分钟硬门槛、可重选终态 | 单测/全量/标准重启/worker ready | 修复前本窗 3 次；修复后未暴露 | 真实投稿与后窗缺失 | 自然定时投稿成功；21173=0；窗口不足准确进入 waiting_user_reselect |
| `EXTERNAL-CALL-RESILIENCE/1059` | ZSXQ provider 1059，物理原因未知 | 既有有界 retry、幂等和 verify | 2 任务最终成功但 7 upload failure/1 retry | 是 | 补 provider/内容/账号/网络 A/B | 真实发布成功后完整窗 failure/retry/pending 全 0 |
| `YOUTUBE-21-56` | 两源永久不可枚举 | 受保护停用 SQL 待人工 | channel 49/51、video errors=2 | 是，第 29 窗 | 人工 DBeaver | 连续两自然周期 49/49、errors=0 |
| `TTS-HISTORY-POLLING-AUDIO-FANOUT` | 终态轮询、媒体预载和失效 URL | 上轮 active/visible/single-flight、preload none、存在性投影 | 本窗自然样本 0 | 未暴露 | 不能由零样本关闭 | 自然任务 + 长开页，空闲轮询/404=0，完整后窗无指纹放大 |
| `BILIBILI-DNS-TUN` | 库内空 proxy 未继承环境代理 | 显式库级 proxy | 本窗 24/24 周期无 DNS 失败 | 否 | 常规监控 | 已 CLOSED；仅同指纹复发才重开 |

## 下一轮明确验收计划

1. 第一优先复核新 worker 后的自然 B站定时投稿：准入余量、提交余量、终态、21173、原时点保留和用户可重选。
2. 第一时间确认旧 Mac `100.119.238.48` 会话、actor_missing 和日志外 writer 是否归零；没有物理隔离证据仍 OPEN。
3. audio catalog cold miss 同秒采 client/server socket、route、fetch bytes、driver fetch、executor 与 response bytes。
4. 分别统计 ZSXQ upload failure/publish retry/verify pending/最终状态；继续复核 YouTube 21/56 第 30 窗及人工 SQL。
5. 主动覆盖一个自然 TTS 任务和长开页；零样本必须标 UNOBSERVED。
6. 保持全站 HTTP/Celery/SQL 统计和三类异常分组；只有真实 SQL、reset 后统计与 ANALYZE/BUFFERS 共同支持时
   才生成带超时、验证、回滚的 DBeaver SQL 草案。
