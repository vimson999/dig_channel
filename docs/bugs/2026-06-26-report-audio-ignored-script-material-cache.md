# 2026-06-26 研报转语音忽略基础文案缓存导致重复跑 Gemini

## 基本信息

- 状态：已修复，已重启后端与 `report_audio_workflow` worker 生效
- 严重级别：中高
- 影响模块：`report_audio_workflow`、`report_script_material_workflow`、`report_pdf_gemini`、研报转语音测试页
- 首次发现：用户在同一份研报已显示“基础文案 成功”后，再投递研报转语音编排，页面仍显示正在打开 Gemini 获取文案
- 修复提交：待提交

## 现象

同一份 PDF：

- 基础文案物料台显示已生成基础文案。
- 研报转语音编排页仍创建 `report_pdf_gemini` 子任务，并显示 Gemini 阶段正在运行。
- 编排列表标题一度显示 `#外资研报`，而不是 PDF 文件名或结构化标题。

真实样例：

```text
PDF attachment_id: 2d3d893c-e9b0-4e30-a59b-18d58700cf28
已有基础文案任务: c1ad123f-c976-449c-82a3-2c40fc251f1a
基础文案 feature: report_script_material_workflow
基础文案字数: 3436
重复创建的 Gemini 子任务: 2ef19763-ad4a-4a33-95e9-4c23ed66a346
```

## 影响

- 已经生成过的单篇研报文案没有被复用，重复消耗 Gemini 浏览器自动化容量。
- 增加 Gemini 验证码、安全验证、页面状态互抢等风险。
- 同一 PDF 在不同编排里表现不一致，用户无法判断系统到底有没有缓存命中。
- 列表标题显示泛化标签，降低任务定位效率。

## 根因

基础文案物料任务自身已经有复用能力：

- `report_script_material_workflow` 会按 `attachment_id` 查历史完成任务。
- `report_top10_workflow` 也会读取历史单篇基础文案。

但完整研报转语音链路缺少同一层复用：

- `report_audio_workflow` 只检查当前父任务已有 `structured_result` 或当前父任务下已完成 Gemini 子任务。
- 它没有在创建 Gemini 子任务前查询 `report_script_material_workflow` 的已完成基础文案。
- 因此同一 PDF 即使已经有基础物料，完整编排仍会重新投 Gemini。

标题问题的根因是：

- 知识星球 topic 标题可能只是 `#外资研报`。
- 投影和接口标题候选里没有把这类泛化标题降级。
- 当结构化标题还没生成时，列表就可能显示 `#外资研报`。

## 解决思路

把“基础文案物料”明确作为完整研报转语音的上游缓存，而不是只服务 Top10 或物料台。

原则：

- 同一 `attachment_id` 已经有可用 `structured_result.final_script_for_tts` 时，完整编排必须先复用。
- 只有缓存不存在或缓存质量不合格时，才创建 Gemini 子任务。
- 页面要区分“复用基础文案”和“正在跑 Gemini”，避免误导用户。
- 泛化 topic 标题只作为最后兜底，不覆盖 PDF 文件名和结构化标题。

## 实际方案

后端任务：

- 在 `src/backend/tasks/report_audio_workflow.py` 中新增 `_reusable_material_for_pdf()`。
- 支持从以下 feature 查可复用结构化文案：
  - `report_script_material_workflow`
  - `report_audio_workflow`
  - `report_pdf_audio_workflow`
- 在创建 Gemini 子任务前，先按 `attachment_id` 查已完成基础文案。
- 命中后写入 `result.script_material`，直接进入豆包朗读和后续视频阶段。
- 查询从单个 `OR` 查询改成两个索引查询，避免真实库慢查询。

后端 API / 投影：

- `src/backend/api/report_audio_workflow.py` 返回 `script_material`。
- 任务详情标题优先级调整为结构化标题、视频标题、metadata 标题、PDF 文件名、topic 标题。
- `#外资研报`、`研报`、`PDF` 这类泛化标题被降级为最后兜底。
- `src/backend/models/task.py` 同步投影标题规则，任务刚创建时也不会优先显示泛化 topic。

前端：

- `src/frontend/js/report-audio-workflow-test.js` 保留轮询合并中的 `script_material`。
- 如果任务复用了基础文案，第一阶段卡片显示“基础文案”，而不是“Gemini”。
- 标题候选加入 PDF 文件名，并过滤泛化标题。
- `src/frontend/report-audio-workflow-test.html` 更新 JS 版本号，避免浏览器缓存旧脚本。

## 验证

单测：

```bash
PYTHONPATH=src/backend venv/bin/python -m unittest tests.unit.test_report_audio_workflow_task tests.unit.test_report_audio_workflow_api tests.unit.test_task_projection_models
```

结果：

```text
Ran 29 tests in 0.172s
OK
```

语法检查：

```bash
venv/bin/python -m py_compile src/backend/tasks/report_audio_workflow.py src/backend/api/report_audio_workflow.py src/backend/models/task.py
node --check src/frontend/js/report-audio-workflow-test.js
```

真实库验证：

```text
2d3d893c-e9b0-4e30-a59b-18d58700cf28
-> c1ad123f-c976-449c-82a3-2c40fc251f1a
-> report_script_material_workflow
```

查询拆分后同一缓存命中从慢查询秒级降到几十毫秒级。

运行态验证：

```text
report_pdf_gemini queue 0 business 0 active 0
report_audio_workflow queue 0 business 1 active 0
report_script_material_workflow queue 0 business 0 active 0
```

`report_audio_workflow` worker 已重启，新逻辑已生效。已有任务 `4f127b46...` 是修复前已经创建过 Gemini 子任务，不能回滚历史子任务；后续同一 PDF 新投递会先走基础文案缓存。

## 复发预防

- 任何新编排如果消费 PDF 单篇文案，必须先查基础文案物料缓存，不能直接创建 Gemini 子任务。
- “基础文案物料”应被视为可复用生产物料，而不是某个页面的私有结果。
- 新父编排复用已有 worker 时，必须同步复用缓存、幂等、重试、续跑和可观测性策略，不能只复用底层 Celery task。
- 页面必须区分“复用缓存”和“正在外部生成”，否则用户会误判系统重复执行。
- 泛化 topic/tag 标题不得优先作为任务标题；标题候选必须优先业务标题、结构化标题或文件名。

## 后续事项

- 可以抽一个共享 helper，统一给 Top10、研报转语音、基础物料台读取同一份单篇文案缓存。
- 可以在投递 API 返回 `cache_hit=true/false`，让页面在任务刚创建时就明确告诉用户是否会跳过 Gemini。
