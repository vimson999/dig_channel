# 2026-07-13 基础文案任务缺少 Gemini 阈值函数导致批量失败

## 基本信息

- 状态：已修复
- 严重级别：P1
- 影响模块：研报基础文案物料 / Gemini 缓存复用 / 自动编排
- 首次发现：2026-07-13
- 修复提交：待提交
- 根因分类：跨模块投影优化遗漏运行时依赖
- 复发键：`RUNTIME-TOPOLOGY-RELOAD`
- 是否复发：首次
- 关联记录：`2026-07-10-report-audio-tts-cache-full-json-scan.md`
- 防线层级：local_patch / contract_test / observability

## 现象

多条研报基础文案任务创建后立即在 2% 失败，页面统一显示 `name '_min_gemini_final_chars' is not defined`。PDF 文件大小、机构和内容不同，但失败阶段与错误完全一致。

## 影响

- 未命中已记录 Gemini 子任务 ID 的基础文案任务无法查询可复用 Gemini 结果。
- 同批 PDF 全部在进入 Gemini 前失败。
- 自动调度持续创建失败任务，形成批量错误记录。

## 根因

Gemini 缓存查询改用 `gemini_response_chars` 投影列时，基础文案模块新增了 `_min_gemini_final_chars()` 调用。该函数只存在于研报转语音模块，基础文案模块既未导入，也未定义。原测试只覆盖直接复用已完成基础文案的路径，没有执行 Gemini 缓存候选查询分支。

## 解决思路

基础文案与研报转语音使用同一组环境变量和默认阈值语义，并为默认值、环境覆盖和下限建立直接契约测试。

## 实际方案

- 在基础文案任务模块新增 `_min_gemini_final_chars()`。
- 优先读取 `REPORT_AUDIO_GEMINI_MIN_FINAL_CHARS`，回退到 `REPORT_PDF_GEMINI_MIN_RESPONSE_CHARS`，默认 120。
- 阈值最低限制为 0，与研报转语音保持一致。
- 新增直接执行 helper 的回归测试。

## 验证

- 基础文案任务单元测试通过。
- 模块内所有 `_min_gemini_final_chars` 引用均有对应定义。
- 失败任务可在 worker 重载后从原任务续跑，无需重新上传 PDF。

## 复发预防

- 跨工作流复制投影查询时，必须同时检查阈值、状态和 helper 依赖。
- 新增符号引用必须至少有一条直接执行该分支或 helper 的测试。
- 多任务在相同进度出现同一 `NameError` 时，应归类为共享代码回归。

## 后续事项

- 后续将 Gemini 缓存阈值下沉到共享配置模块，去除重复实现。
