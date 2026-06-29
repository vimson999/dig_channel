# 2026-06-23 知识星球编排业务任务卡住但 Celery 消息不可见

## 基本信息

- 状态：已人工恢复，已接入 watchdog 自动 repair
- 严重级别：高
- 影响模块：`zsxq_workflow`、`content_pipeline`、`zsxq_publish`、Celery watchdog
- 首次发现：用户反馈“同步整个编排半天没动静”
- 修复提交：待提交

## 现象

批次 `20260623-191536`，工作流 `25e1d1b0-c126-4046-87d3-11de869a650c` 页面长期停在执行中。

排查时间线：

- `19:15:36` 由 `scheduler_start` 触发第 6 轮系统同步。
- `19:25` 同步完成：拉取 78 个 topic，发现 482 个附件，下载 29 个，下载失败 2 个。
- `19:31` PDF 清洗完成：21 个 PDF。
- 音频清洗子任务 `1d8422b1-c239-4361-b4d7-2b1a9f72e54d` 长时间停在 `pending/init/0%`。
- DB 压力正常：PostgreSQL 约 `6-7/100` 连接，空闲连接充足，不是连接池打满。
- Celery `active/reserved/scheduled` 和队列中找不到音频清洗的旧 Celery 消息 `66712417-11d2-4aaf-92dc-6b64240c9989`。
- 手工补投递音频清洗并重启 `content_pipeline` 后，清洗完成，父编排推进到 `publish`。
- PDF 发布子任务 `91a5f368-ca09-4f63-ade1-56dc10700149` 完成后，音频发布子任务 `981776a6-d9f4-4c0b-9ca0-6e2f9df584f3` 仍停在 `deferred_capacity`。
- 音频发布任务的旧 Celery 消息 `39a6257a-5093-4515-a89e-fa3032c74c70` 同样不在 `active/reserved/scheduled` 中；重新准入检查已允许执行。
- 手工补投递音频发布并重启 `zsxq_publish` 后，任务继续执行并完成。

最终结果：

- 工作流状态：`completed / done`
- 完成时间：`2026-06-23 20:23:13`
- PDF 发布：提交 21 个，确认可见 13 个，待审核/待索引 8 个。
- 音频发布：提交 6 个，确认可见 6 个。

## 影响

- 页面显示“执行中”，但业务实际上卡在不可见的 Celery 消息缺失/消费断点上。
- 用户只能看到“半天没动静”，无法区分是 DB 慢、worker 挂、任务丢消息还是平台上传慢。
- 父编排会一直保持 `running/publish` 或 `running/clean`，阻塞后续轮次判断。
- 如果只看数据库业务状态，会误以为任务还在排队；如果只看 worker 在线，会误以为系统健康。
- 此类问题会反复消耗人工排查成本，必须沉淀为固定检查和自动恢复机制。

## 根因

直接根因有两类：

1. 业务表中存在 `pending` 子任务和 `celery_task_id`，但对应 Celery 消息在 `active/reserved/scheduled/queue` 中不可见。
2. worker 进程在线，但队列积压时没有领取任务，监控已能提示“worker 在线但未领取积压任务，消费连接可能卡住”，但没有自动恢复。

架构层根因：

- 业务状态机把“任务已提交给 Celery”当成足够可靠的事实，没有后续 reconciliation。
- `runtime_guard` 在准入拒绝时会写入 `deferred_capacity` 并投递带 countdown 的消息；如果延迟消息丢失或 worker 消费连接卡住，业务表没有自动重投。
- 父编排只轮询业务任务状态，不能识别“业务 pending 但 Celery 不可见”的孤儿任务。
- watchdog 负责守护进程存在，但对“进程在线、队列有消息、worker 不消费”的恢复动作不足。

## 解决思路

先恢复现场，再沉淀工程化修复。

现场恢复的判断顺序：

1. 先确认数据库压力，避免把连接池问题误判成队列问题。
2. 查工作流 `stage_results`，定位卡在哪个阶段和哪个子任务。
3. 查子任务业务状态、`celery_task_id`、`current_step`、`updated_at`。
4. 查 Celery `active/reserved/scheduled` 和 Redis 队列，确认旧消息是否仍可见。
5. 若业务任务 pending 且 Celery 不可见，同时准入检查已允许，则补投递同一个业务任务 ID。
6. 若队列长度大于 0、worker 在线但 `active=0`，重启对应 worker 恢复消费连接。
7. 等子任务完成后，确认父编排是否进入下一阶段或最终完成。

不能通过新建业务任务绕过，因为那会造成重复发布或重复清洗。必须重投同一个 `PublicationTask.id`。

