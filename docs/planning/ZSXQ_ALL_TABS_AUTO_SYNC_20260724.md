# 生财有术 / 洪灝全部 Tab 自动同步

## 目标

把 ZSXQ Lab 的“从远端同步全部 Tab”手动行为任务化：

- 生财有术（`1824528822`）。
- 洪灝的宏观策略（`88885882121542`）。
- 每 180 分钟触发一次。
- 动态读取远端官方 Tab；知识星球接口每页最多 20 条，生财有术每 Tab 最多取 2 页、目标 40 条，洪灝每 Tab 取 1 页、目标 20 条。
- 只同步主题、评论和附件索引，不下载附件。
- 复用前沿系统同步当前偏好的同一个 `zsxq.sync` DB 凭据。

生财有术需要刷新最新一页已有主题的评论，以便识别亦仁明确的中标/入选通知并幂等加入本地收藏。

## 成功经验复用

参考实现：

- ZSXQ Lab `syncTabs('all')`。
- `tasks.zsxq_sync.run_zsxq_sync`。
- `ZsxqSyncRun` 业务状态、心跳、失败和 stale 收口。
- `run_zsxq_auto_scheduler.py` 唯一核心调度进程。
- Celery Monitor 的 `zsxq_sync` worker 与业务运行双层快照。
- `platform_credential_service` 与系统同步的软偏好凭据回退。

复用能力：

- 官方菜单转 Tab、现有同步服务和 `zsxq_sync` 串行队列。
- 数据库瞬断和上游临时错误 Celery retry。
- 每页 topic/attachment 集合查询，避免逐条点查。
- 评论分页补全、楼中楼同步和评论幂等入库。
- 本地收藏唯一约束与幂等写入。
- scheduler canonical 项目根、PID、日志和后端自举。

未复用项：

- 不进入前沿专属“同步 → 清洗 → 发布”工作流，因为两个目标只要求普通全部 Tab 索引同步。
- 不复用前沿“外资研报/调研纪要”附件阈值规则，因为它们是前沿专属业务。

## 事故防线

- `zsxq-scheduler-not-autostarted`：继续依附默认自启的核心 scheduler，不新增手动启动依赖。
- `zsxq-scheduler-wrong-root-duplicate-instance`：不新增第二 scheduler 进程；触发入口另有 canonical runtime 文件锁。
- `RUNTIME-TOPOLOGY-RELOAD`：PID 存活检查排除 zombie；退出但未被父进程回收的调度器不会阻塞安全重载。
- `zsxq-sync-stale-credential-binding`：前沿凭据 ID 仅作软偏好，删除或失效后回退 active `zsxq.sync` 凭据。
- `zsxq-sync-download-held-db-session`：凭据物化和 active-run 查询后立即释放 session，再访问远端菜单和 broker。
- `zsxq-workflow-queue-failure-and-sync-orphan`：先创建 queued 业务记录；broker 入队失败立即把同一 run 收口为 failed。
- `zsxq-manual-sync-queue-wait-opaque`：使用既有 `ZsxqSyncRun`，页面和 Celery Monitor 能显示队列位置、阻塞任务和业务状态。
- `zsxq-sync-point-query-amplification`：不新增逐 topic/评论旁路查询，继续走同步服务的页面级集合处理。
- `zsxq-topic-unchanged-payload-rewrite`：已有主题继续走 payload hash 和结构化差异更新。

## 任务策略

| 星球 | mode | 每 Tab 数量 | 最大页数 | stop_on_existing | 原因 |
|---|---|---:|---:|---:|---|
| 生财有术 | `auto_all_tabs_sc` | 40 | 2 | false | 活跃度较高；按 20×2 刷新最新 40 条主题评论，捕获旧帖新增的亦仁中标通知 |
| 洪灝的宏观策略 | `auto_all_tabs_hh` | 20 | 1 | true | 发帖频率较低；与手工全部 Tab 增量行为一致，遇到首个已有主题即停止 |

两个星球分别检查 active run。一个星球仍在 queued/running/retrying 时只跳过该星球，不阻塞另一个星球。

## 配置来源

复用项目统一的 `system_configs` 表，不新增重复配置表。每个星球独立一行：

- `type = zsxq_sync_schedule`
- `scope = production`
- 生财：`key = zsxq.sync.schedule.shengcai`
- 洪灝：`key = zsxq.sync.schedule.honghao`

`value` 是结构化 JSON，保存 `group_id`、名称、`credential_id`、启停状态之外的调度参数、每 Tab 数量、页数、增量策略和附件策略；`status` 作为该星球的启停开关。两个默认配置的 `credential_id` 都引用前沿当前使用的同一条数据库凭据，不复制 Cookie 明文。

调度器为每条启用配置注册独立 schedule job，并每 30 秒比较配置签名；修改其中一颗星球不会覆盖另一颗，且不需要重启调度器。配置可通过 `GET /api/v1/zsxq/all-tabs-auto-sync/configs` 查询，通过 `PUT /api/v1/zsxq/all-tabs-auto-sync/configs/<schedule_key>` 更新。

## 亦仁中标收藏

真实数据中存在两个显示名为“亦仁”的账号：

- 获选通知账号：`844145551241182`。
- 个人讨论账号：`552158581144`。

判断优先使用稳定 `owner_user_id`，同时要求评论表达明确、正向、已经发生的中标/入选/获选语义。否定、疑问、假设、候选和一般讨论不得触发。历史缺少 user ID 的评论保留严格语义兼容。

收藏只写系统本地 `ZsxqTopicFavorite`，不操作知识星球远端收藏。

## Celery Monitor

- scheduler 层：沿用 `zsxq_auto_scheduler`，描述同时展示前沿编排和两个全部 Tab 三小时任务。
- worker 层：沿用 `zsxq_sync` 队列、worker、watchdog 和队列长度。
- 业务层：active `ZsxqSyncRun` 显示“生财有术/洪灝 · 全部 Tab 定时同步”，即使 Celery inspect 瞬时不可见仍有业务状态。
- retry 层：`retrying` 继续作为 active 业务任务展示，不会在等待 Celery 重试时从 Monitor 消失。

## 验收

- scheduler 同时注册前沿原间隔和全部 Tab 180 分钟两个 schedule tag。
- 两个目标共用一次解析出的前沿偏好凭据。
- 每个目标动态发现官方 Tab，并创建独立 queued run。
- 生财 payload 为最多两页、每页 20 条、目标 40 条、`stop_on_existing=false`、禁止附件下载。
- 洪灝 payload 为一页 20 条、`stop_on_existing=true`、禁止附件下载。
- active-run 门禁阻止同星球重复入队。
- 入队失败收口同一个 run，不留下 queued 孤儿。
- 亦仁通知账号的正向变体触发收藏；否定、假设、未知同名账号和非生财星球不触发。
- Celery Monitor 使用明确的定时同步标题。
- Python 编译、定向单元测试和相关已知问题回归通过。
