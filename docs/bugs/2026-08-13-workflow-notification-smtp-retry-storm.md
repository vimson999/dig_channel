# 工作流通知 SMTP 断连触发同步重试风暴

## 基本信息

- 状态：RUNTIME_REPLACED / OBSERVING
- 严重级别：高
- 影响模块：workflow_notify / SMTP / MP3 里程碑通知 / Celery 重试
- 首次发现：2026-08-13 日常数据库与后台任务巡检
- 修复提交：当前工作区；2026-08-16 新 workflow_notify worker 已加载单预算与指数抖动策略
- 根因分类：外部 I/O 背压与分层重试叠加
- 复发键：EXTERNAL-CALL-RESILIENCE
- 是否复发：首次登记；同类固定重试/外部调用韧性簇复发
- 关联记录：`2026-07-13-mp3-db-disconnect-marked-parent-failed.md`
- 防线层级：observability / shared_guardrail / runtime_acceptance

## 现象

固定窗口 `2026-08-12 06:02:19` 至 `2026-08-13 06:02:19` 内，workflow notification 完成 172 个、retry 83 个中的 71 个来自该队列、终态失败 18 个。失败集中在 07:40–07:55，错误均为 `SMTPServerDisconnected('Connection unexpectedly closed')`。

单次任务内部先做 3 次、间隔 2 秒的同步 SMTP 重试；任务层随后再以固定 30 秒最多重投 3 次。大量 MP3 里程碑通知在短时间连续到达时，两层固定节奏重试同步对齐，形成失败风暴。

## 影响

- 18 个真实工作流通知终态失败，受影响用户没有收到相应里程碑邮件。
- 71 次任务级重试叠加服务内重试，放大 SMTP 连接压力与队列延迟。
- workflow_notify 为单并发队列；失败风暴会阻塞后续正常通知。该问题主要是外部 I/O，不是 PostgreSQL executor 慢，但属于本轮后台任务端到端失败率恶化的重要来源。

## 根因

SMTP 服务在连续发送后主动断开连接。应用在服务内和 Celery 任务边界都使用固定间隔重试，缺少 provider 级背压、抖动退避、熔断窗口和持久待发语义。两层重试相乘并在相同时间轴重新聚集，因此一次外部抖动被放大为同步重试风暴。

## 解决思路

先保留通知内容和真实失败，不用吞异常或伪造 sent。后续应把责任收敛到单一重试层，按 SMTP 临时错误使用指数退避加抖动，并增加 provider 级发送节流/短熔断与 durable pending 状态；恢复后按 event id 幂等补发。不能仅增加超时或无限重试。

## 实际方案

本轮只完成真实日志定位、复发登记和重启后的恢复观察，没有贸然改变通知语义。安全重启后，新 workflow_notify worker 于 06:15:42 ready，06:16:10–06:16:15 连续 3 个真实通知均在 `1.000–1.202s` 成功，证明当前 SMTP 已恢复，但不构成故障根因闭环。

## 验证

- 日志聚合：172 success、71 retry、18 terminal failed；失败窗口和错误类型一致。
- 运行替换：新 worker PID 由 backend bootstrap 启动，ready 后 3 个真实通知成功。
- 未完成：没有在受控 SMTP 断连下注入 20+ 并发通知验证退避、熔断、持久补发和用户最终送达，因此保持 OPEN。

## 复发预防

- 外部 provider 的重试只能有一个拥有最终预算的层级；多层重试必须共享 attempt/deadline。
- 固定倒计时不得用于同一 provider 的批量失败；必须抖动并有 provider 级背压。
- 通知任务按 event id 幂等，失败状态必须可查询、可补发，不能用日志成功率代替最终送达证据。

## 后续事项

- 下一轮优先复核 workflow_notify retry/failed、SMTP 错误簇、队列等待和受影响 event id。
- 实施前先补故障注入测试；实现后要求定向测试、完整 unit、新 worker、真实通知与下一完整窗口终态失败为 0。
- 在上述证据链完成前不得标记已解决。

## 2026-08-14 复发复核

