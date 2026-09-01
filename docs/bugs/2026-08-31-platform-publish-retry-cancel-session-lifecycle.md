# 平台发布重试、取消与后台撤销跨数据库事务等待

## 基本信息

- 状态：RUNTIME_REPLACED / OBSERVING；全量撤销门禁与结构化边界事件已通过完整测试并标准安全重启，待自然发布/取消/清理动作和完整 24 小时后窗
- 严重级别：P0
- 影响模块：B站/抖音/视频号发布子任务、六类主工作流取消 API、Publisher 修复器与重复子任务清理、简笔画图片配额批量取消、Celery control
- 首次发现：2026-08-31 对 B站上传创建链完成全调用点复核时
- 修复提交：当前工作区
- 根因分类：大 JSON 宽读、过期 ORM 回读、Session 生命周期、外部 broker/control I/O、取消终态持久化顺序
- 复发键：`DB-SESSION-LIFECYCLE`、`HOT-PATH-PAYLOAD-POLLING`
- 是否复发：是；历史“数据库事务跨 broker/远程 I/O”根因在 retry、cancel、duplicate cleanup 和 orphan repair 分支继续存在
- 关联记录：`2026-07-24-db-session-crossed-broker-and-remote-io.md`、`2026-08-31-mp3-bilibili-upload-wide-parent-double-commit.md`、`2026-08-31-report-audio-bilibili-upload-wide-session-hold.md`
- 防线层级：narrow_action_projection / detached_response_snapshot / durable_terminal_before_revoke / shared_runtime_guard / structured_boundary_event / batch_revoke_guard / broker_failure_terminal_guard / cross_route_contract_test / runtime_observation

## 现象

创建链完成窄化后，横向复核发现同一能力的后续动作仍保留四组旧生命周期：

1. B站 retry 以完整 `PublicationTask + PublicationTaskBilibiliUpload` ORM JOIN 读取任务，包含无关
   `params/result` 与全部投影；commit 后返回过期 ORM，HTTP 序列化再触发整行任务 SELECT。
2. B站、抖音和视频号 cancel 在任务仍处于活动数据库事务时同步调用 `celery_app.control.revoke`；control
   调用失败会直接抛出，使客户端看到失败，而取消终态可能尚未持久化。
3. 豆包 TTS、Gemini、MP3、报告音频、基础文案物料和 Top10 六个主工作流的 cancel API 都复制了
   “先 revoke，后 commit”的顺序。
4. 报告音频/基础文案/Top10 的 child revoke helper、MP3/报告音频重复 Publisher 清理，以及
   Publisher processing orphan 收口也会在读事务或未持久化终态内执行 Celery control I/O。

旧行为的真实数据库回归不是静态猜测：六个主工作流 cancel 在 mock revoke 回调中均记录到
`db.session().in_transaction() == True`；B站 retry 的 SQL 捕获明确包含
`publication_tasks.params`；B站、浏览器发布与主工作流 cancel 的 broker 故障注入都会把异常传播到 API。

## 影响

- 远端 PgBouncer/网络只要出现尾延迟，取消或撤销就会把本可立即归还的数据库连接一同占住；多个页面、
  worker 修复器和重复任务清理可并发放大连接排队。
- retry/cancel 搬运与动作无关的原始 JSON，commit 后又整行回读；Publisher 生产样本的 `params/result`
  最大分别达到约 22KB/50.8KB，网络抖动时放大用户等待和累计数据库耗时。
- broker control 不可用时，如果取消终态没有先 durable commit，旧消息仍可能执行；即使最终回滚，用户也会
  收到失败并重复点击，进一步形成重试/取消风暴。
- 只修创建路径会让同一复发键在 retry、cancel、orphan repair 和后台清理中继续出现，无法形成根因闭环。

## 根因

1. 动作路由复用面向详情展示的完整 ORM loader，没有为 retry/cancel 建立显式列预算。
2. SQLAlchemy commit 后对象过期，响应仍从 ORM 读取，隐式触发整行刷新；代码没有 commit 前 DTO 契约。
3. 撤销被误当成数据库状态变更的一部分；实际上 Celery control 是不可靠的外部 I/O，不能参与数据库事务
   原子性，也不能阻止 durable 终态先落库。
