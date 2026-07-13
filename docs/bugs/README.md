# Bug 修复记录

本目录记录项目中真实发生并已定位处理的 bug。每条记录必须说明现象、影响、根因、解决思路、实际方案、验证方式和复发预防。

项目纪律见：[Bug 修复记录纪律](../rules/bugfix-record-discipline.md) 和 [Bug 定期回顾与复发升级纪律](../rules/bug-retrospective-discipline.md)。

复发根因登记表：[`recurrence-registry.json`](recurrence-registry.json)。正式回顾归档在 [`reviews/`](reviews/)。

执行完整性检查：

```bash
./venv/bin/python scripts/dev/review_bug_records.py --strict
```

## 索引

| 日期 | Bug | 模块 | 状态 |
| --- | --- | --- | --- |
| 2026-07-13 | [MP3 publisher 子任务等待 ID 未定义](2026-07-13-mp3-publisher-child-key-undefined.md) | MP3 视频编排 / publisher 父子等待 | 已修复 |
| 2026-07-13 | [MP3 publisher 复用查询在 limit 后追加 filter](2026-07-13-mp3-publisher-reuse-filter-after-limit.md) | MP3 视频编排 / publisher 资产复用 | 已修复 |
| 2026-07-13 | [MP3 数据库瞬时断连被写成父任务永久失败](2026-07-13-mp3-db-disconnect-marked-parent-failed.md) | MP3 视频编排 / runtime guard / publisher | 已修复 |
| 2026-07-13 | [MP3 热路径继续批量读取任务 JSON](2026-07-13-mp3-hot-path-json-payload-regression.md) | MP3 API/worker / publisher reuse / watchdog | 已修复待 SQL 与重启验收 |
| 2026-07-13 | [基础文案任务缺少 Gemini 阈值函数导致批量失败](2026-07-13-report-script-material-missing-gemini-threshold-helper.md) | report script material / Gemini 缓存 | 已修复 |
| 2026-07-11 | [后端重启误把旧监听进程判定为启动成功](2026-07-11-backend-restart-accepted-stale-listener.md) | Flask backend / restart scripts / Celery bootstrap | 已修复待真实重启验收 |
| 2026-07-11 | [MP3 父任务等待状态误占容量导致死锁](2026-07-11-mp3-parent-wait-state-capacity-deadlock.md) | MP3 视频编排 / publisher / runtime guard | 已修复 |
| 2026-07-10 | [研报音频方案二完整合成缺少 B站封皮](2026-07-10-report-audio-scheme2-bilibili-cover-missing.md) | report audio / publisher / 方案二视觉资产 | 已修复 |
| 2026-07-10 | [Publisher 封面浏览器缺少进程级硬超时](2026-07-10-publisher-cover-browser-no-hard-timeout.md) | publisher / Playwright / render worker | 已修复待进程重载 |
| 2026-07-10 | [研报音频 B站封皮未提升为正式资产](2026-07-10-report-audio-bilibili-cover-not-promoted.md) | report audio detail / B站上传 / artifact projection | 已修复 |
| 2026-07-10 | [Celery 停止脚本误杀控制进程](2026-07-10-celery-stop-script-killed-controller.md) | Celery restart / report audio / Doubao TTS | 已修复 |
| 2026-07-10 | [研报音频列表大载荷与父编排重复数据库往返](2026-07-10-report-audio-projection-and-parent-roundtrips.md) | report audio / Doubao / publisher / PostgreSQL | 已部署待验收 |
| 2026-07-10 | [知识星球同步绑定已删除凭据后无法自动切换](2026-07-10-zsxq-sync-stale-credential-binding.md) | ZSXQ system sync / platform credential / scheduler preflight | 已修复 |
| 2026-07-10 | [研报音频 Gemini 完成后快照被父任务提交过期](2026-07-10-report-audio-gemini-completed-snapshot-expired.md) | report audio workflow / Gemini child wait / SQLAlchemy session | 已修复 |
| 2026-07-10 | [已完成研报任务的竖屏后补被自动收口](2026-07-10-report-audio-vertical-backfill-auto-canceled.md) | report audio / vertical backfill / publisher repair | 已修复 |
| 2026-07-10 | [研报音频已完成父任务被延迟消息再次执行](2026-07-10-report-audio-completed-parent-redelivered.md) | report audio workflow / delayed retry / idempotency | 已修复 |
| 2026-07-10 | [研报音频 TTS 缓存扫描 500 条完整大 JSON](2026-07-10-report-audio-tts-cache-full-json-scan.md) | report audio workflow / doubao cache / PostgreSQL | 已修复 |
| 2026-07-10 | [研报音频 Gemini 等待续跑读取 detached 子任务 ID](2026-07-10-report-audio-gemini-defer-detached-id.md) | report audio workflow / Gemini wait / SQLAlchemy session | 已修复 |
| 2026-07-09 | [Report Audio TTS 文案混入分段结构标签](2026-07-09-report-audio-tts-section-outline-labels.md) | report_audio_workflow / report_script_material_workflow / DeepSeek normalizer / 详情页 | 已修复 |
| 2026-07-09 | [Report Script Material 结构化器超时且无重试](2026-07-09-report-script-material-normalizer-timeout-no-retry.md) | report_script_material_workflow / DeepSeek normalizer / Gemini 子任务续跑 | 已修复 |
| 2026-07-07 | [MP3 封面后台渲染被外部 CDN 阻塞超时](2026-07-07-mp3-cover-render-cdn-timeout.md) | MP3 视频编排 / 四张封皮背景图生成 / Playwright 后台渲染 | 已修复 |
| 2026-07-07 | [MP3 视频合成 1.2 倍速未真正生效](2026-07-07-mp3-video-audio-speed-not-applied.md) | MP3 视频编排 / publisher 音频预处理 / 横竖屏视频合成 | 已修复 |
| 2026-07-07 | [平台上传 worker 重启导致 B站上传任务假运行](2026-07-07-platform-upload-worker-restart-orphan.md) | MP3 详情页 B站上传 / platform_upload / publisher queue repair | 已修复 |
| 2026-07-07 | [平台上传误用 render 合成队列](2026-07-07-platform-upload-used-render-queue.md) | B站/YouTube 上传 / publisher / Celery 队列 | 已修复 |
| 2026-07-07 | [MP3 详情页 DS 清洗误报未识别时间戳](2026-07-07-mp3-detail-ds-cleaning-false-missing-timestamp.md) | mp3-video-workflow-detail / DS 返回值展示 | 已修复 |
| 2026-07-07 | [MP3 手动 DS 返回值不完整仍生成垃圾封面和视频](2026-07-07-mp3-invalid-ds-response-generated-garbage-assets.md) | mp3_video_workflow / 手动 DS 返回值 / IMA 全文 DS / 视觉资产 | 已修复 |
| 2026-07-07 | [MP3 B站后投上传被误取消且标题混入占位模板](2026-07-07-mp3-bilibili-upload-canceled-and-dirty-title.md) | mp3 detail / B站上传 / publisher cleanup / DS 标题清洗 | 已修复 |
| 2026-07-07 | [MP3 详情页 B站上传记录停留在排队中](2026-07-07-mp3-detail-bilibili-upload-status-stale.md) | mp3-video-workflow-detail / B站上传子任务 / publisher | 已修复 |
| 2026-07-07 | [MP3 视频文件已生成但父编排仍显示运行中](2026-07-07-mp3-video-publisher-file-done-parent-waiting.md) | mp3_video_workflow / publisher / render worker | 已修复，当前任务已完成收口 |
| 2026-07-03 | [知识星球工作流入队失败与同步 orphan running](2026-07-03-zsxq-workflow-queue-failure-and-sync-orphan.md) | zsxq workflow / zsxq sync / scheduler / SQLAlchemy session | 已修复，当前阻塞已释放 |
| 2026-07-02 | [研报视频关键帧渲染误用占位段落名](2026-07-02-report-video-keyframe-placeholder-labels.md) | report video / keyframe preview / preview clip renderer | 已修复 |
| 2026-07-02 | [Report Script Material 任务达到修复上限后仍 pending](2026-07-02-report-script-material-orphan-pending-after-repair-limit.md) | report_script_material_workflow / celery watchdog / queue repair | 已修复，5 个历史任务已完成 |
| 2026-07-02 | [历史文件清理重复调度器覆盖执行结果](2026-07-02-content-retention-duplicate-scheduler-overwrote-result.md) | content retention cleanup scheduler / runtime status / email notification | 已修复 |
| 2026-06-29 | [Celery worker 假活但不消费队列](2026-06-29-celery-worker-fake-alive-backlog-not-consuming.md) | celery watchdog / report audio workflow / Redis broker | 已修复 |
| 2026-06-29 | [研报音频豆包分段失败记录后访问 detached 子任务](2026-06-29-report-audio-doubao-child-detached-after-failure-record.md) | report audio workflow / doubao browser tts / SQLAlchemy session | 已修复 |
| 2026-06-29 | [飞书 Cookie 函数已存在但旧 Celery worker 仍导入失败](2026-06-29-feishu-cookie-import-stale-celery-workers.md) | Celery worker / external documents / platform credentials | 已处置 |
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
- 根因分类：
- 复发键：
- 是否复发：首次 / 第 N 次
- 关联记录：
- 防线层级：local_patch / shared_guardrail / contract_test / observability

## 现象

## 影响

## 根因

## 解决思路

## 实际方案

## 验证

## 复发预防

## 后续事项
```
