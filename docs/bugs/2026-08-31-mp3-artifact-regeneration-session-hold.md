# MP3 视觉素材重生成跨浏览器渲染持有 Session 并提交后整行回读

## 基本信息

- 状态：RUNTIME_REPLACED / OBSERVING；新 backend 已完成两次自然成功重生成，待完整 24 小时后窗确认
- 严重级别：P0
- 影响模块：MP3 视频编排详情 / 视觉素材重生成 / `publication_tasks` / PgBouncer
- 首次发现：2026-08-31 每日数据库巡检的第二次重启后运行态追踪
- 修复提交：当前工作区
- 根因分类：读事务跨浏览器/文件 I/O、提交后 ORM 过期整行刷新、宽 ORM 更新、并发覆盖缺少 CAS
- 复发键：`DB-SESSION-LIFECYCLE`、`HOT-PATH-PAYLOAD-POLLING`
- 是否复发：是；与历史 MP3 下载锁、封面浏览器、提交后 JSON 回读属于同一 Session/宽载荷根因簇
- 关联记录：`2026-08-06-stick-image-expired-orm-browser-session.md`、`2026-08-06-mp3-download-lock-session-hold.md`、`2026-07-21-report-child-create-post-commit-json-reload.md`
- 防线层级：detached_snapshot / session_boundary / optimistic_cas / sql_shape_contract / runtime_observation

## 现象与影响

第二次安全重启后的真实请求 `request_id=daab9c3a` 调用
`POST /api/mp3-video-workflows/jobs/15dd7a8f-76ad-49fd-86ac-21b3acf913e9/artifacts/xiaojunjun_background/regenerate`，
返回 200，但端到端耗时 `21,702.34ms`。

- 首次 `load_task` 整行读取 `params/result` 耗时 `12,558.96ms`，1 行，SQL 指纹
  `590220bb0dd49238`；
- ORM 更新除完整 `result` 外还写入多个投影列，耗时 `1,032.29ms`；
- commit 后 `_job_payload(task, include_raw=True)` 访问已过期 ORM，再次整行读取，耗时
  `2,607.81ms`，SQL 指纹 `773ba32db4924cf2`；
- Session 生命周期告警为 `16,792.92ms`，其中 SQL `13,591.25ms`、非 SQL 持有
  `3,201.67ms`。非 SQL 区间覆盖 Playwright/浏览器封面生成与文件系统工作。

真实任务的 `params` 仅 `4,300` bytes、`result` 存储 `12,089` bytes、文本约 `32,401`
bytes；链路恢复后同一行体积查询为 `53.76ms`，`jsonb_each` 为 `28.74ms`。因此 12.6 秒首读
主要是 PgBouncer/网络尾延迟，不是 PostgreSQL executor 或 JSON 体积；但应用把一次网络长尾继续放大成
长连接占用、渲染期持有与第二次整行网络往返。

## 根因

旧路由读取 ORM `PublicationTask` 后直接调用 `regenerate_visual_artifact()`。该函数会启动浏览器上下文、
渲染画布并写图片，但读事务及连接仍由请求 Session 持有。生成后又给 ORM 的完整 `result` 赋值；模型
`before_update` 投影同步使 SQL 写入多个与本次素材变化无关的投影列。commit 默认过期 ORM，响应构建再次
访问 `task.params/result`，触发整行刷新。

同时，读取与生成之间没有版本比较；若用户在渲染期间投递视频重生成或其他请求更新任务，旧快照会覆盖
新状态的 `result`。这不是加索引、放宽 timeout 或重试可以根治的问题。

## 实际方案

- 初次读取后立即把响应所需标量、`params` 与完整 `result` 深拷贝成 `SimpleNamespace` DTO；运行
  `assert_db_session_clean` 后 rollback/remove，浏览器和文件 I/O 阶段不再持有数据库事务。
