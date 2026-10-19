# 2026-10-13 数据库性能与错误巡检

## 根因闭环结论

固定窗口为 `2026-10-12 06:00:00`–`2026-10-13 06:00:00`（Asia/Shanghai）。本轮不能写成“已解决”：

- 主机固定窗内只在约 15:11–17:49 有应用日志，覆盖约 2 小时 38 分；17:49 系统关机，10 月 13 日
  09:12 再次启动。上一自然窗口为全停机零样本，不能当健康基线。
- 应用慢 SQL 6 条/14.357 秒，P50/P95/max 为 1.485/7.114/7.114 秒。较最近可用基线 09-29 的
  47 条/68.197 秒，数量与累计下降 87.2%/78.9%，但 P95 上升 181.1%、最大上升 3.8%；覆盖时长不同，
  数量下降不能证明改善。
- 最大 SQL 指纹 `be891b5c1af362da` 是 MP3 audio catalog：应用 7.114 秒，单次请求 7.473 秒，
  Session hold 7.198 秒且非 SQL 仅 84ms。它仍是 executor 外的 driver/fetch/私网尾延迟；同形既往生产
  executor 3.656ms，本轮表/索引与代表 EXPLAIN 也未给出缺索引证据。
- 关机前 17:47:57–17:49:16 的 5 个 Watchdog 查询各约 15 秒后 `server closed`；同秒 PgBouncer
  5 次 server crash、5 个 query-wait episode、1 个 connect timeout、1 个 TLS receive error，且新连接
  route/source 回退到 `198.18.0.1`。这是主机关机拆除 Tailscale 路由造成的失败风暴，不是 PostgreSQL
  executor、锁或索引。
- 最严重业务问题仍是远端旧运行时：25 条 Publisher 全部 old-root，9 条失败（36.0%）；当前
  `100.117.177.98` 仍有 6 个空 `application_name` 数据库会话，Tailscale 映射为在线的 MacBook Pro。
  本地代码已正确 fail closed 三条缺素材重投，但不能阻止远端旧栈继续写入，状态 **OPEN/REPEATED**。
- 已实现启动依赖门禁和可审计 LaunchAgent 模板，定向测试及当前协议级探针通过；模板未人工安装，也未经历
  下一次真实重启验收，状态 **CODE_VERIFIED/WAITING_MANUAL_INSTALL/OBSERVING**。

## 与基线的量化对比

| 指标 | 09-29 最近有效基线 | 本窗 | 变化/判定 |
| --- | ---: | ---: | --- |
| 应用慢 SQL | 47 | 6 | -87.2%；但本窗仅约 2.63h 覆盖 |
| 慢 SQL 累计 | 68.197s | 14.357s | -78.9%；不可按 24h 吞吐比较 |
| P50 / P95 / max | 1.101 / 2.531 / 6.851s | 1.485 / 7.114 / 7.114s | P50 +34.9%、P95 +181.1%、max +3.8%，尾部恶化 |
| 完整 HTTP | 537 | 182 | 181 成功、1 失败；短覆盖 |
| HTTP P50/P95/max | 6.41ms / 2.112s / 7.838s | 4.65ms / 947.31ms / 7.473s | 中位与 P95 改善，最大仍高 |
| PgBouncer wait P95 | 51.61ms | 56.53ms | +9.5% |
| PgBouncer query P95 | 85.65ms | 89.98ms | +5.1% |
| Publisher old-root | 17/25 | 25/25 | 恶化到 100% |
| Publisher 失败 | 5/17 old-root | 9/25，36.0% | 失败率 +6.59pp |

上一顺序窗口 `10-11 06:00`–`10-12 06:00` 因主机/应用全停而全零；该窗口只证明观测缺失，不证明新增、改善或
未复发。自动化提供的“Last run=10-13 01:13Z”也没有对应新的 memory/报告落盘，最后持久化基线仍为 09-29，
这是自动化状态连续性的观测缺口。

## 主要问题排序

排序综合累计耗时、P95、最大耗时、失败率、受影响页面/任务和复发次数。

