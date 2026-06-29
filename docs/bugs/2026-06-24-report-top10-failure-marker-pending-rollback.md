# 2026-06-24 Top10 总编辑失败后父任务因 PendingRollbackError 未收口

## 基本信息

- 状态：已修复，`report_top10_workflow` worker 已重启生效
- 严重级别：高
- 影响模块：`report_top10_workflow`、数据库 session 失败收口
- 首次发现：今日研报 Top10 编排长期停在 `editor_gemini_waiting / 74%`
- 修复提交：待提交

## 现象

任务 `95abe64a-9560-4ecc-9567-d5c47ad3b70e` 页面显示仍在运行：

```text
status=processing
current_step=editor_gemini_waiting
progress=74
```

但数据库和子任务实际状态显示：

```text
source_reports=10
editor_input_chars=30863
final_script_len=0
```

总编辑 Gemini 子任务已失败两次：

```text
2026-06-24 14:25:42 等待 Gemini 回复超时
2026-06-24 14:39:46 等待 Gemini 回复超时
```

父任务没有被标记为失败，页面误显示为“运行中”。

## 影响

- 用户无法判断任务到底还在执行还是已经失败。
- 任务卡在伪运行状态后，后续续跑入口和监控判断都会被误导。
- DB session 处于失败事务状态时继续查询，放大为 `PendingRollbackError`，掩盖了原始业务错误。

## 根因

Top10 父任务在总编辑 Gem 子任务失败后进入失败收口逻辑，但 `_mark_failed_safely()` 没有先执行 `db.session.rollback()`。

如果前序 `commit/flush` 已经因为连接失效或事务错误进入失败状态，SQLAlchemy 要求先 rollback。旧代码直接 `_load_task()`，触发：

```text
sqlalchemy.exc.PendingRollbackError:
This Session's transaction has been rolled back due to a previous exception during flush.
To begin a new transaction with this Session, first issue Session.rollback().
```

更严重的是，异常发生在“失败收口”的兜底路径里，所以父任务没有被写成失败，形成伪运行。

同类模块 `report_audio_workflow` 和 `mp3_video_workflow` 已经采用“失败收口先 rollback，再重载任务”的模式；Top10 新模块没有复用这个经验。

## 解决思路

失败收口函数必须假定上游 DB session 已经不干净：

1. 进入失败收口前先 rollback。
2. 如果 rollback 本身失败，remove 当前 scoped session。
3. 再用干净 session 重载任务。
4. 失败状态写入失败时再次 rollback/remove，避免二次污染。

## 实际方案

`src/backend/tasks/report_top10_workflow.py`：

- `process_report_top10_workflow_job()` 捕获异常后先调用 `_reset_session_for_failure_marker()`。
- `_mark_failed_safely()` 开始时也调用 `_reset_session_for_failure_marker()`。
- 新增 `_reset_session_for_failure_marker()`：
  - 优先 `db.session.rollback()`
  - rollback 失败时 `db.session.remove()`
  - 避免坏事务继续污染失败收口路径

测试：

- 新增 `tests/unit/test_report_top10_workflow_task.py`
- 覆盖：
  - `_mark_failed_safely()` 必须先 rollback 再 load task
  - rollback 失败时必须 remove session，并继续尝试标记失败

现场处理：

- 已将任务 `95abe64a-9560-4ecc-9567-d5c47ad3b70e` 从伪运行修正为失败。
- 已重启 `report_top10_workflow` worker，使修复生效。

## 验证

单元测试：

```bash
PYTHONPATH=src/backend venv/bin/python -m unittest tests.unit.test_report_top10_workflow_task tests.unit.test_report_top10_workflow_service
```

结果：

```text
Ran 4 tests ... OK
```

语法检查：

```bash
venv/bin/python -m py_compile src/backend/tasks/report_top10_workflow.py
```

运行态：

```text
report_top10_workflow@Mac-mini-3.local ready
```

任务状态复查：

```text
status=failed
current_step=failed
progress=74
error=总编辑 Gem 子任务失败并触发父任务失败收口异常；已修复 Top10 失败收口 DB session rollback 逻辑，请从失败处续跑总编辑阶段。
```

## 复发预防

- 所有 Celery 父编排任务的“异常兜底失败收口”必须先 rollback/remove session。
- 新编排模块必须复用成熟模块的失败收口模式，不能重新实现一套弱兜底。
- 所有长编排任务的失败收口都应有单元测试，验证坏 session 下仍能写入终态。
- 页面看到长期 `processing` 时，必须交叉检查子任务终态和父任务 `updated_at`，不能只相信进度条。

## 后续事项

- 将同样的“坏 session 下失败收口”单元测试模板推广到其他新编排模块。
- 优化 Top10 详情接口/内部查询，避免反复加载 `PublicationTask.result` 大 JSON 导致慢查询。
