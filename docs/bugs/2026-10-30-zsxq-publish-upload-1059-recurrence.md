# 2026-10-30 ZSXQ 发布上传 1059 反复触发有界重试

## 基本信息

- 状态：OPEN / REPEATED / OBSERVING
- 严重级别：中
- 影响模块：ZSXQ clean output publish / 文件上传 / 发布重试
- 首次单独建档：2026-10-30
- 修复提交：无业务代码变更
- 根因分类：外部服务返回 1059 / 上游瞬时失败 / 最终成功掩盖中间失败风险
- 复发键：`EXTERNAL-CALL-RESILIENCE`
- 是否复发：是
- 关联记录：`2026-07-29-zsxq-all-tabs-menu-1059-no-retry.md`
- 防线层级：bounded_retry / idempotency / runtime_observation

## 现象与影响

2026-10-29 06:00 至 2026-10-30 06:00 固定窗仅有 1 个 ZSXQ 发布 durable task，最终 completed、壁钟
395.255 秒，但 `logs/celery/zsxq_publish.log` 仍记录 3 次结构化 upload failure、1 次 publish retry；其中
首个 MP3 在 attempt 1/2 均收到上游 `code=1059` 后触发一次发布级重试，另一运行的第二个 MP3 也出现 1 次
1059。最终无 verify pending，不能把任务成功等同于中间失败已根治。

上一窗为 10 次 upload failure、6 次 publish retry、1 次 verify pending；本窗数量明显改善，但同一错误码和
调用阶段再次出现，仍会拉长用户等待、消耗上传额度，并可能在幂等边界失效时造成重复发布风险。

## 根因与现有防线

当前证据只把根因定位到 ZSXQ 外部上传/发布接口返回 1059；应用数据库没有匹配慢 SQL、锁、长 Session 或
失败风暴，不能归因本地 executor。既有有界重试、上传/发布幂等和最终验证把本窗任务收敛为成功，这是止血与
容错，不是上游 1059 的物理根治。没有故障现场 provider trace，不能臆断为配额、会话、内容或网络中的某一种。

## 本轮动作与验证

- 保留结构化 upload failure、attempt、文件序号、task/run 关联和最终 verify 结果，不删除审计数据。
- 不增加重试次数、不延长超时、不伪造成功，也不在生产库执行配置或 DDL。
- 固定窗确认 1 个 durable task 最终成功，3/1/0 指标较上一窗 10/6/1 改善；同时按复发保持 OPEN。

## 防复发与验收条件

- 每轮分别统计 upload failure、publish retry、verify pending 和最终任务状态，禁止只报最终成功数。
- 下一次 1059 必须保留 provider 响应分类、上传阶段、文件序号、attempt、请求关联键和幂等结果；若同一文件
  连续两轮复发，继续追会话/配额/内容特征与网络 A/B，不得继续加重试掩盖。
- 只有外部根因或调用契约已落地修复、真实发布验收成功，并在后续完整窗口中 upload failure/publish retry/
  verify pending 全为 0，才可进入 CLOSED 或 RUNTIME_ACCEPTED。

## 2026-10-31 复核：失败事件再次恶化

固定窗 1 个 ZSXQ publish durable task 最终 completed、壁钟 503.068 秒，但日志记录 8 次
`upload_failure code=1059`，其中 1 次进入 attempt 2，verify pending 为 0。相较上一窗 3/1/0，upload failure
由 3 增至 8；最终成功只证明有界重试与幂等收敛有效，不能证明上游根因消失。

本窗仍无匹配锁、长 Session 或数据库 executor 故障，状态保持 **OPEN / REPEATED / OBSERVING**。下一轮必须
继续分别统计 upload failure、attempt2/publish retry、verify pending 和最终结果，并在复发现场补 provider 阶段、
响应分类、文件序号、关联键与内容/会话/网络 A/B；禁止通过增加重试或放宽超时掩盖。

## 2026-11-02 两任务最终成功但 1059 继续复发

固定窗 2 个 ZSXQ publish durable task 最终 completed、累计壁钟 660.467 秒；日志仍记录 7 次
`upload_failure code=1059`（其中一文件进入 attempt 2）及 1 次 publish retry。相较上一窗 8 次 upload
failure 略有改善，但新增任务暴露后同一错误仍持续，最终成功只证明既有有界重试与幂等收敛，没有根治上游断点。

状态保持 **OPEN / REPEATED / OBSERVING**。本窗无对应 DB lock、timeout、长 Session 或 executor 故障；
不得增加 retry/timeout。下一轮必须按 run/file/attempt 保留 provider 响应与内容、账号/会话、网络 A/B，且以
真实发布成功后完整窗 upload failure/publish retry/verify pending 全 0 才能升级状态。

## 2026-11-13 君君0621 真实续投再次复发

14:43 批次的 28 个 MP3 因原账号 401 未发布。更新“君君0621”后，15:32–15:40 从既有清洗产物续投 4 轮，最终 28/28 上传、提交、回查确认，4 个主题、pending verify 0。然而结构化事件记录 **8 次 upload_failure code=1059**、**1 次 publish_retry code=1059**，发帖重试 60 秒后成功。相较 2026-11-02 记录的 7 次上传失败，本次升至 8 次；不能因最终成功把该上游故障标为解决。保持 **OPEN / REPEATED / OBSERVING**，下一轮继续按 run/file/attempt 与账号、内容大小和上游阶段对比。