新固定窗口记录 workflow notification `133 success / 53 retry / 12 terminal failed`，较上一轮 `172 / 71 / 18` 数量下降，但 08:58–09:08 仍以相同 `Connection unexpectedly closed` 和相同“服务内 3×2 秒 + Celery 3×30 秒”固定节奏聚集。下降可能来自流量变化，不能替代根因修复；12 个终态失败仍代表真实通知未送达。当前未实现共享 retry budget、抖动退避、provider 背压或 durable pending，故状态保持 OPEN，下一轮仍按失败率、队列等待和受影响 event id 优先复核。

## 2026-08-15 低流量窗口复核

本轮固定窗口只捕获 `workflow_notify 8 success / 0 retry / 0 failed`，累计 10.065s，
P95/最大 1.373s。该窗口没有受控 SMTP 断连，也没有 durable pending/幂等补发或共享
retry budget 的故障注入证据；因此“零失败”仅表示本轮低流量未复现，不能关闭。状态保持
OPEN/OBSERVING，下一轮继续按 terminal failure、event id 最终送达和故障注入门禁验收。

## 2026-08-16 同一 event 双层重试实证与代码修复

本轮固定窗口为 `161 success / 3 retry / 1 terminal failed`。失败 event id
`bc2ede13-2a5b-4fdd-98a2-59b18ea69d20` 在 21:29:31–21:31:25 共执行 4 轮 Celery
delivery；每轮服务内又执行 3 次固定间隔 SSL 连接和 1 次 STARTTLS fallback。即一个 event
在约两分钟内最多放大到 16 次 SMTP 连接，双层固定重试相乘已由同一 event 的真实日志证实。

本轮按 TDD 将默认服务内 SMTP attempts 从 3 收敛为 1，由 Celery task 独占 3 次重试预算；
任务倒计时从固定 30s 改为 `30/60/120s` 指数退避并叠加 `0–30s` 随机抖动，保留最终异常、
失败日志和真实 `sent` 语义。新测试先在旧实现得到 3 个预期失败，修复后通知 service/policy
定向 `14 passed`。

重启安全门在真实数据库连接下确认 47 条任务记录中没有不可中断任务；随后只通过 Celery Monitor
管理接口窄重启 workflow_notify，旧 PID 67092/67097 warm shutdown，新 PID 80463 于 06:25:25
连接 broker 并 ready，队列/active/reserved/scheduled 均为 0。没有重启其他 worker、backend 或
scheduler。

当前没有受控 SMTP 故障注入、durable pending/幂等补发和新 worker 下的真实失败/最终送达证据，
因此状态提升到 `RUNTIME_REPLACED/OPEN` 而不是 CLOSED。下一轮先确认新 worker 的 retry 日志出现
指数抖动且单轮只有一次 SSL（另可有一次协议 fallback），再要求 terminal failure=0、受影响 event
最终送达和完整 24 小时无风暴，不能仅凭 ready 或测试关闭。

## 2026-08-19 跨 event provider 风暴复发

固定窗口记录 `142 success / 51 retry / 13 terminal failed`。08:52 前后多条 MP3 里程碑同时进入单并发队列；新 worker 已按 30/60/120 秒指数抖动执行，且单轮不再做三次服务内重试，证明 2026-08-16 的“双层固定重试相乘”修复已加载。

但每个 event 仍独立拥有完整 provider 预算，SMTP 故障时每个 event 都执行 SSL 后再做 STARTTLS fallback，并分别进入三次 Celery retry。13 个 event 在 provider 恢复前耗尽预算并永久失败。根因因此上溯为缺少跨 event 共享的 provider 背压/熔断与 durable pending/最终补发；现状仍为 OPEN，不得把指数抖动写成已根治。下一轮必须以 event id 建立待发与最终送达对账，并验证熔断窗口内实际 SMTP 连接数受全局预算约束。

## 2026-08-29 跨 event 失败风暴再次出现

本轮固定窗口记录 `workflow_notify 116 success / 107 retry / 29 terminal failed`，全部 retry/fail 集中在 18 时。虽然 2026-08-16 的单 event 双层固定重试已经替换，但 provider 故障时各 event 仍分别消耗完整 Celery 预算，因此失败数量较 2026-08-19 的 13 个增至 29 个。

