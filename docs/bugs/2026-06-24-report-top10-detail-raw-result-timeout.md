# 2026-06-24 Top10 详情接口返回 raw 大字段导致超时

## 基本信息

- 状态：已修复，已重启 Flask 后端生效
- 严重级别：中高
- 影响模块：`report_top10_workflow` API、Top10 详情页
- 首次发现：Top10 任务成功后，请求详情接口 20-30 秒超时
- 修复提交：待提交

## 现象

调用：

```text
GET /api/report-top10-workflows/jobs/<job_id>?include_detail=1
```

在 30 秒内未返回。任务本身已成功，结果文件也存在。

## 影响

- 页面可能继续显示“正在加载任务详情”或请求超时。
- 用户无法在页面确认最终口播稿、视频段落和单篇素材详情。
- 已成功任务看起来像系统又卡住。

## 根因

详情接口在 `include_detail=True` 时默认返回：

- `raw_result`
- `raw_params`
- `editor_response_text`
- `editor_input`
- `source_reports`

其中 `raw_result` 已包含大量重复数据，`editor_response_text` 又包含完整 Gemini 原始响应。Top10 结果文件约 589KB，接口默认重复返回多个大字段，放大了数据库 JSONB 读取、序列化和网络响应成本。

后续复测发现还有第二层根因：

- 历史列表接口为了渲染卡片，仍然加载 `publication_tasks.result` 大 JSON 列，导致一页历史返回约 1.18MB，轮询时反复拖慢页面。
- completed 任务详情即使已把大字段瘦身，仍然从 DB 读取完整 `result` JSONB；在本地 PostgreSQL/PgBouncer 压力下，单次按主键 SELECT 也可能达到 5-13 秒。

## 解决思路

列表页只需要轻量摘要：

- 状态、步骤、进度
- 标题投影 `task_title`
- 创建/完成时间
- 估算单篇完成数量

页面详情只需要：

- 单篇素材公开字段
- 总编辑输入
- 最终口播稿
- 视频段落
- 下载完整结果 JSON 的链接

原始大字段应默认不返回；需要排障时显式 `include_raw=1`。

completed 任务详情应优先读取落盘结果 JSON 文件，因为文件已经包含页面展示需要的 `source_reports`、`editor_input`、`final_script_for_tts`、`video_segments`；DB 只负责权限和状态。

## 实际方案

在 `src/backend/api/report_top10_workflow.py` 中：

- `include_detail=True` 默认不再返回 `raw_result` 和 `raw_params`。
- `editor_response_text` 也只在 `include_raw=1` 时返回。
- 完整结果保留在下载接口 `/download-result`。
- 拆分查询列：
  - `TASK_LIST_COLUMNS`：列表只查轻量状态列和 `task_title`。
  - `TASK_REQUEST_COLUMNS`：详情入口只查轻量状态列和 `params`。
  - `TASK_DETAIL_COLUMNS`：仅取消、续跑等需要写回结果的路径加载完整 `result`。
- 列表 payload 改成 `_job_summary_payload()`，禁止隐式访问 ORM `task.result`。
- `GET /jobs/<job_id>` 对 completed 任务优先读取 `storage_path_for_job(job_id, created_at)` 结果文件；文件不存在时才 fallback 到 DB `result`。
- 详情响应不再把完整 payload 同时展开到顶层和 `data`，避免重复序列化。
- `PublicationTask._derive_task_title()` 增加 `result.editor_result.program_title/cover_title`，完成后把总编辑标题同步到投影列。

## 验证

第一版修复后，同一详情接口返回：

```text
elapsed 5.84s
status succeeded
progress 100
final_script_chars 29323
video_segments 34
has_raw_result False
```

最终修复并重启 Flask 后端后：

```text
GET /api/report-top10-workflows/jobs?page=1&per_page=10
before: 1.18MB / 5.46s
after:  11KB / 0.22s

GET /api/report-top10-workflows/jobs/95abe64a-9560-4ecc-9567-d5c47ad3b70e
before: 2.21MB / 5.20s，压力下曾到 13.33s
after:  771KB / 0.16s
```

后端日志确认最终详情请求：

```text
SELECT publication_tasks 轻量列约 91ms
GET /api/report-top10-workflows/jobs/<job_id> -> 200 约 128ms
```

内容校验：

```text
status=succeeded
source_reports=10
final_script_for_tts=true
editor_input=true
R10 可见
```

## 复发预防

- 详情接口默认只返回页面展示字段。
- 大型调试字段必须通过显式参数开启。
- 列表接口、轮询接口禁止返回或搜索大 JSONB 原文。
- completed 编排详情优先读结果文件，避免把 DB 当大文档存储/读取通道。
- 新编排页必须先设计“列表摘要 payload”和“详情 payload”两套契约，禁止用一个全量结果接口同时服务列表、轮询和详情。
