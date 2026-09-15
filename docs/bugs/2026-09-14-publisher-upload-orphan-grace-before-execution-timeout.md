# Publisher 上传尚在合法执行窗口时被 Watchdog 误收口

## 基本信息

- 日期：2026-09-14
- 状态：RUNTIME_REPLACED / OBSERVING
- 严重级别：P0
- 影响模块：Publisher upload-only / Celery Watchdog / `platform_upload` / Bilibili 投稿
- 首次发现：2026-09-14 每日数据库性能巡检
- 修复提交：当前工作区
- 根因分类：非幂等外部副作用、孤儿判定契约、结构化任务路由
- 复发键：`ORCH-PARENT-CHILD-STATE`
- 是否复发：是；2026-07-07 的上传孤儿收口防线把 90 秒当作安全失联阈值，本次真实负载证明该假设会误杀健康任务
- 关联记录：`2026-07-07-platform-upload-worker-restart-orphan.md`、`2026-09-13-publisher-success-preserved-orphan-error.md`
- 防线层级：shared_guardrail / contract_test / observability

## 现象

固定窗口 `2026-09-13 06:00:00` 至 `2026-09-14 06:00:00`（Asia/Shanghai）内，两条结构化
Bilibili upload-only 任务 `34f36a3f…`、`65df0ec7…` 在正常上传期间先被 Watchdog 写成
`failed/orphaned_processing`，之后又取得有效 `aid/bvid` 并落为 `completed/done/100%`。两条终态均保留
`processing_orphan_repair.reason=upload_step_not_requeued`，且错误审计里的队列被写成 `render`。

## 影响

- 正在执行的投稿会被错误撤销和标记失败，页面与告警短暂或永久展示错误状态。
- Bilibili 投稿属于非幂等外部副作用；错误重投或错误撤销都可能造成重复投稿或难以判断的远端结果。
- 后到的成功虽然保留了有效 `aid/bvid`，仍可能留下 `completed + error_message` 的矛盾状态。
- 错误的 `render` 队列审计会误导排障，并绕过 `platform_upload` 的队列可见性检查。

## 根因

第一层是时间契约冲突：Watchdog 对上传步骤使用默认 90 秒 orphan grace，而真实成功上传耗时为
81.6–212.8 秒；上传器本身允许 `BILIBILI_UPLOAD_TIMEOUT_SECONDS`，当前环境为 1200 秒。Watchdog 因此会在
上传器仍处于合法执行窗口时先宣布任务死亡。

第二层是可见性契约不完整：processing 上传只查询 worker active/reserved/scheduled，未继续检查目标 broker
队列与 Redis unacked；当 Celery inspect 因重复 nodename、响应覆盖或瞬时拓扑盲区看不到任务时，会直接收口。

第三层是结构化路由丢失：pending 扫描会加载 `PublicationTaskBilibiliUpload` 投影，processing 扫描没有。
后者又刻意不加载 `params` 大 JSON，因此 `_publisher_child_repair_target` 无法识别 upload-only，只能回退为
`run_publication_pipeline/render`。数据库中两条错误审计与这条生产代码路径完全一致。

## 解决思路

不能单纯把 90 秒换成另一个拍脑袋常量。非幂等上传的孤儿阈值必须至少覆盖上传器允许的完整执行窗口，并在
worker inspect 不可见时继续核对 broker/unacked。结构化任务类型应从一对一投影表批量读取，不能重新依赖
`publication_tasks.params`。

## 实际方案

- Bilibili processing orphan grace 改为
  `max(PUBLISHER_UPLOAD_PROCESSING_ORPHAN_GRACE_SECONDS, BILIBILI_UPLOAD_TIMEOUT_SECONDS + settle)`；结算余量默认
  60 秒，可由 `PUBLISHER_UPLOAD_PROCESSING_ORPHAN_SETTLE_SECONDS` 调整。
