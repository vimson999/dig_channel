# 2026-06-23 父编排终态后强绑定子任务仍保持 active

## 基本信息

- 状态：已解决；watchdog 已覆盖 publisher、豆包 TTS 和 Gemini 子任务，2026-07-18 现场遗留任务已收口
- 严重级别：中高
- 影响模块：`mp3_video_workflow`、研报工作流、`publisher`、豆包 TTS、Gemini、`celery_monitor`、Celery watchdog
- 首次发现：用户在 Celery 监控页发现 `render` 队列显示“业务进行中 1”，但 `active/reserved/scheduled` 均为 0
- 修复提交：待提交
- 根因分类：父子状态机、终态级联、运行时对账、重启中断
- 复发键：`terminal-parent-active-child-cleanup-gap`
- 是否复发：2026-07-18 以非 publisher 子任务再次复发
- 关联记录：`2026-06-26-doubao-browser-tts-stale-processing-child.md`、`2026-07-18-full-restart-interrupted-active-render.md`
- 防线层级：shared_guardrail / contract_test / observability

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

2026-07-18 再次发现同一不变量被破坏：研报父任务 `97f4fd9a-...` 已失败，但豆包子任务 `951548d6-...` 仍停在 `processing / capture_audio / 72%`。延迟重投后又被 12:04 的完整重启中断；旧收口器只扫描 `feature='publisher'`，因此无法处理该孤儿记录，并会持续阻止安全重启。

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
- 首轮终态收口随后仍只把 `publisher` 当成子任务类型，修复的是现场值而不是“强绑定子任务”的生命周期规则，导致豆包/Gemini 换一种 feature 后复发。

这和“业务 pending 但 Celery 消息不可见”属于同一类问题：数据库业务状态和运行时事实没有持续对账。

## 解决思路

不能简单隐藏监控页数据，也不能把所有 publisher 历史任务全局重投。

正确边界：

- 父任务仍在运行：publisher 子任务可被等待或按原任务 ID 修复。
- 父任务已终止：publisher 子任务必须被收口为 `canceled`，不得继续算业务进行中。
- 不新建业务任务，不自动拉起旧视频。

## 实际方案

扩展 `services.publisher_queue_repair.cleanup_publisher_children_of_terminal_parents_once()`：

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
- 子任务矩阵覆盖 `publisher`、`doubao_browser_tts`、`report_pdf_gemini` 和兼容值 `gemini_browser`。
- 父工作流矩阵覆盖 MP3、研报音频、基础文案和 Top10；只有“父任务完成后由用户明确触发的上传型 publisher”保留业务例外。
- 候选扫描只读结构化列；仅最终命中的子任务读取一次 `result`，上传例外才按需读取 `params`。

现场已手工执行同一收口逻辑：

```text
task_id=7cc41d8e-1280-440d-8403-e24b2c14e549
parent_task_id=8ef684b6-fd2c-4453-be02-0c19c8d94b56
parent_status=canceled
cleaned=True
```

2026-07-18 现场再次执行统一收口器：`checked=1 / cleaned=1`，豆包孤儿任务 `951548d6-...` 被撤销并写为 `canceled`；随后重启安全检查返回 `OK: no non-interruptible processing tasks found.`

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

2026-07-18 新增失败研报父任务 + processing 豆包子任务，以及基础文案/Top10 父任务 + Gemini 子任务的模型级回归，验证统一收口和上传例外同时成立。

## 复发预防

- 父子任务生命周期必须满足：父编排终态后，强绑定子任务不得继续 active。
- 每新增一种父工作流或子任务 feature，必须同步更新并测试“父类型 × 子类型 × 父终态”的收口矩阵，禁止按现场 feature 打补丁。
- `publisher` 旧任务不做全局自动重投，只做父任务终态后的自动收口。
- 完整重启预检只负责阻断新事故；历史孤儿必须由 reconciler 收口，二者缺一不可。
- 如需重做视频，应由用户在具体业务页显式触发“重新生成”，而不是 watchdog 拉起历史任务。
- 监控页出现“业务进行中 > 0 但 Celery active/reserved/scheduled/queue 全为 0”时，必须优先检查业务表和 Celery 运行态是否不一致。

## 后续事项

- 在业务详情页展示 `orphan_child_cleanup`，让用户看到任务为什么被自动取消。
- 对 `report_audio_workflow` 的 publisher 子任务复用同一生命周期收口规则。
- 后续可把父任务取消动作本身也做成级联取消，watchdog 作为兜底对账。
