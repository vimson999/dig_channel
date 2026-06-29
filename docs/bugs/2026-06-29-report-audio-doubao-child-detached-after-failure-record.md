# 2026-06-29 研报音频豆包分段失败记录后访问 detached 子任务

## 基本信息

- 状态：已修复
- 严重级别：高
- 影响模块：`report_pdf_audio_workflow` / 豆包浏览器朗读分段 / SQLAlchemy session
- 首次发现：2026-06-29 研报音频编排任务在 62% 处失败
- 修复提交：本次提交

## 现象

研报音频编排任务中，基础文案已经复用成功，两个豆包浏览器朗读子任务失败后，父任务页面显示：

```text
Instance <PublicationTask ...> is not bound to a Session; attribute refresh operation cannot proceed
```

页面上能看到豆包朗读 1 / 2 已失败，但父任务最终错误不是豆包本身的失败原因，而是 SQLAlchemy detached instance 错误。

## 影响

- 父任务没有保留真实的豆包失败原因，用户只能看到底层 session 错误。
- 失败 attempts / children 写回后，父任务可能无法继续判断是否需要重试、是否已达到重试上限。
- 同一类问题会在多分段豆包朗读失败、验证码阻塞、完成但 WAV 缺失等路径复发。

## 根因

豆包多分段编排在处理失败子任务时，会先调用 `_record_child_attempt()` 写回父任务结果。该函数内部会 `commit()`。

提交后 SQLAlchemy 可能过期或分离当前 ORM 实例；后续代码仍继续读取同一个 `PublicationTask` 对象的 `result`、`error_message`、`status`、`params` 等属性，用来判断验证码、计算下一次 attempt、生成最终错误文案。

一旦 session 已释放或对象 detached，访问这些属性就触发 refresh，最终报：

```text
Instance <PublicationTask ...> is not bound to a Session
```

架构层根因是：长耗时编排和多次 DB 写回路径中，把“子任务状态快照”和“ORM 实例生命周期”混在了一起。

## 解决思路

进入记录函数前先把子任务需要的状态转成普通 dict snapshot。之后父任务的 children、attempts、错误判断、重试上限判断都只读 snapshot，不再依赖原 ORM 对象是否仍绑定 session。

同时让公共 helper 能同时接受 ORM 对象和 snapshot dict，避免后续新增路径又把 ORM 对象传回去。

## 实际方案

- 新增 `_task_state_snapshot()`，集中提取子任务 ID、状态、进度、错误、metadata、audio、params/result。
- 新增 `_task_id()`、`_task_result_payload()`、`_task_params_payload()`、`_task_doubao_metadata()`，让 helper 支持 snapshot。
- `_run_doubao_tts_chunks()` 在复用、活跃、失败、新建、等待完成后都先生成 snapshot，再写 children / attempts。
- `_child_error_message()`、`_is_doubao_captcha_blocked()`、`_task_chunk_attempt()`、`_task_audio_wav_path()` 等 helper 改为读 snapshot 兼容结构。
- `_ordered_doubao_tts_outputs()` 支持 snapshot，避免复用完成任务后再次读取可能已过期的 ORM 对象。

## 验证

- Python 编译检查：

```text
PYTHONPATH=src/backend venv/bin/python -m py_compile src/backend/tasks/report_audio_workflow.py
PYTHONPATH=src/backend venv/bin/python -m py_compile tests/unit/test_report_audio_workflow_task.py
```

- 新增回归测试，模拟“记录失败 attempt 后子任务对象立刻 detached”，确认父流程仍抛出原始豆包失败原因：

```text
PYTHONPATH=src/backend venv/bin/python -m unittest tests.unit.test_report_audio_workflow_task.ReportAudioWorkflowTaskTest
```

- 当前整组 `tests.unit.test_report_audio_workflow_task` 仍被既有 SQLite 建库问题阻塞：`external_documents` 的 `ix_external_documents_tts_status` 索引名重复，测试在 `db.create_all()` 阶段失败，尚未进入本次逻辑。

## 复发预防

- 编排逻辑里，凡是调用会 `commit()` 的记录函数后还要继续判断子任务状态，必须使用 commit 前 snapshot。
- 长耗时父任务不要把 ORM 对象当跨阶段状态载体；跨等待、跨写回阶段只传 ID 或 snapshot。
- 新增子任务 attempts / children 记录逻辑时，优先复用 `_task_state_snapshot()` 和 `_task_public_summary()`。

## 后续事项

- 单独处理 `external_documents` SQLite 测试索引重名问题，恢复整组 report audio workflow 单测。
- 逐步把 Gemini、publisher 视频等待路径也收口为“ID + snapshot”模式，减少其它 commit 后读 ORM 对象的风险。
