# Publisher 队列修复替换仍可见消息导致任务自我饿死

## 2026-09-19 复发复核：五条持久任务达到修复上限

09-18 06:00–09-19 06:00 新建的 Publisher 任务中，`aea06290`、`a6069ada`、
`7f5b80e2`、`d3b39435`、`c0d1f09f` 五条被标记为 `failed/failed`，
错误均为入队修复达到上限。14:12–14:32 的 watchdog 日志对这些任务反复判断
当前 Celery ID 不在 worker/队列；每次检查后换 ID 并 revoke 旧修复消息，render
worker 随后收到被替换的 ID 并按 current-id guard 跳过。Celery 成功计数里的五次
`run_publication_pipeline` 实际是这些 stale delivery 的 no-op，不能作为业务成功。

数据库 SQL 同窗只有 11 条完成慢查询、无 PgBouncer timeout；任务失败不是 SQL
executor 或索引问题。当前 `_message_visible_in_queue` 检查目标 Redis list 前
`PUBLISHER_CHILD_ORPHAN_QUEUE_SCAN_LIMIT`（默认 1000）项和 `unacked` 前 1000 项，
检查范围是否在当时被截断、目标队列/worker 是否错位、或 broker 出队到消费之间
是否存在检查竞态，尚无同秒队列深度、目标消息位置和 worker reserved 快照，不能
任选一个猜测为已证根因。尤其不能再重复只改 grace 或修复次数上限。

状态重开为 **OPEN/REPEATED**。下一轮在首次 `not_visible` 决策前按消息 ID
记录 queue 名、list 长度、扫描数/是否截断、unacked 总数/扫描数、worker
active/reserved/scheduled 可用性、consumer/PID/心跳及原 ID 的 delivery 时间；
检查不完整时必须 fail-closed，不得换 ID 或终态收口。随后用现场回放写端到端
契约、定向和全量测试、安全门重启及自然 Publisher 任务验收；既有失败任务
不得无人值守重投。此次只读诊断没有做业务修复，也不得标记已解决。

## 2026-09-20 无业务样本复核

固定窗内 `publication_tasks` 没有新建任务，Publisher 也没有新任务、失败或 queue-repair 审计；
Celery 的 43 条完成记录均不能替代缺失的 Publisher 自然样本。PgBouncer 六类故障、长 Session hold、
idle in transaction 均为 0，但这只能排除本窗数据库侧放大，不能证明队列可见性误判不再发生。
状态保持 OPEN/REPEATED；下一条真实 Publisher pending/queued 候选仍须采集 queue 长度、扫描截断、
unacked、worker active/reserved/scheduled 与消息 delivery 时间后再决定修复。

## 基本信息

- 日期：2026-09-11
- 状态：OPEN / REPEATED / OBSERVING / WAITING_DISTRIBUTED_ISOLATION（2026-09-23 自然窗口证实本机路径门禁假绿，旧 actor 仍短暂写入）
- 严重级别：P0
- 影响模块：Publisher pending repair / render queue / Redis unacked / MP3 视频编排
- 首次发现：2026-09-11 每日数据库性能巡检
- 修复提交：当前工作区
- 根因分类：另一个旧 checkout 的 Celery/Watchdog 与当前项目共享 broker、数据库、队列和重复 nodename，旧代码继续写入已删除的队列修复语义
- 复发键：`ORCH-PARENT-CHILD-STATE` / `RUNTIME-TOPOLOGY-RELOAD`
- 是否复发：是；09-23 固定窗 12 条真实任务中 4 条仍被旧 actor 累计换 ID 7 次，虽最终完成但根因未隔离
- 关联记录：`2026-07-07-platform-upload-worker-restart-orphan.md`、`2026-07-24-auto-image-replay-downgraded-completed-video.md`、`2026-07-27-celery-solo-inspect-false-stuck-watchdog.md`
- 防线层级：shared_guardrail / contract_test / observability

## 现象

