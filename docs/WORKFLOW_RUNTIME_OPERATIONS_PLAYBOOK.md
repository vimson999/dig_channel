# 工作流运行经验与事故处理纪律

本文记录 2026-06-21 至 2026-06-23 处理知识星球同步、研报音频/视频编排、MP3 视频编排、发布链路与数据库连接问题时沉淀的经验。它不是一次性复盘，而是后续开发、重启、排障和上线必须遵守的运行手册。

相关基础配置见 [生产级运行加固](production-hardening.md)，编排代码规范见 [编排任务开发标准](WORKFLOW_ORCHESTRATION_STANDARD.md)。

## 核心原则

- 不把数据库当无限资源。任何自动任务都要有准入、退避和连接释放策略。
- 不把“已提交”当“已成功”。发布类任务必须区分已提交、审核中、已验证可见、失败。
- 不让看门狗违背人的暂停意图。人工暂停的 worker 不应被 watchdog 或后端自举偷偷拉起。
- 不让上游泛标题覆盖模型产物。分类、标签、topic 标题只做来源信息，不能优先于结构化结果。
- 不在问题未定位时继续放量。先暂停队列或 scheduler，再做重投和修复。
- 不只修当次报错。每次事故都要补观测、补文档、补验证脚本或准入保护。

## 数据库连接池事故

### 现象

系统启动或自动任务集中入队后出现：

```text
QueuePool limit of size 1 overflow 0 reached, connection timed out
```

页面也可能显示“数据库操作失败，请稍后重试”。

### 根因

- SQLAlchemy pool 是每个进程独立的，不是全局共享。即使单个 worker concurrency 是 1，多个 Celery worker、Flask、scheduler、watchdog 叠加后仍会产生很多连接。
- 父编排等待子任务时如果长期持有 db session，会形成 `idle in transaction`，连接看似空闲但不能释放。
- 启动阶段如果同时拉起后端、worker、scheduler、自动入队，会在短时间内放大连接压力。
- 仅把 `POOL_SIZE=1` 当安全策略是错误的。它会让单进程更容易自锁，却不能限制全系统连接数。

### 固定解法

- 生产和本地开发都优先走 PgBouncer transaction pool。
- Web 使用小 QueuePool：`WEB_DB_POOL_SIZE=4`，`WEB_DB_MAX_OVERFLOW=0`。
- Worker、scheduler 使用 `NullPool`，任务用完即释放连接。
- 父编排轮询子任务时必须周期性 `release_db_session_quietly()`。
- 父编排释放 session 后，不能继续持有并读取旧 ORM 对象；跨等待、跨轮询只保存 `task_id`，需要展示或落库摘要时重新加载。
- Celery `task_postrun`、`task_failure`、`task_retry` 必须清理 SQLAlchemy session。
- 所有任务入口和自动入队入口接入 `services.runtime_guard`，数据库压力大时延迟任务，而不是硬冲。
- 常驻脚本、watchdog、scheduler、CLI 和监控快照函数没有 Flask 请求 teardown，必须在每个工作单元或每段 DB 查询后显式 `rollback/remove`。
- 只读查询也会开启事务；Celery 看板、任务快照、自动收口、统计 API 查询后同样必须释放 session。
- 受控 `idle` 连接不是事故；长期 `idle in transaction` 是事故，超过 60 秒必须排查和收口。
- 若 PgBouncer 忽略 startup `options`，应用连接串中的 `statement_timeout` 不会生效，必须使用数据库角色或数据库级 `ALTER ROLE` / `ALTER DATABASE` 设置硬超时。

专项规则见 [数据库 Session 生命周期纪律](rules/database-session-lifecycle-discipline.md)。

### 验证命令

```bash
./scripts/dev/restart-dev-stack.sh
curl http://localhost:5001/api/v1/celery/status
```

```sql
SELECT application_name, state, count(*)
FROM pg_stat_activity
GROUP BY application_name, state
ORDER BY count(*) DESC;
```

