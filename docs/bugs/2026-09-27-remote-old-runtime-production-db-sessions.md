# 远端旧运行时仍持生产数据库会话并创建 old-root 内容任务

## 基本信息

- 日期：2026-09-27
- 状态：OPEN / REPEATED / ROOT_HOST_LOCATED / OBSERVING / WAITING_DISTRIBUTED_ISOLATION
- 严重级别：P0
- 影响模块：内容清洗、ZSXQ 发布、Publisher、PostgreSQL、Redis/Celery 分布式运行拓扑
- 根因分类：旧部署与当前部署共享生产数据库及 broker 凭据，且任务缺少不可伪造的 deployment identity
- 复发键：`RUNTIME-TOPOLOGY-RELOAD` / `DB-SESSION-LIFECYCLE`
- 是否复发：是；延续 2026-09-11 起的 old-root Publisher 问题，本轮首次定位到具体远端主机
- 关联记录：`2026-09-11-publisher-queue-repair-visible-message-starvation.md`
- 防线层级：durable_task_evidence / pg_stat_activity / tailscale_identity / manual_isolation_pending

## 修复前证据与影响

固定窗口 `2026-09-26 06:00:00`–`2026-09-27 06:00:00` 内没有 Publisher 自然任务，但生产库新增
5 条 `content_pdf_clean` 与 5 条 `zsxq_clean_output_publish`，全部完成且 10/10 的 `params/result/path`
携带旧 checkout `dig_ytb_data_claude_v1`，当前 checkout 命中为 0。前者累计壁钟 2552.684 秒、
P95/max 631.670/659.734 秒，后者累计 1261.279 秒、P95/max 414.690/452.119 秒。

当前本机 Celery 日志只识别到 2 条 PDF clean 成功、没有相应 5 条发布任务的本地 worker 完成日志；
durable 记录与本机日志数量不一致，说明至少部分执行链来自日志集合之外的运行时。

## 根因定位

06:03–06:08 的只读 `pg_stat_activity` 连续看到另一私网源 `100.94.17.45` 保持 6 个
`cappa_rw` 生产库会话，`application_name` 为空；当前 Mac mini 地址为 `100.96.3.12`。Tailscale
本机状态把 `100.94.17.45` 映射为仍在线的 `v9 的 MacBook Pro`。旧任务路径是该类 macOS 绝对路径，
与此前只扫描 Mac mini 本机进程却持续出现 old-root durable 写入的现象吻合。

因此根因从“未知共享凭据远端 actor”推进为“已定位候选主机与活动数据库连接”。但当前证据不能从
PostgreSQL 会话反推出具体 PID/checkout；也没有在未经授权的远端主机上停止进程、断会话或改凭据。
直接终止 6 个会话只是短暂止血，旧运行时可以重连，不构成根治。

## 已落地防线

- 新增只读 `scripts/diagnostics/daily_db_runtime_snapshot.py`，以固定 UTC-naive 窗口统一输出 durable
  任务、Publisher 复发指标、通知、ZSXQ run 与当前数据库客户端来源。
- JSONB 子树交给 Python 汇总，避免诊断 SQL 因 `json_typeof(jsonb)` 类型不匹配失败；TDD 回归覆盖
  `queue_repair_history` 为 list/NULL 的生产形态。
- 本轮继续保留旧根路径与数据库客户端来源双证据；不把“本窗 Publisher=0”解释为旧 actor 已停止。

## 为什么仍未闭环

物理停止远端旧部署、轮换生产 PostgreSQL/Redis 凭据或隔离 broker namespace 都是跨主机生产动作，
本轮没有获得远端进程清单与停机授权。状态保持 OPEN；本地代码、索引、超时或重试都不能让远端旧
运行时失去写权限。

## 下一轮验收条件

1. 在 `100.94.17.45` 对应 MacBook Pro 上盘点并停止所有持当前生产 DB/Redis 凭据的 backend、Celery、
   Watchdog、scheduler 与手工脚本；不能证明全停时轮换凭据并隔离 broker namespace。
2. 隔离后 `pg_stat_activity` 不再出现该源的生产连接；所有受管连接必须携 deployment/host/process
   application identity，避免再次以空 `application_name` 归因。
3. 下一条自然内容清洗、ZSXQ 发布与 MP3→Publisher 任务只能携当前 checkout/deployment identity，
   无 old-root、旧 repair reason、ID replacement、素材不可达或 child failure。