- 上传步骤在 worker 不可见后继续扫描目标队列与 Redis unacked；任何来源仍可见时保持原 `processing` 与原
  Celery ID，不撤销、不降级、不重投。
- processing 扫描一次批量读取结构化 Bilibili projection 的 task id，并把该投影传给统一路由器；不加载
  `params`，结构化 upload-only 始终归属 `platform_upload`。
- 保持“所有来源均确认不可见且超过执行预算后，只失败收口、不自动重投”的既有安全策略。

## 验证

- TDD RED 1：2 分钟任务在上传器 600 秒合法窗口内仍进入 worker inspect/关闭路径。
- TDD RED 2：生产批量 scanner 对带结构化 projection 的任务记录 `queue=render`。
- TDD RED 3：worker 不可见但 Redis unacked 可见时，上传任务仍被撤销并关闭。
- GREEN：三个历史场景均通过；queue-repair 全文件 `Ran 51 tests / OK`。
- Publisher queue/API/uploader 交叉套件 `Ran 161 tests / OK`。
- 完整 unit 在可绑定 loopback 的运行环境中 `Ran 1675 tests in 76.775s / OK`；sandbox 内首次执行的 34 个
  error 均为测试 HTTP server 绑定 loopback 的 `PermissionError`，不是产品失败。
- `check-worker-app-factory-boundary.py`（27 files）、`check-celery-revoke-boundary.py`（318 files）、
  `compileall`、严格 bug registry、JSON 工具与 `git diff --check` 均通过。
- 重启安全门两次读取 46 条近期 Publisher 任务、0 条活动 sync，冻结 17 个队列后未使用 FORCE 完成标准整栈
  重启。新 backend PID `20631` healthy；bootstrap 17/17、`errors=[]`；`platform_upload` PID `20707`
  ready，Watchdog PID `21170` 已启动，目标运行态均 `running=true/paused=false/queue_length=0`。
- 重启后只读数据库核对为 active 1、idle 9、idle-in-transaction 0、completed-with-error 新增 0、活动上传 0。
  尚无新进程承接的自然 Bilibili 投稿，因此保持观察，不能标记为已解决。

## 复发预防

- 非幂等外部副作用的 orphan grace 必须由执行器硬超时派生，回收者不得早于执行者的合法截止时间。
- processing/pending 的可见性合同统一覆盖 active、reserved、scheduled、broker queue 与 Redis unacked；任一
  来源可见只能等待原 delivery。
- 任务路由必须优先使用结构化投影；热扫描禁止为了判型重新加载大 JSON。
- 契约测试同时覆盖“合法长任务”“确实超过硬超时且全部不可见”“unacked 可见”“生产 scanner 路由”四类边界。

## 后续事项

- 已通过双安全门标准整栈重启，当前为 `RUNTIME_REPLACED/OBSERVING`。
- 等待至少一条新进程承接的自然 Bilibili upload-only；要求上传成功、无中途 orphan close、队列审计为
  `platform_upload`，且 `completed AND error_message IS NOT NULL` 新增为 0。
- 两条历史矛盾状态只作为审计证据保留，本轮不伪造远端结果、不自动重投、不做破坏性数据修正。

### 2026-09-15 后窗复核

- 固定窗新增 8 条带结构化 Bilibili projection 的完整 Publisher 任务，均为 `completed/done`、有效 BVID、
  `error_message=NULL`、queue repair count=0。
- 这些任务的 `params.feature` 为 `publisher`，不能证明它们走过 `run_bilibili_upload_only`；本机
  `platform_upload` 日志唯一一条 upload-only success 是 CAS-loser stale guard skip，并未执行真实投稿。
- 因共享 nodename/多消费者仍存在日志盲区，本轮只能确认完整 Publisher 业务成功，不能验证 upload-only 的
  1260 秒 grace 与成功清错分支。状态保持 `RUNTIME_REPLACED / OBSERVING`，不提前关闭。