| 排名 | 问题 | 量化影响 | 根因与状态 |
| --- | --- | --- | --- |
| 1 | 旧运行时共享 DB/broker | Publisher 25/25 old-root、9 失败、失败壁钟 2,623.303s、P95/max 428.028/465.194s；另有 MP3 4/4、audio clean 2/2 old-root | 远端 MacBook Pro 在线且仍持共享凭据；OPEN/REPEATED/WAITING_DISTRIBUTED_ISOLATION |
| 2 | 后台工作流长耗时 | ZSXQ publish 完成 9 条累计 36,082.296s、P95 18,890.839s；PDF clean 7 条累计 20,327.397s；MP3 4 条累计 5,607.985s | 多为外部 I/O/文件/跨部署执行；没有前台端点可代替验收，OPEN |
| 3 | MP3 audio catalog 尾延迟 | SQL/请求 7.114/7.473s，hold 7.198s，目录一次最多 2,500 行 | executor 外 driver/fetch/网络；既有有界投影与缓存，OPEN/REPEATED |
| 4 | 关机阶段 DB 失败风暴 | 5 条约 15s Watchdog SQL、4 个 SQL 错误指纹、5 个 PgBouncer crash/wait episode | shutdown 先拆 Tailscale 路由；启动/恢复编排缺口，代码门禁已落地但未安装验收 |
| 5 | 页面/API 无慢 SQL 长尾 | rankings/all 8 次累计 6.420s；charging-dashboard 5 次累计 4.868s；data/table 5 次累计 4.208s | 多次低于 1s DB 往返、序列化或其他应用工作；OPEN/OBSERVING |
| 6 | 通知与平台失败 | durable 通知 sent 80/pending 13/dead_letter 2/retry_wait 1；Publisher 另有 3 发布时间、2 未登录、1 缺视频失败 | 外部平台状态/配置，不是索引；需按各自复发键继续验收 |

## SQL、页面/API 与后台任务关联

### SQL 与 Session/PgBouncer

| 指纹/来源 | 次数 | 累计 / max | 关联与判定 |
| --- | ---: | ---: | --- |
| `be891b5c1af362da` `mp3_video_workflow.py:495` | 1 | 7.114 / 7.114s | audio-assets request 7.473s、hold 7.198s；SQL 占主导，非外部 I/O 跨 Session |
| `674016d39deab3f5` Insights aggregate | 1 | 1.878 / 1.878s | overview 冷请求；canonical-topic EXPLAIN 28.607ms、8,229 扫描/6,707 输出，无秒级 executor |
| `9583c...` notification outbox | 1 | 1.838s | workflow notification 扫描；后台通知链 |
| `e3a01...` notification delivery | 1 | 1.485s | workflow notification 状态读取/写回 |
| `050372...` provider state | 1 | 1.002s | workflow notification provider 状态 |
| `c8bc5...` Publisher repair | 1 | 1.039s | Watchdog repair；关机前正常完成样本 |

6 个长 hold 中 5 个 scheduler hold 为 15.10–15.33 秒，均与关机阶段失败 SQL 匹配；web audio hold 7.198 秒
与 7.114 秒 SQL 匹配。所有 hold 的非 SQL 合计仅 172ms，没有外部 I/O 与 Session 交叉持有证据。
statement timeout、lock timeout、deadlock 和 idle-in-transaction 均 0；waiting lock 为 0。

PgBouncer 原始 query-wait 日志 10 行去重为 5 个 episode，server crash 5、connect timeout 1、TLS receive 1；
正常连接 source 33 次为 `100.96.3.12`，故障时 5 次为代理 TUN `198.18.0.1`。系统 shutdown 记录为
10 月 12 日 17:49，与故障尾部吻合。

### 页面/API

固定窗 182 个完整请求、58 个端点，181 成功/1 失败，累计 36.727 秒，P50/P95/max
4.65ms/947.31ms/7.473s。唯一失败需结合原日志处理，不能从 0.55% 推断全站健康，因为覆盖仅约 2.63 小时。

- 低频高延迟：`audio-assets` 1 次 7.473s，匹配 7.114s SQL。
- 慢但无 `>=1s` SQL：`rankings/all` 8 次累计 6.420s、P95 1.088s；B站 charging-dashboard 5 次累计
  4.868s、P95 1.235s；`data/table` 5 次累计 4.208s、P95 1.275s。应继续拆低于阈值的 SQL 数、bytes、
  序列化与缓存，不得误判为“无 DB 问题”。
- 后台长但无前台端点：通知 186 个成功 task 累计 575.738s、P95 3.826s、max 6.117s；9 条 Publisher
  失败和内容工作流小时级壁钟来自 durable 数据，不在 HTTP 汇总中。

重点链路在最终 PID 上的正确请求验收：health 200/42ms、overview 200/37ms、topics/local POST
200/1.020s、topics/local/tabs POST 200/416ms、system-sync-scheduler/status GET 200/426ms；scheduler alive。
09:20 后 10 分钟分析为慢 SQL 0、连接错误 0、idle-in-transaction 0。早先对三个不存在的 GET 路由产生
3 个 404，是本次巡检探针自污染，不能计作产品自然失败。

### 后台任务与跨部署根因

