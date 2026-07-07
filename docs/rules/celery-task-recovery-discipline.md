# Celery 任务恢复纪律

本文档约束 Celery 任务卡死、孤儿消息、worker 假活和人工恢复的处理方式。

## 必须进入终态

- 达到自动修复上限的业务任务，不能继续停在 `pending` 或 `processing`。
- 如果 Celery 消息不可见，必须标记为 `failed`、`canceled` 或写入明确的人工恢复记录后重新入队。
- 修复器不能只打 warning 后返回；warning 不是状态机终态。

## 恢复前检查输入

- 人工重跑历史任务前，必须确认输入文件、外部账号、父子任务引用仍然可用。
- 文件路径不存在时，必须先重定位可用 artifact 或明确失败，不能盲目 requeue。
- 修改输入路径时，需要同时更新 `params` 和 `result` 中会被任务读取的路径字段。

## 保留恢复审计

- 人工恢复必须记录 `manual_recovery` 或 `manual_recovery_history`。
- 至少记录旧 celery id、新 celery id、旧状态、旧 step、修复原因、输入路径变更。
- 允许重置本轮 `queue_repair.count`，但旧历史必须保留，避免排查时丢失因果链。

## 验证不止入队

- `apply_async` 成功只说明消息提交成功，不代表任务执行成功。
- 必须继续观察 DB 状态、Redis 队列长度、worker 日志，直到任务进入 `completed` 或 `failed`。
- 如果 worker 在线但队列不下降，按 worker 假活处理，优先重启对应单队列 worker。
- 恢复验收必须至少确认三件事：父任务终态、关键子任务终态、输出 artifact 存在。
- 对视频类任务，MP4 文件存在只说明渲染产物落盘，不等于 publisher 子任务或父编排完成；必须同时核对 `PublicationTask.status/current_step/progress`、父任务 `waiting_child/deferred_waits` 和实际文件路径。
- 如果视频文件已稳定落盘但 DB 未收口，应优先按同一业务任务补写 completed 结果，并记录恢复原因；不要盲目 requeue 重新合成大视频。
- 复用或恢复 publisher 子任务时必须校验关键语义字段，例如角色、横竖屏比例、字幕开关，避免把旧字幕版本当成无字幕版本使用。

## Watchdog 标准

- Watchdog 判断健康必须同时看 worker 进程、队列长度、active/reserved/scheduled 和业务任务状态。
- 业务任务最近更新不能证明 worker 正在消费 broker 消息。
- 队列有积压且 worker 无领取迹象时，应保留明确 warning，并进入连续确认后的自动恢复流程。

## 外部调用纪律

- Celery 编排里的外部 AI、浏览器自动化、网络请求必须有业务场景级超时；不能直接复用过长默认值。
- worker 并发小的队列，外部调用超时要更短，并提供确定性 fallback、重试或失败收口。
- 子任务失败必须写入 DB 终态和错误原因，由父编排决定重试或失败；不能只依赖日志。
- 父编排轮询子任务时必须有自己的 timeout，避免子任务状态异常时永久占用父 worker。

## 测试纪律

- 针对单表逻辑的单测应创建最小表集合，不应无差别 `db.create_all()` 导致无关模型/index 污染测试。
- 修复 watchdog、队列恢复、任务收口逻辑时，必须覆盖“消息不可见”“达到上限”“失败后终态”三类路径。
- 涉及环境变量的超时配置，必须测试默认值、非法值处理和上限约束。
