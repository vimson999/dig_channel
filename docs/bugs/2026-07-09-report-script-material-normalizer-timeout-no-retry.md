# 2026-07-09 Report Script Material 结构化器超时且无重试

## 基本信息

- 状态：已修复，已用 2 个历史失败/卡住任务验证完成
- 严重级别：高
- 影响模块：report_script_material_workflow / DeepSeek normalizer / Gemini 子任务续跑
- 首次发现：2026-07-09 基础文案列表连续出现 DeepSeek Read timed out、Gemini ERR_CONNECTION_CLOSED
- 修复提交：待提交

## 现象

基础文案物料任务近期大量失败，页面常见错误包括：

```text
结构化器不可用或返回不可用结果: HTTPSConnectionPool(host='api.deepseek.com', port=443): Read timed out. 已禁止 local_fallback 产出最终文案。
Gemini 子任务失败: Page.goto: net::ERR_CONNECTION_CLOSED
```

排查最近 40 个 `report_script_material_workflow` 任务时，失败 39 个，其中多数失败在 `normalize_result` 附近。

## 影响

- PDF 已完成 Gemini 解析后，最终基础文案仍无法产出。
- 已禁止 `local_fallback` 后，系统不再产出垃圾文案，这是正确防线，但没有配套重试导致失败率飙升。
- 用户在页面上看到“获取基础文案几乎没成功过”，且需要人工判断是否可以重试。
- Gemini 已经成功的任务如果不复用结果，会重复消耗浏览器自动化和外部模型资源。

## 根因

代码层根因：

1. `report_script_material_workflow` 的 DeepSeek 结构化器默认超时只有 20 秒，不足以处理 Gemini 返回的长篇研报文案。
2. DeepSeek 结构化调用没有本编排内的自动重试，单次网络抖动、限流或慢响应会直接让父任务失败。
3. `local_fallback` 已被禁止用于最终文案，但旧策略没有同步升级为“可重试、可续跑、可观察”的生产策略。
4. 个别任务还存在 Gemini 浏览器层连接关闭或子任务陈旧处理中的失败，但这属于另一个上游不稳定面；本次主失败集中在结构化器。

架构层根因：

- 外部 AI 调用不能只靠单次请求成功率，必须具备超时预算、有限重试、状态记录和失败后续跑能力。
- 禁止低质量兜底是必要的质量控制，但禁用兜底后必须补上可靠性机制，否则会把“垃圾成功”变成“大量失败”。
- 父任务应该复用已完成的 Gemini 子任务结果，只重跑失败阶段，避免重复花费和重复踩浏览器问题。

## 解决思路

- 保持 `local_fallback` 禁止状态，不恢复不可用的垃圾兜底。
- 将结构化器超时从短超时调到更符合业务长度的默认值。
- 对可恢复错误增加有限重试，覆盖 timeout、429、5xx 和连接类错误。
- 每次结构化尝试写入 `normalizer_attempts`，让页面和排查脚本能看到到底重试了几次。
- 结构化外部调用前释放 DB session，避免外部等待期间占用数据库连接。
- 复用已完成 Gemini 子任务，重跑时只补结构化阶段。

## 实际方案

在 `src/backend/tasks/report_script_material_workflow.py` 中：

- `DEFAULT_NORMALIZER_TIMEOUT_SECONDS` 从 20 秒调整为 90 秒。
- 新增 `REPORT_SCRIPT_MATERIAL_NORMALIZER_RETRY_LIMIT`，默认 2 次重试。
- 新增 `REPORT_SCRIPT_MATERIAL_NORMALIZER_RETRY_DELAY_SECONDS`，默认 15 秒。
- 新增 `_normalize_for_tts_with_retries()`，统一处理结构化器调用、重试和状态记录。
- 新增 `normalizer_attempts` 结果字段，记录 attempt、max_attempts、status、error、updated_at。
- 结构化器调用前执行 `release_db_session_quietly()`，避免长外部调用持有数据库连接。
- 重跑验证时复用已有 Gemini 完成结果；陈旧 Gemini 子任务则标记失败并让父任务创建新子任务。

## 验证

静态验证：

```text
venv/bin/python -m py_compile src/backend/tasks/report_script_material_workflow.py
```

运行态验证：

```text
eb76b06f-c24b-4933-9857-bf66b901b525
status=completed step=done progress=100
normalizer_attempts=[attempt=1 status=completed max_attempts=3]
chars=1926

2db4b4b4-2fbc-437a-be7a-4e5271709084
status=completed step=done progress=100
normalizer_attempts=[attempt=1 status=completed max_attempts=3]
chars=2334
```

后台 worker 已重新启动：

```text
report_script_material_workflow@Mac-mini-3.local ready
```

## 复发预防

- 禁止把不可用 `local_fallback` 当成功产物；质量不达标的兜底只能用于调试或明确标记的草稿，不能进入最终基础文案。
- 所有外部 AI 结构化调用必须有业务级超时、有限重试、错误分类和可观测 attempt 记录。
- 失败任务续跑必须优先复用已完成子任务结果，不能从头重新消耗 Gemini。
- worker 修复后必须用真实历史失败任务验证到终态，而不是只看代码编译通过。
- 后续同类编排新增 DeepSeek/Gemini 阶段时，应先检查是否复用了本次的“外部调用重试 + DB session 释放 + 子任务续跑复用”模式。

## 后续事项

- 批量筛选最近失败任务中 Gemini 已完成的记录，提供一键续跑，只补 DeepSeek 结构化阶段。
- 将 `normalizer_attempts` 展示到基础文案页面详情里，减少只能查 DB 的排障成本。
- 继续单独治理 Gemini `ERR_CONNECTION_CLOSED`：这是浏览器自动化/网络侧问题，不应和 DeepSeek 结构化失败混为一个故障。