这不是数据库 executor 慢，却是本轮后台失败率最高的根因。跨 event 熔断、durable pending、event id 最终送达对账仍未落地，故状态继续 `RUNTIME_REPLACED / OPEN / REPEATED`。下一轮必须按 event id 证明失败可持久待发、provider 恢复后幂等补送，且熔断窗口内 SMTP 建连总量受全局预算约束；暂时成功或单窗失败下降均不能关闭。

## 2026-08-29 方案 1：durable outbox 与 provider 级熔断已落地

用户明确授权按方案 1 修改数据库和重启项目。本轮新增 additive migration
`20260829_workflow_notification_outbox`，生产已创建
`workflow_notification_deliveries` 与 `workflow_notification_provider_states`；前者以
provider、event type、稳定 workflow identity 生成 SHA-256 dedupe key，并由命名 UNIQUE
约束强制幂等，后者保存全 provider 唯一发送租约、连续失败、熔断窗口和最近成功/失败。
runtime schema 同时硬校验两张表、必要列/索引/权限及 dedupe UNIQUE 约束，避免只建表不建约束的
半迁移状态启动。

入队顺序改为：先拒绝尚未提交的 ORM 变更并结束调用方数据库事务，再用独立短事务写 outbox，
释放连接后投递 broker。broker 失败只记录到 delivery，状态保持 pending；watchdog 周期对账到期
provider 并补投。通知对账本身又被隔离为 best-effort 子步骤，即使 outbox 查询失败，也不会跳过
同轮 Celery worker 自愈。

发送路径不再使用每 event 的 `self.retry`。provider 短事务先锁 provider state，再以
`FOR UPDATE SKIP LOCKED` 取得一个 delivery；SMTP 期间数据库 Session 已释放。临时错误第一次即
打开共享指数退避熔断并加 0–30 秒抖动，窗口内其他 event 不再建连；永久认证、收件人或 5xx
错误进入可审计 dead letter。服务内 attempts 固定为 1，普通断连/timeout 不再触发第二协议；只有
明确 SSL protocol mismatch 才允许 STARTTLS fallback。邮件增加稳定
`X-Workflow-Event-ID`。

并发复核又消除了 provider/delivery 反向锁序；完成与失败统一为先 provider、后 delivery，避免租约
到期交界的 PostgreSQL 死锁。租约按 SMTP socket timeout 与最多两个协议预算计算，默认配置为
126 秒，上限 15 分钟，避免发送尚未结束时被第二 worker 接管。SMTP 没有服务端幂等 API，若服务端
已接受而进程在 `sent` 提交前崩溃，仍存在极小的 at-least-once 重复歧义；稳定 event header 和
审计行将该边界显式暴露，不能宣称绝对 exactly-once。

故障注入回归中，20 个不同 event 在首次临时失败后总 attempt 仅为 1；熔断恢复后 20 条全部转
sent，总 attempt 为 21（首条一次失败加一次恢复，其余各一次）。另外覆盖 broker 提交后失败仍
pending、重复 enqueue 只一行、租约崩溃恢复、配置缺失审计、永久 dead letter、业务事务失败不产生
幽灵 outbox 和 watchdog 隔离；PostgreSQL 首次 provider 行采用 conflict-safe insert 后再
`SELECT FOR UPDATE`。通知定向 39 项通过。代码和 schema 已落地，
但在新 worker 重启、真实邮件和后续完整窗口证据完成前，状态只能是
`CODE_AND_SCHEMA_APPLIED / AWAITING_RUNTIME_ACCEPTANCE`，不得写成已解决。

## 2026-08-29 新 worker 与真实邮件运行态验收

重启前门禁读取 47 条 processing 记录和 0 条活动 ZSXQ sync，确认没有不可中断任务；标准整栈
重启在冻结消费后再次通过门禁，没有使用 FORCE。新 backend PID 44341 健康，17/17 个服务均为
running，`workflow_notify` PID 44815 ready。watchdog 新循环已实际查询 outbox，并在发现 due
provider 时输出 `providers=1 deliveries=1`，证明 broker 丢投补偿路径已被新进程加载。

