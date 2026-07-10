# 2026-07-10 研报音频 Gemini 等待续跑读取 detached 子任务 ID

## 基本信息

- 状态：已修复
- 严重级别：高
- 影响模块：`report_pdf_audio_workflow` / Gemini 子任务等待与续跑 / SQLAlchemy session
- 首次发现：2026-07-10，任务 `c8cd785d-966f-4e25-a716-4f052b009dee`
- 修复提交：待提交

## 现象

父编排在 Gemini 子任务运行到 PDF 上传阶段后，以 20% 进度失败：

```text
Instance <PublicationTask ...> is not bound to a Session; attribute refresh operation cannot proceed
```

关联 Gemini 子任务 `c9a83231-1e7e-478b-aa8e-5ce1fac6a39b` 随后正常完成，说明浏览器解析本身没有失败，父任务错误属于状态收口误报。

## 影响

- Gemini 子任务正常执行，但父编排在等待转为延迟续跑时被错误标记为失败。
- 已完成的 Gemini 结果不能自动进入结构化、朗读和关键帧阶段。
- 用户可能重复投递同一 PDF，造成额外浏览器任务和人工判断成本。

## 根因

父编排的 `_wait_child()` 会在轮询间隔释放数据库 session。本次轮询重新查询父任务时，PostgreSQL 连接发生瞬断并抛出 `OperationalError: server closed the connection unexpectedly`。

`_run_gemini_task_with_retries()` 把这个本应交给 Celery 延迟重试的数据库异常当作 Gemini 业务失败处理，又访问进入等待前保存的旧 ORM 对象 `gemini_task.id`。此时对象已被 `session.remove()` 分离；即便只是读取 ID，也会触发 SQLAlchemy refresh，从而用 detached 异常覆盖真正的数据库瞬断。更危险的是，如果第二次查询碰巧成功，旧逻辑还可能撤销仍在正常执行的 Gemini 子任务。

该问题在 6 月的 detached session 事故中已经形成“跨等待只保存 ID 和 snapshot”的纪律，但 Gemini 重试包装层没有完成同样的收口，属于既有经验未完整落实。

## 解决思路

在进入等待前，将子任务身份和所需状态固化为普通字符串 ID 与 dict snapshot。等待结束、超时或异常后，只按 ID 重新查询数据库；查询失败时只使用等待前快照，不回退读取旧 ORM 对象。

## 实际方案

- Gemini 子任务创建后立即保存 `gemini_task_id` 和 `gemini_task_snapshot`。
- `_wait_child()` 只接收稳定字符串 ID。
- `ChildTaskStillRunning` 与普通异常分支按保存的 ID 重新加载，不再访问旧 `gemini_task.id`。
- 数据库瞬断从业务异常处理中直接重新抛出，交给外层 Celery DB 重试策略，禁止撤销或标记 Gemini 子任务失败。
- 父任务复用已完成 Gemini 时按 ID 重新加载完整结果，并以等待前 snapshot 作为只读兜底。
- Gemini 结果、状态和 publisher 状态判断改用 snapshot helper，减少 commit 后隐式刷新。
- 新增回归测试，模拟等待函数释放 session 后旧对象任何属性访问都会报错，确认父流程仍保留 `ChildTaskStillRunning`。

## 验证

- Python 编译检查通过。
- Gemini detached 续跑回归测试通过。
- 数据库瞬断不撤销、不标记 Gemini 子任务失败的回归测试通过。
- 失败任务续跑后应直接复用已完成 Gemini 子任务，不重复上传 PDF，并继续后续阶段。

## 复发预防

- 跨 `_wait_child()` 边界只允许保存 ID 和 snapshot，异常处理也不例外。
- 禁止使用 `重新加载失败则回退旧 ORM 对象` 的写法；应回退普通 dict snapshot。
- `is_retryable_db_error()` 命中的异常必须进入 Celery DB 重试，不能转换成子任务业务失败。
- 子任务等待测试必须覆盖完成、失败、取消和 `ChildTaskStillRunning` 四条路径。
- 继续按 [数据库 Session 生命周期纪律](../rules/database-session-lifecycle-discipline.md) 审查所有父编排。

## 后续事项

- 对 Top10 等其他父编排的等待包装层进行同类静态审计，逐步统一为 ID + snapshot 模式。
