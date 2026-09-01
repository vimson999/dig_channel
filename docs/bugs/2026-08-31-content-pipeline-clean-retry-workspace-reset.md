# 内容清洗在数据库重试时删除已完成工作区并整批重做

## 基本信息

- 状态：RUNTIME_REPLACED / OBSERVING；安全重启和新 worker readiness 已通过，待下一次自然 DB redelivery 证明不再整批重做
- 严重级别：P1
- 影响模块：PDF/音频内容清洗 / Celery retry / canonical asset / SQLAlchemy Session
- 首次发现：2026-08-31 每日数据库巡检的运行态追踪
- 修复提交：当前工作区
- 根因分类：commit ACK 不确定、重试破坏 durable 产物、状态倒退、缺少 checkpoint adoption
- 复发键：`DB-SESSION-LIFECYCLE`、`ORCH-PARENT-CHILD-STATE`
- 是否复发：是；同类 content-pipeline 数据库重试在 6 月 22、7 月 16、8 月 5、8 月 22、8 月 31 日反复出现
- 关联记录：`2026-07-17-content-pipeline-progress-json-and-retry-contract.md`、`2026-08-07-storyboard-ambiguous-commit-false-failure.md`、`2026-08-08-postgres-tunneled-route-query-timeout.md`
- 防线层级：durable_checkpoint / monotonic_state / retry_contract / runtime_acceptance

## 现象与影响

真实 PDF 清洗任务 `5341ebed-3ef5-4ce0-aa63-8cb1fe99ca1b` 于 08:41:58 开始，首次已逐个完成
31 份 PDF，并在 08:51:05 向 31 个 `content_pipeline_assets` 写 cleaned 投影。就在 commit/确认边界，
psycopg2 报 `server closed the connection unexpectedly`；Celery 进入第 1/5 次、60 秒退避重试。

旧重试路径先把 31 个资产重新标为 `cleaning`，再固定传 `reset_workspace=True`。因此它删除了刚写好的
manifest 和 `pdf_cleaned` 目录，从 08:52:07 起把同一 31 份 PDF 全部 OCR 一遍。第二轮耗时
532.224 秒，于 09:00:58 才成功；整条用户任务约 19 分钟，其中约 8 分 52 秒是可避免的重复清洗，
并占用单 worker、CPU/OCR 与后续发布时延。若第一次 commit 实际成功，旧失败/重试收口还可能把
已 cleaned 的 canonical 状态降回 `failed/cleaning`。

## 根因

任务已经正确把数据库断连分类为 transient 并有界重试，但重试只保留了“再执行一次”的控制流，
没有保留昂贵本地产物的 durable completion 事实。manifest 与产物先于数据库登记存在，却没有被当作
可对账 checkpoint；adapter 每次都无条件清工作区。异常处理又在判定 retryable 之前写业务失败状态，
并允许 bulk status 覆盖 terminal `cleaned`。因此网络瞬断被放大为重复外部/本地工作和状态倒退。

这不是 PostgreSQL executor 或索引问题。现场失败发生在 31 行 canonical 更新后的连接异常，随后同形
第二轮更新成功 1,466.98ms；缺少索引无法解释 manifest 被应用主动删除。

## 解决思路与实际方案

- 新增共享 `load_completed_clean_result`：只在 batch id、输入文件多重集、预期输出目录、成功源→输出
  映射逐项一致时复用 manifest；每个输出必须位于该任务目录、非空、扩展名合法，PDF 还必须有
  `%PDF-` 头。任何缺项、路径越界、伪造成功或损坏文件一律 fail closed 并继续正常清洗。
- PDF 与音频 adapter 在 retry delivery 先采用已验证 checkpoint；PDF 同时允许在未完成 manifest 下
  保留既有工作区，让 `overwrite=False` 跳过已完成文件。音频的完整 manifest 可在校验 gap/ffmpeg
  运行依赖前直接恢复。
