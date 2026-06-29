# 2026-06-23 父编排取消后 publisher 子任务仍显示业务进行中

## 基本信息

- 状态：已修复，已接入 watchdog 自动收口
- 严重级别：中高
- 影响模块：`mp3_video_workflow`、`publisher`、`celery_monitor`、Celery watchdog
- 首次发现：用户在 Celery 监控页发现 `render` 队列显示“业务进行中 1”，但 `active/reserved/scheduled` 均为 0
- 修复提交：待提交

## 现象

Celery 监控页 `主视频合成 / render` 卡片显示：

- 队列积压：0
- 执行中：0
- 已领取：0
- 定时：0
- 业务进行中：1
- 业务任务：`7cc41d8e-1280-440d-8403-e24b2c14e549`
- 父任务：`mp3_video_workflow: 8ef684b6-fd2c-4453-be02-0c19c8d94b56`

数据库确认：

- 子任务 `7cc41d8e-1280-440d-8403-e24b2c14e549`：
  - `feature=publisher`
  - `status=pending`
  - `current_step=queued`
  - `celery_task_id=ad6d222c-0028-49a4-b906-e0baa3e0ca84`
  - 创建时间：`2026-06-22 11:16:34`
- 父任务 `8ef684b6-fd2c-4453-be02-0c19c8d94b56`：
  - `feature=mp3_video_workflow`
  - `status=canceled`
  - `current_step=canceled`
  - 完成时间：`2026-06-22 11:18:51`

因此这不是正在执行的视频合成，而是父任务取消后遗留的旧 publisher 子任务。

## 影响

- Celery 监控页错误显示“业务进行中”，干扰运维判断。
- watchdog 会看到 worker 在线、队列为空、业务表仍有 active 任务，造成状态解释混乱。
- 旧子任务如果被错误重投，可能生成已经取消的历史视频，产生重复产物。
- 用户无法判断当前系统到底是在跑任务，还是历史脏状态未清理。

## 根因

直接根因：

- `mp3_video_workflow` 父任务被取消后，已创建的 publisher 子任务没有随父任务一起进入终态。
- 监控页按 `PublicationTask.status in ('pending', 'processing')` 汇总业务进行中，因此把这个遗留子任务展示出来。

架构层根因：

- 子任务生命周期缺少不变量：父编排一旦终止，强依赖父任务的子任务不能继续保持 active。
- 之前的 repair 只覆盖“父任务仍在等待 publisher 子任务时，发现 Celery 消息不可见则重投”。
- 没覆盖“父任务已经终止，不再等待子任务，子任务应该自动收口”的分支。

这和“业务 pending 但 Celery 消息不可见”属于同一类问题：数据库业务状态和运行时事实没有持续对账。

## 解决思路

不能简单隐藏监控页数据，也不能把所有 publisher 历史任务全局重投。

正确边界：

- 父任务仍在运行：publisher 子任务可被等待或按原任务 ID 修复。
- 父任务已终止：publisher 子任务必须被收口为 `canceled`，不得继续算业务进行中。
- 不新建业务任务，不自动拉起旧视频。

## 实际方案

新增 `services.publisher_queue_repair.cleanup_publisher_children_of_terminal_parents_once()`：

- 扫描 `feature='publisher'` 且 `status in ('pending', 'processing')` 的子任务。
- 只处理有 `parent_task_id` 或 `source_task_id` 的任务。
- 查询父任务，如果父任务属于：
  - `mp3_video_workflow`
  - `report_audio_workflow`
  - `report_pdf_audio_workflow`
- 且父任务状态属于：
  - `completed`
  - `failed`
  - `blocked_by_captcha`
  - `canceled`
- 则撤销旧 Celery ID，并把子任务收口为：
  - `status=canceled`
  - `current_step=canceled`
  - `completed_at=now`
  - `error_message=父编排任务已终止(...)，视频子任务自动收口。`
  - `result.orphan_child_cleanup` 记录父任务状态、旧 Celery ID、旧状态和收口时间。

watchdog 已接入该函数：

- 每轮业务任务 repair 前先收口父任务已终止的 publisher 子任务。
- 如果发生收口，写入 watchdog event，并清理监控状态缓存。

现场已手工执行同一收口逻辑：

```text
task_id=7cc41d8e-1280-440d-8403-e24b2c14e549
parent_task_id=8ef684b6-fd2c-4453-be02-0c19c8d94b56
parent_status=canceled
cleaned=True
```

## 验证

数据库验证：

- `7cc41d8e-1280-440d-8403-e24b2c14e549`
  - `status=canceled`
  - `current_step=canceled`
  - `completed_at=2026-06-23T12:47:15Z`
  - `result.orphan_child_cleanup.reason=parent_workflow_terminal`

监控 API 验证：

```json
{
  "id": "render",
  "queue_length": 0,
  "active_count": 0,
  "reserved_count": 0,
  "scheduled_count": 0,
  "business_active_count": 0,
  "business_tasks": []
}
```

测试：

```bash
PYTHONPATH=src/backend venv/bin/python -m unittest \
  tests.unit.test_publication_task_queue_repair \
  tests.unit.test_celery_monitor_watchdog
```

结果：9 个测试通过。

## 复发预防

- 父子任务生命周期必须满足：父编排终态后，强绑定子任务不得继续 active。
- `publisher` 旧任务不做全局自动重投，只做父任务终态后的自动收口。
- 如需重做视频，应由用户在具体业务页显式触发“重新生成”，而不是 watchdog 拉起历史任务。
- 监控页出现“业务进行中 > 0 但 Celery active/reserved/scheduled/queue 全为 0”时，必须优先检查业务表和 Celery 运行态是否不一致。

## 后续事项

- 在业务详情页展示 `orphan_child_cleanup`，让用户看到任务为什么被自动取消。
- 对 `report_audio_workflow` 的 publisher 子任务复用同一生命周期收口规则。
- 后续可把父任务取消动作本身也做成级联取消，watchdog 作为兜底对账。