4. 多个工作流各自复制 `control.revoke`，缺少共享“无未提交变更 → 结束只读事务 → 外部 revoke → 故障降级”
   门禁；后台 helper 因只读取 task id 也错误地认为没有事务持有。
5. 旧消息消费者已有 terminal/current-id guard，但取消 API 没有把它作为 broker revoke 失败后的明确兜底，
   因而把 control plane 可用性错误地提升为业务取消成功的必要条件。

生产只读证据排除了索引误诊。失败 B站 child 的旧 `t.*, u.*` 形状序列化为 `8,252B`，planning/executor
仅 `0.171/0.045ms`、shared hit 6/read 0；三次驱动为 `31.35/31.21/123.94ms`。新 retry 窄快照含必要
result 为 `2,637B`，减少 `68.0%`，executor `0.029ms`；cancel 不读 result 为 `1,435B`，减少
`82.6%`，executor `0.056ms`。同轮 `SET TRANSACTION READ ONLY` 本身仍需 `107.78–115.65ms`，说明
主要等待在 PgBouncer/网络与应用行/事务放大，不在 PostgreSQL executor、锁、磁盘或缺索引。

## 解决思路

把每个动作拆成严格的三个阶段：数据库内以窄投影锁定并写入新状态，同时在 commit 前冻结响应 DTO；提交并
归还连接；最后执行 broker publish/control I/O。取消必须先把 `canceled` durable commit，再 best-effort
revoke；revoke 失败返回成功并给出“终态门禁将拦截旧消息”的明确告警。retry 在第一次事务预分配稳定
broker UUID，正常发布成功后不再回读或宽写 ORM。

## 实际方案

- B站新增 `bilibili_upload_action_row`：retry 只加载状态、错误、必要 result 与结构化上传投影；cancel 不读
  result。retry 在 commit 前生成 `_bilibili_retry_response_payload` detached snapshot，首事务写入新
  broker UUID；正常发布成功无 post-broker SELECT/UPDATE，异常 ID 与入队失败仅窄写。
- B站 cancel 在 commit 前生成响应、先持久化 `canceled` 和 projection cancel reason，再调用 revoke；
  control 失败不回滚取消，由 child terminal/current-id guard 拦截旧 delivery。
- 共享抖音/视频号 browser publish 创建链改为首事务预分配 broker UUID并返回 detached snapshot；enqueue
  正常路径不再访问过期 ORM。cancel 使用带 `status=pending` 的 compare-and-set 窄 UPDATE，commit 后才
  revoke，调用方直接使用预建 payload。
- 新增 `revoke_celery_task_after_db_commit`：拒绝携带 `new/dirty/deleted` 进入 control I/O，结束干净读事务
  并归还连接，捕获 control failure。六个主工作流 cancel 统一改为“payload → commit → shared revoke”。
- 报告音频/基础文案/Top10 child helper、MP3 与报告音频重复 Publisher 清理、Publisher queue repair 与
  processing orphan 收口共用同一边界；重复项/孤儿项先 durable 写入 `canceled/failed`，再逐个撤销旧消息。
- 继续做全源码撤销审计后，把 B站、browser publish、Publisher superseded repair 和简笔画图片配额批量
  revoke 全部收口到 `runtime_guard`。新增批量 helper，保留一次 Celery control 调用、去重顺序、
  `terminate` 与 `SIGTERM` 语义；broker 故障只记录失败事件，不再破坏已提交的图片任务/资产终态。
- 单条和批量 helper 都在外部 I/O 前后输出 `[DB_SESSION_BOUNDARY]`，携带 request id、Celery request/task、
  business task、broker task/count、terminate、Session 状态、durable commit 标记、结果与耗时。源码审计中
  直接 `.control.revoke` 只允许存在于该共享守卫内部两处。
- 新增可直接执行的 AST 门禁 `scripts/dev/check-celery-revoke-boundary.py`：扫描全部 backend Python 源码，
  拒绝共享守卫外的 direct `control.revoke`，并要求每个单条/批量 helper 调用显式提供 `operation` 与
  `business_task_id`。门禁按 Python 编译器的 bytes 语义解析，避免被历史非 UTF-8 注释或缓存字节码绕过。