固定窗口 `2026-09-10 06:00:00` 至 `2026-09-11 06:00:00`（Asia/Shanghai）中，MP3 父任务
`c6247250…` 的 Publisher 子任务 `d588a3db…` 最终被 queue repair 写成 `failed/failed`，错误为
“任务入队修复已达到上限，Celery 中也找不到对应消息”。Celery 业务日志却显示被替换的消息随后陆续到达：

- 16:54：当前消息 `aaa3…` 仍 queued/unacked，但 repair 改投 `51e1…`。
- 16:56：`aaa3…` 到达 worker，因数据库已经记录 `51e1…` 而被 stale guard 跳过。
- 16:57：repair 再把 `51e1…` 替换为 `0caf…`；16:59 到达的 `51e1…` 再次被跳过。
- 17:00：repair 把 `0caf…` 替换为 `9703…`；17:04 达到三次上限并关闭任务。
- 17:04:39/17:04:46：`0caf…` 与 `9703…` 先后到达，但任务已终态，均无法继续。

## 影响

- 有效排队消息被人为作废，Publisher 永远得不到一次可执行 delivery。
- 修复器自身制造 stale delivery，并最终把没有业务错误的任务误收口为失败。
- Celery delivery 可以记录为正常跳过，单看 Celery 成败数会漏掉数据库中的业务终态失败。

## 根因

`maybe_requeue_stale_publisher_child` 的注释与总体设计要求 worker inspect 和 queue scan 都成功、且消息在两侧
都不可见后才能判定 orphan。但旧实现只在消息 active 时返回；当 `_message_visible_in_queue` 返回
`(True, True)`，即普通队列或 Redis unacked 中仍存在当前消息时，它仍发布新消息并把数据库
`celery_task_id` 改成新 ID。原消息到达后必然被 current-id guard 判 stale，周期 repair 又对新 ID 重复同一动作，
形成确定性的自我饿死。

## 解决思路

保留 current-id guard 与真正消息丢失时的自动修复，只修正可见性状态迁移：只要当前 ID 在 worker、普通队列
或 Redis unacked 任一处可见，就等待原消息，不发布替代消息，也不增加 repair count。消费者积压或 worker 健康
由现有 watchdog 负责，不能由 lost-message repair 通过换 ID 处理。

## 实际方案

- queue scan 成功且 `queue_visible=True` 时立即返回原快照与 `requeued=False`。
- 保留原 `celery_task_id`、步骤、状态与 repair 计数，不产生 broker 副作用。
- 删除可见消息分支中已经不可达的“仍重排”原因与告警，避免后续误读。
- 真正 worker/queue 都不可见时仍沿用既有发布、CAS 和竞态撤销流程。

## 验证

- TDD RED：将旧测试改为“queued/unacked 时必须等待”，旧实现得到 `requeued=1`，断言失败。
- GREEN：最小实现后目标测试通过；完整 `test_publication_task_queue_repair` 套件 50/50 通过。
- 重构后目标测试与完整套件联合运行 51/51 通过。
- 完整 unit 在受限沙箱运行 1,666 项，34 项仅因禁止绑定 `127.0.0.1` 报 `PermissionError`；允许
  loopback 后原命令复跑为 `Ran 1666 tests in 83.477s / OK`。
- `compileall`、worker app factory 27 文件、Celery revoke 317 文件门禁、registry JSON、Bug strict
  197/197 与 `git diff --check` 均通过。
- 真实时间线证明四个 ID 不是 broker 丢失，而是 repair 更新 current ID 后被消费者正确拒绝。

## 复发预防

- pending/queued repair 的状态表必须明确区分 active、reserved/scheduled、普通队列、Redis unacked 与所有来源不可见。
- “消息可见但未 active”只能等待或触发消费者健康检查，禁止替换消息身份。
- 队列修复回归必须断言 broker 调用次数、`celery_task_id`、repair count 与业务状态同时不变。
- 每日巡检必须把 Celery delivery 结果与 `publication_tasks` durable 终态对账，不能用 worker success/skip 替代业务成功。

## 后续事项

