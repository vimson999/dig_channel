# MP3 从关键帧续跑完整视频在两次 commit 后重复整行回读

## 基本信息

- 状态：RUNTIME_REPLACED / OBSERVING；安全门放行并完成标准重启，待下一次自然入口与完整 24 小时验收
- 严重级别：P0
- 影响模块：MP3 视频编排 / `continue-full-video` / retry / regenerate-videos / `publication_tasks`
- 首次发现：2026-08-31 MP3 素材修复重启后的自然成功流量
- 修复提交：当前工作区
- 根因分类：commit 后过期 ORM 回读、宽 JSON 载荷、broker ID 宽写、响应阶段再次查库
- 复发键：`DB-SESSION-LIFECYCLE`、`HOT-PATH-PAYLOAD-POLLING`
- 是否复发：是；与同日素材重生成以及历史 expired ORM / post-commit JSON reload 属同一根因簇
- 关联记录：`2026-08-31-mp3-artifact-regeneration-session-hold.md`、`2026-07-21-report-child-create-post-commit-json-reload.md`、`2026-07-16-zsxq-expired-orm-full-row-refresh.md`
- 防线层级：detached_snapshot / broker_boundary / narrow_state_update / sql_order_contract / runtime_observation

## 现象与影响

第二次重启后的 backend PID `70045` 上，自然 request `8b097844` 调用
`POST /api/mp3-video-workflows/jobs/35a5f9d7-b0e4-4ff6-85a0-b6df0d14d69c/continue-full-video`，
虽然返回 200，但端到端耗时 `18,561.90ms`，三条慢 SQL 累计 `13,500.44ms`。

1. 初次 `_get_owned_task` 读取必要 `params/result`，指纹 `590220bb0dd49238`，耗时
   `7,475.88ms`；首个连接租约总持有 `8,156.80ms`，其中 SQL `7,858.15ms`、非 SQL仅
   `298.65ms`。
2. `_prepare_keyframe_job_for_full_video` 后第一次 commit 使 ORM 过期；代码在 broker 参数/后续写回
   继续访问 `task`，触发指纹 `773ba32db4924cf2` 的整行 SELECT，耗时 `3,884.66ms`。
3. 写入 `celery_task_id` 并第二次 commit 后，清缓存和 `_job_payload(task)` 再访问同一过期 ORM，
   又触发同指纹整行 SELECT `2,139.90ms`；随后还查询 B站子任务 `412.17ms`。

同一时段两个已修素材重生成成功请求只需 244–365ms 首读、131–252ms child 预取和 156–160ms
窄 UPDATE，说明 PostgreSQL 行本身并非稳定慢；本请求主要受远端网络/PgBouncer 尾延迟影响，而应用
又以两次完全不必要的 commit 后回读把它放大到 18.6 秒。

## 根因

旧入口把 ORM `task` 同时当作事务实体、broker 参数来源和 HTTP 响应 DTO。第一次 commit 后仍用
`task.id`/`task.celery_task_id`，SQLAlchemy 为恢复过期对象加载整行；第二次 commit 后又把同一对象传入
`_job_payload`，再次加载 `params/result`。celery id 的写回还走 ORM mapper，而它只需要一个窄列更新。

`retry` 与 `regenerate-videos` 使用完全相同的“commit → 访问旧 ORM → broker → ORM 写 celery id →
commit → 用旧 ORM 响应”结构，因此只修现场入口会在下一次流量中复发。

## 已落地修复

- 新增共享 `_commit_and_enqueue_prepared_job`：在第一次 commit 前冻结完整响应 DTO、稳定 task id 和
  B站 child 摘要；commit 后立即 release/remove 并运行 Session clean assertion。
- broker 只接收字符串 task id，不再接触过期 ORM；成功后以无同步 bulk UPDATE 只写
  `celery_task_id`，失败后只写 `status/current_step/error_message/completed_at`。
- 第二个短事务结束后再次 release/remove；清缓存和 `_job_payload` 全部使用 detached DTO 与预取 child，
  不再查询任务主体。
- `continue-full-video`、`retry`、`regenerate-videos` 三个同构入口统一接入，避免按端点重复修症状。
- 完整 `params/result`、规划、素材、日志和原始审计 JSON 均保留；没有执行 DDL、回填或超时掩盖。

## 验证

- TDD 红灯先证明旧入口在 mocked broker 调用前 `Session.in_transaction()` 为真，原因正是 commit 后
  读取过期 `task.id`；接口因此走 502。
- 修复后成功路径锁定：child 预取先于 enqueue、broker 入口无事务、恰好两次 UPDATE，第一次业务状态
  写入后不再出现带 `params/result` 的 SELECT，最终 celery id UPDATE 不含 params/result/status。
- broker 失败路径锁定：同样没有 commit 后宽 SELECT，最终失败 UPDATE 不含 params/result，持久化状态
  为 `failed/failed` 且保留真实 broker 错误。
- MP3 API + workflow service + DB projection 定向：`192 passed, 3 subtests passed`。
- 最终完整 unit：`1,533 passed, 3 skipped, 305 subtests passed`；compileall/diff/registry/Bug strict
  均通过。
- 10:28 安全门读取 51 条近期任务、0 条活动 sync，但检测到 publisher
  `392799b4…/upload_bilibili/10%` 与 `c1061e9a…/synthesis/10%`，因此 fail closed；未用 FORCE。
- 10:41 再次门禁读取 47 条近期任务、0 条活动 sync，确认无不可中断任务；标准脚本冻结消费、关闭 API
  后又执行二次门禁并通过，未用 FORCE。新 backend PID `78053`，`/health` healthy，bootstrap
  17/17、`errors=[]`；MP3、content-pipeline 与 ZSXQ publish worker 均重新连接 Redis 并 ready。
- 10:42 后窗仅捕获一条 scheduler 的 `system_configs` 读取 2,070.26ms，未捕获 SQL error、PgBouncer
  timeout、长 Session 或新的 continue/retry/regenerate 自然请求。故代码已由运行进程替换，但目标成功
  调用链仍无真实新样本，不能标记 CLOSED。

## 复发预防与下一轮验收

- 任何“准备任务 → commit → broker → 写 celery id → 构建响应”入口只能跨阶段传 ID/DTO；celery id
  和 enqueue failure 必须是窄写，SQL 顺序测试必须拒绝第一次 UPDATE 后的宽任务 SELECT。
- 等待下一次真实 `continue-full-video`、retry 或 regenerate-videos：允许每请求一次必要的初始
  `590...` 快照读取，
  但 commit 后 `773...` 宽回读必须为 0，broker 阶段无 Session，celery id 只有一次窄 UPDATE。
- 旧 request 18.562s 作为基线；新样本需同时报告端到端、初始 SQL、child 查询、窄 UPDATE 和 Session
  hold。完整 24 小时后窗确认旧“两次 commit 后回读”调用链不复发前保持 OBSERVING，不得 CLOSED。
