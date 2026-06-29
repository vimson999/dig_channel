# 2026-06-24 Top10 任务详情轮询闪烁且单篇素材无法查看返回值

## 基本信息

- 状态：已修复，后端已重启生效
- 严重级别：中
- 影响模块：`report_top10_workflow`、Top10 前端历史详情
- 首次发现：用户在今日研报 Top10 编排页查看运行中任务时，详情区在“加载中”和详情内容之间反复闪烁，并且点击 R01/R02 单篇 item 不能查看该 item 的 Gemini 返回值详情
- 修复提交：待提交

## 现象

Top10 编排页展开任务后，页面会周期性变成：

```text
正在加载任务详情...
```

随后又恢复为任务详情，表现为“页面一闪一闪”。

同时，“单篇素材来源”列表只显示标题、字数和 PDF 文件名，点击某一条 R01/R02 后没有展示该条单篇 Gemini 的完整返回内容。

## 影响

- 运行中任务详情难以阅读，用户会误以为接口或任务状态不稳定。
- 无法定位 Top10 中某篇单篇文案的结构化输出是否正常。
- 当总编辑稿件质量异常时，无法快速追溯是单篇 Gemini 输出问题，还是总编辑汇总问题。

## 根因

直接根因：

- 前端轮询定时器每 8 秒调用一次 `openJob(this.selectedJobId)`。
- `openJob()` 每次都会先清空 `selectedJobDetail` 并设置 `selectedJobLoading=true`。
- 因此即使只是后台静默刷新当前任务，也会把已展开详情先重置成加载态，造成闪烁。

数据根因：

- 后端任务详情接口中的 `source_reports` 只返回了单篇素材的摘要字段，例如标题、summary、字数。
- 单篇 Gemini 的完整结果字段 `structured_result` 和 `single_report_script` 没有暴露给前端。
- 前端也没有“选中某个 source report 并展示详情”的状态和 UI。

## 解决思路

分两层修：

1. 轮询刷新当前已展开任务时走静默刷新，不清空已有详情，不切回 loading。
2. 后端补齐单篇素材的完整返回值，前端把“单篇素材来源”改成可点击列表，右侧展示所选 item 的返回值详情。

## 实际方案

后端 `src/backend/api/report_top10_workflow.py`：

- `_public_source_report()` 增加返回：
  - `cover_subtitle`
  - `risk_notes`
  - `single_report_script`
  - `final_script_for_tts`
  - `script_chars`
  - `structured_result`

前端 `src/frontend/js/report-top10-workflow-test.js`：

- 新增 `selectedSourceReportIndex`，记录当前选中的 R01/R02 item。
- `openJob(jobId, { silent: true })` 支持静默刷新：
  - 当前任务已有详情时，不清空 `selectedJobDetail`
  - 不切换到 loading 态
  - 接口返回后直接替换最新详情
- 轮询改为调用：

```js
this.openJob(this.selectedJobId, { silent: true });
```

- “单篇素材来源”改成左右布局：
  - 左侧为可点击的 R01/R02 列表
  - 右侧为该 item 的返回值详情
- 返回值详情展示：
  - 标题、副标题、封面标题、封面副标题
  - 摘要、风险提示
  - PDF 文件名/Topic
  - 单篇口播稿
  - 结构化 JSON

前端 HTML 已 bump JS cache：

```text
20260624-detail-no-flicker
```

## 验证

语法检查：

```bash
node --check src/frontend/js/report-top10-workflow-test.js
venv/bin/python -m py_compile src/backend/api/report_top10_workflow.py src/backend/celery_config.py src/backend/tasks/report_top10_workflow.py
```

运行态验证：

- Flask 后端已重启生效。
- 后端健康检查返回 `healthy`。
- 当前 Top10 任务 `95abe64a-9560-4ecc-9567-d5c47ad3b70e` 查询结果：

```text
status=running
current_step=editor_gemini_waiting
progress=74
source_report_count=10
source_reports=10
```

- 第一条单篇素材已确认返回字段：

```text
title, cover_title, cover_subtitle, summary, risk_notes, single_report_script, structured_result
script_len=3050
structured_result=true
```

## 复发预防

- 编排详情页轮询不能清空用户正在阅读的详情状态；刷新已有详情必须支持 `silent` 模式。
- 列表态接口可以瘦身，但详情态接口必须返回排查质量问题所需的原始/结构化结果。
- 新增编排页时优先复用已有详情轮询和 item 明细模式，避免再次只展示摘要、不展示返回值。

## 后续事项

- 抽取通用的 workflow detail silent-refresh helper，供 Top10、研报音频、MP3 视频编排共用。
- 抽取通用的 source item detail 组件，减少各编排页重复实现。
