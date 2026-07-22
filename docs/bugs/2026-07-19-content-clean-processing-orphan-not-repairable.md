# 内容清洗 processing 孤儿不在自动修复范围

## 基本信息

- 状态：已修复并完成现场恢复与重启验收
- 严重级别：P0
- 影响模块：Content Pipeline / Celery watchdog / 重启安全门 / ZSXQ workflow
- 首次发现：2026-07-19
- 修复提交：未提交
- 根因分类：迁移与进程版本不一致、失败收口失效、孤儿修复矩阵缺项
- 复发键：`ORCH-PARENT-CHILD-STATE`
- 是否复发：同类孤儿任务问题复发
- 关联记录：`2026-06-23-zsxq-workflow-stale-celery-message.md`、`2026-07-17-content-pipeline-progress-json-and-retry-contract.md`、`2026-07-18-full-restart-interrupted-active-render.md`
- 防线层级：shared_guardrail / contract_test / observability

## 现象

PDF 清洗任务 `7b1a0e6a-3bc3-468f-9762-d89f8c298bd2` 长期停留在
`processing / local_cleaning / 38%`，并阻断全栈重启安全门。Celery 中旧消息已经终止，
但数据库任务和所属 ZSXQ workflow 仍显示运行中。

## 影响

- 清洗和后续自动发布无法继续，工作流长期显示运行中。
- 正确的重启安全门会持续阻断，操作者容易被迫选择不安全的强制重启。
- Watchdog 只能修复 pending 清洗孤儿，不能修复已经进入 processing 的同类任务。

## 根因

1. 旧 worker 在数据库迁移完成前加载了包含新投影列的 ORM 模型，任务启动时因
   `publisher_bilibili_cover_path` 缺失而失败。
2. 外层失败收口同样需要加载该模型，因此失败状态也未能写回，留下 processing 孤儿。
3. `REPAIRABLE_FEATURES` 和清洗任务重投目标已经存在，但
   `BUSINESS_PROCESSING_REPAIRABLE_FEATURES` 只登记了 MP3 编排，形成不完整的修复矩阵。

## 解决思路

- 先用运行时结构门禁确认迁移字段、权限、索引和回填全部完成。
- 只有在旧 Celery ID 同时不在 worker 快照和 Redis 队列中时才允许恢复。
- 清洗任务只允许在可重入的准备/本地清洗阶段重排；发布交接阶段保持 fail-closed，
  避免重复发布。
- 保留原业务任务 ID，生成新 Celery ID，并写入 `processing_orphan_repair` 审计。

## 实际方案

- 为 `content_pdf_clean` 增加 `prepare/local_cleaning` processing 孤儿修复规则。
- 为 `content_audio_clean` 增加 `prepare/cleaning/audio_cleaning` processing 孤儿修复规则。
- 明确不允许 `enqueue_publish` 自动重排。
- 现场任务安全重排后完成 10 份 PDF 清洗，并完成后续知识星球发布。
- 业务链路清空后执行完整重启，后端、16 个 Celery 服务、watchdog 和两个 scheduler
  均以新进程启动。

## 验证

- 运行时结构门禁：`20260718_bili_upload_exec` 完整。
- 队列修复、清洗进度、watchdog 和 runtime guard 回归测试：43 项通过。
- 现场清洗任务：`completed / done / 100%`，错误为空。
- 现场发布任务：`completed / done / 100%`，错误为空。
- 重启后 `/health` 返回 healthy，broker 正常，全部托管 worker 为 running。
- 重启后日志未发现缺列、Traceback、statement timeout 或数据库连接错误。

## 复发预防

1. 新增 ORM 列的代码不得在运行时结构门禁通过前加载到 worker。
2. 每个可排队 feature 必须同时评审 pending 与 processing 两张孤儿修复矩阵。
3. 自动重排必须按业务阶段设置白名单，不能把发布、上传等非幂等阶段泛化为可重试。
4. 重启安全门发现长期 processing 时，必须先验证 Celery 所有权并恢复业务，禁止直接强制绕过。
5. 回归测试必须同时覆盖“允许恢复”和“禁止恢复”的阶段边界。

## 后续事项

- 将运行时结构门禁继续保留在完整重启脚本最前面。
- 后续新增 content pipeline 阶段时同步更新并评审 processing 孤儿修复矩阵。
