# MP3 双封皮标题版本被格式标题覆盖

## 基本信息

- 状态：代码已修复并完成确定性回归，待新任务运行态验收
- 严重级别：P1
- 影响模块：mp3_video_workflow / DS 标题规划 / 横屏封皮
- 首次发现：2026-07-19，任务 `f5b0505a-7ae3-4476-9726-cef883706b16` 的两种横屏封皮出现相同黄色标题文案
- 修复提交：待提交
- 根因分类：AI/媒体产物语义校验
- 复发键：OUTPUT-SEMANTIC-VALIDATION
- 是否复发：第 N 次
- 关联记录：[MP3 手动 DS 返回值不完整仍生成垃圾封面和视频](2026-07-07-mp3-invalid-ds-response-generated-garbage-assets.md)、[MP3 竖屏信息墙误用 AI 寒暄与任务说明](2026-07-15-mp3-infographic-used-ai-preamble.md)
- 防线层级：shared_guardrail / contract_test

## 现象

详情页中的“此刻此声-横屏视频封皮 1”和“小君君-横屏视频封皮”多次显示相同的黄色标题。提示词原本要求两个独立版本，但最终 plan 未保留该差异。

## 影响

- 两个封皮失去 A/B 文案价值，无法提供不同点击切入点。
- 页面和任务均正常完成，属于用户可见的静默产物错误。
- 旧规划版本会被复用，重复执行可能继续沿用错误文案。

## 根因

`validate_plan` 在读取 DS 专门返回的 `xiaojunjun_subtitle` 之前，优先从第 6 条斜杠版完整视频标题生成小君君高亮文案。第 6、7 条通常只是分隔符和格式不同，内容可能一致，因此格式化结果会覆盖 AI 已生成的第二创意版本。该优先分支也没有与 `finance_cover_subtitle` 做重复校验。

## 解决思路

将“两个封皮是两个独立创意版本”提升为生成和落库前共同执行的不变量：AI 专用字段优先；每个候选都必须与第一封皮及竖屏标题去重；格式化完整视频标题只能作为后置兜底。手动 DS/本地兜底也分别使用标题版本一和版本二。

## 实际方案

- 强化 DS Prompt：第一封皮必须恰好 3 行、每行约 10-13 个视觉字符；小君君文案必须采用不同切入点，禁止只换标点、换行或顺序。
- 新增统一的候选去重选择器，优先保留 DS 的独立 `xiaojunjun_subtitle`，重复时依次选择第 6 条和版本二兜底。
- 手动 DS 和本地 fallback 不再默认用版本一同时生成两张封皮。
- Planner 增加 `DEEPSEEK_FLASH_MODEL` 专用配置优先级，默认仍为 `deepseek-v4-flash`。
- 提升 planner/视觉资产版本，防止后续续跑复用旧规划和旧封皮。

## 验证

- `PYTHONPATH=src/backend venv/bin/python -m pytest tests/unit/test_mp3_video_workflow_service.py -q`
- 结果：`76 passed`。
- 新增覆盖：第 6/7 条内容相同时仍优先使用 DS 第二创意；DS 第二创意自身重复时拒绝并采用独立候选；Flash 专用模型配置优先级。
- `git diff --check -- src/backend/services/mp3_video_workflow.py tests/unit/test_mp3_video_workflow_service.py`
- 当前本地后端与前端服务未运行，历史任务图片未做运行态重生成。

## 复发预防

- 双封皮文案差异必须作为 plan 校验契约，而不能只写在 Prompt 中。
- 完整发布标题和视觉封皮短文案分开建模，禁止格式化发布标题无条件覆盖专用视觉字段。
- 变更标题规划或视觉模板时同步提升版本，避免历史错误缓存继续被复用。

## 后续事项

- 新 worker 启动后，用新任务验证两张封皮文案、三行约束和实际 DS 模型；历史任务需要重新规划并重生成封皮才会改变。