- 保留全部业务字段、原始 JSON、retry history、上传投影、合法多账号/横竖屏 intent 和既有终态语义；没有
  删除审计数据，也没有以 timeout、限流、重试或假数据掩盖根因。

## 验证

- TDD 红灯先证明：B站 retry SQL 包含 `publication_tasks.params`；B站/浏览器 cancel 与六个主工作流 cancel
  在 revoke 时均有活动事务；background child/duplicate/orphan 撤销也有活动事务；control failure 会传播。
- 绿灯回归新增/扩展：`test_cancel_session_lifecycle.py`、`test_background_revoke_session_lifecycle.py`、
  `test_report_audio_workflow_api.py`、`test_mp3_video_upload_api.py`、
  `test_publication_task_queue_repair.py`。回调同时断言 revoke 时 `in_transaction=False` 且数据库中已是
  `canceled/failed`；故障注入断言 API 仍成功、终态保持 durable。
- 后续防线 TDD 先证明旧 shared helper 没有成功边界日志、没有批量 helper，且简笔画配额 revoke 故障会
  传播；绿灯断言结构化 before/after 事件、request id、Session=false、批量单次 control 调用与 SIGTERM，
  以及 broker 故障后数据库终态保持 durable。相关跨模块套件最终 `170 tests`、`OK`。
- 相关跨模块回归先后为 139、191、376 项全部通过；完整项目标准入口
  `PYTHONPATH=src/backend venv/bin/python -m unittest discover -s tests/unit -b` 于 2026-08-31 运行
  `1517 tests in 84.757s`、`OK`。首次沙箱运行的 34 个错误均为本地临时 HTTP 端口 bind 被拒绝；同一
  命令在允许 loopback 的纯测试环境无断言失败。`compileall`、`git diff --check`、registry JSON、
  Bug strict 187/187 及 AST revoke 门禁均通过；门禁直接检查 304 个 backend Python 文件。
- 生产只读旧/新 SQL 量化见根因节；所有 executor 都小于 0.06ms，buffer 无磁盘读，不支持新增索引。
- 13:31 重启安全门读取 47 条近期任务、0 条活动 sync，确认没有不可中断任务；标准脚本冻结 17 个队列、
  关闭旧 API，并在竞态收口的第二次门禁仍通过后完成重启，全程未使用 FORCE。新 backend PID `96754`
  healthy，bootstrap 17/17 Celery service `ok=true`、`errors=[]`。
- `platform_upload`、`report_audio_workflow`、`mp3_video_workflow`、`report_script_material_workflow`、
  `report_top10_workflow` 均在 13:32 ready；13:35 runtime-only API 再确认五个服务全部
  `running=true`、`paused=false`、`queue_length=0`。
- 13:32 后目标日志没有 SQL error、statement/lock/connect/query timeout、server closed、5xx 或长事务；
  只读 `pg_stat_activity` 为 active 1、idle 7、`idle in transaction=0`。唯一慢 SQL 是 B站情报 scheduler
  读取一条 `system_configs` 的 1,248.67ms，继续符合既有网络往返长尾，不构成索引证据。
- `publication_tasks.updated_at` 是 `timestamp without time zone`；按本地切点 `2026-08-31 13:31:59`
  复核后，目标功能重启后任务数为 0。没有制造生产上传/取消来冒充自然验收，因此当前只能标记
  `RUNTIME_REPLACED / OBSERVING`，不能标记 CLOSED 或已解决。
- 防线补全后 13:55 安全门再次读取 47 条近期任务、0 条活动 sync 并放行；标准脚本冻结 17 个队列、停止
  旧 API 后的第二次门禁同样通过，未使用 FORCE，重启到 backend PID `99802`。`/health` healthy，
  bootstrap 17/17 `ok=true`、`errors=[]`；platform upload、报告音频、MP3、基础文案、Top10 与
  report visual plan 六个相关服务均 `running=true`、`paused=false`、queue 0。