- 2026-09-12 06:04，安全门读取 47 条近期任务与 0 条活动同步并放行；标准双门禁整栈重启完成，
  未使用 `FORCE_RESTART_ACTIVE_TASKS`。新 backend PID `7204` healthy，bootstrap 17/17、`errors=[]`，
  Watchdog 与目标 worker 均 ready。
- 新 Watchdog 首轮 Publisher 扫描约 `102–122ms`，无长 hold 或 SQL 错误；但没有自然 queued/unacked
  候选，当前只能确认代码已加载，不能提前写成业务验收完成。
- 下一完整窗口确认不再出现 queued/unacked 消息被换 ID、连续 stale skip 或同类 repair-limit 失败。

### 2026-09-13 固定窗复核：发现旧语义运行态证据，重新替换

- Publisher child `91b5f0f4…` 在 20:25 的 `queue_repair` 审计仍记录
  `pending_message_not_active_but_queue_visible`，并把 `celery_task_id` 从 `ac45…` 改为 `039e…`；随后投出的
  CAS loser `8973…` 被正确撤销，最终 `039e…` 成功完成。这次没有形成连续自我饿死，但“消息可见仍换 ID”
  指纹证明上一轮运行验收是假绿。
- 09-11 最终修复文件写入时间晚于当时进程启动时间，而旧报告只核对了 healthy/ready，没有核对所有调用者
  的代码版本与自然业务审计。状态重新按 `RUNTIME-TOPOLOGY-RELOAD` 和
  `REGRESSION-ACCEPTANCE-INTEGRITY` 复发处理。
- 2026-09-13 06:09 双安全门读取 46 条近期任务、0 条活动同步并放行；标准脚本冻结 17 个队列后完成整栈
  重启，未使用 `FORCE_RESTART_ACTIVE_TASKS`。新 backend PID `46428` healthy，bootstrap 17/17、
  `errors=[]`，关键 worker 均 running、queue 0。
- 当前代码的 queued/unacked 合同与 queue-repair 完整套件继续通过；重启后尚无自然 queued/unacked 候选，
  因而保持 `RUNTIME_REPLACED/OBSERVING`，不能提前关闭。

### 2026-09-15 固定窗复核：CAS 竞态防线生效，原分支仍待自然覆盖

两次 pending repair 在判定旧消息不可见并发布替代 delivery 后遇到并发 owner，CAS loser 被撤销或由
current-id guard 跳过；对应业务任务最终由数据库当前 ID 完成，projection 的 repair count/reason 保持干净。
这证明 CAS/current-id 防线没有把竞态放大为业务失败，但并未直接覆盖“原消息仍在普通队列或 Redis unacked 时
必须等待”的目标分支。状态继续 `RUNTIME_REPLACED / OBSERVING`。

### 2026-09-21 固定窗复核：旧语义审计再现，增加现场快照

- 真实 Publisher 子任务 `22f523f5…` 最终 `completed/done`，没有重演 09-19 的 repair-limit 终态失败；但其
  durable `result.queue_repair` 在 09:33:34 记录 `pending_message_not_active_but_queue_visible`，把当前 ID 从
  `2be4…` 改为 `78c3…`，且 `previous_message_visible_in_queue=true`。这是当前源码已删除的旧语义，不得按
  “最终成功”误报为根治。
- 09:33:36 当前 Watchdog 又基于旧 ID 发布 `54040…`，CAS UPDATE 为 0 后按防线撤销；render worker 在
  09:33:37 也因数据库当前 ID 已是 `78c3…` 正确 stale-skip。CAS/current-id 防线再次避免扩大故障，但未解释
  09:33:34 的写入者；当前唯一 Watchdog PID `23976` 启动于 06:22 且源码/pyc 均不含旧原因，现有日志没有
  写入者 PID/request/task origin，运行拓扑归因仍不完整。
- 代码新增 fail-safe 观测：每次 pending repair 现场记录 worker active/reserved/scheduled 数量与命中来源，
  Redis 普通队列各 priority 深度、已扫描条数、是否截断，以及 unacked 深度/扫描数/是否截断。观测不改变
  可见即等待、检查失败即不修复、CAS loser 必须撤销的行为。
