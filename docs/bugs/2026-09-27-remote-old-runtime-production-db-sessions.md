# 远端旧运行时仍持生产数据库会话并创建 old-root 内容任务

## 基本信息

- 日期：2026-09-27
- 状态：OPEN / REPEATED / ROOT_HOST_LOCATED / HOST_OFFLINE_OBSERVED / WAITING_FULL_WINDOW
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
