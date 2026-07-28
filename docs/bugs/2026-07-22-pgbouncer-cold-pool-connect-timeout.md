# PgBouncer 冷连接扩容把窄查询放大到十秒

## 基本信息

- 状态：迁移、完整重启和关键路径真实负载已通过，进入持续观察
- 严重级别：P0
- 影响模块：Flask / PgBouncer / 远端 PostgreSQL / 研报音频媒体接口
- 首次发现：2026-07-22
- 修复提交：当前工作区
- 根因分类：数据库连接生命周期、热点请求扇出、运行时配置验收
- 复发键：`DB-SESSION-LIFECYCLE` / `HOT-PATH-PAYLOAD-POLLING` / `RUNTIME-TOPOLOGY-RELOAD`
- 是否复发：第 N 次；此前多次把应用端等待误判为执行计划或 JSON 载荷问题
- 关联记录：`2026-07-10-report-audio-projection-and-parent-roundtrips.md`、`2026-07-18-publication-task-progress-json-write-amplification.md`、`2026-07-22-terminal-polling-image-repair-payload-recurrence.md`
- 防线层级：shared_guardrail / contract_test / observability

## 现象

- 当前后端 PID 启动后的首轮页面请求中，主键窄投影查询耗时 `3100ms`，关键帧任务查询耗时 `12130ms`。
- 同一批请求中的平台凭据、附件和结构化子任务查询也普遍耗时 `1-3s`，慢的不只是某一张表或某一个 JSON 字段。
- PgBouncer 在该请求窗口记录 `connect timeout (age=10s)`；重启前配置只保留 2 条后端热连接，空闲 120 秒即回收。

## 影响

- 页面并发加载多个媒体资源时，连接池需要临时建立公网 TLS PostgreSQL 连接；任一连接建立超时会让业务 SQL 计时看起来像执行了十秒。
- 持续对已经正确使用主键、投影列和索引的查询加索引，无法修复连接等待，还会增加写放大和维护成本。
- 研报音频关键帧接口仍读取完整 `result`，让连接抖动与不必要的大字段物化叠加。

## 根因

1. 本地 PgBouncer 的 `min_pool_size=2` 小于页面冷启动时的数据库并发扇出，`server_idle_timeout=120` 又让热连接很快回落。
2. 以往慢 SQL 验收主要比较 SQL 指纹和索引，没有把 PostgreSQL executor 时间、PgBouncer 后端连接事件和应用端总耗时放在同一时间轴。
3. 关键帧路径已经生成，但没有同步到结构化投影列；媒体下载接口只能读取完整任务 `result`。
4. ZSXQ 附件 PDF 元数据查询沿用整行 ORM，顺带物化未使用的 `raw_payload`。

## 解决思路

- 先用相同参数执行 `EXPLAIN (ANALYZE, BUFFERS)`，确认执行器是否真的慢；只有执行计划慢才进入索引设计。
- 保持足以覆盖常见页面扇出的 PgBouncer 后端热连接，并延长空闲连接和连接生命周期。
- 从入库端维护关键帧路径投影，热点下载接口只读取权限校验和文件定位所需列。
- 迁移、代码、worker 和后端必须作为一个版本单元完成验收。

## 实际方案

- PgBouncer 默认热连接数从 2 提升到 6，空闲回收从 120 秒延长到 600 秒，连接生命周期从 1 小时延长到 6 小时；参数仍可由环境变量覆盖。
- 在线 reload 后通过 `SHOW CONFIG`、`SHOW POOLS` 和 `SHOW SERVERS` 验证配置与 6 条后端连接已经生效。
- `publication_tasks` 增加 `report_audio_landscape_keyframe_path`，模型入库同步 `result.video.keyframes.landscape_cover.path`，迁移负责历史回填。
- 研报音频横屏关键帧下载改为窄投影读取；未知历史 key 继续走显式兼容路径，不牺牲历史业务完整性。
- ZSXQ PDF 元数据查询改为附件和 topic 的明确列投影，不再读取 `raw_payload`。
- 完整重启的 schema 门禁升级到本次 migration head，同时验证新列存在且与 JSON 来源一致；漏迁移会在停服务前 fail closed。

## 验证

- 精确复现参数下，PostgreSQL executor：窄父任务查询 `0.026ms`、完整关键帧任务查询 `0.018ms`、关键帧投影查询 `0.138ms`、浏览器子任务查询 `0.016ms`。
- 同一时窗应用日志分别记录 `3100ms` 和 `12130ms`，PgBouncer 同步出现 10 秒连接超时，证明确认瓶颈位于连接建立而非执行计划。
- PgBouncer reload 后：`min_pool_size=6`、`server_idle_timeout=600`、`server_lifetime=21600`，连接池 `cl_waiting=0`、`sv_idle=5`、`sv_used=1`、总后端连接 6。
- 定向测试 `63 passed, 4 subtests`；完整确定性回归 `789 passed, 5 skipped, 42 subtests`；数据库纪律门禁 `87 passed, 3 subtests`。
- Alembic 已到 `20260722_report_audio_keyframe (head)`；历史回填为 `source_rows=30 / projected_rows=30 / missing_rows=0`。
- 安全完整重启先后两次确认无不可中断任务，并冻结 17 个队列消费后替换旧进程；新后端 PID `37251`，17 个服务全部 running，Redis broker 正常，watchdog 与两个 scheduler 已恢复。
- 真实横屏关键帧请求由历史同一路径的 `19291ms` 降为冷请求 `152.89ms`，数据库投影查询 `62.18ms`；第二次缓存命中 `1ms`，两次均返回 200。
- 新 PID 观察窗只有一条 ZSXQ scheduler 启动窄查询 `1241.95ms`；没有 SQL 错误、statement timeout、QueryCanceled、锁超时或 PgBouncer 后端建连超时。

## 复发预防

- 任一应用侧 SQL 超过 500ms、但相同参数的 PostgreSQL executor 小于 10ms 时，禁止直接加索引；必须先检查 PgBouncer `connect timeout`、池等待和远端网络。
- PgBouncer 的 `min_pool_size` 必须不低于代表性冷页面的数据库并发扇出；重启验收必须记录 `SHOW CONFIG/POOLS/SERVERS`。
- 页面媒体接口只允许读取鉴权、文件定位和响应头所需列；完整 JSON 只能用于显式兼容或原始详情入口。
- 新增 JSON 资产路径时必须同步定义入库投影、历史回填、窄查询和契约测试，禁止只改读取端。
- 每次后端重启后的首个真实页面负载必须联合检查应用慢日志与 PgBouncer 日志，不能用单一日志宣告修复完成。

## 后续事项

- 保持复发簇开放，继续按新 PID 边界观察真实业务流量；连续两个回顾周期无同类事故后才允许系统性关闭。
- 若应用侧再次超过 500ms，先记录相同参数 executor、PgBouncer stats 和连接事件，再决定是否进入查询次数合并或远端网络治理。
