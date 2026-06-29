# 2026-06-26 研报音频编排已生成竖屏视频但列表仍显示补生成

## 基本信息

- 状态：已修复
- 严重级别：P2
- 影响模块：report audio workflow / publisher / frontend task list
- 首次发现：任务已生成竖屏 MP4，但任务卡片右下角仍显示“生成竖屏”
- 修复提交：待提交

## 现象

研报音频编排任务完成后，父任务 `result.video.vertical_video_path` 和 publisher 子任务 `result.vertical_video_path` 都已经有竖屏 MP4 文件，但测试页列表卡片没有展示“竖屏 MP4”下载按钮，仍展示生成竖屏入口。

## 影响

用户容易误以为竖屏视频没有生成，再次点击后补生成入口，造成重复任务、重复排队和重复渲染成本。

## 根因

详情接口能从父任务 `result.video.vertical_video_path` 识别竖屏产物，但列表摘要接口走投影列和子任务摘要。旧逻辑只把“后补竖屏子任务”识别为竖屏来源，没有把正常“视频合成子任务”里的 `vertical_video_path` 纳入列表摘要。

此外，竖屏路径提取函数对普通 publisher 子任务和后补竖屏子任务没有区分。后补竖屏任务可以把 `video_path` 视作竖屏输出，但普通 publisher 子任务的 `video_path` 是横屏输出，不能用它判断竖屏已生成。

## 解决思路

统一把竖屏产物识别为：

1. 父任务 `result.video.vertical_video_path` 已存在。
2. publisher 子任务 `result.vertical_video_path` 已存在。
3. 后补竖屏子任务完成，且其竖屏输出存在。

普通 publisher 子任务只有 `vertical_video_path` 才算竖屏，避免把横屏 `video_path` 误判为竖屏。

## 实际方案

- 后端竖屏子任务选择逻辑从“只认后补竖屏任务”扩展为“后补任务或带竖屏产物的主 publisher 任务”。
- 列表摘要把选中的竖屏产物写入 `video.vertical_video` 和 `download_vertical_video_url`。
- 前端未生成时按钮文案从“生成竖屏”改为“补生成竖屏”；已有竖屏下载地址时展示“竖屏 MP4”。
- 增加回归测试覆盖主 publisher 子任务竖屏产物复用。

## 验证

- 单元测试：`tests.unit.test_report_audio_workflow_api`
- 语法检查：`py_compile src/backend/api/report_audio_workflow.py`
- 前端语法检查：`node --check src/frontend/js/report-audio-workflow-test.js`

## 复发预防

- 竖屏下载按钮的判断必须以真实竖屏产物路径为准，不能只依赖某一种子任务类型。
- 新增视频产物字段时，同步检查详情接口、列表摘要接口和前端按钮逻辑。

## 后续事项

- 如列表查询性能成为瓶颈，可以考虑增加 `report_audio_vertical_video_path` 投影列，减少对子任务 JSON 的读取。