4. 再观察一个完整 24 小时窗口；未捕获自然任务时继续 OBSERVING，不得 CLOSED。

## 2026-09-29 固定窗复核：同一主机重入网换 IP，并触发跨部署素材重投失败

固定窗内五类 durable task 共 39 条，其中 27 条携旧 checkout：音频清洗 3/3、PDF 1/2、MP3 3/5、
Publisher 17/25、ZSXQ 发布 3/4。旧根任务壁钟合计 14,463.677 秒（含父子嵌套）；旧根 Publisher
17 条中 5 条失败，失败率 29.41%。失败行的 `params/result` 都保存
`/Users/v9/Documents/workspace/v9/code/dig_ytb_data_claude_v1/...` 本地绝对素材路径，并带
`queue_repair.reason=pending_queued_not_visible_in_celery`；当前 Watchdog 重投后分别报
`max() arg is an empty sequence` 与 `No valid audio clips`。这是用户可见失败，不是单纯观测噪声。

Tailscale 证据把前一地址 `100.94.17.45`（最后在线 08:39）与新 node 地址 `100.119.248.103`
（08:47 创建、最后在线 22:37）都映射到 `v9的MacBook Pro`。旧根任务出现在 16:39–17:36，证明同一
物理主机重新入网后换地址，上一轮按固定 IP 追踪会遗漏。稳定复发身份必须升级为 host/node/deployment，
而不是地址字符串。

本轮对 Publisher pending/processing repair 增加通用必需素材门禁：只有 worker/queue/unacked 都确认原消息
不存在后，且 DB Session 已释放，才校验 audio/image/video 本地文件；不可达则写
`required_material_unavailable` 并 fail closed，不再投递到当前 render。upload-only 保持原语义与轻投影。
定向 88 项、完整 1,722 项和全部静态门禁通过；最终双重安全门后标准重启到 backend PID 71629，17/17
Celery、Watchdog、6/6 scheduler 均 ok、`errors=[]`，health 与四个只读 ZSXQ E2E 均 200。

状态为 **OPEN / REPEATED / RUNTIME_REPLACED / OBSERVING**。代码已阻止已知失败放大，但无法撤销旧部署
的生产写权限；且重启后尚无同类自然任务。下一轮必须以完整后窗证明 old-root=0、
`required_material_unavailable` 只精确阻断不可达素材且旧两类 render 错误为 0，并完成远端旧栈停机或凭据/
broker namespace 物理隔离，才可推进关闭。

## 2026-09-28 固定窗复核：本窗仍有旧根写入，主机当前离线不等于已隔离

固定窗口 `2026-09-27 06:00:00`–`2026-09-28 06:00:00` 内，生产库新增 2 条
`content_pdf_clean` 和 2 条 `zsxq_clean_output_publish`。4/4 均为 completed，但所有任务的
`params/result/path` 都只命中 `dig_ytb_data_claude_v1`，当前 checkout 命中 0；PDF 清洗累计
1275.736 秒，ZSXQ 发布累计 433.519 秒。本地 content pipeline 与 ZSXQ publish 日志文件在这些任务期间
没有更新，继续证明执行器位于当前日志集合之外。数量较上窗 10 条下降不能视为根因改善。

06:03–06:07 两次只读 `pg_stat_activity` 已看不到 `100.94.17.45` 的生产会话；Tailscale 状态显示该
MacBook Pro 当前 `Online=false`，最后在线时间为 `2026-09-27 22:20:00 +0800`。然而本窗四条旧根任务
在 16:27–16:51 已经完成，且远端主机随后仍在线数小时。因此“当前离线/当前连接为 0”只是一段正向观察，
不能反证固定窗复发，也不能证明旧进程已卸载、凭据已轮换或 broker namespace 已隔离。

为防止日快照只看 Publisher 而漏掉跨工作流复发，`daily_db_runtime_snapshot.py` 已用 TDD 增加所有
`publication_tasks.feature` 的 old/current/unclassified root 汇总和旧根逐行证据；生产只读回放正确列出
4 条旧根任务。根因状态继续 OPEN/REPEATED，进入 HOST_OFFLINE_OBSERVED/WAITING_FULL_WINDOW；下一轮必须
确认整窗远端连接为 0，并取得自然 PDF、ZSXQ 与 MP3→Publisher 仅由当前 root 执行的证据。