- Celery 依据 `request.retries` 区分首次投递与 retry，重试不再传 `reset_workspace=True`。
- retryable DB 异常先 rollback/remove 并重新抛出，不写 `failed`；`cleaning` 与最终 `failed` 批量更新
  都排除 terminal `cleaned`，保证 canonical 资产状态单调。
- 自动发布 handoff 对同一 `source_task_id` 先取事务级 advisory lock，再复用既有发布子任务；redelivery
  不再创建或重复投递第二个子任务。完整来源和发布审计仍保留在任务 JSON/结构化投影中。
- 保留完整 manifest、原始 PDF/音频与审计 JSON；没有伪造输出、删除字段、自动回填或执行 DDL。

## 验证

- TDD 红灯 4 项：retry 仍传 reset、重复调用本地 PDF cleaner、音频恢复仍依赖缺失 gap 文件、
  `cleaned` 资产会被降级；新增严格 manifest 对账又先捕获“清单漏掉一个成功输入仍被接受”。
- 修复后相关定向测试 20/20：覆盖 retry delivery、数据库异常不写 failed、PDF/音频 checkpoint、
  source-output 完整映射、损坏/缺项拒绝，以及 cleaning/failed 均不降级 cleaned。
- 真实旧任务最终 manifest 为 151KB，31 个 source、31 个 cleaned、0 failed。用新 checkpoint 代码对该
  真实 manifest 和全部文件做只读验证，得到 `accepted=True, resumed=True, sources=31, cleaned=31`。
- 最终完整 unit 为 `1,533 passed, 3 skipped, 305 subtests passed`；`compileall`、Bug strict 与
  `git diff --check` 均通过。
- 后续发布任务 `15d73154-7e61-44b8-bc65-f0544a49e90b` 自然结束后，09:10:24 安全门以 47 条任务
  记录、0 条活动 sync 判定可重启；未使用 FORCE 完成标准整栈重启。新 content-pipeline starter
  PID 59411、worker PID 59416 于 09:13:41 ready；运行态 API 返回 `running=true`、队列 0。
- 自动发布虽在个别上传 attempt 遇到上游 1059/日额度提示，最终仍以 4 轮完成 31/31 selected、
  uploaded、verified，0 failed/0 pending verification；这证明旧任务及下游完整结束，但不等于新
  checkpoint 已经历真实数据库 redelivery。
- 后续真实 PDF 清洗 `a96435a4-e015-461f-b15e-c1bfc7976687`、音频清洗
  `693ee226-7e98-4b61-9533-fce46cf8d4c3` 及各自发布子任务也全部 `completed/done/100%`。生产只读
  对账显示三个来源任务各恰好一个发布子任务：`15d73154…`、`c186c4fa…`、`08544642…`，均 completed，
  当前没有重复子任务。
- 10:01 再次通过安全门并完成标准整栈重启；新 backend PID `70045` healthy，bootstrap 17/17、
  `errors=[]`，新 content-pipeline worker ready。代码级 redelivery 回归已证明会复用同一 child 且不再
  dispatch，但生产尚未捕获“同一 delivery 自然重复到达”的样本。
- 因现场第二轮已由旧 worker 完成，当前证据只能把代码标为 `RUNTIME_REPLACED / OBSERVING`；在新
  worker 上出现一次自然瞬断并直接采用 checkpoint 前，不能标记 CLOSED。

## 复发预防与后续事项

- 任何昂贵本地/外部工作都必须把 durable 产物与数据库登记拆成可对账阶段；commit ACK 不确定时先验证
  产物和 ownership，再决定采用或重做，不能无条件删 workspace。
- 状态迁移必须单调：已 cleaned 不能被 retry start 或最终失败覆盖；retryable 异常在耗尽预算前不得
  产生永久失败副作用。
- 安全门等待任务自然结束、标准重启和 worker readiness 已完成。最终闭环仍需下一次自然
  transient/redelivery 证明：进度直接进入 `resume_completed`，31 个文件不再次出现 START，
  canonical 状态不倒退，任务/通知/下游发布只完成一次且同一 source 始终只有一个 child。完整后窗
  无复发前保持 OBSERVING。
