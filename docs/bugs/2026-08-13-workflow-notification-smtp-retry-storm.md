# 工作流通知 SMTP 断连触发同步重试风暴

## 基本信息

- 状态：RUNTIME_REPLACED / OPEN
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