## 2026-10-13 固定窗复核：旧运行时全面复发且远端主机当前在线

窗口内 25 条 Publisher 全部为 old-root，其中 9 条失败，失败率 36.0%，累计失败壁钟 2,623.303 秒、
P95 428.028 秒、最大 465.194 秒。失败拆分为：3 条 `required_material_unavailable` 被现有 fail-closed
门禁正确阻断、3 条发布时间非法、2 条 B站账号未登录、1 条缺少已生成视频。另有 4/4 MP3 workflow、
2/2 audio clean 和 2/9 ZSXQ publish 为 old-root；仅 PDF clean 因路径字段未分类，不能据此判为 current-root。
这不是偶发旧行回放，而是固定窗内持续由旧部署创建和执行的真实任务。

当前 `pg_stat_activity` 仍看到 `100.117.177.98` 的 6 个空 `application_name` idle 会话；Tailscale 将该地址
映射到在线的“v9的MacBook Pro”。当前 Mac mini `100.96.3.12` 另有 5 个会话。旧主机从此前地址再次换 IP，
进一步证明固定 IP 不是 deployment identity。当前本地代码能够阻止一部分跨根重投扩大，但不能阻止远端旧代码
继续写 DB、投 broker 或使用过期平台凭据。

状态升级为 **OPEN / REPEATED / WAITING_DISTRIBUTED_ISOLATION**。必须人工在该 MacBook Pro 停止全部旧
backend/Celery/Watchdog/scheduler/手工脚本，或轮换 PostgreSQL/Redis 凭据并隔离 broker namespace；随后要求
远端会话归零、old-root 自然任务为 0、三类当前根自然任务成功且 Publisher 失败指纹不复发，连续完整 24 小时
后才可推进关闭。

## 2026-10-14 固定窗复核：old-root 与远端会话归零，但未达到关闭窗口

固定窗五类 durable task 未发现 old-root：audio clean 3/3 current、MP3 4/4 current、Publisher 17/17
current；PDF clean 5 条和 ZSXQ publish 5/8 因缺路径证据仍为 unclassified，不能把未分类冒充 current-root。
Publisher 16 completed/1 failed，唯一失败为当前根找不到已生成视频文件，不是旧根素材路径或旧平台凭据。

当前 `pg_stat_activity` 只见本机 PgBouncer source `100.96.3.12` 与本机 gunicorn 会话，上一窗
`100.117.177.98` 的 6 个空 application_name 会话已归零。该结果相较 10-13 明显改善，但 backend 日志只覆盖
约 20 小时 34 分，且没有证据证明旧主机进程已卸载、生产凭据已轮换或 broker namespace 已隔离。

状态由 REMOTE_HOST_ONLINE 降为 **REMOTE_SESSIONS_ZERO_OBSERVED**，整体仍是
**OPEN / REPEATED / OBSERVING / WAITING_DISTRIBUTED_ISOLATION**。下一轮必须覆盖完整 24 小时、远端会话和
old-root 继续为 0，并取得 PDF、ZSXQ、MP3→Publisher 三类当前根自然成功；物理隔离未完成前不得 CLOSED。

## 2026-10-15 固定窗复核：同一旧主机会话与 old-root 自然任务再次复发

上一窗的远端会话归零没有延续。当前 `pg_stat_activity` 再次看到 `100.117.177.98` 的 6 个空
`application_name` 会话，backend start 每约 2 分钟出现一批，均为 idle/ClientRead 或 `DISCARD ALL`；Tailscale
将该地址稳定映射到在线的“v9的MacBook Pro”。这说明旧连接池仍在运行，直接终止连接只会重连。

固定窗 2/2 `audio_transcription` durable task 都携旧 checkout，壁钟 277.283/915.122 秒；当前所有本地日志均
找不到两个 task id，进一步证明执行器在本日志集合之外。本窗 PDF、MP3、Publisher、ZSXQ 任务虽均为 current，
也不能抵消 audio transcription 的自然复发。

状态回升为 **OPEN / REPEATED / WAITING_DISTRIBUTED_ISOLATION**。责任动作仍是人工在旧 MacBook Pro 停止
backend/Celery/Watchdog/scheduler/手工脚本；不能证明全停时轮换 PostgreSQL/Redis 凭据并隔离 broker namespace。
验收要求完整 24 小时远端会话=0、所有 feature old-root=0，并有当前根自然任务成功；未完成物理隔离前不得关闭。