- 定向联合套件 93/93 通过。只有新进程加载后再次出现自然候选并能以完整快照定位具体漏见断点，或连续后窗
  证明旧语义不再写入，才可进入关闭评估；当前继续 `OPEN/REPEATED/OBSERVING`。
- 完整 unit 1698/1698 及 `compileall`、diff、Bug strict、registry JSON、Celery revoke boundary 门禁均通过。
  06:26 双安全门无不可中断任务，未用 FORCE 的标准整栈重启到 backend PID `6472`；bootstrap 17/17
  Celery service、Watchdog、6/6 scheduler 均成功且 `errors=[]`。新现场快照代码已 RUNTIME_REPLACED，
  但没有自然 pending repair 候选，问题本身仍为 OPEN/REPEATED/OBSERVING。

### 2026-09-22 固定窗复核：定位跨 checkout 双栈并增加运行拓扑门禁

固定窗口内 24 条 Publisher 任务只有 20 条完成，`a4c579ca…`、`e0d746f0…`、`a8be6fa6…`、
`cdd6a33b…` 四条因 queue repair count 达到 3 而失败；另有 `60c2a657…`、`ec86fdc3…`、
`b5a3020d…`、`6563134b…` 等成功任务仍写入当前源码早已删除的
`pending_message_not_active_but_queue_visible`。同一任务约每 3 分 13 秒换一次 ID，随后旧 delivery 被
current-id guard 正确跳过。这不是 PostgreSQL executor、索引或锁问题：同窗无 statement/lock/query/connect
timeout、无 idle in transaction，Publisher 相关慢 SQL 只是 repair 扫描与终态清理。

跨源证据补齐了此前缺失的写入者归因：这些任务的父编排均为 `mp3_video_workflow`，通知与产物路径指向
`/Users/v9/Documents/workspace/v9/code/dig_ytb_data_claude_v1`，而本项目根目录是
`/Users/v9/Documents/3---work/06---dev---python/dig_ytb/dig_ytb_channel`。旧 checkout 的 worker/Watchdog
与当前项目共用 PostgreSQL、Redis、队列及相同 Celery nodename；Celery inspect 会按 nodename 合并响应，
所以旧进程虽能消费和改写当前任务，却不一定在原有 inspect 结果里表现为第二个节点。当前 checkout 的
MP3 worker 日志只看到 revoke 广播，而 durable notification 与产物来自旧根目录，完成了调用链闭环。

已在 Celery 控制面增加 fail-closed 防复发合同：

- 进程命令明确包含另一个绝对 checkout 路径时，服务状态暴露 `runtime_topology_conflict`、外部 PID 与强告警；
- Watchdog 在任何 durable business repair 之前检查外部 Celery/Watchdog，发现冲突即返回
  `foreign_runtime_topology`，不做 requeue、终态收口或其他数据库修复；
- 只有明确绝对路径才判冲突；只显示 `celery -A … worker` 的短进程标题视为路径未知，继续沿用原消费者健康合同，
  避免误报；
- 新增两个回归分别锁定外部 checkout 告警与 repair fail-closed，完整 Watchdog、Publisher repair、bootstrap
  联合套件 99/99 通过。

本轮标准重启前进程现场已只剩当前项目根目录；标准停止脚本会按队列清理同机残留 worker，因此旧双栈被运行态
替换。新门禁代码完成安全重启、health、worker ready 与空队列验收后标记 RUNTIME_REPLACED；但尚无重启后的
自然 Publisher 候选，且后续完整窗口尚未到达，问题继续 **OPEN/REPEATED/OBSERVING**，不得写成已解决。

下一轮必须先检查 `runtime_topology_conflict`、全部 Celery/Watchdog 的 cwd/命令根目录与 nodename 唯一性，再对
自然 MP3→Publisher 任务核对 repair history、current-id、worker/queue/unacked 快照和业务终态。验收条件是：
只有当前 checkout 消费、自然 Publisher 任务完成、没有旧 reason/换可见 ID/repair-limit，且下一完整窗口无复发。

