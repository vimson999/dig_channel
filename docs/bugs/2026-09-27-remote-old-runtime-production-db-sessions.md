# 远端旧运行时仍持生产数据库会话并创建 old-root 内容任务

## 基本信息

- 日期：2026-09-27
- 状态：OPEN / REPEATED / ROOT_HOST_LOCATED / REMOTE_SESSIONS_ZERO_OBSERVED / WAITING_DISTRIBUTED_ISOLATION
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