PgBouncer：

```sql
SHOW POOLS;
SHOW CLIENTS;
SHOW SERVERS;
```

### 禁止事项

- 禁止靠无限增大 PostgreSQL `max_connections` 解决应用泄漏或任务风暴。
- 禁止在 DB 压力告警时继续重启 scheduler 放量。
- 禁止把 QueuePool timeout 当普通业务失败吞掉，应识别为可重试/可延迟。

### PostgreSQL 服务端断开正在使用的连接

另一类现象是：

```text
server closed the connection unexpectedly
```

这不是“连接池借不到连接”，而是 PostgreSQL 或中间连接层把当前正在执行 SQL 的连接切掉了。同步任务里最危险的场景是附件文件已经下载到本地，但更新 `zsxq_attachments.local_path/download_status` 时连接断开，事务回滚，导致文件存在但数据库状态没有落稳。

处理纪律：

- 该批次不能被标为成功；数据库事务没有提交，就不能宣称附件状态已确认。
- `zsxq_sync` 必须把该异常识别为可重试数据库异常，先 `rollback`，再按退避重试。
- 重试期间不发送最终失败邮件；重试耗尽后才进入失败通知。
- 排查时同时确认三个事实：workflow run 状态、sync run 状态、附件文件是否已经落盘。
- 文件已落盘但 DB 记录回滚时，下次同步必须允许重新落库或复用本地文件，不能靠人工假改成功。

## 重启与调度纪律

### 本地重启

默认使用：

```bash
./scripts/dev/restart-dev-stack.sh
```

该脚本会：

- 停 Flask 后端。
- 停所有 Celery worker、watchdog、scheduler。
- 启动 Flask 后端。
- 后端自举补齐 worker 和 watchdog。
- 默认补齐知识星球系统同步计划 `zsxq_auto`。

这是系统核心链路的默认行为：本地重启后应自动恢复“知识星球同步 → 清洗 → 发布”计划任务。代码修复验证前如果不希望 scheduler 产生新批次，启动前显式设置：

```bash
BACKEND_PROCESS_BOOTSTRAP_SCHEDULERS=none ./scripts/dev/restart-dev-stack.sh
```

### 恢复 scheduler

独立的自动发帖、本地文件清理、研报资产补漏等非核心 scheduler 仍按需启动：

```bash
./scripts/dev/run-zsxq-auto-publish-scheduler.sh
```

状态检查：

```bash
curl http://localhost:5001/api/v1/zsxq/system-sync-scheduler/status
curl http://localhost:5001/api/v1/zsxq/auto-publish/scheduler/status
```

### 暂停 worker

当发现封皮、发布、上传或模型解析有系统性问题时，先暂停相关 worker：

```bash
curl -X POST http://localhost:5001/api/v1/celery/pause/report_audio_workflow
curl -X POST http://localhost:5001/api/v1/celery/pause/mp3_video_workflow
```

恢复必须显式执行：

```bash
curl -X POST http://localhost:5001/api/v1/celery/resume/report_audio_workflow
```

看门狗和后端自举必须尊重暂停状态。不要用 kill 进程来表达长期暂停意图。

## Watchdog 责任边界

Watchdog 的目标是守护所有被声明为托管的关键 worker，而不是只守护某两个队列。一个 worker 是否重要，应由 `SERVICES` 注册和业务依赖决定，不由临时判断决定。

必须守护的维度：

- 进程是否存在。
- 队列是否有消费者。
- 人工暂停状态。
- 业务 pending/processing 是否长期未收尾。

不能守护的情况：

- 服务被人工 pause。
- scheduler 被刻意关闭等待排障。
- 任务因 DB 准入保护进入 `deferred_db_pressure` 或 `deferred_capacity`。

## 封皮标题事故

### 现象

14 点前生成的研报封皮标题正常，15 点后自动生成的封皮主标题变成 `#外资`，副标题才是正文内容。