### 2026-09-23 固定窗复核：本机拓扑门禁假绿，改为等待分布式隔离

固定窗口 12 条 Publisher 全部最终 `completed/done`，较上一窗 4 条 repair-limit 失败改善；但
`4c6a3d87…`、`2da2b7d2…`、`46528e4e…`、`ce7bcee9…` 仍持久化当前源码已删除的
`pending_message_not_active_but_queue_visible`，repair count 为 1/2/3/1，累计 7 次替换 Celery ID。
终态成功只证明 CAS loser 撤销与 current-id guard 限制了放大，不能证明旧 actor 被根治。

巡检时 Celery raw broadcast 收到 27 个独立 worker 回包，PID、host、broker/vhost 均对应当前本机进程；
旧 checkout 路径已不存在，本机 process、launchd 与 crontab 也没有旧入口。由此只能判定旧 actor 在采样时已退出，
或位于其他共享凭据的主机/入口。上一轮 `_foreign_runtime_processes()` 只扫描本机命令行，
`foreign_runtime=0` 不是分布式唯一性证明，上一轮 `RUNTIME_REPLACED` 验收被本轮自然持久化证据推翻。

本轮没有继续添加超时、repair 次数或另一层本机路径扫描，也没有无人值守轮换生产凭据。真正的写入围栏需要人工
盘点并停止全部旧部署；若无法证明全量停止，则协调轮换 PostgreSQL/Redis 凭据与 broker namespace，使旧部署
物理失去共享 DB/broker 写权限。状态重开为 **OPEN/REPEATED/WAITING_DISTRIBUTED_ISOLATION**。下一轮只有在
隔离动作后，自然 MP3→Publisher 无旧 reason、无可见消息换 ID、无 repair-limit，且随后完整 24 小时窗口无复发，
才允许进入 CLOSED 评估。

### 2026-09-24 固定窗复核：旧运行时继续创建不可跨根访问的任务

本窗 26 条 Publisher 任务全部携带旧根目录
`/Users/v9/Documents/workspace/v9/code/dig_ytb_data_claude_v1`，其中 5 条完成任务仍写入当前源码已删除的
`pending_message_not_active_but_queue_visible`，历史共换 ID 8 次；较上窗 4/12 条、7 次换 ID 继续复发。

本窗还出现新的影响放大链：3 条待执行消息被当前 Watchdog 按
`pending_queued_not_visible_in_celery` 重排到当前 render worker，而 payload 的 MP3/图片仍指向已不存在的旧根。
文件过滤后可用音频片段为 0，2 条横屏分别以 `max() arg is an empty sequence` 失败，1 条竖屏以
`No valid audio clips.` 失败。三个父 MP3 工作流后续都有其他 Publisher 子任务完成而最终收口，
但多产生了 3 个真实失败 delivery 与约 3–4 分钟的修复等待，不得因父任务最终 completed 而忽略。

生产库只读快照确认当前无遗留的 old-root pending/processing 任务，现场进程命令行也全部来自当前项目根。
这只证明采样时旧 actor 不在本机，不能推翻窗口内 26 条 durable path 证据。本轮没有将缺失文件自动换路径、
伪造产物或盲目重试；真正的闭环条件仍是跨主机停旧栈，或轮换 DB/Redis 凭据与 broker namespace 形成物理写入围栏。
状态保持 **OPEN / REPEATED / WAITING_DISTRIBUTED_ISOLATION**。

### 2026-09-25 固定窗复核：旧根写入下降但物理隔离仍缺

本窗 16 条 Publisher 为 12 completed、3 failed、1 canceled。7 条在当前 backend 06:41 启动后仍携带
旧 checkout 路径，证明旧写入者在新栈运行期间仍可写共享生产库；但旧
`pending_message_not_active_but_queue_visible` 为 0、celery id replacement 为 0，较上窗 5 条/8 次改善。
三条失败均为 B站业务错误（定时余量、充电专属等级、账号未登录），没有再出现旧根资产不可达渲染失败；
随后 8 条 current-root Publisher 完成、1 条取消。