## 2026-10-16 固定窗复核：完整窗 old-root 为 0，但物理隔离仍未证明

本窗 audio clean 2/2、PDF clean 8/8、MP3 workflow 4/4、Publisher 17/17、ZSXQ publish 10/10 均归类为
current-root，old-root 与 unclassified 均为 0；当前 `pg_stat_activity` 也只看到本机 PgBouncer source
`100.96.3.12` 和 localhost gunicorn，不再看到 `100.117.177.98`。这是相较上一窗 2/2 audio transcription
old-root 与 6 个远端会话的明确改善。

但尚无旧 Mac 全部服务已停、凭据轮换或 broker namespace 隔离的物理证据，一个完整绿窗也不能撤销此前
反复重连事实。状态保持 **OPEN / REPEATED / OBSERVING / WAITING_DISTRIBUTED_ISOLATION**。下一轮继续要求
全 feature old-root=0、远端会话=0、自然任务仅由当前根完成；物理写入围栏未完成前不得关闭。

## 2026-10-17 固定窗复核：old-root 为 0，但日志外 actor 假关闭当前根任务

纠正巡检快照时区后，本窗 audio/PDF/MP3/Publisher/ZSXQ 共 39 条 durable task 全为 current-root，当前
`pg_stat_activity` 也未看到旧主机。然而 Publisher 19 条中 3 条被写成
`queue_repair_blocked/required_material_unavailable`，失败率 15.8%，影响 2/4 MP3 父任务。三条关闭所列的
四个当前根 image/audio 文件实际都存在，mtime 均早于关闭时间；本地日志能看到消息仍在队列及 worker 接收终态，
却没有当前代码写入该关闭原因的日志。

这证明共享 DB/Redis 的日志外 actor 在不同文件系统视角执行了素材门禁：`old-root=0` 和当前远端 DB 会话=0
仍可能假绿。状态保持 **OPEN / REPEATED / WAITING_DISTRIBUTED_ISOLATION**。责任动作不变：人工停旧 Mac 的
backend/Celery/Watchdog/scheduler/脚本；无法证明全停时轮换 PostgreSQL/Redis 凭据并隔离 broker namespace。
下一轮必须为每次终态写入记录 deployment/host/PID/code identity，并要求完整窗假关闭=0、自然 Publisher 成功。

## 2026-10-18 固定窗复核：29/29 old-root，shared broker 再次自然复发

固定窗 29 条目标 durable task 全部携 `dig_ytb_data_claude_v1` 旧 checkout：audio clean 1/1、PDF clean
4/4、MP3 workflow 3/3、Publisher 16/16、ZSXQ publish 5/5。Publisher 12 completed/4 failed，失败率
25%，失败壁钟累计 897.952 秒；四条均为 `queue_repair_blocked/required_material_unavailable`。当前
content worker 还消费过一条旧根 PDF task 并因本机无该路径返回 False，进一步证明共享 DB/Redis/Celery
namespace 会把文件本地性任务送到不兼容部署。

Tailscale 显示旧 MacBook Pro 在这些任务运行时段仍在线，22:25 后才离线；当前 Mac mini 的进程快照只含当前
checkout，因此“当前旧机离线/当前本机无旧进程”不能抵消固定窗复发。已为 Publisher pending/processing 的
素材不可达终态持久化 deployment root、hostname、PID、code identity，并在日志写同一 actor；TDD 定向、
54 项相关测试与 1,737 项完整 unit 通过。双重安全门后标准重启到 backend PID 14483，17/17 worker、
Watchdog、6/6 scheduler 均 ok、`errors=[]`，重点只读 API 全部 200。

状态保持 **OPEN / REPEATED / RUNTIME_REPLACED / OBSERVING / WAITING_DISTRIBUTED_ISOLATION**。新审计只解决
下次归因，不会隔离旧 actor；必须人工停旧 Mac 全栈，或轮换 PostgreSQL/Redis 凭据并隔离 broker namespace。
下一轮要求完整 24 小时所有 feature old-root=0、任何终态 writer 可归因、自然当前根任务成功且假关闭=0。

## 2026-10-19 固定窗复核：Web 提交再次由旧 Mac 全链执行

