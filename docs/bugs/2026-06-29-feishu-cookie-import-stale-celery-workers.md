# 2026-06-29 飞书 Cookie 函数已存在但旧 Celery worker 仍导入失败

## 基本信息

- 状态：2026-08-11 再次打开，RUNTIME_REPLACED / OBSERVING
- 严重级别：高
- 影响模块：Celery worker / report audio workflow / publisher render / external documents / platform credentials
- 首次发现：2026-06-28 发布任务报 `resolve_feishu_cookie` ImportError，2026-06-29 研报音频编排再次复现
- 修复提交：`70aee3c feat: harden workflows and zsxq integrations`
- 根因分类：运行时拓扑与代码重载
- 复发键：RUNTIME-TOPOLOGY-RELOAD
- 是否复发：第 2 次已确认复发
- 防线层级：shared_guardrail / contract_test / observability

## 现象

任务启动时调用 `create_app()`，注册 API 蓝图会加载 `apis.v1.external_documents`，再加载 `services.external_document_service`。该服务从 `services.platform_credential_service` 导入 `resolve_feishu_cookie`，旧 worker 内报错：

```text
ImportError: cannot import name 'resolve_feishu_cookie' from 'services.platform_credential_service'
```

已确认磁盘代码中 `resolve_feishu_cookie` 存在，新的 Python 进程也能正常导入：

```text
resolve_feishu_cookie True
external_document_service True
```

但以下长驻 worker 仍使用旧模块缓存：

- 2026-06-28：`render` / `zsxq_publish` 相关发布任务报错。
- 2026-06-29：`report_audio_workflow` worker 继续报同一 ImportError。

## 影响

- `tasks.publisher.run_publication_pipeline` 消息被旧 worker 消耗后，DB 任务仍停在 `pending/init`，不会自动继续。
- `tasks.report_audio_workflow.process_job` 外层启动失败，任务返回 `False`，DB 任务停在 `pending/queued`。
- 用户看到任务不动或被动等待，但实际 Celery 消息已经被旧 worker 消耗。
- 因为错误发生在 `create_app()` 阶段，业务内部失败处理和常规结果持久化可能还没有完全接管。

## 根因

飞书外链功能新增了 `external_document_service`，它依赖 `platform_credential_service.resolve_feishu_cookie`。代码和提交已经包含该函数，但部分 Celery worker 是代码变更前启动的长驻进程。

Python worker 进程已加载旧版 `services.platform_credential_service` 到 `sys.modules`。后续任务在同一进程内调用 `create_app()` 注册新蓝图时，仍从旧模块对象导入，因此出现“磁盘文件有函数，但 worker 导入不到”的错觉。

这不是代码未提交，也不是 PYTHONPATH 指错，而是部署/本地热更新后没有滚动重启所有受影响 worker。

## 解决思路

- 先在全新 Python 进程里验证磁盘代码和导入链，确认不是代码缺失。
- 定位正在运行的旧 worker 进程和队列。
- 只重启实际报错或可能消费该队列的 worker，避免误伤其它长任务。
- 对已经被旧 worker 消耗的 Celery 消息，按原任务 ID 原地重投。
- 记录新 Celery ID，确认任务进入新 worker 并推进。

## 实际方案

代码防线：

- `external_document_service` 不再在模块导入期直接 `from services.platform_credential_service import resolve_feishu_cookie`。
- 改为导入 `platform_credential_service` 模块，并在真正执行飞书抓取时通过 `_resolve_feishu_cookie()` 懒解析。
- 如果旧 worker 里凭证模块仍缺少该函数，只让飞书抓取动作返回明确错误：

```text
飞书凭据解析函数未加载。当前 Celery worker 可能仍缓存旧代码，请滚动重启 worker 后重试。
```

- 这样 `create_app()` 注册 API 蓝图时不会因为飞书凭证函数缺失而拖垮无关的 publisher / report audio workflow 任务。

2026-06-28 现场处置：

- 重启 `zsxq_publish` worker。
- 重启真正消费 `run_publication_pipeline` 的 `render` worker。
- 原地重投发布任务：
  - `PublicationTask`: `b86552ff-edea-4ee8-ae59-c7ebb5cca160`
  - 新 Celery ID: `5bab236f-1807-49b9-a3d1-08a646e3c17c`
  - Redis `render` 队列确认消息存在。

2026-06-29 现场处置：

- 确认当前磁盘代码可导入 `resolve_feishu_cookie` 和 `external_document_service`。
- 停止旧 `report_audio_workflow` worker：
  - 旧 PID: `29876`
- 启动新 `report_audio_workflow` worker：
  - 新 worker PID: `22363`
  - 日志显示 `report_audio_workflow@... ready`
- 原地重投研报音频编排任务：
  - `PublicationTask`: `89fee11c-e340-4e66-bfd8-40b2e8da0611`
  - 旧 Celery ID: `c8482764-45b5-44da-a201-a39a3c764b87`
  - 新 Celery ID: `3a84a799-ec64-462e-aded-e844c43be51d`

## 验证

- 新 Python 进程导入验证通过：

```text
resolve_feishu_cookie True
external_document_service True
```

