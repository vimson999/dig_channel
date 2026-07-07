# 2026-07-02 Report Script Material 任务达到修复上限后仍 pending

## 基本信息

- 状态：已修复，5 个历史任务已全部完成
- 严重级别：高
- 影响模块：report_script_material_workflow / Celery watchdog / publisher_queue_repair / content retention
- 首次发现：2026-07-02 排查 Celery 监控时发现 5 个 2026-06-29 任务长期 pending
- 修复提交：待提交

## 现象

Celery 监控页显示 `report_script_material_workflow` 有 5 个业务活跃任务，但队列和 worker 状态不匹配：

```text
business_active_count=5
status=pending
current_step=queued
queue_repair.count=3
queue_length=0 或重新入队后 queue_length=5
worker 在线但未领取积压任务
```

涉及任务：

```text
e8602717-0db0-4761-a898-ffca55c12655
c696e28a-dd11-4d7c-b241-f12226fc0728
b3c48d33-e4b4-4fe0-b440-6c7c0a659c6f
9e0ee068-e523-4ac5-b48c-a699cf043bec
05da6705-6ec2-44b4-b2de-268a5bb93499
```

排查时还发现任务参数里的原始 PDF 路径已经不存在：

```text
data/zsxq/.../files/*.pdf
```

但磁盘中存在同名可用副本：

```text
data/tasks/.../reports/.../*.pdf
```

## 影响

- 监控长期显示有业务任务活跃，但 worker 实际没有处理这些任务。
- 队列修复器反复输出 `Business task queue repair skipped after limit`，但任务不进入终态。
- 后续容量保护和人工判断会被这些假活跃 pending 干扰。
- 如果直接重跑，因原始 PDF 路径被清理，任务可能再次失败。

## 根因

代码层根因：

1. `publisher_queue_repair.maybe_requeue_stale_publication_task()` 达到 `BUSINESS_TASK_QUEUE_REPAIR_LIMIT` 后只记录 warning 并返回，不会把任务标成 `failed` 或其他终态。
2. 这些任务的旧 Celery 消息已经不可见，但 DB 行仍保持 `pending/queued`。
3. 任务输入 PDF 的原路径被后续文件保留/清理流程移除，任务没有在恢复前重新解析可用本地副本。
4. `report_script_material_workflow` worker 再次出现进程在线但不消费队列的假活，需要单独重启 worker 才开始领取消息。
5. 恢复后又暴露出第二个健壮性问题：文案归一化阶段调用 DeepSeek 缺少此编排场景下的短超时，可能长时间占住 `report_script_material_workflow` 并发槽。

架构层根因：

- 队列修复器把“修复次数耗尽”当成停止动作，而不是状态收口动作。
- 文件生命周期与任务续跑输入没有统一契约：任务保存了易失的下载目录路径，但没有在续跑前检查或重定位文件。
- 外部 AI / 浏览器自动化链路没有统一纪律：每一层都必须有超时、失败落库、重试或降级策略，不能依赖“进程还活着”代表任务健康。

## 解决思路

- 不能让超过修复上限的任务继续保持 active 状态。
- 恢复历史任务时，先修正输入文件路径，再重新提交 Celery 消息。
- 重新提交前重置本轮 `queue_repair.count`，但保留旧历史到 `manual_recovery_history`，避免 watchdog 立刻按旧计数关闭新消息。
- 如果 worker 在线但不消费队列，必须重启对应单队列 worker，并继续观察队列下降和 DB 状态推进。

## 实际方案

代码修复：

- 在 `src/backend/services/publisher_queue_repair.py` 中，当业务任务达到修复上限且 Celery 消息不可见时，调用 `_mark_business_task_queue_repair_limit_failed()`，将任务明确标记为 `failed`，并记录 `queue_repair_limit_reached`。
- 修复器扫描继续使用轻量列，避免为了 watchdog 扫描加载整行大 JSON。
- 在 `src/backend/tasks/report_script_material_workflow.py` 中，为文案归一化传入 `REPORT_SCRIPT_MATERIAL_NORMALIZER_TIMEOUT_SECONDS`，默认 20 秒、最大 120 秒；DeepSeek 超时后使用本地兜底，避免长期占用 worker。
- 在 `src/backend/services/report_audio_workflow.py` 中支持 `normalize_for_tts(..., normalizer_timeout=...)`，让编排按业务场景设置超时。
- 增加单测覆盖：修复上限收口、归一化超时参数传递、脚本文案编排默认/环境变量/上限配置。

