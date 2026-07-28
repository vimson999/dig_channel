# 视觉详情把研报音频来源误当基础文案任务

## 基本信息

- 状态：已修复并完成新后端进程页面验收
- 严重级别：中
- 影响模块：report audio / 火柴人视觉视频 / 详情页 / 来源任务合同
- 首次发现：2026-07-24
- 修复提交：待提交
- 根因分类：跨编排来源类型合同缺失
- 复发键：`WORKFLOW-SOURCE-TYPE-CONTRACT`
- 是否复发：首次
- 关联记录：`2026-06-26-report-audio-ignored-script-material-cache.md`
- 防线层级：shared_guardrail / contract_test

## 现象

研报音频详情页能够显示自动创建的白板火柴人方案，任务
`b3a746da-94af-42fb-93d7-b0236645dfb5` 处于 `storyboard_queued`，但打开“视觉任务详情”后提示“加载失败：任务不存在”。

## 影响

视觉父任务和分镜子任务实际都存在，后台仍在正常运行，但用户无法进入详情页查看分镜、音画素材和后续视频合成状态，容易误判任务已丢失。

## 根因

视觉工作流允许两类可复用文案来源：

- `report_script_material_workflow`
- `report_pdf_audio_workflow`

后端详情响应只返回了含义模糊的 `material_job_id`。前端随后无条件请求
`/api/report-script-material-workflows/jobs/<id>`。本次父任务的来源实际是研报音频任务
`0718483f-3e66-4b9c-aa4f-e09b87022888`，因此第二跳 API 返回“任务不存在”。

创建、分镜投递和详情展示没有共享同一个“来源 ID + 来源 feature”合同，是本次跨编排复用只复用了 ID、没有复用完整行为合同的直接原因。

## 解决思路

不修改历史任务 ID，也不把研报音频来源伪装成基础文案任务。视觉工作流详情明确投影来源类型和来源 ID，页面按来源类型选择对应的详情 API，并把两类响应归一为页面需要的文案模型。

保留 `material_job_id` 作为分镜 API 和旧客户端的兼容字段，避免破坏已经支持研报音频任务作为文案来源的分镜合同。

## 实际方案

- 新增共享 `_workflow_source_payload`，统一返回 `source_feature`、`source_job_id` 和兼容的 `material_job_id`。
- 研报音频来源额外返回 `report_audio_job_id`，避免客户端继续猜测 ID 类型。
- 终态详情 Redis 缓存键升级到 `v3`，避免旧的无来源类型响应跨重启继续命中。
- 视觉详情页根据 `source_feature` 选择研报音频或基础文案详情 API。
- 将研报音频的 `structured_result.final_script_for_tts`、`audio_script.text` 和 PDF 预览地址归一为现有详情页面模型。
- 更新静态资源版本，确保浏览器加载修复后的脚本。

## 验证

- 现场只读核对确认视觉父任务、研报音频来源任务和分镜子任务均存在，错误发生在来源详情的第二跳请求。
- 新增来源合同单元测试，覆盖研报音频来源的 feature、source ID 和兼容 ID。
- 新增前端合同测试，约束页面按 `source_feature` 选择研报音频 API。
- Python 编译和前端 JavaScript 语法检查通过。
- 仅重启 Flask 后端，Celery worker 均保持原 PID 运行；新后端 PID `71304` 独占监听 `127.0.0.1:5001`。
- 现场页面已能加载研报标题、1368 字口播文案、PDF 预览和真实分镜状态；“文案 API”正确指向研报音频详情接口，不再出现“任务不存在”。

## 复发预防

1. 所有跨编排引用必须同时传递来源 ID 与来源 feature，禁止客户端仅凭 UUID 猜测资源类型。
2. 新增第二种来源时，必须覆盖创建、API 投影、详情读取、重试和历史任务五个入口。
3. 页面可以使用归一化模型，但不能把不同来源强制路由到同一个业务 API。

## 后续事项

- 后续发现并修复的分镜业务失败见
  [Gem 分镜漏句被误判为提示词改写](2026-07-24-visual-plan-gem-gap-false-prompt-rewrite.md)；
  当前真实任务已成功复跑出 32 个镜头并开始自动生成图片。