Celery 固定窗 186 成功、1 失败；成功全部是 workflow notification。durable 数据显示：

- Publisher 25 条：16 完成、9 失败；9 条失败分别为缺素材门禁 3、发布时间非法 3、账号未登录 2、缺视频 1。
- `zsxq_clean_output_publish` 9 条完成，累计 36,082.296s，P95 18,890.839s，最大 29,845.441s；
  2 条可确认 old-root，7 条 unclassified，不能把未分类当 current-root。
- `content_pdf_clean` 7 条完成，累计 20,327.397s，P95 5,685.420s；均因没有根路径证据而 unclassified。
- `mp3_video_workflow` 4/4 old-root 完成，累计 5,607.985s，P95 1,452.684s。
- `content_audio_clean` 2/2 old-root 完成，累计 205.466s。

当前数据库客户端同时存在 MacBook Pro `100.117.177.98` 6 会话与本机 `100.96.3.12` 5 会话；远端会话
`application_name` 为空。25 条 Publisher 全部带 `dig_ytb_data_claude_v1`，本窗 3 条缺素材被 09-29 防线正确
阻断，说明代码防放大有效；但远端写入本身明显复发，不能标 CLOSED 或 RUNTIME_ACCEPTED。

## 已落地修复、测试与运行态验证

本轮新增：

1. `scripts/dev/check-boot-runtime-dependencies.py`：校验 Tailscale identity、self online、私网地址、DB/Redis
   route=`utun*` 且不含 `198.18.0.1`，再执行 PostgreSQL `SELECT 1` 与 Redis `PING`；失败只输出异常类型并 exit 3。
2. `scripts/dev/start-dev-stack-after-boot.sh`：有界等待依赖，健康实例幂等退出；否则启动 PgBouncer、执行
   `check-restart-safety.py`，最后以前台 backend 进程承载既有完整 bootstrap。
3. `docs/operations/com.dig-ytb.dev-stack.plist.example` 与 README：仅提供人工审阅/安装模板，未无人值守修改
   `~/Library/LaunchAgents`。
4. `tests/unit/test_boot_runtime_guard.py`：覆盖 Tailscale identity、拒绝 proxy TUN、必须 utun、PostgreSQL
   认证查询和 Redis PING。协议探针测试先 2 fail，修复后 5/5 pass；实际门禁当前通过。

重启前安全门检查 46 条近期任务、0 活动 ZSXQ run；标准脚本未用 FORCE。最终运行边界为 backend PID 10371，
2026-10-13 09:25:55 启动；bootstrap 于 09:26:42 完成，17/17 Celery、Watchdog、6/6 scheduler 均 ok、
`errors=[]`。Tailscale `Running`、本机 `100.96.3.12`、DB route=`utun5`。定向门禁 5/5 通过；完整
unit/integration 为 **1817 passed、7 skipped、363 subtests passed**，用时 108.94 秒。Bug strict 检查、
`compileall`、registry JSON、shell/plist 语法、`git diff --check` 全部通过。启动门禁代码尚未由 LaunchAgent
承接真实 reboot，故只到 CODE_VERIFIED/OBSERVING。

## 表与索引复核

| 表 | heap / TOAST / index / total | live/dead | seq/index scan | 判定 |
| --- | --- | ---: | ---: | --- |
| `publication_tasks` | 18/115/14/146MB | 14,136/472 | 7,154/3,979,666 | index 99.82%；TOAST 与低 HOT 1.18% 反映大 JSON 写放大，继续结构化投影 |
| `zsxq_topics` | 13/79/36/129MB | 12,637/1,584 | 4,265/892,596 | index 99.52%；Insights 代表 executor 28.607ms |
| `zsxq_topic_comments` | 91/4.2/23/118MB | 41,333/5,644 | 526/359,259 | index 99.85%；HOT 70.46% |
| `zsxq_attachments` | 35/0.1/42/78MB | 33,906/37 | 645/1,135,928 | index 99.94% |
| `zsxq_sync_runs` | 1.2/35/0.9/37MB | 1,062/46 | 308/337,957 | index 99.91%；代表查询命中 partial index，executor 32.897ms |
| `external_documents` | 0.9/4.8/1.2/6.9MB | 1,817/146 | 1,077/84,997 | index 98.75% |

精确定义重复索引为 0。低扫描候选仍包括 attachments PDF group/tab 5,088KB/1、topics group/type/create
3,720KB/1、external TTS 232KB/0、summary 184KB/0、publisher MP3 match 184KB/0；stats 自 08-08 未 reset，
这些索引可能是低频恢复/写入契约，缺 owner、维护成本与完整自然查询证据，不能直接 DROP。