- 新切点 `2026-08-31 13:56:44` 后没有自然 retry/cancel/duplicate/orphan/quota 动作，因此尚无生产
  `[DB_SESSION_BOUNDARY]` 样本；这只能记为“未捕获”。只读活动视图为 active 1、idle 7、
  `idle in transaction=0`。人工活动视图探针自身的 `SET TRANSACTION READ ONLY` 为 1,576.42ms，继续
  支持 PgBouncer/网络长尾仍 OPEN，而不是索引或 executor 问题。

## 复发预防

- cancel/retry/repair 必须与 create/dispatch 使用同一 Session 生命周期合同；只读 helper 也必须在 Celery
  control 前结束事务，不得因“没有写入”例外。
- 取消顺序固定为“终态与响应快照 → durable commit → release → best-effort revoke”；control failure 只能
  告警并依赖 terminal/current-id guard，不得回滚终态或诱导用户重复取消。
- retry/create 必须在首事务产生并持久化稳定 broker ID；响应复用 commit 前 DTO；正常 broker 成功路径禁止
  再 SELECT 完整任务或二次 commit。
- 业务代码禁止直接调用 `control.revoke`；必须使用共享单条/批量守卫并有 SQL/transaction callback 合同
  测试，证明调用时无事务、终态已可从独立查询看到。broker failure、批量去重、signal 和结构化
  `[DB_SESSION_BOUNDARY]` 字段也必须覆盖。
- 完整测试/发布门禁必须执行 `venv/bin/python scripts/dev/check-celery-revoke-boundary.py`；新增 helper 调用
  若缺 `operation` 或 `business_task_id`、或任何模块重新直接访问 Celery control，均不得进入运行替换。
- recurrence registry 将该记录同时归入 `DB-SESSION-LIFECYCLE` 与 `HOT-PATH-PAYLOAD-POLLING`，下一轮
  优先按 route、request id、task id、SQL origin、broker id 和终态逐项复核。

## 后续事项

- 已按门禁完成本轮标准重启；下一次任何运行态替换仍必须先运行 `check-restart-safety.py`，若存在不可中断
  任务则等待安全窗口，禁止 FORCE。
- 继续从新 PID `99802`、本地切点 `2026-08-31 13:56:44` 统计 SQL error、PgBouncer timeout、
  server closed、长 Session 和目标旧宽读/commit 后刷新指纹，不得把重启探针混入自然流量。
- 下一次自然 B站 retry/cancel、抖音/视频号 enqueue/cancel、六个主工作流 cancel 或后台 orphan/duplicate
  cleanup 必须证明：revoke/publish 阶段 Session 为 0；终态先 durable；正常路径无 post-broker 宽读；
  control failure 不改变业务终态；before/after 事件可按 request/task/broker id 关联；字段与 retry history 完整。
- 完整 24 小时旧宽读、跨 revoke hold、取消失败风暴和重复 delivery 为 0，且后续窗口再次确认前，保持
  `RUNTIME_REPLACED / OBSERVING` 或更低状态；PgBouncer/网络根因继续 `OPEN / REPEATED`。

## 2026-08-31 14:43 后续运行替换

- 后续 watchdog 展示轮询修复改变共享 `celery_monitor.py`，因此再次执行双安全门并标准重启；两次门禁均
  读取 47 条近期任务、0 条活动 sync，全程未 FORCE。此前平台 retry/cancel/revoke 修复现已随 backend
  PID `4274` 运行；bootstrap 17/17、`errors=[]`，七个相关 worker 均 running、paused=false、queue 0。
- 新增 watchdog 契约后完整确定性回归为 `1565 passed, 5 skipped, 314 subtests passed`；compileall、
  TypeScript、109 项 DB/重启专项、Alembic 单 head、diff check 和 Bug strict 均通过。
- 本地切点 `2026-08-31 14:37:55` 后 `publication_tasks` 更新行为 0 条，仍没有自然 retry/cancel/
  duplicate/orphan/quota 动作或生产 `[DB_SESSION_BOUNDARY]` 样本。新运行态 SQL error、六类 PgBouncer
  错误、连接长持有与 idle-in-transaction 均为 0；这只是未捕获，平台根因继续
  `RUNTIME_REPLACED / OBSERVING`，下一轮仍须按自然事件和完整 24 小时后窗验收。
