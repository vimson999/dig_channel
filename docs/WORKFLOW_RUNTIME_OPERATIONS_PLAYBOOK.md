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
- Celery `task_postrun`、`task_failure`、`task_retry` 必须清理 SQLAlchemy session。
- 所有任务入口和自动入队入口接入 `services.runtime_guard`，数据库压力大时延迟任务，而不是硬冲。

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
- 默认不启动 scheduler。

这是故意的。重启代码和恢复计划任务是两件事。代码修复验证前，不应让 scheduler 自动产生新批次。

### 恢复 scheduler

确认 worker、数据库、页面和关键任务都正常后，再按需启动：

```bash
./scripts/dev/run-zsxq-auto-scheduler.sh
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

### 豆包浏览器朗读

若出现 CAPTCHA 或页面提示“Chrome 正受到自动测试软件控制”引发拦截，应将任务标记为阻塞并发邮件说明，而不是静默失败或无限重试。

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
