# 2026-06-23 研报音频编排历史列表 15 秒超时

## 现象

`report-audio-workflow-test.html` 页面加载历史时提示：

`加载历史失败：请求超时（15s）`

## 根因

历史列表接口原本已经使用投影列避免读取 `PublicationTask.result` 大 JSON，但为了在列表里带出重做封皮的 `cover_subtitle`，临时把 `result` 又加回了列表查询。

`result` 中可能包含 Gemini 原文、结构化结果、音频/视频阶段状态和子任务摘要。列表一次加载 10 条任务时，会把多条大 JSON 从远端 PostgreSQL 拉回并序列化，导致冷查询接近或超过前端 15 秒超时窗口。

## 修复

1. 新增 `publication_tasks.report_audio_cover_subtitle` 投影列。
2. `PublicationTask.sync_projection_fields()` 同步 `structured_result.cover_subtitle` / `video.cover_subtitle` 到投影列。
3. 历史列表接口改为读取 `report_audio_cover_subtitle`，不再查询 `PublicationTask.result`。
4. 列表摘要去掉重复的 `children` 大包，只保留顶层 `gemini`、`doubao_browser_tts`、`publisher_video`、`publisher_vertical_video`。
5. 增加 Alembic migration 和 DBeaver 手工 SQL 文档回填旧数据。
6. 本地库已执行幂等 hotfix：新增列并回填 157 条历史任务。

## 验证

接口 `GET /api/report-audio-workflows/jobs?page=1&per_page=10`：

1. 修复后首次冷请求约 4.58 秒，低于前端 15 秒超时。
2. 缓存命中请求约 0.006-0.007 秒。
3. 响应体从约 352KB 降至约 213KB。

## 项目纪律

列表页、轮询页、监控页属于热路径：

1. 不允许为了一个展示字段读取整块 JSON 大字段。
2. 新展示字段如果来自大 JSON，必须投影成独立列或单独详情接口读取。
3. 列表接口响应必须做冷请求计时；不能只看缓存命中。
4. 兼容字段要避免重复序列化，尤其是父子任务摘要。
