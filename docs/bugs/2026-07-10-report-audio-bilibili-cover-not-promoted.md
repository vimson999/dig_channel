# 2026-07-10 Report Audio B站封皮未提升为正式资产

## 基本信息

- 状态：已修复
- 严重级别：P1
- 影响模块：report audio detail / B站上传 / artifact projection
- 首次发现：2026-07-10
- 修复提交：待提交
- 根因分类：产物投影与操作能力不一致
- 复发键：`ASSET-PROJECTION-CONSISTENCY`
- 是否复发：第二次
- 关联记录：`2026-06-26-report-audio-vertical-video-download-missing.md`
- 防线层级：shared_guardrail / contract_test

## 现象

- B站封皮已经生成并存在于 `video.keyframes.bilibili_cover`。
- 详情页只在“关键帧预览”展示，没有在“封皮 / 音视频预览”中展示。
- B站上传操作区的封面下拉框没有“B站封皮”。

## 影响

- 用户无法把专门生成的 1146×717 B站封皮用于上传。
- 页面把可交付封皮误归为调试关键帧，造成产物能力和操作能力不一致。
- 即使前端单独增加选项，后端原有上传解析器也会以“封面产物不存在”拒绝。

## 根因

关键帧生成链路已经写入 `bilibili_cover_path`，但详情 API、前端封皮资产列表和 B站上传 `cover_key` 映射分别维护。新增资产时只更新了关键帧公开逻辑，没有更新完整投影契约。

这与此前“竖屏视频已生成但下载入口缺失”属于同类问题：产物存在，但 API、展示、下载和操作入口没有一起注册。

## 解决思路

把 B站封皮从“仅关键帧”提升为正式资产，并同时对齐四个面：公开 API、下载路由、详情展示、B站上传解析。

## 实际方案

- 新增正式下载路由 `/download-bilibili-cover`。
- 详情 API 暴露 `download_bilibili_cover_url` 和 `video.bilibili_cover`。
- 上传解析器接受 `cover_key=bilibili_cover` 并解析真实文件。
- 详情页在正式封皮区展示 B站封皮，并从关键帧区移除重复项。
- B站上传封面下拉框加入 B站封皮，并作为优先默认项。
- 更新前端资源版本，避免浏览器继续使用旧 JS。

## 验证

- API 单测确认公开资产、下载 URL 和上传 cover resolver 使用同一文件。
- 浏览器验证目标详情页正式封皮区和上传下拉框均出现 B站封皮。
- 验证选项实际提交 `cover_key=bilibili_cover`，不是只增加显示文案。

## 复发预防

- 将本问题和竖屏下载入口遗漏合并为 `ASSET-PROJECTION-CONSISTENCY` 根因簇。
- 新增视觉产物时必须同时检查：生成、持久化、API 投影、下载、预览、操作入口、后端 resolver。

## 后续事项

- 将 report audio 的视觉资产注册进一步收敛为单一资产表，减少 API 和前端分别维护 key 列表。