固定窗新增 1 个 `content_pdf_clean` 和 1 个 `zsxq_clean_output_publish`，2/2 均为 completed，但
`web_submission_root`、clean output 与 publish source/output 全部指向 `dig_ytb_data_claude_v1` 旧 checkout；
PDF 清洗 36/36、发布 36/36，累计壁钟 1,818.552 秒。当前节点 content/publish worker 日志在任务期间没有更新，
只由当前 notification worker 消费了共享 outbox，证明上传入口、清洗与发布执行器均在日志外旧部署。

旧 MacBook Pro 在任务完成后仍在线，当前离线只是一张快照，未证明服务卸载、凭据轮换或 broker namespace
隔离。上轮新增的 Publisher 终态 actor 审计本窗没有 Publisher 自然样本，不能验收。状态保持
**OPEN / REPEATED / WAITING_DISTRIBUTED_ISOLATION**；责任动作仍是人工停旧 Mac 全栈，无法证明全停时轮换
PostgreSQL/Redis 凭据并隔离 Celery namespace。下一轮要求完整 24 小时所有 feature old-root=0、远端 actor=0、
当前根 PDF→ZSXQ 与 MP3→Publisher 自然成功，且终态 writer 可归因。

## 2026-10-20 固定窗复核：current-root 字符串假绿，8 条终态确认日志外 writer

固定窗 32 条目标 durable task 的路径字符串均为 current-root，表面上 old-root=0；但 Publisher 24 条仅
16 completed，8 条被关闭为 `queue_repair_blocked/required_material_unavailable`，失败率 33.3%，失败壁钟
累计 1,707.053 秒。8/8 终态缺少 10-18 已运行替换的 deployment/host/PID/code actor 字段，当前业务日志也没有
写入动作；更关键的是，8 条列出的全部 image/audio 路径在当前节点实际存在且 mtime 早于关闭时间。生产只读
快照新增门禁后稳定输出 `foreign_actor_evidence=8`。

旧 MacBook Pro 的 Tailscale 最后在线时间晚于这些终态写入，当前离线仍不能证明旧服务卸载。由此把复发键从
“路径是否 old-root”提升为“终态 actor 是否属于当前部署 + 素材在当前节点是否真实存在”；current-root 字符串
不再作为 actor 归属证据。状态继续 **OPEN / REPEATED / WAITING_DISTRIBUTED_ISOLATION**。责任动作仍是人工
停用旧 backend/Celery/Watchdog/scheduler/脚本；无法证明全停时轮换 PostgreSQL/Redis 凭据并隔离 Celery
namespace。下一轮必须完整 24 小时 `foreign_actor_evidence=0`、old-root=0、远端 actor=0，并有当前根
PDF→ZSXQ、MP3→Publisher 自然成功；否则不得关闭。

## 2026-10-21 固定窗复核：补传缺视频终态暴露第二条 actor 审计盲区

Publisher 36 条仅 16 completed、20 failed。除 10 条 `required_material_unavailable` 外，另有 7 条
upload-only 被写为“找不到已生成的视频文件”；17/17 都缺 actor，引用文件在当前节点实际存在，且 7 个视频
mtime 早于失败时刻。两类 foreign actor 合计 17/36（47.2%），累计壁钟 3,830.170 秒，较上一窗 8/24
（33.3%）明确恶化。旧 `old-root=0` 与当前远端会话 0 再次不能作为隔离证据。

已按 TDD 为 upload-only 缺视频终态增加 deployment/hostname/PID/code identity、原路径、关闭时间的 durable
审计和同 actor 错误日志；日快照增加关联表与 mtime-before-close 门禁，生产同窗重放正确把旧口径 10 修正为
17。1,740 项 unit 通过，双重安全门后标准重启，backend、17/17 worker、Watchdog、6/6 scheduler 均正常。
该防线已 RUNTIME_REPLACED，但重启后无自然同类终态；共享旧栈未物理隔离，状态继续 **OPEN / REPEATED /
OBSERVING / WAITING_DISTRIBUTED_ISOLATION**。

## 2026-10-22 固定窗复核：旧 Mac watchdog 获得直接数据库连接证据

本窗 Publisher 9 条为 8 completed/1 failed；失败 task
`054f52a6-ef58-4d00-9194-9f504c276c1e` 被写成
`queue_repair_blocked/required_material_unavailable`，但所列当前根 image/audio 在当前节点均存在。
本地 mp3 worker 只记录消息可见与 active，render worker 收到时任务已是终态，本地没有失败写入；快照按
actor 缺失和 mtime-before-close 计为 `foreign_actor_evidence=1`。

