# 2026-11-12 MP3 新任务默认跳过封皮确认

## 基本信息

- 状态：RUNTIME_REPLACED / OBSERVING（线上提交页已恢复默认人工确认；下一条自然新任务待验收）
- 严重级别：P1
- 影响模块：MP3 视频编排提交页 / 封皮确认 / 横竖屏合成
- 首次发现：2026-11-12
- 根因分类：工作流默认值绕过人工确认门禁
- 复发键：`MP3-COVER-REVIEW-GATE-BYPASSED-BY-DEFAULT`
- 是否复发：首次确认
- 关联记录：`2026-10-27-mp3-waiting-parent-processing-orphan-requeued.md`、`2026-07-29-report-audio-keyframe-stage-mislabeled-video.md`
- 防线层级：contract_test / observability

## 现象

用户提交 `c50f86fc-36d6-490b-a125-f395e50479a0` 后，系统在四张封皮/背景生成完毕时直接创建横屏和竖屏合成子任务，没有等待用户确认或修改封皮并手动投递。

现场数据库记录显示该任务保存了 `render_mode=full_video`。横屏子任务 `a351a576-942e-4c64-81cd-13f87270a71a` 和竖屏子任务 `4d936c10-3309-4994-ae06-c90f4d467d25` 均已完成；父任务最终为 `completed / done / 100%`。

## 影响

封皮人工审核步骤被跳过，系统消耗了横竖屏视频合成资源。四张视觉文件与两版成片均保留，用户仍可在详情页修改封皮后手动重生成视频。

## 根因

上一轮为续跑两条已失败任务，我错误地把提交页的输出模式默认选中项从 `keyframes` 改为 `full_video`。前端 `currentRenderMode()` 将当前选中值直接写入提交 JSON；服务端对 `full_video` 的既有语义就是生成封皮后立即合成。用户要求的是修复并续跑特定任务，不是改变新任务的人工确认流程。

## 解决思路

恢复“先生成封皮与关键帧，人工确认后再投递完整视频”的默认值，并在选项文案中明确立即合成会跳过封皮确认。保留用户主动选择立即合成的能力。

## 实际方案

- `src/frontend/mp3-video-workflow.html` 恢复 `keyframes` 为唯一默认选项。
- 选项文案说明“确认后手动合成”与“跳过封皮确认”的区别，增加页面提示。
- 前端默认值契约测试固定检查人工确认路径；服务端未传模式时原本就默认 `keyframes`，无需改动。

## 验证

- 线上 `GET /mp3-video-workflow.html` 返回 HTTP 200，实际 HTML 中仅 `keyframes` 被选中。
- `node tests/frontend/mp3-video-workflow-default-source.test.cjs` 通过。
- workflow/API 定向测试：119 passed，5 个 subtests passed。
- `git diff --check` 通过。
- 现场任务父子状态和四张封皮/背景文件已核对；横屏与竖屏成片均存在。该任务的自动合成发生在默认值恢复前，不能作为恢复后新建任务的验收样本。

## 复发预防

- 默认输出模式属于流程门禁变更，修改前须确认用户是否愿意改变封皮人工审核步骤。
- 提交页契约测试固定检查默认 `keyframes`，并保留明确的手动续跑入口。
- 下一轮优先核对一条新建任务的 `render_mode=keyframes`、封皮与关键帧生成，以及用户点击“继续生成完整视频”之前没有 publisher 视频子任务。

## 后续事项

- 等下一条自然新建任务验证默认值与人工确认流程；当前只有在线 HTML 与现有代码路径证据，保持 OBSERVING。