- 新增单元测试覆盖缺少 `resolve_feishu_cookie` 时只在懒解析阶段失败：

```text
PYTHONPATH=src/backend venv/bin/python -m unittest tests.unit.test_external_document_service
```

- 新 `report_audio_workflow` worker 日志显示收到重投任务：

```text
Task tasks.report_audio_workflow.process_job[3a84a799-ec64-462e-aded-e844c43be51d] received
```

- DB 状态确认该任务已进入业务处理，不再停在启动导入阶段：

```text
89fee11c-e340-4e66-bfd8-40b2e8da0611
status=processing
step=reuse_script_materia
progress=58
error=None
```

## 复发预防

- 修改 `app.create_app()` 注册路径、全局 API 蓝图、共享 service 导入、credential service 这类会被所有 worker 间接加载的模块后，必须滚动重启所有相关 Celery worker。
- 本地或生产发布后不能只重启 Flask 后端；至少要覆盖会调用 `create_app()` 的 worker：
  - `render`
  - `report_audio_workflow`
  - `zsxq_publish`
  - `zsxq_workflow`
  - `zsxq_sync`
  - 其它 report workflow worker
- 当看到“磁盘代码存在但 worker ImportError”时，优先排查旧 worker 进程启动时间和模块缓存，而不是重复修改代码。
- 对被旧 worker 消耗的消息，需要检查 DB 任务状态和 Redis 队列可见性，必要时原地重投同一业务任务。

## 后续事项

- 将“共享导入变更后滚动重启 worker”纳入发布检查清单。
- 考虑让后端自举或 watchdog 在代码版本变化后提示 stale worker，而不是等业务任务触发 ImportError。

## 2026-08-11 复发

`mp3_video_workflow` worker 在 2026-08-10 16:38 启动，早于 18:05 加入 `DEFAULT_INITIAL_SCRIPT_MIN_CHARS` 的共享服务变更。2026-08-11 03:28，该旧 worker 连续消费 5 条消息；任务内 `create_app()` 从磁盘加载新 `api.report_audio_workflow`，但 `services.report_audio_workflow` 仍是进程内旧模块，因而连续报：

```text
ImportError: cannot import name 'DEFAULT_INITIAL_SCRIPT_MIN_CHARS'
```

失败 Celery id：

- `2c2eee63-6f78-44dc-8669-f9c261302d26`
- `e2613f08-7c13-4fb0-b198-04889bc2d12d`
- `37ccdda6-a66e-4821-9aaa-36d61a94c741`
- `d8dd7f6b-2a8c-4461-9f35-d6236e511ac5`
- `b525e322-dc7d-424d-8956-1d2c09a91a7b`

全新 Python 进程可正常导入该常量，进一步排除了磁盘缺代码。故障时段创建的 5 条 MP3 业务任务随后均由新 worker/自动重试推进到 `completed/done/100%`，没有遗留孤儿；但成功重试不能抵消这次运行时混版复发。

本轮按安全门禁执行完整重启以替换全部 worker。下一轮必须确认所有 worker 的启动时间晚于共享导入变更，且 `DEFAULT_INITIAL_SCRIPT_MIN_CHARS` ImportError 为 0；发布门禁仍需补充“共享模块变更必须完整滚动重启”和 worker 代码版本指纹，完成前保持 `OBSERVING`。

## 2026-08-13 再次复发

固定窗口出现 3 次 `public_browser_human_intervention_notification` ImportError：MP3 主任务 1 次，report-audio 的 Gemini/豆包子任务失败传播 2 次。磁盘代码和相关单测中该函数均已存在，失败 worker 的启动时间早于共享通知服务变更，根因仍是运行时模块缓存混版，不是函数缺失。

本轮完整 unit 1154 项通过后，两次重启安全门均确认无不可中断任务，随后完整重启 backend、17 个 worker、watchdog 和调度器。新 MP3、report-audio、workflow_notify worker 分别于 06:15:40、06:15:37、06:15:42 ready；新通知 worker 又完成 3 个真实通知。由于尚未有一条新 MP3/report-audio 业务任务再次走到该导入调用链，状态保持 `RUNTIME_REPLACED/OBSERVING`，下一窗口必须确认同符号 ImportError=0 且真实相关任务成功后才可关闭。

## 2026-08-14 传播失败仍复发

固定窗口内 report-audio 父任务在 10:32 和 10:34 两次因豆包子任务保存的同一 `public_browser_human_intervention_notification` ImportError 失败，Celery id 分别为 `1d5d56cb-6dbf-4a91-aac0-469b291ed3ee` 和 `fe3d3360-9900-48e1-ab4a-5c1cce92de88`，耗时 `20.163s`、`23.111s`。这两条是父任务传播既有子任务失败，尚不能证明新 worker 又在同一符号处首次导入失败；但用户可见调用链确实再次失败，故不能按“新日志未直接捕获”关闭。

下一轮需同时区分“新 worker 新 ImportError”与“历史 failed child 被父任务重复传播”，并验收失败子任务的可恢复/重建策略；在真实相关任务成功且整个窗口同符号新 ImportError=0 前保持 OPEN/OBSERVING。