### 根因

自动同步旁路创建研报音频/视频任务时，把星球 topic 标题 `#外资研报` 写入了 `video.title`。发布阶段 `_publisher_main_title()` 又把 `video.title` 放在 `structured.cover_title` 前面，导致 DS/Gemini 产出的正确封皮标题被覆盖。

这不是模型质量问题，也不是图片模板问题，而是字段优先级和字段语义混用。

### 固定解法

- `video.title` 只能表示人工明确指定的标题。
- `topic_title`、`tab_label`、`#外资研报` 这类分类信息只能进 metadata 或 tag，不可作为封皮标题 override。
- 发布阶段必须识别泛分类标题，并让 `structured.cover_title` 优先。
- 已生成坏封皮的历史任务应使用现有 `structured_result.cover_title` 重生成封皮/视频，不需要重跑 Gemini。

### 排查 SQL/脚本思路

检查异常任务时看三组字段：

- `params.report_audio_workflow.video.title`
- `result.structured_result.cover_title`
- `result.children.publisher_video.job_id`

如果 `video.title=#外资研报` 且 `structured_result.cover_title` 正常，问题就是覆盖链路。

## 浏览器自动化投递纪律

### Gemini MP3/PDF

大文件上传必须等待上传完成再点击提交。否则页面可能把任务识别成“没有输入”或只提交了提示词。

必须做到：

- 上传控件出现文件卡片后再进入提交动作。
- 对 MP3/PDF 大文件设置更长 upload timeout。
- 提交后等待模型响应区域出现新回复，而不是只看按钮状态。
- DS 解析 Gemini 回复时必须提取标题候选和推荐时间戳，并对缺失字段给出明确失败原因。
- DS/DeepSeek 结构化整理是增强步骤，不是单点硬依赖。Gemini 已返回有效文案时，DS 返回空内容、非 JSON 或临时异常，必须记录 warning 并走本地兜底抽取，不能把整条编排打失败。
- 本地兜底必须从 Gemini 原文中裁掉“标题方案”等包装段落，清理 `PDF + n` 这类页面标记，并尽量保留主标题/副标题候选，避免朗读噪音进入豆包。

### 豆包浏览器朗读

若出现 CAPTCHA 或页面提示“Chrome 正受到自动测试软件控制”引发拦截，应将任务标记为阻塞并发邮件说明，而不是静默失败或无限重试。

豆包音频捕获不能只看“音频 chunk 一段时间不增长”。必须同时满足按文本长度估算的最低音频时长，再允许导出 WAV。典型异常：

```text
豆包音频时长异常：文本 427 字，音频 0.487s，低于最低预期 17.1s
```

这类短音频通常是朗读还没完成、点到了错误朗读入口，或插件只捕获到瞬时空音频。处理纪律：

- 子任务必须保留音频秒数和 chunk attempt，方便判断是偶发短捕获还是系统性失败。
- 父编排应允许分段重试，不能因为一次 0.x 秒音频直接宣告整条任务失败。
- 捕获结束条件必须包含最低时长阈值，不能只依赖 stable seconds。
- 修复后要用原失败任务续跑验证，确认已完成子任务可复用，失败分段可继续重试。

邮件应包含：

- 任务 ID。
- 阶段。
- 浏览器页面状态。
- 是否需要人工处理 CAPTCHA。
- 后续是否会自动重试。

## 发布成功判定纪律

发布链路必须区分以下状态：

- `submitted`: 请求已提交到平台。
- `reviewing`: 平台接收但不可见，可能在审核中或账号额度限制中。
- `visible`: 已通过接口或页面确认目标用户可见。
- `failed`: 明确失败。
- `unknown`: 提交后无法确认。

邮件和 UI 不能把 `submitted` 或 `reviewing` 写成“成功”。如果到了当日上限，应切换账号重试；如果仍不可见，应报告为待审核/待确认，而不是成功。