代表 EXPLAIN：Watchdog PK 0.045ms；channel chart 2.730ms；scheduler config 0.023ms；Publisher 列表
0.043ms；content pipeline 列表 0.072ms；publication task 宽/窄列表 0.025/0.027ms；ZSXQ sync 32.897ms；
Insights canonical selection 28.607ms。无 waiting lock，各类 timeout role setting 已为 statement 60s、lock 5s、
idle-in-transaction 60s。本轮无 CREATE/DROP INDEX、ALTER、回填或生产配置 SQL，也无可合理提交的人工 DDL。

## 未捕获边界与下一轮明确验收计划

- 固定窗缺约 21 小时 22 分应用覆盖；未记录不等于未发生。`logs/scheduler.log` 仍不完整，使用独立 scheduler
  日志，但 YouTube 00:03 周期因停机未运行，id 21/56 本窗没有自然复核样本，继续 OPEN。
- 慢 SQL 阈值无法捕获多条亚秒 SQL、N+1、acquire/fetch/serialize 分段；页面慢而无匹配慢 SQL 只能列为异常，
  不能判无问题。
- 当前另一 MacBook Pro 在线且有 6 会话；必须人工停止旧栈或轮换 DB/Redis 凭据并隔离 broker namespace。
- LaunchAgent 模板必须人工审阅安装；下一次真实 reboot 要证明 Tailscale 未 ready 时应用不启动、不出现 proxy TUN
  重连风暴，ready 后自动通过协议探针、安全门并单实例启动完整拓扑。
- 从 PID 10371/09:25:55 切点观察完整 24 小时：proxy source、server crash、query/connect/statement/lock timeout、
  idle-in-transaction 均为 0；关键五 API 各至少 5 个自然样本，并补 SQL 次数、返回 bytes、cache hit 与分段。
- old-root 必须为 0，至少各一条自然 PDF、ZSXQ publish、MP3→Publisher 由当前 root 完整成功；三条
  `required_material_unavailable` 只能精确阻断真正不可达素材，不能再出现跨根重投、缺视频、旧平台凭据失败。
- YouTube id 21/56 人工停用后要求 channel 49/49、video errors=0 两个自然周期；无自然周期时保持 OPEN。

## 根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险/责任动作 | 下一轮验收条件 |
| --- | --- | --- | --- | --- | --- | --- |
| `old-root + 100.117.177.98 + empty application_name` | 旧 MacBook Pro 与当前部署共享 DB/broker/平台凭据 | 既有素材可达性 fail-closed；Bug/registry 更新 | 3 条缺素材正确阻断；远端当前 6 会话 | 是，明显恶化 | **OPEN/WAITING_MANUAL_ISOLATION**；人工停旧栈或轮换凭据/namespace | 完整窗远端会话和 old-root=0，三类当前根自然成功 |
| `shutdown → 198.18.0.1 → server closed` | 主机关机时先失去 Tailscale 私网路由，应用/PgBouncer 后停 | 协议级 boot guard、启动编排、plist 模板 | 实际 SELECT 1/PING 通过；PID10371 全拓扑 ready | 是；新防线尚未经历 reboot | **CODE_VERIFIED/WAITING_MANUAL_INSTALL** | 人工安装后真实重启自动恢复，完整后窗六类错误 0 |
| `be891b5c1af362da` | executor 外 driver/fetch/私网尾延迟；有界 2,500 行目录 | 既有投影、Session 释放、TTL cache 保持 | request 7.473s/SQL 7.114s/hold 7.198s；无锁/长事务 | 是 | **OPEN/OBSERVING**；补 bytes、pool/fetch、冷暖样本 | 至少 5 次，暖缓存 SQL=0，冷请求尾延迟回落 |
| 页面慢无慢 SQL | 多个亚秒 DB 往返、序列化/缓存边界未拆 | 现有 request id/SQL origin telemetry | rankings/charging/data-table P95 1.09–1.27s | 是 | **OPEN/OBSERVING** | 分段与 SQL 次数预算，P95 连续完整窗达标 |
| Publisher 平台失败 | 旧运行时使用过期计划/凭据与本地素材 | fail-closed 只覆盖缺素材，不掩盖其他失败 | 9/25 failed：3+3+2+1 分类完整 | 是 | **OPEN**；远端隔离并修平台配置 | 当前根自然 Publisher 成功，四类失败 0 |
| YouTube `21/56` | 已知失效频道仍启用 | 停用脚本待人工 | 本窗因停机无自然周期 | 未捕获 | **OPEN/WAITING_MANUAL_CONFIG** | 人工停用后两个周期 49/49、errors=0 |