更直接的是 15:30:40 生产守卫日志从 PostgreSQL 捕获
`application_name=dig_ytb_scheduler_celery_watchdog_loop_py_92458`、
`client_addr=100.117.177.98/32`；Tailscale 将该地址映射到旧 MacBook Pro，且其最后在线时间晚于误关任务。
这把“日志外 actor”从间接推断推进为固定窗内真实生产库连接证据。相较上窗 17/36 已明显改善，但 1/9 仍属
自然复发。状态保持 **OPEN / REPEATED / OBSERVING / WAITING_DISTRIBUTED_ISOLATION**；必须人工停旧 Mac
全栈，或轮换 PostgreSQL/Redis 凭据并隔离 broker namespace。下一完整窗要求 remote/foreign/old-root 全 0，
并有当前根 PDF→ZSXQ、MP3→Publisher 自然成功。

## 2026-10-23 固定窗复核：日志外终态写入大幅反弹

固定窗 Publisher 共 30 条：15 completed、14 failed、1 canceled，失败率 46.7%。其中 9 条
`required_material_unavailable` 和 3 条 upload-only“缺视频”终态均缺少已运行防线要求的 actor identity，
但其引用的当前根 image/audio/video 在本机均存在且 mtime 不晚于关闭时间；生产快照因此计
`foreign_actor_evidence=12/30`。本地 MP3/Watchdog 只看见排队或队列可见，本地没有对应失败写入，证明
终态来自日志外/旧代码 writer。相较上一窗 1/9 明显恶化。

巡检时 Tailscale 显示旧 MacBook Pro 当前 offline、last seen 约 8 小时，时间恰晚于 16:49–17:36 的失败段；
当前 `pg_stat_activity` 也只有本机与当前 Mac mini。它们都只是窗后快照，不能抵消同窗 12 条缺 actor 的 durable
终态证据。状态继续 **OPEN / REPEATED / WAITING_DISTRIBUTED_ISOLATION**。责任动作仍是人工停用旧 Mac 的
backend/Celery/Watchdog/scheduler/脚本；无法证明全停时轮换 PostgreSQL/Redis 凭据并隔离 broker namespace。
下一轮必须完整 24 小时 `foreign_actor_evidence=0`、old-root=0、远端 actor=0，并有当前根 PDF→ZSXQ、
MP3→Publisher 自然成功。

## 2026-10-24 固定窗复核：数量改善但日志外 writer 仍复发

固定窗 Publisher 20 条：16 completed、4 failed，失败率从 46.7% 降至 20.0%。其中两条
`required_material_unavailable` 终态仍缺 actor，列出的当前根素材全部存在；本地 MP3 日志只记录排队/
message-visible，render 收到时任务已经终态，本地没有失败写入。快照因此仍计
`foreign_actor_evidence=2/20`，虽较上一窗 12/30 改善，但同一复发键未归零。

固定窗目标任务各 feature 均为 current-root，巡检时只见本机与 `100.96.3.12` 当前部署连接，没有旧私网地址；
这些仍是路径和窗后快照，不能推翻 durable actor 缺失与本地无写入日志。状态继续
**OPEN / REPEATED / OBSERVING / WAITING_DISTRIBUTED_ISOLATION**。必须人工停旧 Mac 全栈，或轮换 PostgreSQL/
Redis 凭据并隔离 broker namespace；下一完整窗仍要求 old-root=0、foreign actor=0、远端 actor=0，并有
当前根自然任务成功。

## 2026-10-25 固定窗复核：旧根 MP3 任务再次由日志外运行体完成

固定窗唯一目标 durable task 为 `mp3_video_workflow`
`46fa834c-a887-4cdc-be3a-6a394f972388`，状态 completed、壁钟 21.660 秒；输入、结果、信息图、封面、关键帧与
背景路径全部来自 `/Users/v9/Documents/workspace/v9/code/dig_ytb_data_claude_v1/`。当前 MP3 worker 日志没有
该 task id，仅当前 notification worker 消费了共享 outbox，证明执行 actor 不在当前日志集合。