发布成功邮件必须同时说明：

- 提交数量。
- 已确认可见数量。
- 待审核/待确认数量。
- 失败数量。
- 账号切换情况。

## 排队任务处理纪律

当发现某个时间点后产物系统性异常时：

1. 暂停相关 worker。
2. 停 scheduler，防止新批次进入。
3. 查询 queued/pending 任务的创建时间和参数来源。
4. 对坏批次进行取消或标记，不让它继续执行。
5. 修复代码。
6. 重启 worker 让修复生效。
7. 只用一两个任务小批量重投验证。
8. 验证通过后再恢复 scheduler。

不要在未定位根因时“重投全部”。

## 编排卡住排查纪律

当用户反馈“同步整个编排半天没动静”时，不要先猜数据库挂了，也不要直接重启全部服务。按以下顺序排查，目标是区分四类状态：真实执行中、平台上传慢、业务任务孤儿、worker 消费卡住。

### 1. 先定位工作流和阶段

检查最新 `zsxq_workflow_runs`：

- `status`
- `current_stage`
- `batch_id`
- `sync_run_id`
- `clean_task_ids`
- `publish_task_ids`
- `stage_results`
- `updated_at`

判断口径：

- `sync` 已完成但 `clean` 未推进：优先查清洗子任务。
- `clean` 已完成但 `publish` 未推进：优先查发布子任务。
- `publish` 中 PDF 已完成、音频 pending：优先查准入保护和 `zsxq_publish` 队列。
- `updated_at` 长时间不变不等于数据库挂死，要继续查子任务和 Celery 可见性。

### 2. 排除数据库连接池问题

先看 PostgreSQL 当前连接，而不是凭页面超时判断：

```sql
SELECT
  count(*) AS total_connections,
  count(*) FILTER (WHERE state = 'idle in transaction') AS idle_in_transaction_connections
FROM pg_stat_activity;
```

如果连接数远低于 `max_connections`，并且没有大量 `idle in transaction`，本次问题优先按队列/worker 排查。

### 3. 查子任务是否是 stale pending

对 `PublicationTask` 子任务检查：

- `id`
- `feature`
- `status`
- `current_step`
- `progress`
- `celery_task_id`
- `updated_at`
- `error_message`
- `result.runtime_guard`

危险状态：

- 业务任务是 `pending` 或 `processing`。
- `updated_at` 超过预期阈值不变。
- 对应 `celery_task_id` 不在 Celery `active/reserved/scheduled`。
- Redis 队列里也找不到该消息。

这就是业务任务孤儿，不应新建任务，应重投同一个业务任务 ID。

### 4. 查 worker 是否在线但不消费

监控接口：

```bash
curl http://localhost:5001/api/v1/celery/status
```

重点看目标队列：

- `pids`
- `queue_length`
- `active_count`
- `business_active_count`
- `consumer_warning`

如果 `queue_length > 0`、worker 在线、`active_count=0`，并出现“worker 在线但未领取积压任务，消费连接可能卡住”，可以重启该队列 worker。重启前必须确认没有正在执行的 active 任务。

```bash
curl -X POST http://localhost:5001/api/v1/celery/restart/content_pipeline
curl -X POST http://localhost:5001/api/v1/celery/restart/zsxq_publish
```

### 5. 安全补投递原则

补投递只能使用原业务任务 ID：

- `content_audio_clean` -> `tasks.content_pipeline.run_audio_clean_task(task_id)`
- `content_pdf_clean` -> `tasks.content_pipeline.run_pdf_clean_task(task_id)`
- `zsxq_clean_output_publish` -> `tasks.zsxq_publish.run_publish_clean_output_task(task_id)`

补投递前必须满足：

- 业务任务未取消。
- 旧 Celery 消息不可见。
- 当前准入检查允许或明确会 deferred。
- 该任务的业务动作幂等，或任务结果中能识别已处理文件。

