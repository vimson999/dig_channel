# 2026-07-10 研报音频 TTS 缓存扫描 500 条完整大 JSON

## 基本信息

- 状态：结构化投影已部署，等待完整业务流程验收
- 严重级别：高
- 影响模块：`report_pdf_audio_workflow` / 豆包朗读复用 / PostgreSQL
- 首次发现：2026-07-10，修复 Gemini detached 任务并现场续跑时
- 修复提交：待提交

## 现象

父编排进入豆包朗读阶段后，`_completed_tts_task_for_text()` 每次查找可复用音频都会读取最近 500 条已完成豆包任务的完整 `params/result`。现场两次查询分别耗时约 22 秒和 31.6 秒，同时详情 API 出现 10 秒以上超时。

## 影响

- 单个编排包含多段文案时会重复触发大查询，显著拖慢续跑。
- 远程 PostgreSQL 需要传输大量无关 JSON，增加数据库、网络和 Python 反序列化压力。
- 与页面轮询并发时会放大数据库连接占用和接口超时。

## 根因

缓存查找逻辑需要按 `text_hash` 精确匹配，却先加载 500 个完整 ORM 对象，再在 Python 中逐条读取 metadata 和原文比对。查询条件没有下推到数据库，且加载列包含任务大 JSON。

## 解决思路

第一步让 PostgreSQL 在 JSON metadata 中按 `text_hash` 精确过滤；第二步把哈希、分段序号和 WAV 路径提升为独立投影列，使热查询完全脱离 JSON。

## 实际方案

- 查询条件同时兼容 `result.metadata.text_hash` 和 `params.doubao_browser_tts.metadata.text_hash`。
- 候选上限从“扫描 500 条”改为“最多加载 20 条已匹配记录”。
- 保留 SHA-256 命中后的全文一致性和文件存在性校验。
- 新增 `doubao_tts_text_hash`、`doubao_tts_chunk_index`、`doubao_tts_chunk_attempt`、`doubao_tts_wav_path` 投影列。
- 入库自动同步投影，历史数据已回填；新增哈希完成索引和父任务分段状态索引。

## 验证

- 现场相同哈希查询从约 31.6 秒降到约 1.16 秒。
- 正确命中当前任务及历史同文本音频候选。
- 现有跨任务精确文本音频复用单元测试通过。
- 结构化哈希查询执行计划为 0.017ms，真实链路中位数约 30.75ms。
- 回填 TTS 哈希 192 条、已完成 WAV 路径 516 条；新 worker 的查询 SQL 已不再包含 `params/result`。

## 复发预防

- 缓存查找必须把高选择性键下推到数据库，禁止先批量加载大 JSON 再在 Python 中筛选。
- 热路径新增 JSON 条件时，必须记录实际返回行数和现场耗时。
- `text_hash` 已提升为独立投影列；静态测试禁止该缓存路径重新使用 JSON 条件。

## 后续事项

- 用至少一次完整真实研报流程确认缓存命中、失败重试、WAV 合并和最终通知均保持完整。
