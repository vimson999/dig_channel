# 长驻通知 Worker 混用新旧模块导致导入失败风暴

## 基本信息

- 状态：RUNTIME_REPLACED / OBSERVING
- 严重级别：P0
- 影响模块：workflow notification / Celery worker / Flask app factory / durable outbox
- 首次发现：2026-09-01 每日数据库性能与错误巡检
- 修复提交：当前工作区
- 根因分类：长驻进程混版导入、任务无关 Web API 注册、outbox 重复补投
- 复发键：`EXTERNAL-CALL-RESILIENCE`、`RUNTIME-TOPOLOGY-RELOAD`
- 是否复发：是；与 2026-06-29 飞书 Cookie 旧 worker 导入失败属于同一混版进程根因
- 关联记录：`2026-06-29-feishu-cookie-import-stale-celery-workers.md`、`2026-08-13-workflow-notification-smtp-retry-storm.md`
- 防线层级：minimal_app_factory / process_restart / durable_outbox / runtime_observation

## 现象

固定窗口中 analyzer 把 `tasks.workflow_notify.send_workflow_notification` 统计为 181 次成功、622 次失败。
逐条还原 `logs/celery/workflow_notify.log` 后，622 次不是邮件 provider 的独立失败，而是同一批 durable pending
被 watchdog 约每 85 秒补投；每次任务都在 `create_app()` 注册完整 Web API 时失败：
`ImportError: cannot import name 'audio_upload_max_bytes' from services.script_video_workflow`。

磁盘上的 `services/script_video_workflow.py` 已存在该函数，且新 Python 进程执行完整 `create_app()` 成功；旧
workflow_notify worker 却在 API 文件更新前已缓存旧 service module，之后动态导入新 route module，形成同一
进程内“旧 service + 新 API”的模块图。失败发生在任务进入 app context、获取 outbox delivery 之前，delivery
保持 pending，watchdog 因而持续补投并制造失败风暴。

## 影响

- 622 次 Celery 失败淹没后台任务终态失败率，并让通知单并发队列反复消费无效任务。
- durable outbox 中的业务事件并未丢失，但通知延迟；首次重启后仍有 20 pending、1 retry_wait，SMTP provider
  在真实恢复发送后又因 `SMTPServerDisconnected` 打开共享熔断，属于既有 provider 韧性根因而非本次导入根因。
- 每次任务加载全部 Web API，本身也扩大长驻 worker 与热更新源码之间的兼容表面。

## 根因

通知任务只需要 Flask extensions 和数据库 app context，却调用默认 `create_app()`；默认工厂会导入并注册
所有 API。开发期间 route 与 service 文件更新时间不同，长驻 worker 保留旧 service module，下一次任务又
导入磁盘上的新 route；Python 模块缓存不会自动把旧 service 替换成新文件，于是导入符号不一致。缺少“DB-only
app factory”合同使完全无关的 Web 路由变化可以击穿 durable notification drain。

## 解决思路

保留 Web 进程默认注册行为，只为 worker 的数据库上下文增加显式最小模式。通知任务必须在任何 outbox 操作
之前调用 `create_app(register_apis=False)`，从根因上移除对全部 route module 的动态导入；同时执行无 FORCE
标准重启，确保新 worker 模块图一致。不能通过吞 ImportError、删除 outbox、伪造 sent 或无限重试来收口。

## 实际方案

- `create_app()` 新增仅限关键字的 `register_apis=True` 参数；默认值保持 Web/API/既有调用行为兼容。
- API 注册 import 与 `register_all_apis(app)` 仅在该参数为真时执行。
- `send_workflow_notification` 固定使用 `create_app(register_apis=False)`；注释明确禁止长驻任务为数据库上下文
  导入可独立热更新的完整 Web API 图。
- 第一轮安全门确认 47 条近期任务、0 个活动 sync 后，标准整栈重启且未使用 FORCE；新 backend PID 74071
  healthy，bootstrap 17/17、`errors=[]`，新通知 worker 06:06:17 ready。06:06:24 起已有真实 backlog
  delivery 成功，重启后未再出现该 ImportError。
- 最终代码回归后，06:38 再次由安全门确认 0 个不可中断任务并执行无 FORCE 标准整栈重启。最终 backend
  PID 77653 healthy，bootstrap 17/17、`errors=[]`；workflow_notify PID 78132 于 06:39:24 ready。

## 验证

- 行为测试直接调用任务，mock 工厂在调用点终止，并断言唯一调用为
  `create_app(register_apis=False)`；不是只搜索源码字符串。
- 默认 `create_app()` smoke 仍成功，证明 Web API 注册默认语义未改变。
- 第一轮运行替换后，旧 ImportError 立即停止，真实 durable backlog 开始转 sent；截至 06:22 数据库为
  sent 119、pending 20、retry_wait 1。provider 状态 open、连续失败 4、最近错误为真实
  `SMTPServerDisconnected`，说明剩余积压受共享 provider 熔断控制，没有被错误写成 sent。
- 定向根因套件 66 项通过；全量 unit 在 sandbox 中运行 1,538 项，只有 34 个 loopback bind
  `PermissionError` 且无断言失败；对应三个 loopback 文件在获准环境单独运行 32 项全部通过。compileall、
  前端合同、Remotion typecheck、AST revoke gate、迁移 single-head、registry/bug strict 与 diff check 均通过。
- 最终进程真实消费 backlog：06:41:43–06:41:57 连续多条 `sync_completed` delivery 均记录邮件发送与 task
  success，未出现旧 ImportError。06:43 只读对账为 `sent=140`、无 pending/retry_wait/dead，provider
  `closed`、连续失败 0、idle-in-transaction 0；此前真实 SMTP 断连积压已由 durable outbox 自然排空。
- 这证明最终代码和 DB-only app factory 已运行，但短窗仍不能替代下一完整窗口，因此保持
  `RUNTIME_REPLACED / OBSERVING`，不标记 CLOSED。

## 复发预防

- 只需数据库上下文的 Celery task/scheduler 不得注册完整 Web API；app factory 的最小模式必须用调用行为测试
  锁定，默认 Web 模式另保兼容 smoke。
- 共享 service/API 契约变更后必须重启所有长期驻留消费者；磁盘函数存在与新解释器 import 成功不能证明旧
  worker 已加载。
- durable outbox 的任务级失败、provider 失败和最终 delivery 状态必须分层统计；补投次数不能误算成 622 个
  业务事件失败。
- 发布验收必须包含 worker start time、源码 mtime、ready、真实 delivery、ImportError 指纹、pending/sent
  对账和后续完整窗口；仅单元测试或进程 ready 不得 CLOSED。

## 后续事项

- 最终代码已走安全门标准重启，并确认 backend bootstrap 17/17、worker ready、健康接口、真实 delivery 和
  重启切点后 ImportError=0。
- durable outbox 已按 provider 共享熔断自然排空为 140/140 sent；继续在下一窗口分别统计
  sent/pending/retry_wait/dead-letter 和真实 SMTP 错误，不得人为删除或伪造送达。
- 下一个完整 24 小时及后续窗口要求混版 ImportError 为 0；provider 断连仍按既有
  `2026-08-13-workflow-notification-smtp-retry-storm.md` 独立保持 OPEN/OBSERVING。