Tailscale 显示旧 MacBook Pro 最后在线时间约为 21:30，本任务约 16:52 完成，旧主机在写入后仍在线约
4 小时 38 分；当前 `pg_stat_activity` 仅见本机和 `100.96.3.12` 是窗后快照，不能抵消 durable old-root 证据。
状态保持 **OPEN / REPEATED / WAITING_DISTRIBUTED_ISOLATION**。责任动作仍是人工停止旧机 backend/Celery/
Watchdog/scheduler/脚本；无法证明全停时轮换 PostgreSQL/Redis 凭据并隔离 broker namespace。下一轮必须完整
24 小时 old-root/foreign/remote actor 全 0，并有当前根自然 PDF→ZSXQ、MP3→Publisher 成功。

## 2026-10-26 固定窗复核：旧运行体完成整条内容发布链并与当前 Watchdog 竞态

固定窗内 12/12 个目标 durable task 全部引用旧根
`/Users/v9/Documents/workspace/v9/code/dig_ytb_data_claude_v1/`：audio clean 1、ZSXQ publish 1、MP3 workflow 1、
Publisher 9。Publisher 为 6 completed/3 failed，失败率 33.3%，累计壁钟 6,951.350 秒，P50/P95/max
336.170/2,191.850/2,790.105 秒；三类父子任务累计壁钟不能相加解释为独立用户时间，但足以证明旧部署完整执行了
PDF→发布与 MP3→Publisher 链路。

当前 Watchdog 对旧根 MP3 task 做了重投，本机 MP3 worker 在 4.737 秒内读取宽 `publication_tasks` 行、写入失败
尝试并返回 `False`；同一原任务随后又被旧 actor 完成，形成共享 DB/Redis/broker 下的直接竞态。当前 Watchdog 对一条
Publisher 正确按素材不可达 fail closed，并为一条 processing orphan 写入当前 Mac mini actor；另一条
`orphaned_processing` 终态缺少 `processing_orphan_repair.actor`，只能归为旧/未知代码 writer，不能臆断具体主机。

已按 TDD 修正日快照：processing orphan 必须读取自己的 repair 子树，并只统计当前仍为
`orphaned_processing` 的终态，避免历史已恢复任务误计；生产只读重放得到 closed=2、actor_missing=1，6 项定向测试
和独立评审通过。该诊断修复已运行验收，但不能替代物理隔离。巡检时两个旧 Mac 均 offline，last seen 约 8/12 小时，
时间晚于固定窗任务；当前远端会话 0 仍只是窗后快照。状态继续 **OPEN / REPEATED /
WAITING_DISTRIBUTED_ISOLATION**。必须人工停旧栈，或轮换 PostgreSQL/Redis 凭据并隔离 broker namespace；下一轮
要求完整 24 小时 old-root/foreign/remote actor 全 0，且当前根自然 PDF→ZSXQ、MP3→Publisher 成功。

## 2026-10-27 业务任务全绿，但在线 MacBook Pro 生产会话仍存在

固定窗 28/28 个目标 durable task 全部 current-root：audio clean 4、ZSXQ publish 4、MP3 workflow 4、
Publisher 16；Publisher 16/16 completed，old-root、foreign actor、processing orphan、素材不可达和补传缺视频均
为 0。当前本地业务日志覆盖这些 task，证明上轮 12/12 old-root 与 33.3% Publisher 失败在本窗没有复发，并为
当前根 PDF→ZSXQ、MP3→Publisher 提供自然正向样本。

但巡检时生产 `pg_stat_activity` 仍见 `100.119.238.48` 的 6 个空 `application_name` 会话，Tailscale 映射为
在线 `v9的MacBook Pro`；当前 Mac mini 为 `100.96.3.12`。相同 checkout 绝对路径不能证明 writer actor 相同，
空 application name 又无法归因具体进程，故一个绿窗不能证明旧部署已失去写权限。状态改为
**OPEN / REPEATED / OBSERVING / WAITING_DISTRIBUTED_ISOLATION**。必须人工停并卸载远端旧栈；无法证明全停时
轮换 PostgreSQL/Redis 凭据、隔离 broker namespace，并强制 deployment identity/application_name。下一轮要求
完整 24 小时 remote/old/foreign actor 全 0，且两条当前根自然链继续成功。

## 2026-10-29 current-root 全绿，但远端生产会话增至 7 个

固定窗 23/23 个目标 durable task 全部 current-root：audio clean 1、ZSXQ publish 1、MP3 workflow 5、
Publisher 16；Publisher 16/16 completed，old/mixed/unclassified、replacement/orphan、素材不可达和 foreign actor
均为 0。该自然窗证明当前 Mac mini 业务链可完整运行。

