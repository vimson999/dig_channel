# 2026-06-24 Top10 单篇 Gemini 子任务缺少外层重试

## 基本信息

- 状态：已修复，已重启 `report_top10_workflow` worker 生效
- 严重级别：中高
- 影响模块：`report_top10_workflow`、`report_pdf_gemini`、Gemini 浏览器自动化
- 首次发现：用户在今日研报 Top10 编排页看到第 2 篇单篇 Gemini 子任务因文件上传菜单未找到而直接导致父编排失败
- 修复提交：待提交

## 现象

Top10 编排任务在 12% 左右失败，错误信息类似：

```text
第 2 篇单篇 Gemini 子任务失败: 未找到 Gemini 文件上传菜单项;
attach_error=Timeout 50000ms exceeded while waiting for event "filechooser"
```

同类 PDF 上传逻辑在“研报转视频/研报转语音”编排中已经存在自动重试，但 Top10 编排遇到同类浏览器瞬时错误时直接把父任务判失败。

## 影响

- 一次 Gemini 页面菜单定位或 `filechooser` 超时，会导致整期 Top10 编排失败。
- 用户需要手工重试整期任务，已经完成的前序单篇解析只能依赖父任务恢复逻辑复用。
- 页面上只看到父任务失败，不容易判断这是底层上传实现问题，还是外层编排缺少重试策略。

## 根因

底层 Gemini 浏览器任务是复用的：

- Top10 单篇解析使用 `tasks.gemini_browser.process_report_pdf_gemini_job`
- 研报转视频/研报转语音也使用同一个 worker 和 `services.gemini_browser`

真正不一致的是父编排策略：

- `report_audio_workflow` 对 Gemini 子任务包了 `_run_gemini_task_with_retries()`，遇到可恢复错误会新建子任务重试。
- `report_top10_workflow` 原来只创建一次单篇 Gemini 子任务，等待后如果子任务失败，就直接抛错失败父任务。
- 总编辑 Gemini 子任务也同样缺少外层重试。

因此这不是“上传代码另写了一套”，而是 Top10 没有复用同级别的编排韧性。

## 解决思路

保持底层浏览器上传实现复用，不复制一套上传逻辑。

在 Top10 父编排层补齐和研报转视频/研报转语音一致的能力：

- 识别可重试的 Gemini 子任务错误。
- 对单篇解析和总编辑 Gemini 子任务都做外层自动重试。
- 每次尝试都写入 `attempt/max_attempts/retry_index`。
- 父任务 `result.child_attempts` 和 `result.retry_state` 记录每次尝试的状态和错误。
- 如果等待子任务异常或超时，先撤销并标记旧子任务失败，再启动下一次尝试，避免遗留 active 子任务。

## 实际方案

在 `src/backend/tasks/report_top10_workflow.py` 中新增：

- `REPORT_TOP10_GEMINI_RETRY_LIMIT`，默认 `2`，即首次 + 2 次自动重试。
- `RETRYABLE_CHILD_ERROR_FRAGMENTS`，覆盖：
  - `timeout`
  - `filechooser`
  - `未找到 Gemini 文件上传入口`
  - `未找到 Gemini 文件上传菜单项`
  - `文件上传失败`
  - `等待 Gemini`
  - `Browser closed`
  - `Protocol error`
  - `net::ERR`
- `NON_RETRYABLE_CHILD_ERROR_FRAGMENTS`，避免父任务取消、任务不存在、PDF 缺失等错误被盲目重试。
- `_run_single_report_gemini()` 外层重试。
- `_run_editor_gemini_with_retries()` 总编辑外层重试。
- `_record_child_attempt()`、`_mark_child_failed()`、`_revoke_child_task()` 等状态记录和收口工具。

单篇和总编辑子任务的 metadata 均写入：

- `attempt`
- `max_attempts`
- `retry_index`

## 验证

静态验证：

```bash
venv/bin/python -m py_compile src/backend/tasks/report_top10_workflow.py
```

运行态验证：

- 已通过 Flask Celery Monitor API 重启 `report_top10_workflow` worker。
- worker 状态：
  - `running=true`
  - `pids=[58404]`
  - `queue_length=0`
  - `active_count=0`
  - `reserved_count=0`
  - `scheduled_count=0`
- 启动日志确认：

```text
Connected to redis://...
report_top10_workflow@Mac-mini-3.local ready.
```

## 复发预防

- 任何新父编排复用浏览器子任务时，必须同时复用外层韧性策略：可重试错误识别、尝试记录、失败后新建子任务、父任务恢复复用已完成结果。
- 不能只复用底层 worker 就认为行为一致；父编排层的重试、恢复、子任务生命周期也是复用边界的一部分。
- 用户看到“同一个底层 worker 在 A 编排能恢复、B 编排不能恢复”时，应优先检查父编排策略是否一致。

## 后续事项

- 可以把 `report_audio_workflow` 和 `report_top10_workflow` 的 Gemini retry helper 进一步抽到共享模块，减少策略漂移。
- Top10 页面后续可展示 `child_attempts/retry_state`，让用户直观看到自动重试轨迹。
