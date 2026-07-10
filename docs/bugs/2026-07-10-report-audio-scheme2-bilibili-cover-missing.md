# 2026-07-10 Report Audio 方案二完整合成缺少 B站封皮

## 基本信息

- 状态：已修复
- 严重级别：P1
- 影响模块：report audio / publisher / 方案二视觉资产
- 首次发现：2026-07-10
- 修复提交：待提交
- 根因分类：视觉资产生成合同不完整
- 复发键：`ASSET-PROJECTION-CONSISTENCY`
- 是否复发：第三次
- 关联记录：`2026-06-26-report-audio-vertical-video-download-missing.md`、`2026-07-10-report-audio-bilibili-cover-not-promoted.md`
- 防线层级：shared_guardrail / contract_test

## 现象

- 方案二完整合成成功后只有横屏封皮、竖版封皮和播客封皮三张图。
- B站封皮只在关键帧模式生成，完整视频合成结果没有 `bilibili_cover_path`。
- 前一轮详情页和上传入口已经能识别 B站封皮，但完整合成链路没有实际产出可供展示的文件。

## 影响

- 新完成的方案二任务无法直接选择专用的 1146×717 B站封皮上传。
- 用户看到的是“页面能力已经存在、业务产物仍然缺失”，必须人工发现并补图。
- 资产展示修复与资产生成修复分离，暴露出视觉产物没有统一全链路合同。

## 根因

方案二视觉资产分别在两条路径实现：关键帧函数直接生成 B站封皮，而 Publisher 完整合成只生成竖屏背景、竖版封皮和播客封皮。新增 B站封皮时只补了关键帧及详情投影，没有把它注册到 Publisher 的方案二参数、生成、结果回写和历史任务补生链路。

## 解决思路

把 B站封皮提升为方案二的固定正式产物，完整覆盖“任务参数 -> Publisher 生成 -> 子任务结果 -> 父任务摘要 -> 详情 API -> 上传 resolver”；同时提供单图补生，历史任务不重跑音频和视频。

## 实际方案

- report audio 创建 Publisher 子任务时增加 `bilibili_cover` 配置，方案二自动启用且不显示底款。
- Publisher 方案二合成调用统一的 B站封皮生成器，写入 `bilibili_cover_path`。
- 父编排摘要、竖屏后补结果和详情子任务摘要均传递该路径。
- 增加 `/generate-bilibili-cover` 轻量补生入口，已完成任务可只重建封皮。
- 为任务 `44b47706-a388-4c1e-acfa-1ad2158d72b4` 补生 1146×717 B站封皮，没有重跑视频。

## 验证

- `tests.unit.test_report_audio_workflow_task` 与 `tests.unit.test_report_audio_workflow_api` 共 47 个测试通过。
- 单测覆盖方案二 Publisher 参数、无底款生成调用、父结果摘要和已完成任务轻量补生。
- 现场文件尺寸为 1146×717，下载接口返回 `200 image/jpeg`、165212 bytes。
- 浏览器确认详情页显示四张封皮，B站上传封皮下拉框默认选中“B站封皮”。
- render 与 report_audio_workflow Worker 在队列空闲时完成代码重载。

## 复发预防

- 视觉资产新增或变更必须通过统一清单检查：生成、持久化、父子回写、API、下载、预览、操作 resolver、历史补生。
- 不能再以“某一个页面已经显示”作为资产功能完成标准；必须用完整合成任务验证真实产物。
- 方案一与方案二的预期产物数量进入契约测试：方案二固定包含 B站封皮。

## 后续事项

- 将 report audio 视觉资产配置收敛为单一资产注册表，由关键帧与完整合成共同消费，消除两套产物清单。
