# 2026-06-24 Top10 总编辑输入过大导致 Gemini 未真正提交

## 基本信息

- 状态：已修复，待重启 `report_top10_workflow` 与 `report_pdf_gemini` worker 生效
- 严重级别：高
- 影响模块：`report_top10_workflow`、`report_pdf_gemini`、Gemini 浏览器自动化、数据库热路径
- 首次发现：今日研报 Top10 编排在 10 篇单篇文案完成后，总编辑 Gem 阶段反复卡在 `wait_response` 并最终失败
- 修复提交：待提交

## 现象

Top10 父任务 `95abe64a-9560-4ecc-9567-d5c47ad3b70e` 已完成 10 篇单篇文案，但总编辑 Gem 子任务连续失败，错误类似：

```text
等待 Gemini 回复超时; submit_state(hasName=False, busy=False, canceled=False,
enabledSend=True, sendButtonCount=1, composer_chars=30851)
```

页面表现为父任务 74% 左右失败或长时间停在 `editor_gemini_waiting`。

## 影响

- 单篇解析已经花费时间完成，但总编辑阶段无法稳定进入生成。
- 父任务重复续跑会创建新的总编辑子任务，旧 worker 重启时还可能留下 processing 孤儿。
- 总编辑输入和父任务 result 中保存 3 万字级大 JSON，会放大数据库查询、页面轮询和 worker 等待压力。

## 根因

Top10 总编辑输入直接拼接 10 篇单篇完整口播稿。单篇口播通常 2000-3000 字，10 篇合计约 3 万字。

这种输入方式存在两个问题：

1. 浏览器版 Gemini 对超长输入不稳定。自动化点击发送后，输入框仍保留完整 prompt，页面没有进入生成状态，但按钮仍显示可发送。
2. 父任务轮询、历史详情和调试脚本容易读取包含大 `result/params` 的任务行，让数据库热路径进一步变慢。

此前补了“输入框残留时重新点击提交”，但对于 3 万字输入只能缓解偶发点击失败，不能解决输入规模本身的问题。

## 解决思路

Top10 总编辑并不需要每篇完整单篇稿全文。它需要稳定的结构化素材：

- 原始 PDF 标题和主题
- 单篇标题、封面标题、副标题
- 摘要和风险提示
- 单篇正文的精华摘录

因此应把总编辑输入改成“结构化摘要 + 正文摘录上限”，完整单篇结果继续保存在 `source_reports`，供详情页和追溯使用。

## 实际方案

在 `src/backend/services/report_top10_workflow.py` 中：

- 新增 `DEFAULT_EDITOR_SCRIPT_CHAR_LIMIT = 800`。
- `build_editor_input()` 增加 `single_report_script_char_limit` 元信息。
- 每篇 `single_report_script` 超过上限时保留头部和尾部，中间用省略标记压缩。
- 增加 `single_report_script_original_chars` 和说明，明确完整单篇结果仍在 `source_reports`。
- 支持通过 `REPORT_TOP10_EDITOR_SCRIPT_CHAR_LIMIT` 或 payload `editor_script_char_limit` 调整上限。

配套测试：

- 短单篇文案保持原样。
- 长单篇文案必须压缩并保留开头、结尾和原文字数标记。

## 验证

静态验证：

```bash
venv/bin/python -m py_compile src/backend/services/report_top10_workflow.py
venv/bin/python -m unittest tests.unit.test_report_top10_workflow_service
```

运行态验证要求：

- 重启 `report_top10_workflow` 与 `report_pdf_gemini` worker。
- 将旧的长 prompt 总编辑子任务收口为失败。
- 从失败处续跑父任务，确认新生成的 `editor_input_chars` 明显小于原来的约 30863。
- 总编辑 Gem 能进入生成并返回 JSON。

## 复发预防

- 浏览器自动化投递大模型 prompt 不能默认无限长，必须定义输入规模预算。
- 新编排在保存 `params/result` 时要区分“业务完整追溯数据”和“热路径轮询数据”，轮询只能读取轻量字段。
- 子任务等待循环禁止每秒读取包含大 JSON 的 ORM 对象。
- 新功能复用已有 worker 时，必须同时复用父编排的重试、恢复、输入预算和 DB 热路径纪律。

## 后续事项

- 把 Top10 总编辑 prompt 进一步改成两段式：先本地压缩 10 篇素材，再交给总编辑 Gem。
- 为 `publication_tasks(parent_task_id, created_at)` 等高频查询补齐索引，降低父子任务列表查询延迟。