运行态恢复：

- 为 5 个任务找到 `data/tasks/.../reports/...` 下的同名 PDF。
- 更新任务 `params.report_script_material_workflow.pdf_path`、`params.report_script_material_workflow.pdf.path` 和 `result.pdf.path`。
- 记录 `manual_recovery` 与 `manual_recovery_history`。
- 重置本轮 `queue_repair.count=0`，重新提交到 `report_script_material_workflow` 队列。
- 发现 worker 假活后，单独重启 `report_script_material_workflow` worker。
- 恢复过程中，最后一个任务的第一次 Gemini 追问子任务超时失败，但子任务明确落库 `failed`，父编排自动创建第二个 Gemini 子任务并最终完成。

## 验证

已完成：

```text
python3 -m py_compile src/backend/services/report_audio_workflow.py src/backend/tasks/report_script_material_workflow.py src/backend/services/publisher_queue_repair.py tests/unit/test_report_audio_workflow_service.py tests/unit/test_report_script_material_workflow_task.py tests/unit/test_publication_task_queue_repair.py
PYTHONPATH=src/backend venv/bin/python -m unittest tests.unit.test_report_audio_workflow_service tests.unit.test_report_script_material_workflow_task tests.unit.test_publication_task_queue_repair
Ran 21 tests in 0.210s OK
```

运行态验证：

```text
e8602717-0db0-4761-a898-ffca55c12655 completed output_exists=true final_script_for_tts_chars=2648
c696e28a-dd11-4d7c-b241-f12226fc0728 completed output_exists=true final_script_for_tts_chars=3379
b3c48d33-e4b4-4fe0-b440-6c7c0a659c6f completed output_exists=true final_script_for_tts_chars=3375
9e0ee068-e523-4ac5-b48c-a699cf043bec completed output_exists=true final_script_for_tts_chars=1988
05da6705-6ec2-44b4-b2de-268a5bb93499 completed output_exists=true final_script_for_tts_chars=2478
```

补充验证：

- `report_script_material_workflow` 队列回到 0。
- 监控页不再出现这 5 个假活跃 pending。
- DeepSeek 归一化超时后日志明确记录本地兜底：`provider=local_fallback`。
- Gemini 追问子任务首次超时后明确进入 `failed`，父编排自动重试并完成，未再静默卡住。

## 复发预防

- 队列修复达到上限后，任务必须进入明确终态，不能继续停留在 `pending/queued`。
- 人工恢复任务前必须检查输入文件是否存在；文件不存在时先重定位或回填可用副本。
- 重新提交历史任务必须记录 `manual_recovery_history`，保留原始 celery id、旧修复历史和新 celery id。
- 看到“worker 在线但未领取积压任务”时，不允许只看进程；必须检查 Redis 队列长度、DB 任务状态和 worker 日志。
- 任务恢复后必须持续观察到终态，不能只看“apply_async 成功”就结束。
- 外部 AI 调用必须有业务场景级超时和确定性兜底；worker 并发越小，越不能允许单次外部调用长时间占住槽位。
- 子任务失败不可怕，但必须可见、可重试、可收口；父编排不能无限等待“可能还会回来”的子任务。

## 后续事项

- 把易被 retention 清理的源 PDF 路径改为稳定 artifact 引用，或在任务续跑前自动重定位同名/同 attachment 文件。
- 评估 `report_script_material_workflow` worker 假活是否需要纳入 watchdog 自动重启的强制确认路径。
- 继续优化 `publication_tasks.result` 大 JSON 读取路径：本次日志中仍能看到单行读取 `result` 的查询偶发 1s-2s。
