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
| 2026-08-20 | [研报基础文案物料页在后台持续刷新 Celery 状态](2026-08-20-report-script-material-hidden-worker-polling.md) | 研报基础文案物料台 / Celery / Page Visibility | RUNTIME_REPLACED/OBSERVING；旧浏览器实例待退出，下一完整窗口复核 |
| 2026-08-19 | [B站情报双调度器与未变化 JSON 重写放大数据库](2026-08-19-bilibili-intelligence-duplicate-scheduler-write-amplification.md) | Bilibili intelligence / scheduler / JSON 写放大 / 索引 | RUNTIME_REPLACED/OBSERVING；待完整窗口与人工索引维护 |
| 2026-08-18 | [视频编辑器后台标签页持续轮询 Celery 与 Publisher](2026-08-18-video-editor-hidden-polling.md) | 视频编辑器 / Celery / Publisher / Page Visibility | CODE_CHANGED/OBSERVING；待完整窗口 hidden 验收 |
| 2026-08-18 | [Watchdog 成功修复分支未归还数据库 Session](2026-08-18-watchdog-success-session-hold.md) | Celery Watchdog / Session / publication_tasks | RUNTIME_REPLACED/OBSERVING；新进程已验收，待完整窗口 |
| 2026-08-18 | [ZSXQ Insights 冷聚合在查询后继续持有 Session](2026-08-18-zsxq-insights-session-held-after-read.md) | 生财情报台 / PgBouncer / Session | Session 边界已替换；远端网络长尾 OPEN |
| 2026-08-18 | [DB 巡检把托管日志中的 SQL 失败误分为临时诊断](2026-08-18-managed-sql-error-source-misclassified.md) | DB 分析器 / scheduler / PgBouncer | CODE_CHANGED/OBSERVING；回放已通过 |
| 2026-08-16 | [后台状态页持续轮询并放大 API 与数据库往返](2026-08-16-hidden-status-pages-unbounded-polling.md) | Celery Monitor / ZSXQ 系统同步 / Page Visibility / PgBouncer | CODE_CHANGED/OBSERVING；真实页面已加载，待完整窗口验收 |
| 2026-08-15 | [DB 巡检未识别 PgBouncer 路由回退与等待错误](2026-08-15-db-audit-missed-pgbouncer-route-fallback.md) | DB 巡检 / PgBouncer / Tailscale / 代理 TUN | 分析器已修复并回放验证；网络根因 OPEN/OBSERVING |
| 2026-08-14 | [研报音频 DeepSeek 结构化跨外部调用持有数据库 Session](2026-08-14-report-audio-deepseek-session-hold.md) | report audio / DeepSeek / Session / PgBouncer | RUNTIME_REPLACED/OPEN；待真实任务和完整窗口验收 |
| 2026-08-13 | [工作流通知 SMTP 断连触发同步重试风暴](2026-08-13-workflow-notification-smtp-retry-storm.md) | workflow_notify / SMTP / Celery 重试 | RUNTIME_REPLACED/OPEN；单预算已加载，待真实失败与最终送达 |
| 2026-08-12 | [ZSXQ 自动发布预览跨远程上传持有数据库 Session](2026-08-12-zsxq-auto-publish-preview-session-hold.md) | ZSXQ 自动发布 / 内容流水线 / 七牛 / Session | CLOSED；29 个真实批次和下一完整窗口确认未复发 |
| 2026-08-11 | [Celery 状态接口跨 broker inspect 持有鉴权 Session](2026-08-11-celery-status-auth-session-held-across-inspect.md) | Celery 状态页 / 鉴权 / PgBouncer / Session | 并发强刷单飞已运行替换；单次 17.4 秒仍 OBSERVING |
| 2026-08-10 | [研报完整视频编排需要人工恢复与跨页面判断](2026-08-10-report-production-orchestration-manual-recovery.md) | report audio / Publisher / 方案三 / runtime guard / 工作台 | CODE_MODIFIED，等待新三 PDF 批次运行验收 |
| 2026-08-10 | [失败 SQL 耗时被错记为非 SQL Session 持有](2026-08-10-failed-sql-lifecycle-duration-misclassified.md) | database logger / Session 生命周期 / PgBouncer 归因 | 新代码已加载，等待真实失败样本校准，OBSERVING |
| 2026-08-10 | [Report audio finalization 周期 JSON 扫描与详情 N+1](2026-08-10-report-audio-finalization-json-polling-n-plus-one.md) | report audio / publication_tasks / watchdog / 详情列表 | 2026-08-14 单任务路径复发已重新修复并完成真实 API 验收，OBSERVING |
| 2026-08-09 | [ZSXQ Insights 冷查询把全量正文跨网络搬到应用](2026-08-09-zsxq-insights-cold-fulltext-transfer.md) | 生财 Insights / zsxq_topics / 网络传输 | 新代码已部署并完成真实冷/热 API 验收，OBSERVING |
| 2026-08-08 | [PostgreSQL 长连接经代理虚拟隧道反复黑洞超时](2026-08-08-postgres-tunneled-route-query-timeout.md) | 全站数据库链路 / PgBouncer / 本机代理 TUN/Tailscale | 两轮隔离后仍有应用 timeout 与 server crash，OPEN/OBSERVING |
| 2026-08-07 | [分镜提交成功后断连被误写为失败](2026-08-07-storyboard-ambiguous-commit-false-failure.md) | 火柴人视觉视频 / PostgreSQL / 父子任务状态机 | 指定任务端到端验收完成，RUNTIME_REPLACED/OBSERVING |
| 2026-08-07 | [火柴人分镜轮询误回读任务大字段并放大网络超时](2026-08-07-visual-storyboard-wide-polling-network-timeout.md) | 火柴人视觉视频详情 / publication_tasks / PgBouncer | 新后端与真实 API 已验收，待完整窗口确认不复发 |
| 2026-08-06 | [分镜多轮回复丢失前轮内容并误报提示词改写](2026-08-06-visual-plan-multiturn-prompt-loss.md) | 火柴人视觉视频 / 标题与正文分镜 / Gemini / DS | 代码已修复，待 worker 重载及真实任务验收 |
| 2026-08-06 | [研报音频方案三在视觉资产未就绪时反复创建 Publisher 失败任务](2026-08-06-report-audio-scheme3-publisher-failure-storm.md) | 研报音频 / 方案三 / Publisher / render queue | 前置门禁已加载，待下一条真实方案三任务验收 |
| 2026-08-06 | [后端进程 bootstrap 在凭据检查后跨进程启动持有数据库 Session](2026-08-06-backend-bootstrap-credential-session-hold.md) | 后端 bootstrap / ZSXQ 配置 / 平台凭据 / Session | 新 PID 已加载并完成长持有验收，持续观察 |
| 2026-08-06 | [ZSXQ 页内主题更新与同步心跳逐条往返放大](2026-08-06-zsxq-page-update-roundtrip-amplification.md) | ZSXQ 全 tabs / zsxq_topics / zsxq_sync_runs / Session | 主题/评论页级批量已验收，继续观察远端长尾 |
| 2026-08-06 | [ZSXQ 附件下载状态逐条往返放大](2026-08-06-zsxq-attachment-state-roundtrip-amplification.md) | ZSXQ 同步 / zsxq_attachments / 下载状态 / PgBouncer | 新 worker 边界证据已验收，继续观察新增下载 |
| 2026-08-06 | [ZSXQ 工作流通知在连续事件间刷新过期 ORM](2026-08-06-zsxq-workflow-notification-expired-session.md) | ZSXQ workflow / workflow notification / zsxq_workflow_runs / Session | 新 worker 连续通知已验收，持续观察 |
| 2026-08-06 | [ZSXQ 页内评论与外链对账 N+1 持有数据库连接](2026-08-06-zsxq-page-reconciliation-n-plus-one.md) | ZSXQ 同步 / zsxq_topic_comments / external_documents / Session | 评论页级路径已验收，继续观察真实外链样本 |
| 2026-08-06 | [MP3 媒体下载在全局缓存锁等待时持有数据库 Session](2026-08-06-mp3-download-lock-session-hold.md) | MP3 媒体下载 / 状态轮询 / publication_tasks / PgBouncer | 新后端已加载，待真实并发下载验收 |
| 2026-08-06 | [火柴人图片生成在浏览器等待期间持有数据库连接](2026-08-06-stick-image-expired-orm-browser-session.md) | 火柴人视觉资产 / Gemini 浏览器 / publication_tasks / SQLAlchemy Session | 新 worker 已加载，待真实图片任务验收 |
| 2026-08-06 | [DB 巡检漏报任务门禁捕获的 idle-in-transaction 压力](2026-08-06-idle-transaction-pressure-observability-gap.md) | DB 巡检 / runtime guard / Celery / PostgreSQL Session | 分析器与只读诊断已修复，待下一完整窗口归因验收 |
| 2026-08-05 | [研报音频后台标签页轮询放大为 HTTP/SQL N+1](2026-08-05-report-audio-hidden-tab-polling-n-plus-one.md) | 研报音频列表与详情页 / publication_tasks / PgBouncer | 代码与回归完成，待下一完整窗口验收 |
| 2026-08-04 | [PostgreSQL 白名单拒绝变化后的出口 IP](2026-08-04-postgres-hba-egress-ip-outage.md) | 全站 / PgBouncer / 远端 PostgreSQL / 重启门禁 | 再次复现，等待管理员恢复 223.80.177.164 白名单 |
| 2026-08-04 | [DB 巡检遗漏当日 gzip 轮转日志](2026-08-04-db-audit-skipped-gzip-rotations.md) | DB 巡检 / API E2E / PgBouncer 统计 | 已修复并重跑完整窗口 |
| 2026-08-03 | [ZSXQ 全部 Tab 调度器重复读取远端配置](2026-08-03-zsxq-scheduler-config-polling-roundtrip.md) | ZSXQ 全部 Tab / scheduler / system_configs / PgBouncer | RUNTIME_REPLACED/OBSERVING；5 分钟节奏已验收，待完整 24 小时 |
| 2026-08-02 | [请求鉴权在全局守卫和路由装饰器中重复查询用户](2026-08-02-request-auth-duplicate-user-query.md) | 全站受保护页面/API / 用户鉴权 / PostgreSQL | 已完成进程重载，待真实请求定量验收 |
| 2026-07-30 | [MP3 强制重生成在延迟续跑中重复创建已完成视频](2026-07-30-mp3-force-regenerate-repeated-completed-video.md) | MP3 视频编排 / Publisher / 延迟续跑 / 重启门禁 | 已部署并完成真实幂等验收，父任务已推进竖屏 |
| 2026-07-30 | [视觉视频端到端流程停在素材就绪并误阻重启](2026-07-30-visual-ready-state-blocked-restart.md) | 火柴人视觉视频 / 自动成片 / 重启安全门禁 / 父子任务状态机 | 已部署，历史任务自动对齐和最终视频补投进行中 |
| 2026-07-29 | [方案三前置校验连带阻断横屏成片](2026-07-29-report-audio-scheme3-preflight-blocked-landscape.md) | 研报音频详情 / 横屏成片 / 方案三竖屏 / Publisher | 已修复、部署并完成现场任务补投 |
| 2026-07-29 | [PDF 证据批量选择在截图期间持有数据库事务](2026-07-29-pdf-evidence-bulk-selection-held-db-transaction.md) | PDF 证据映射 / 人工确认 / 证据截图 / 任务详情页 | 已修复、部署并完成真实任务验收 |
| 2026-07-29 | [PDF 证据输出在必填字段赋值前被自动写入](2026-07-29-pdf-evidence-output-autoflush-before-required-fields.md) | 研报转语音 / 基础文案 / PDF 证据映射 / 任务详情页 | 已修复并完成真实任务回填 |
| 2026-07-29 | [PDF 证据截图误用带水印原文件](2026-07-29-pdf-evidence-screenshot-used-watermarked-source.md) | PDF 证据映射 / 清洗产物 / 火柴人证据画面 | 已修复、迁移并完成指定真实任务回填验收 |
| 2026-07-29 | [火柴人分镜图片与整篇 WAV 仍依赖手工续跑](2026-07-29-stick-scene-media-required-manual-recovery.md) | 火柴人视觉视频 / Gemini 分镜图片 / 整篇 WAV 时间轴对齐 | 已修复、部署并完成指定真实任务验收 |
| 2026-07-29 | [研报关键帧任务被误标为视频生成完成](2026-07-29-report-audio-keyframe-stage-mislabeled-video.md) | 研报转语音详情页 / 任务流程状态 | 已修复 |
| 2026-07-29 | [ZSXQ 全部 Tab 菜单发现遇到 1059 时未重试](2026-07-29-zsxq-all-tabs-menu-1059-no-retry.md) | 生财/洪灝全部 Tab 同步 / 上游瞬时错误 | 已修复，待真实计划轮次验收 |
| 2026-07-28 | [整篇音频对齐完成后被 Web 状态缓存隐藏](2026-07-28-stick-audio-worker-state-hidden-by-web-cache.md) | 视觉视频详情 / 整篇 WAV 对齐 / 横屏合成 / 方案三竖屏 | 已修复并完成真实横屏、方案三成片验收 |
| 2026-07-28 | [PDF 证据契约被旧前端覆盖且旧格式无法验证](2026-07-28-report-pdf-evidence-contract-bypassed.md) | 研报音频 / Gemini 浏览器 / PDF 证据映射 / 视觉分镜 | 已修复并完成两份真实 PDF 横竖成片验收 |
| 2026-07-28 | [上传 PDF 字节数字段类型不一致阻断方案三](2026-07-28-report-audio-upload-pdf-bytes-type-mismatch.md) | 研报音频详情 / 视觉工作流联动 / 方案三竖屏 | 已修复，运行态验收进行中 |
| 2026-07-28 | [研报自动成片按字数估算字幕导致语音漂移](2026-07-28-report-audio-publisher-script-weight-subtitle-drift.md) | 研报转语音 / Publisher / 方案三竖屏 / 字幕与分镜 | 已修复并完成指定真实任务重新成片验收 |
| 2026-07-28 | [YouTube 频道与视频同步跨远端请求持有数据库 Session](2026-07-28-youtube-sync-held-db-session.md) | 首页频道管理 / 视频管理 / YouTube scheduler / PostgreSQL | 已修复，待首次真实执行验收 |
| 2026-07-27 | [Watchdog 停止脚本误匹配管理命令自身](2026-07-27-watchdog-stop-script-self-match.md) | Celery Watchdog / 本地运行维护脚本 | 已修复并完成进程边界回归 |
| 2026-07-27 | [Celery solo Worker 被误判卡死且 Watchdog 误报重启](2026-07-27-celery-solo-inspect-false-stuck-watchdog.md) | Celery Monitor / Watchdog / report_pdf_gemini | 已修复并完成真实运行时验收 |
| 2026-07-27 | [评论索引清理迁移被混合所有者阻断](2026-07-27-comment-cleanup-migration-mixed-index-owner.md) | Alembic / PostgreSQL 索引维护 / 后端启动门禁 | 已修复并完成真实数据库迁移验收 |
| 2026-07-27 | [方案三错误依赖完整视觉视频并重复编码](2026-07-27-scheme3-redundant-intermediate-video.md) | 研报音频 / 方案三竖屏 / 火柴人视觉工作流 / Publisher | 已修复并完成真实预览与完整成片验收 |
| 2026-07-27 | [分镜多轮 Gem 被固定总时限截断且丢失已完成回复](2026-07-27-visual-plan-multiturn-gemini-total-timeout.md) | 火柴人视觉视频 / 标题与正文分镜 / Gemini 浏览器自动化 | 已修复并完成新 worker、真实长任务和 checkpoint 续跑验收 |
| 2026-07-27 | [研报音频方案三关键帧缺失与标题契约漂移](2026-07-27-report-audio-scheme3-keyframe-title-contract.md) | 研报转语音 / 方案三关键帧 / 标题评审 / 封面工具台 | 已修复并完成真实任务、真实页面与新 worker 验收 |
| 2026-07-27 | [ZSXQ 全部 Tab 计划缺少节点通知与配置入口](2026-07-27-zsxq-all-tabs-notification-and-config-ui-gap.md) | ZSXQ 洪灝/生财全部 Tab 同步 / 工作流邮件 / 系统同步页面 | 代码与回归完成，待运行时重载与真实同步验收 |
| 2026-07-27 | [知识星球不完整 Cookie 被误报保存成功](2026-07-27-zsxq-incomplete-cookie-save-false-success.md) | 平台凭据 / ZSXQ 同步 / Cookie JSON 解析 | 代码与回归完成，待新凭据真实同步验收 |
| 2026-07-27 | [ZSXQ 调度器数据库恢复后跳过补跑](2026-07-27-zsxq-scheduler-db-recovery-skipped-catch-up.md) | ZSXQ 自动调度器 / 洪灝与生财有术全部 Tab 同步 | 代码与回归完成，待凭据更新和真实补跑 |
| 2026-07-24 | [数据库 Session 跨 Celery Broker 与远程 I/O](2026-07-24-db-session-crossed-broker-and-remote-io.md) | runtime guard / 外链文档 / Celery broker / SQLAlchemy Session | 共享防线和回归已完成，待重启后真实任务验收 |
| 2026-07-24 | [自动图片重放把已完成视频父任务降回处理中](2026-07-24-auto-image-replay-downgraded-completed-video.md) | 火柴人视觉视频 / 自动图片初始化 / 重启安全门 | 代码、回归与现场任务收口已完成，待新 worker 验收 |
| 2026-07-24 | [ZSXQ 评论重复重写与数据库连接、索引治理复发](2026-07-24-zsxq-comment-rewrite-stale-db-pool-index-sprawl.md) | ZSXQ 同步入库 / PgBouncer / PostgreSQL 重点表索引 | 代码、迁移和契约测试已落地，待数据库迁移、新 worker 与真实同步验收 |
| 2026-07-24 | [B站自动投稿后仍需手工调整双比例封皮](2026-07-24-bilibili-dual-ratio-cover-manual-crop.md) | report audio / B站自动投稿 / 双比例封皮 | 已修复并完成双比例渲染验收，待新投稿观察 |
| 2026-07-24 | [整篇 WAV 就绪后试听区仍显示无音频](2026-07-24-whole-audio-preview-placeholder-stale.md) | 火柴人视觉视频 / 视频合成 / 倍速试听 | 已修复并完成真实页面音频加载验收 |
| 2026-07-24 | [ZSXQ watchdog 误杀活动中的全部 Tab 同步](2026-07-24-zsxq-watchdog-killed-active-all-tabs-sync.md) | Celery watchdog / ZSXQ sync / Celery Monitor | 已修复并完成真实任务重投与运行时门禁验收 |
| 2026-07-24 | [Gem 分镜漏句被误判为提示词改写](2026-07-24-visual-plan-gem-gap-false-prompt-rewrite.md) | 火柴人视觉视频 / Gem 分镜解析 / DS 整理 | 已修复并完成真实任务页面复跑验收 |
| 2026-07-24 | [视觉详情把研报音频来源误当基础文案任务](2026-07-24-visual-detail-report-audio-source-misrouted.md) | report audio / 火柴人视觉视频 / 详情页 | 已修复并完成新后端进程页面验收 |
| 2026-07-23 | [研报横屏关键帧投影再次被核心写入器绕过](2026-07-23-report-audio-landscape-projection-core-writer-bypass.md) | report audio / publication_tasks / 媒体下载 | Core 写入器已修、4 条漂移已回填，待新 worker 真实任务验收 |
| 2026-07-23 | [优化视频缓存误连本机导致数据库并发回源](2026-07-23-optimized-video-redis-localhost-db-thundering-herd.md) | optimized video sync / Redis / video_data / PostgreSQL | 根因与降载防线已修，待完整重启后的真实同步观察 |
| 2026-07-22 | [ZSXQ 发布收尾逐条回写附件状态](2026-07-22-zsxq-publish-attachment-status-n-plus-one.md) | ZSXQ 发布收尾 / zsxq_attachments / PostgreSQL | 代码与查询预算测试已通过，待新 worker 和真实发布验收 |
| 2026-07-22 | [ZSXQ 已有主题无差别重写原始载荷](2026-07-22-zsxq-topic-unchanged-payload-rewrite.md) | ZSXQ 同步入库 / zsxq_topics / PostgreSQL | 代码与全量回归已通过，管理员迁移脚本已就绪，待迁移、新 worker 和真实同步验收 |
| 2026-07-22 | [PgBouncer 冷连接扩容把窄查询放大到十秒](2026-07-22-pgbouncer-cold-pool-connect-timeout.md) | PgBouncer / PostgreSQL / 研报音频媒体接口 | 迁移、完整重启和关键路径真实负载已通过，持续观察中 |
| 2026-07-22 | [终态轮询与图片孤儿修复再次放大任务表载荷](2026-07-22-terminal-polling-image-repair-payload-recurrence.md) | publication_tasks / 火柴人图片与 TTS / watchdog / ZSXQ | 全量回归通过；入库映射与过期 ORM 复发入口已修并完成托管新 PID 日志验收 |
| 2026-07-21 | [火柴人视频音频滤镜拼接语法阻断后端启动](2026-07-21-stick-video-audio-filter-syntax-startup-block.md) | 火柴人视频 / Flask 启动 / ffmpeg | 已修复并完成新 PID 启动验收 |
| 2026-07-21 | [已完成视觉方案详情重复查询远端数据库](2026-07-21-visual-plan-completed-detail-repeat-query.md) | 视觉方案 / 分镜详情 / PostgreSQL | 已部署并完成跨后端进程真实复验 |
| 2026-07-21 | [火柴人视频预检失败未关闭父任务](2026-07-21-stick-video-preflight-failure-parent-not-closed.md) | 火柴人视频 / render / 父子任务状态机 | 已修复、现场收口并部署验收 |
| 2026-07-21 | [研报子任务创建后重复回读整条 JSON 任务](2026-07-21-report-child-create-post-commit-json-reload.md) | 基础文案 / 研报音频 / Gemini / 豆包 / Publisher | 四个入口已修、全量回归并部署 |
| 2026-07-21 | [ZSXQ 集合更新缺少 JSONB 类型转换导致整轮失败](2026-07-21-zsxq-set-update-jsonb-cast-regression.md) | ZSXQ 同步 / PostgreSQL JSONB / 真实负载验收 | 第 138 轮新 worker 真实验收通过 |
| 2026-07-21 | [火柴人资产入队和媒体请求放大父任务查询](2026-07-21-stick-figure-asset-owner-query-amplification.md) | 火柴人视频 / 分镜资产 / publication_tasks | 已部署并完成 31 镜头真实页面验收 |
| 2026-07-21 | [ZSXQ schema 合并查询把 PostgreSQL 数组当字符集合](2026-07-21-zsxq-schema-array-text-misread.md) | ZSXQ workflow / scheduler / PostgreSQL 驱动 / 真实验收 | 已修复并用应用连接复现验证，待补跑失败轮次 |
| 2026-07-21 | [重启安全门无条件切换公网数据库导致无法验收](2026-07-21-restart-guard-unconditional-direct-db.md) | 完整重启 / 活动任务门禁 / PostgreSQL / PgBouncer | 代码与双分支测试已修，待真实安全重启验收 |
| 2026-07-21 | [媒体宽字段被批量 JSON 更新绕过并持续漂移](2026-07-21-media-projection-bulk-update-drift.md) | report audio / publisher / publication_tasks / 启动门禁 | 入库已修、现场已回填，待新进程真实任务验收 |
| 2026-07-21 | [ZSXQ 同步逐条点查放大远端数据库抖动](2026-07-21-zsxq-sync-point-query-amplification.md) | ZSXQ 同步 / 附件入库与下载 / PostgreSQL | 第 138 轮页面级真实负载验收通过 |
| 2026-07-21 | [Watchdog 修复事务跨外部检查与轮询重叠](2026-07-21-watchdog-repair-idle-transaction-and-poll-overlap.md) | publisher watchdog / PostgreSQL / Celery / Redis | 2026-08-01 第四次打开；processing/terminal cleanup 已修，待安全重启与真实窗口验收 |
| 2026-07-19 | [MP3 双封皮标题版本被格式标题覆盖](2026-07-19-mp3-cover-copy-variants-collapsed.md) | mp3_video_workflow / DS 标题规划 / 横屏封皮 | 代码已修复并完成回归，待新任务运行态验收 |
| 2026-07-19 | [Report audio pending 父任务不在孤儿修复范围](2026-07-19-report-audio-pending-orphan-not-repairable.md) | report_audio_workflow / watchdog / publication_tasks | 已修复并完成历史任务收口与新进程验收 |
| 2026-07-19 | [内容清洗 processing 孤儿不在自动修复范围](2026-07-19-content-clean-processing-orphan-not-repairable.md) | Content Pipeline / watchdog / 重启安全门 / ZSXQ workflow | 已修复并完成现场恢复与重启验收 |
| 2026-07-18 | [回归套件范围不足并混入真实副作用测试](2026-07-18-regression-suite-false-green-and-live-side-effects.md) | pytest / 编译 / TypeScript / 迁移与运行时验收 | 确定性门禁已通过，待生产迁移和新进程验收 |
| 2026-07-18 | [ZSXQ 发帖凭据会话跨远程等待后失效](2026-07-18-zsxq-credential-session-held-across-remote-publish.md) | ZSXQ 发帖 / 平台凭据 / DB Session | 代码已修复并回归，待新 worker 真实验收 |
| 2026-07-18 | [重启安全检查依赖已停掉的 PgBouncer](2026-07-18-restart-safety-guard-depended-on-stopped-pgbouncer.md) | 开发栈重启 / PgBouncer / 活动任务保护 | 代码已修复，待完整重启验收 |
| 2026-07-18 | [B站上传 JSON 轮询与在线 DDL 锁队列](2026-07-18-bilibili-upload-json-polling-and-online-ddl-lock-convoy.md) | MP3 / report audio / B站上传 / watchdog / PostgreSQL | 主迁移已应用；worker 遗漏已修，待增量迁移、新 PID 和真实验收 |
| 2026-07-18 | [ZSXQ 批次结果被清洗与资产扫描重复搬运](2026-07-18-zsxq-run-result-clean-handoff-payload.md) | ZSXQ / Content Pipeline / 资产扫描 / PostgreSQL | 代码已修改，待迁移与新进程真实验收 |
| 2026-07-18 | [完整重启误中断活动 render 任务](2026-07-18-full-restart-interrupted-active-render.md) | 开发栈重启 / Celery render / publisher | 代码已修改，真实阻断验收中 |
| 2026-07-18 | [任务进度 JSON 写放大与热点读 120 秒失败](2026-07-18-publication-task-progress-json-write-amplification.md) | publication_tasks / report audio / Doubao / Gemini / MP3 / PgBouncer | 主迁移后取消收口再次复发；代码已修，待增量重启与真实验收 |
| 2026-07-17 | [后台停止残留检查失败却报告已清理](2026-07-17-background-stop-residual-check-false-success.md) | 开发栈重启 / Celery / watchdog / scheduler | 已修复并完成真实重启验收 |
| 2026-07-17 | [内容清洗进度反复读取 JSON 且重试异常被调用契约覆盖](2026-07-17-content-pipeline-progress-json-and-retry-contract.md) | Content Pipeline / Celery 重试 / PostgreSQL | 已加载新 worker，待真实任务验收 |
| 2026-07-16 | [ZSXQ 任务提交后过期 ORM 触发整行大字段刷新](2026-07-16-zsxq-expired-orm-full-row-refresh.md) | ZSXQ / 内容清洗 / 研报视频 / 平台凭据 / PostgreSQL | 第 106 轮再次打开；第四轮待迁移和新进程验收 |
| 2026-07-15 | [微信大页面正文被提前截断](2026-07-15-wechat-large-html-body-truncated.md) | 关联内容中心 / 微信文章抓取 | 已修复待 worker 回放 |
| 2026-07-15 | [飞书动态文档壳层被误报为 Cookie 失效](2026-07-15-feishu-dynamic-docs-shell-misclassified-cookie-expired.md) | ZSXQ Lab / 外链文档 / 飞书抓取 | 已修复，真实 item 已回放 |
| 2026-07-15 | [ZSXQ Lab 热度排行泄漏到非前沿信息收录组](2026-07-15-zsxq-lab-hot-rank-group-scope-leak.md) | ZSXQ Lab / 热度排行 / 分组切换 | 已修复 |
| 2026-07-15 | [ZSXQ Lab 发布入口泄漏到非君君播客组](2026-07-15-zsxq-lab-publish-panel-group-scope-leak.md) | ZSXQ Lab / 发布入口 / 分组切换 | 已修复 |
| 2026-07-15 | [ZSXQ 手动同步排队状态缺少上下文](2026-07-15-zsxq-manual-sync-queue-wait-opaque.md) | ZSXQ Lab / 同步日志 / zsxq_sync 队列 | 已修复待页面验收 |
| 2026-07-15 | [MP3 竖屏信息墙误用 AI 寒暄与任务说明](2026-07-15-mp3-infographic-used-ai-preamble.md) | MP3 视频编排 / DS 规划 / 竖屏信息图 | 已修复 |
| 2026-07-14 | [ZSXQ Lab 专属组操作区泄漏到普通组](2026-07-14-zsxq-lab-special-group-controls-leaked.md) | ZSXQ Lab / 人工下载计划 / 分组切换 | 已修复 |
| 2026-07-14 | [MP3 子任务在等待终点完成却被判超时](2026-07-14-mp3-child-completed-at-wait-deadline.md) | MP3 视频编排 / publisher 父子等待 / render | 已修复，现场任务已完成收口 |
| 2026-07-13 | [MP3 publisher 子任务等待 ID 未定义](2026-07-13-mp3-publisher-child-key-undefined.md) | MP3 视频编排 / publisher 父子等待 | 已修复 |
| 2026-07-13 | [MP3 publisher 复用查询在 limit 后追加 filter](2026-07-13-mp3-publisher-reuse-filter-after-limit.md) | MP3 视频编排 / publisher 资产复用 | 已修复 |
| 2026-07-13 | [MP3 数据库瞬时断连被写成父任务永久失败](2026-07-13-mp3-db-disconnect-marked-parent-failed.md) | MP3 视频编排 / runtime guard / publisher | 2026-07-30 再次打开；代码已修复，待运行验收 |
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
| 2026-06-29 | [飞书 Cookie 函数已存在但旧 Celery worker 仍导入失败](2026-06-29-feishu-cookie-import-stale-celery-workers.md) | Celery worker / external documents / platform credentials | 2026-08-11 同类共享导入混版再次打开，OBSERVING |
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
| 2026-06-23 | [父编排终态后强绑定子任务仍保持 active](2026-06-23-publisher-child-left-active-after-parent-canceled.md) | 工作流父子状态 / publisher / 豆包 / Gemini / celery monitor | 已扩为统一收口矩阵并完成现场清理 |
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