但巡检时 `100.119.238.48` 的空 `application_name` 生产会话由上一窗 5 个增至 7 个，最长已空闲约 1 小时
36 分；当前 Mac mini 为 `100.96.3.12`。绿窗仍不能证明远端旧栈失去写权限，且缺少 deployment identity 使
会话无法归因进程。状态保持 **OPEN / REPEATED / OBSERVING / WAITING_DISTRIBUTED_ISOLATION**。责任动作仍是
人工停旧栈，或轮换 PostgreSQL/Redis 凭据、隔离 broker namespace 并强制 application_name；下一轮要求完整
24 小时 remote/old/foreign actor 全 0。

## 2026-10-30 current-root 继续全绿，但远端生产会话仍有 6 个

固定窗 23/23 个目标 durable task 均为 current-root：audio clean 1、ZSXQ publish 1、MP3 workflow 5、
Publisher 16；Publisher 16/16 completed，old/mixed/unclassified、foreign actor、replacement/orphan、素材
不可达与缺视频均为 0。相较上窗，远端 `100.119.238.48` 空 `application_name` 会话由 7 降至 6，但仍未归零。

一个继续全绿的业务窗和会话减少都不能证明旧运行体失去写权限。状态保持 **OPEN / REPEATED /
WAITING_DISTRIBUTED_ISOLATION**。责任动作仍是人工停止并卸载远端 backend/Celery/Watchdog/scheduler；若无法
证明全停，则轮换 PostgreSQL/Redis 凭据、隔离 broker namespace，并强制 deployment identity/
`application_name`。验收条件仍是完整 24 小时 remote/old/foreign actor 全 0，且两条当前根自然链成功。

## 2026-10-31 current-root 全绿且快照远端会话为 0

固定窗 14/14 个目标 durable task 均为 current-root：audio clean 1、豆包 TTS 2、MP3 workflow 2、
Publisher 8、ZSXQ publish 1；Publisher 8/8 completed，old/mixed/unclassified 与 foreign actor 均为 0。
巡检快照只见当前 Mac mini `100.96.3.12` 和本地 backend，会话中旧 MacBook 地址由上窗 6 降至 0。

这是明确改善，但单次窗后 `pg_stat_activity` 快照不能证明旧运行体在整个固定窗都失去数据库/Redis/broker 写权限。
状态保持 **OPEN / OBSERVING / WAITING_DISTRIBUTED_ISOLATION**；下一轮仍要求完整 24 小时 remote/old/foreign
actor 全 0，并有当前根自然任务成功，或取得旧栈已卸载/凭据已隔离的物理证据。

## 2026-11-01 无自然任务暴露，远端会话快照继续为 0

固定窗数据库中没有新增 `publication_tasks`，因此 old/current root、Publisher foreign actor 与自然链路均为
未暴露，不是业务绿窗。巡检快照只见当前 `100.96.3.12` 与本地 gunicorn，旧 MacBook 地址继续为 0；但仍无
旧栈卸载、凭据轮换或 broker namespace 隔离证据。

状态保持 **OPEN / OBSERVING / WAITING_DISTRIBUTED_ISOLATION**。下一轮仍需有自然 PDF→ZSXQ、
MP3→Publisher 任务且 remote/old/foreign actor 为 0，或取得物理隔离记录；低流量零样本不能关闭。

## 2026-11-02 远端会话与日志外 writer 再次复发

固定窗 30/30 个目标 durable task 路径均为 current-root：audio clean 2、MP3 workflow 4、Publisher 22、
ZSXQ publish 2。但 Publisher 仍有 3 条“视频文件不存在”失败，素材在当前节点实际存在、终态 actor 缺失，
说明相同 checkout 路径不能证明执行主机相同。巡检快照同时再次看到旧 MacBook 地址 `100.119.238.48`
的 6 个空 `application_name` 生产会话，最长空闲约 27 分钟。

这推翻上一窗“远端会话快照为 0”的暂时改善。状态恢复为 **OPEN / REPEATED /
WAITING_DISTRIBUTED_ISOLATION**。不得无人值守断连接；责任动作仍是人工停止并卸载旧机 backend/Celery/
Watchdog/scheduler，或轮换 PostgreSQL/Redis 凭据并隔离 broker namespace、强制 deployment identity 与
`application_name`。下一轮必须完整 24 小时 remote/old/foreign/actor-missing 全 0，并有当前节点自然链成功。