真实验收 event `db-audit-runtime-034bd8a9-16f9-48f9-8507-f55a7a3cbb24` 连续 enqueue 两次，
两次都返回同一 delivery `9cdd2f9d-45fa-4b9c-8276-d30e474acf70`；数据库 matching rows 为 1。
worker 只记录一次 `durable delivery sent`，最终状态 `sent`、`attempt_count=1`，provider 为
`closed`、连续失败 0、无 lease/open window/error。两个随后到达的重复 drain 均以
`no_due_delivery` 退出，没有第二次 SMTP 发送。真实发送链路已运行接受，但脚本无法读取收件人
邮箱客户端，故 `mailbox_receipt_verified=false`，不能把数据库 sent 夸大为人工收件回执。

固定窗口里的 29 个历史 terminal failure 没有可靠原始 payload outbox，不能无中生有重建、伪造
或自动补发，本轮没有这样做。状态由 `CODE_AND_SCHEMA_APPLIED` 提升为
`RUNTIME_REPLACED / OBSERVING`，仍不是 CLOSED；下一完整 24 小时必须确认新 delivery 的
terminal failure 为 0、临时故障只触发一个 provider probe、恢复后 pending 最终 sent，且没有
同一 `X-Workflow-Event-ID` 重复发送，才能关闭复发键。

## 2026-08-30 后窗复核

混合固定窗口仍记录 `157 success / 34 retry / 9 terminal failed`，较旧窗 `116/107/29` 改善，
但这些旧/新进程混合数据不能用于关闭。最终进程窗口记录 workflow_notify 17 次任务、0 retry、
0 failure；数据库 17 条新 delivery 全部 `sent` 且 `attempt_count=1`，provider `closed`、连续失败 0、
无 lease/open window。巡检后的又一次通知任务也成功、0 retry/0 fail。

新 outbox 路径没有观察到复发，但尚无完整 24 小时、provider 临时故障单探针与恢复后全量送达、
以及邮箱客户端回执。因此继续 `RUNTIME_REPLACED / OBSERVING`，不能写成 CLOSED。

## 2026-08-31 完整后窗复核

固定 24 小时日志记录 workflow_notify 37/37 成功、0 retry、0 failure，累计 87.150 秒，
P50/P95/max 2.639/2.857/3.173 秒。只读数据库对账在巡检结束时覆盖 23 条本窗 delivery：
23/23 最终 `sent`、`attempt_count=1`，0 重复 dedupe key、0 pending/leased/dead；中途观察到的 1 条
leased 随后转 sent。provider 为 `closed`、连续失败 0、无 open window，最近失败为空。

这是 outbox 新进程完整业务窗的持续改善证据，但本窗没有真实 provider 临时故障，且仍无邮箱客户端
回执。因此状态保持 `RUNTIME_REPLACED / OBSERVING`；只有后续自然故障证明全 provider 仅一次探针、
恢复后全部 pending 最终 sent 且无重复 event，才能关闭。

## 2026-09-01 自然 provider 断连与恢复复核

本轮首先出现独立的长驻 worker 混版 ImportError；它在取得 outbox delivery 前失败，已另记
`2026-09-01-workflow-notification-mixed-version-import-storm.md`，不能误算成 provider SMTP 失败。重启后真实
SMTP 又发生 `SMTPServerDisconnected`，provider 共享状态打开、连续失败 4，06:22 outbox 为 sent 119、
pending 20、retry_wait 1。durable delivery 未删除、未伪造 sent，其他 event 受同一 provider 熔断约束。

provider 恢复后 watchdog 自然补投；最终 worker 在 06:41:43–06:41:57 连续成功发送 backlog。06:43
只读对账为 140/140 sent、无 pending/retry_wait/dead，provider closed、连续失败 0、最近成功
2026-09-01 06:41:56（Asia/Shanghai）。这提供了“真实临时断连→共享熔断→自然恢复→积压全量送达”的运行证据，
但仍无邮箱客户端回执和同一 event 服务端重复检查，因此继续 `RUNTIME_REPLACED / OBSERVING`，不标记 CLOSED。