- 文件生成完成后只更新 `result` 与 `updated_at`，不触发 ORM mapper 的宽投影写回。
- 更新条件同时比较任务 ID、读取时 `status` 和 `updated_at`。任何并发状态/版本变化均返回 409，保留
  最新数据库结果，旧请求不得覆盖。
- 在浏览器生成前预取 B站子任务摘要并再次释放短事务；commit 后先释放 Session，再写本地结果 JSON、
  清缓存。响应直接使用内存 DTO，不再刷新 `publication_tasks`，并把新详情写入版本化缓存。
- 保留完整原始 `params/result`、素材历史、日志与原始 JSON 审计；没有删字段、伪造数据或执行 DDL。

## 验证

- TDD 红灯：旧实现进入 mocked 浏览器生成时 `Session.in_transaction()` 为真；并发更新测试还触发 detached
  ORM 访问/旧快照覆盖路径。
- 修复后 2 项根因契约通过：生成阶段无事务；SQL 只有一次 `result + updated_at` CAS UPDATE，UPDATE 后
  不再 SELECT `publication_tasks`；并发把任务切到 `processing` 时接口返回 409，`concurrent_marker`
  保持且新素材不覆盖。
- MP3 API、workflow service、DB projection 定向回归：`189 passed, 3 subtests passed`。
- 最终完整 unit：`1,533 passed, 3 skipped, 305 subtests passed`；单项根因回归持续为绿。
- 09:50 与 09:52 两次重启门分别因真实 PDF 清洗和其发布子任务 fail closed；任务自然结束后，
  10:01 安全门读取 47 条任务、0 条活动 sync 并放行。全程未使用 FORCE。
- 标准整栈重启后的 backend PID 为 `70045`，`/health` healthy；bootstrap 17/17 Celery service
  `ok=true`、`errors=[]`，MP3、ZSXQ publish、content-pipeline worker 均 ready。
- 无副作用真实请求 `48d60e3b` 在新 PID 上对同一任务提交空标题，预期返回 400/5,009.71ms。
  首次快照 SELECT 为 3,860.18ms，B站子任务预取为 120.59ms；日志中没有 UPDATE、没有浏览器/文件
  写入，也没有提交后的任务整行 SELECT。这证明新路由与“外部 I/O 前释放/不再二次回读”路径已加载。
- 随后自然成功 request `86e70f97` 与 `ffa15b05` 分别 200/3,416.01ms、200/4,150.20ms。两者
  SQL 顺序都只有“任务快照 SELECT（364.90/244.02ms）→ child 预取 SELECT（131.34/251.50ms）→
  单次 UPDATE（155.65/160.09ms）”；UPDATE 后无任务 SELECT，下载请求随即 200。相较旧请求
  21,702.34ms，新最大值改善 80.9%，且真实 renderer + CAS 成功路径已捕获。
- 该成功短窗仍不能替代完整后窗；相邻 `continue-full-video` 另暴露同根因的 commit 后回读，已独立记录
  为 `2026-08-31-mp3-continue-full-video-post-commit-reload.md`。本问题保持
  `RUNTIME_REPLACED / OBSERVING`，不能标记 CLOSED。

## 复发预防与下一轮验收

- 所有人工素材生成、浏览器渲染、文件 I/O 前必须冻结 DTO 并释放 Session；测试必须在外部函数入口断言
  无活动事务。
- 对共享任务 JSON 的人工修改必须使用版本 CAS，禁止“读整行 → 长 I/O → 无条件覆盖”。仅改 JSON
  审计载荷时不得借 ORM 钩子重写无关投影列。
- 已验证新 backend PID/health、无副作用 400 和两次自然成功 200；成功路径均仅一次 UPDATE、commit 后
  无任务整行 SELECT。下一完整 24 小时窗口要求 artifact route 的 post-commit `773...` 和跨 I/O
  Session hold 均为 0；必要的初始 `590...` 每请求至多一次，并必须在 renderer 前释放。网络长尾作为
  `DB-SESSION-LIFECYCLE` 基础设施阻塞另行保持 OPEN。完整后窗未确认前保持 OBSERVING。