补投递后必须更新 `celery_task_id`，并在 `result.manual_requeue_history` 记录旧 ID、新 ID、原因和准入结果。

### 6. 收尾确认

恢复后必须确认：

- 子任务 `status=completed` 或明确失败。
- 父工作流进入下一阶段或 `completed/done`。
- 发布结果区分 `verified_count` 和 `pending_verify_count`。
- UI 过期错误文案被清理，避免任务已恢复但页面仍显示旧的 `deferred_capacity`。

本纪律对应事故记录：[知识星球编排业务任务卡住但 Celery 消息不可见](bugs/2026-06-23-zsxq-workflow-stale-celery-message.md)。

## 编排任务 ORM 对象纪律

### 现象

研报 PDF 音频/视频编排在 Gemini 与豆包朗读都成功后，父任务停在视频合成前后，页面显示：

```text
Instance <PublicationTask ...> is not bound to a Session; attribute refresh operation cannot proceed
```

### 根因

父编排为了避免数据库连接长期占用，会在等待子任务期间释放 SQLAlchemy session。释放后，先前保存在列表里的 `PublicationTask` ORM 对象可能已经 detached；后续再读取 `task.result`、`task.status` 等属性时，SQLAlchemy 会尝试 refresh，但对象已经不属于任何 session，于是抛出 DetachedInstanceError。

### 固定解法

- 编排任务跨 `_wait_child()`、长轮询、视频合成、浏览器自动化等待时，只保存子任务 ID。
- 生成 UI 摘要、最终结果 JSON、通知邮件内容前，用子任务 ID 重新加载数据库对象。
- 可以短时间使用刚创建或刚加载的 ORM 对象做立即判断，但不能把它放进长期列表后再跨 session 读取。
- 修复后必须用失败任务原地重试验证，确认可以复用已完成子任务并继续后续阶段。

### 验证样例

本次验证用例为任务 `5692cd61-9dc0-4772-80fa-f9d01d52a88c`：原失败点为 87%，修复后原地重试，复用 1 个 Gemini 子任务和 6 个豆包朗读子任务，最终父任务 `completed / done / 100`，视频合成子任务 `a60dd50f-8411-4b7e-889c-1584214b6787` 完成。

任务 `f09b9cc1-e3ef-4a4c-9519-2d85a547e0fa` 暴露两类问题：DS 结构化返回空/非 JSON 导致 `Expecting value`；豆包朗读捕获 0.4-0.7 秒短音频。修复后原地续跑，父任务 `completed / done / 100`，音频 541.289 秒，视频合成子任务 `53b8ec71-05af-4fb1-bedb-c07ccfb92023` 完成。

## 变更验证清单

每次改动工作流、连接池、worker、发布、模型浏览器自动化后，至少执行：

```bash
git diff --check
venv/bin/python -m py_compile <modified-python-files>
node --check src/frontend/js/zsxq-lab.js
```

运行态验证：

```bash
./scripts/dev/restart-dev-stack.sh
curl http://localhost:5001/api/v1/celery/status
curl http://localhost:5001/api/v1/zsxq/system-sync-scheduler/status
```

期望结果：

- 后端监听 `http://localhost:5001`。
- 关键 worker `running=true`。
- 排障期间 scheduler `running=false`。
- `report_audio_workflow`、`mp3_video_workflow` 队列没有异常堆积。
- PgBouncer pool 没有长期 `cl_waiting`。

## 文档纪律

每次类似事故修复后，必须在项目文档里留下以下内容：

- 现象：用户或系统看到什么。
- 根因：哪条代码路径、配置或运行机制导致。
- 修复：代码和配置分别改了什么。
- 验证：执行了哪些命令，结果是什么。
- 回滚：如果修复有问题，如何停 scheduler、pause worker、恢复旧配置。
- 后续：是否需要补测试、补监控、补 UI 状态或补邮件文案。

文档入口必须挂到 `docs/README.md` 或相关专题文档里，不能只留在聊天记录中。
