# Bug 修复记录

本目录记录项目中真实发生并已定位处理的 bug。每条记录必须说明现象、影响、根因、解决思路、实际方案、验证方式和复发预防。

项目纪律见：[Bug 修复记录纪律](../rules/bugfix-record-discipline.md)。

## 索引

| 日期 | Bug | 模块 | 状态 |
| --- | --- | --- | --- |
| 2026-06-26 | [独立豆包朗读短音频失败后没有自动重试](2026-06-26-doubao-standalone-short-audio-no-auto-retry.md) | doubao browser tts / 测试页 / Celery worker | 已修复 |
| 2026-06-26 | [研报音频 publisher 子任务已完成但父任务仍等待](2026-06-26-report-audio-publisher-child-completed-parent-waiting.md) | report audio workflow / publisher child wait / SQLAlchemy session | 已修复 |
| 2026-06-26 | [豆包朗读 processing 子任务卡死导致父编排无限等待](2026-06-26-doubao-browser-tts-stale-processing-child.md) | report audio workflow / doubao browser tts / celery worker | 已修复 |
| 2026-06-26 | [研报音频编排已生成竖屏视频但列表仍显示补生成](2026-06-26-report-audio-vertical-video-download-missing.md) | report audio workflow / publisher / frontend task list | 已修复 |
| 2026-06-26 | [研报转语音忽略基础文案缓存导致重复跑 Gemini](2026-06-26-report-audio-ignored-script-material-cache.md) | report audio workflow / script material / gemini browser | 已修复 |
| 2026-06-24 | [Watchdog 误杀活跃 render 任务](2026-06-24-watchdog-killed-active-render-tasks.md) | celery watchdog / publisher / render worker | 已修复 |
| 2026-06-24 | [Publisher processing 孤儿任务占满并发槽位](2026-06-24-publisher-processing-orphan-capacity-block.md) | publisher / render worker / celery watchdog | 已修复 |
| 2026-06-24 | [Top10 总编辑失败后父任务因 PendingRollbackError 未收口](2026-06-24-report-top10-failure-marker-pending-rollback.md) | report top10 workflow / database session | 已修复 |
| 2026-06-24 | [Content Pipeline 过期大文件因父目录 mtime 较新未被清理](2026-06-24-content-retention-batch-file-mtime-gap.md) | content retention cleanup | 已修复 |
| 2026-06-24 | [Top10 任务详情轮询闪烁且单篇素材无法查看返回值](2026-06-24-report-top10-detail-flicker-and-missing-item-detail.md) | report top10 workflow / frontend detail | 已修复 |
| 2026-06-24 | [编排续跑接口超时但实际成功导致前端误报失败](2026-06-24-workflow-retry-api-timeout-false-failure.md) | workflow retry / celery broker / frontend | 已修复 |
| 2026-06-24 | [Top10 单篇 Gemini 子任务缺少外层重试](2026-06-24-report-top10-gemini-child-no-retry.md) | report top10 workflow / gemini browser | 已修复 |
| 2026-06-24 | [Top10 总编辑输入过大导致 Gemini 未真正提交](2026-06-24-report-top10-editor-prompt-too-large.md) | report top10 workflow / gemini browser / database hot path | 已修复 |
| 2026-06-24 | [Top10 总编辑返回占位符导致最终口播稿不完整](2026-06-24-report-top10-final-script-placeholder.md) | report top10 workflow / result assembly | 已修复 |
| 2026-06-24 | [Top10 详情接口返回 raw 大字段导致超时](2026-06-24-report-top10-detail-raw-result-timeout.md) | report top10 workflow / API payload | 已修复 |
| 2026-06-24 | [Celery Watchdog 留下 idle in transaction 连接](2026-06-24-watchdog-idle-in-transaction.md) | celery watchdog / database session | 已修复 |
| 2026-06-24 | [知识星球同步下载期间持有 DB session](2026-06-24-zsxq-sync-download-held-db-session.md) | zsxq sync / database session / attachment download | 已修复 |
| 2026-06-24 | [知识星球同步下载后数据库服务端断开导致整轮失败](2026-06-24-zsxq-sync-db-connection-closed-mid-download.md) | zsxq sync / database | 已接入瞬断重试 |
| 2026-06-23 | [知识星球编排业务任务卡住但 Celery 消息不可见](2026-06-23-zsxq-workflow-stale-celery-message.md) | zsxq workflow / content pipeline / publish | 已接入自动 repair |
| 2026-06-23 | [父编排取消后 publisher 子任务仍显示业务进行中](2026-06-23-publisher-child-left-active-after-parent-canceled.md) | mp3 video workflow / publisher / celery monitor | 已接入自动收口 |
| 2026-06-23 | [知识星球系统同步计划未随本地重启启动](2026-06-23-zsxq-system-scheduler-not-autostarted.md) | zsxq scheduler | 已修复 |
| 2026-06-23 | [任务准入保护把排队容量误收窄为运行并发](2026-06-23-task-admission-backlog-limit.md) | runtime guard | 已修复 |
| 2026-06-23 | [研报音频编排历史列表 15 秒超时](2026-06-23-report-audio-history-timeout-large-result.md) | report audio workflow | 已修复 |
| 2026-06-23 | [研报音频本地兜底混入 Gemini 包装文案](2026-06-23-report-audio-local-fallback-gemini-artifact.md) | report audio workflow | 已修复 |
| 2026-06-23 | [MP3 视频编排父任务长等待 render 子任务](2026-06-23-mp3-video-parent-long-wait.md) | mp3 video workflow | 已优化 |
| 2026-06-23 | [研报音频编排父任务等待子任务后访问 detached ORM 对象](2026-06-23-report-audio-detached-session.md) | report audio workflow | 已修复 |

## 新增记录模板

```markdown
# YYYY-MM-DD Bug 标题

## 基本信息

- 状态：
- 严重级别：
- 影响模块：
- 首次发现：
- 修复提交：

## 现象

## 影响

## 根因

## 解决思路

## 实际方案

## 验证

## 复发预防

## 后续事项
```