这只能证明症状下降，不能证明旧 actor 已失去写权限。状态保持
**OPEN / REPEATED / WAITING_DISTRIBUTED_ISOLATION**；责任动作仍是跨主机盘点停旧栈，或轮换生产
PostgreSQL/Redis 凭据并隔离 broker namespace。只有物理隔离后自然任务成功且随后完整窗 old-root/reason/
replacement/repair-limit 全为 0，才可进入 CLOSED 评估。

### 2026-09-26 固定窗复核：19/19 旧根，重投把不可达素材送入当前 worker

本窗 19 条 Publisher 全部携带旧 checkout 根目录，较上窗 7/16 明显恶化；12 条 completed、7 条 failed，
失败率 36.84%，累计执行 4,412.523 秒，P50/P95/max 为 230.907/487.898/499.923 秒。旧 reason 出现 2 条，
replacement history 共 3 次。7 条失败包括 6 条 `'NoneType' object is not subscriptable` 和 1 条
`max() arg is an empty sequence`；6 个父 MP3 workflow 最终都 completed，不能用父终态掩盖 child delivery 失败。

任务 `62663a5e-418c-457f-a478-96c0fc1f39aa` 完成了具体调用链闭环：旧 actor 把已不存在的
`/Users/v9/Documents/workspace/v9/code/dig_ytb_data_claude_v1` 图片/音频路径写入任务；当前 Watchdog 在
active/reserved/scheduled、queue、unacked 都为 0 后，把原消息 `623d…` 以
`pending_queued_not_visible_in_celery` 重投为 `e737…`；当前 render worker 过滤不可达文件后得到
`Audio Segments: 0`，MoviePy `concatenate_audioclips([])` 最终抛出 `max() arg is an empty sequence`。
这不是 PostgreSQL executor、索引或普通 broker 重试问题，而是共享 DB/broker 下的跨 deployment 素材边界缺失。

本机当前 `ps`、launchd、crontab 均只见当前根目录，旧目录已不存在，快照时 old-root active=0；这些只证明
当前本机现场，不能推翻固定窗 durable 写入。纪律已增加“跨 deployment identity 或必需素材不可达时，重投必须
fail closed”的合同；但当前生产 worker 尚未获得一个可无人值守落地的凭据围栏，本轮不以更多重试、超时或错误
吞噬掩盖。状态保持 **OPEN / REPEATED / WAITING_DISTRIBUTED_ISOLATION**。责任动作仍是停止全部共享凭据旧部署；
无法证明全停时轮换 PostgreSQL/Redis 凭据并隔离 broker namespace，再以自然任务和后续完整窗验收。

### 2026-09-27 固定窗复核：无 Publisher 样本，但旧 actor 定位到远端 MacBook Pro

本窗没有自然 Publisher 任务，因此 0 条 old-root/失败不能写成改善或关闭。与此同时，生产库新增
5 条 `content_pdf_clean` 与 5 条 `zsxq_clean_output_publish`，10/10 全部携带旧 checkout，说明同一
分布式旧运行时只是转移到其他工作流而非消失。

只读 `pg_stat_activity` 连续看到 `100.94.17.45` 保持 6 个空 `application_name` 的生产库会话；
Tailscale 将该地址映射到仍在线的 `v9 的 MacBook Pro`，而当前 Mac mini 是 `100.96.3.12`。
这解释了此前本机 `ps`/launchd/crontab 全绿但 durable old-root 持续写入的矛盾。根因状态推进为
**OPEN / REPEATED / ROOT_HOST_LOCATED / WAITING_MANUAL_HOST_SHUTDOWN**，关联新记录
`2026-09-27-remote-old-runtime-production-db-sessions.md`。必须物理停用该主机旧部署或轮换并隔离共享凭据，
再以自然 Publisher 和完整后窗验收；终止当前空闲会话不是根治。