## 实际方案

本次人工恢复动作：

- 音频清洗任务 `1d8422b1-c239-4361-b4d7-2b1a9f72e54d`：
  - 旧 Celery ID：`66712417-11d2-4aaf-92dc-6b64240c9989`
  - 新 Celery ID：`b3778753-8ced-4fe3-b08e-418f965af9cd`
  - 操作：补投递 `run_audio_clean_task`，更新任务 `celery_task_id`，重启 `content_pipeline`。
  - 结果：8 个音频清洗完成。

- 音频发布任务 `981776a6-d9f4-4c0b-9ca0-6e2f9df584f3`：
  - 旧 Celery ID：`39a6257a-5093-4515-a89e-fa3032c74c70`
  - 新 Celery ID：`9c8aa238-5803-4a7e-96db-38c06a5d8b1d`
  - 操作：确认 `check_publication_task_admission(..., mode="submit")` 已允许，补投递 `run_publish_clean_output_task`，重启 `zsxq_publish`。
  - 结果：6 个 MP3 上传并回查确认。

恢复过程中确认 `zsxq_publish` worker 重启前队列长度为 1、active 为 0；重启后日志出现：

```text
Task tasks.zsxq_publish.run_publish_clean_output_task[9c8aa238-5803-4a7e-96db-38c06a5d8b1d] received
```

随后音频发布进入 `run_rule_start`，上传 MP3，并在 `20:23:10` 完成。

## 验证

验证项：

- 工作流 `25e1d1b0-c126-4046-87d3-11de869a650c`：
  - `status=completed`
  - `current_stage=done`
  - `completed_at=2026-06-23T12:23:13`
- PDF 发布子任务：
  - `status=completed`
  - `selected_count=21`
  - `uploaded_count=21`
  - `verified_count=13`
  - `pending_verify_count=8`
- 音频发布子任务：
  - `status=completed`
  - `selected_count=6`
  - `uploaded_count=6`
  - `verified_count=6`
  - `pending_verify_count=0`
- Celery 日志显示音频发布任务被 worker 接收并执行。
- PostgreSQL 连接数正常，排除 DB 连接池耗尽。

## 复发预防

已补工程化自动恢复，不能依赖人工补投递：

- 增加 `services.publisher_queue_repair.repair_stale_publication_tasks_once()`：
  - 扫描 `PublicationTask.status='pending'` 且 `feature in ('content_pdf_clean', 'content_audio_clean', 'zsxq_clean_output_publish')`。
  - 如果 `updated_at` 超过保护阈值，且对应 `celery_task_id` 不在 `active/reserved/scheduled/queue`，按原 `PublicationTask.id` 重投递。
  - 重投前走 `maybe_guarded_apply_async()`，继续遵守 DB 压力和同类任务准入保护。
  - 重投后写入 `result.queue_repair`，记录旧 Celery ID、新 Celery ID、feature、queue、准入结果和历史。
- watchdog 已接入 repair：
  - `CELERY_AUTO_HEAL_ENABLED=true` 且 `CELERY_AUTO_HEAL_REQUEUE_STALE_TASKS=1` 时执行。
  - repair 尊重人工 pause：暂停 `content_pipeline` 时不会重投 `content_pdf_clean/content_audio_clean`，暂停 `zsxq_publish` 时不会重投发布任务。
  - 如果 repair 后队列有积压但 worker 在线不消费，watchdog 原有 consumer-stuck 逻辑继续负责重启 worker。
- 父编排等待子任务时，应能识别 stale pending：
  - 不只看 `pending`，还要看 `updated_at` 和 Celery 可见性。
  - 进入 stale 状态时写入 `stage_results`，方便 UI 和邮件说明“等待恢复”而不是沉默。
- 发布/清洗子任务需要更频繁更新进度，避免上传正在进行但 UI 长时间停在 5%。
- 邮件和 UI 必须保留“待审核/待索引”数量，不得把 `submitted` 误写成全部成功。

## 后续事项

- 在监控页增加“一键修复 stale pending”的只针对同一任务 ID 的受控动作。
- 将 `deferred_capacity` 的旧错误文案在任务真正重新入队后清除，避免 UI 显示过期错误。
- 发布/清洗子任务在长上传、长清洗期间应更频繁写入进度，减少“实际在跑但 UI 停在 5%”的误判。
- `publisher` 视频合成孤儿任务不做全局自动重投，避免旧视频任务被意外拉起；但父编排已终止的 publisher 子任务会由 watchdog 自动收口为 `canceled`。
