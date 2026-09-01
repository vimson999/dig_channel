# MP3 B站上传提交宽读父任务并在 broker 后二次提交

## 基本信息

- 状态：RUNTIME_REPLACED / OBSERVING；代码、完整测试和标准安全重启已完成，待下一次自然成功提交与完整 24 小时后窗
- 严重级别：P0
- 影响模块：MP3 视频详情 / `POST /api/mp3-video-workflows/jobs/<id>/bilibili-upload` / `publication_tasks` / platform_upload broker
- 首次发现：2026-08-31 第四次安全重启后的自然 B站上传提交流量
- 修复提交：当前工作区
- 根因分类：父任务大 JSON 宽读、数据库往返放大、Session 生命周期、broker 后二次事务
- 复发键：`DB-SESSION-LIFECYCLE`、`HOT-PATH-PAYLOAD-POLLING`
- 是否复发：是；与历史 `publication_tasks` 大 JSON 热路径、跨 broker Session 和 commit 后 broker ID 回写属于同一根因簇
- 关联记录：`2026-07-18-bilibili-upload-json-polling-and-online-ddl-lock-convoy.md`、`2026-07-24-db-session-crossed-broker-and-remote-io.md`、`2026-08-31-mp3-continue-full-video-post-commit-reload.md`
- 防线层级：narrow_parent_snapshot / joined_credential_projection / preassigned_broker_id / one_commit_success_path / session_release / sql_order_contract / runtime_observation

## 现象

10:42–11:22 的自然流量捕获 4 次 B站上传提交，全部成功，但端到端分别为
`11,115.04/4,746.24/5,765.53/4,283.42ms`，累计 `25,910.23ms`，P50
`5,765.53ms`、P95/最大 `11,115.04ms`。最慢 request `2aad47a5` 首先以 SQL 指纹
`590220bb0dd49238` 读取父任务完整 `params/result` 和全部投影列，单条 SQL `8,915ms`；首段
Session hold `9,046.21ms`，其中 SQL `9,001.05ms`、非 SQL仅 `45.16ms`。broker 发布后又以指纹
`ef9898665a613dd7` 更新 `celery_task_id`，耗时 `505.81ms` 并产生第二次 commit。

两条父任务的 JSON 已增长到 `params=6,848/7,876B`、`result=40,190/41,042B`。旧查询一次序列化
样本为 `47,436B`，而提交实际只需要视频路径、标题、六个封面路径、owner 和一个凭据显示名。

## 影响

- 每次提交把约 47KB 父任务 JSON 和大量无关投影列跨 PgBouncer/远端网络搬到应用；网络尾延迟会直接
  放大用户等待、连接租约和并发请求排队。
- 凭据显示名另做一次点查；child/projection 首事务完成后，broker 返回再开启第二个数据库事务写
  `celery_task_id`，正常成功路径承担不必要的数据库往返和 commit ACK 不确定窗口。
- broker 消息发布时数据库里的 `celery_task_id` 尚未是当前消息 ID，削弱 worker 当前 ID 防陈旧投递门禁。
- 若误把横竖屏/不同账号提交当作重复投递并加粗暴去重，会破坏现有业务意图，因此必须先完成生产对账。

## 根因

底层网络根因、应用放大器和重复投递必须分开判断：

1. 生产只读 `EXPLAIN (ANALYZE, BUFFERS)` 显示旧父任务点查 executor `0.035ms`、planning
   `0.138ms`、3 shared hit/0 read；新 JOIN 轻投影 executor `0.917ms`、planning `0.306ms`、
   77 shared hit/0 read。两者均不足 1ms，而驱动交替样本仍出现 `85.82–204.42ms` 最大等待，最终轻
   查询 5 次的 P50/P95/最大仍为 `34.31/174.32/174.32ms`。故秒级长尾属于 PgBouncer/网络等待，
   不是 PostgreSQL executor、锁、磁盘或缺索引。
2. 旧路由用完整 ORM 父任务服务一个窄提交命令，又单独查询凭据；创建 child 后 commit，broker 发布后
   再 UPDATE/commit broker ID。远端往返长尾因此被放大成宽读、多往返和双事务。
3. 生产只读对账证明 4 个 child 是 4 个不同意图：父任务 `35a5f9d7…` 分别为竖屏账号
   `…903aec46` 与横屏账号 `…ca981800`，父任务 `7bb3ec64…` 也分别为横/竖屏两账号；四个 intent
   hash 均不同且任务都 completed。这里没有重复 delivery，不能添加会吞掉合法提交的去重键。

## 解决思路

在不删除原始 `params/result`、不减少标题/封面/凭据功能且不改变上传意图语义的前提下，把提交命令在
一次窄数据库快照中冻结；文件探测和 broker 前释放 Session。broker ID 在 child 首次 INSERT 时预分配并
与 child/projection 同事务持久化，使正常成功路径不再有 broker 后 UPDATE/第二次 commit，同时保留异常
broker ID 不一致和入队失败的持久化收口。

## 实际方案

- 新增 `_bilibili_upload_parent_snapshot`：正常路径按标量 `feature/user_id` 鉴权，以一条 LEFT JOIN
  查询视频路径、计划标题、所需封面 JSON scalar 和凭据显示名；历史缺投影任务才进入有界 JSON fallback。
- 父快照冻结为 detached `SimpleNamespace` 后立即 `release_db_session_quietly()`，再执行视频/封面文件
  校验；仅在结构化标题均缺失时，以 7 个 response scalar 做一次有界 fallback，并再次释放 Session。
- `create_bilibili_upload_task` 可接收已 JOIN 的凭据名；其他调用方保持原点查兼容。原始 parent/child JSON
  审计数据和所有上传字段完整保留。
- child 在首事务中预分配 UUID `celery_task_id`；`enqueue_bilibili_upload_task` 把同一 ID 传给 broker。
  正常成功不 SELECT、不 UPDATE、不二次 commit；只有历史缺 ID、broker 返回异常 ID 或入队失败才窄写。
- 未添加基于父任务的提交去重：生产对账已证明横/竖屏和账号维度是合法的不同业务意图。
- 未执行 CREATE/DROP INDEX、ALTER、回填或生产配置修改；executor 与 buffer 证据明确反对 DDL。

## 验证

- TDD 红灯一：旧 enqueue 返回的 broker ID 为空且成功路径依赖后置写回；修复后锁定预分配 ID 与 broker
  `task_id` 完全一致，成功路径 `publication_tasks` SELECT/UPDATE 均为 0。
- TDD 红灯二：旧真实 route 测试捕获父任务完整 `params/result` 且没有凭据 JOIN；修复后 100KB 审计字段
  不进入查询，父任务/凭据恰好一次 JOIN，文件探测回调确认 `in_transaction=False`。
- 定向新增 2 项通过；B站上传 API/service 相关 `89 tests OK`；数据库投影纪律 `81 tests OK`。
- 完整 unit 首次在受限沙箱中仅 34 项 loopback bind 报 `PermissionError`；最终以正确
  `PYTHONPATH` 和纯测试 loopback 权限运行 `Ran 1496 tests in 75.952s`、`OK`。完成门禁期间还修复了
  一个独立的本机 bridge 413/ECONNRESET 缺陷，见 `2026-08-31-frontend-oversized-form-connection-reset.md`。
- `compileall -q src/backend src/frontend scripts/diagnostics scripts/dev`、变更文件 `py_compile`、registry JSON、
  Bug strict 和 `git diff --check` 均通过。
- 生产只读 helper 对同一真实父任务：旧序列化 `47,436B`，新快照 `2,406B`，传输体积减少
  `94.9%`；公开标题、视频/封面路径和凭据显示名保持一致。
- 11:55 安全门读取 47 条近期任务、0 条活动 sync，确认无不可中断任务；标准脚本冻结后再次门禁通过，
  未用 FORCE 重启。新 backend PID `86809` healthy，bootstrap 17/17 Celery service `ok=true`、
  `errors=[]`；platform_upload 与 mp3_video_workflow 分别于 11:56:45/11:56:56 ready，watchdog 已启动。
- 11:56 后短窗未出现 SQL error、PgBouncer timeout、server closed、5xx 或目标旧指纹；仅两个 scheduler
  读取极小 `system_configs` 时出现 `1,542.74/1,414.07ms` 慢 SQL，继续证明网络尾延迟未闭环。
- 重启后尚无自然 `bilibili-upload` POST；没有为验收制造真实外部上传或重复生产 child。因此代码和进程已
  替换，但真实成功提交的 SQL 顺序、端到端耗时及完整 24 小时未复发仍待验收，状态保持 OBSERVING。

## 复发预防

- 提交/入队 API 必须以显式命令投影读取父任务；不得用完整 `PublicationTask` ORM 为视频路径、标题等少量
  scalar 服务，也不得在文件、浏览器、网络或 broker I/O 期间持有读 Session。
- broker/Celery ID 必须在消息发布前与业务 task 同事务持久化；成功路径不得在发布后再回读或二次更新。
  异常分支只能窄写，并由 SQL 顺序测试覆盖成功、ID 不一致和发布失败。
- “同父任务出现多个 child”不能直接等价为重复投递；必须把 video key、credential、charging/season
  等组成稳定 intent 后对账。只有完整 intent 相同且 delivery 身份重复才允许进入幂等复用策略。
- 运行态按旧 `590220bb0dd49238`、`ef9898665a613dd7`、新 origin、request id 与 Session hold 联合复核；
  executor 毫秒级而驱动秒级时优先检查 PgBouncer/网络，不得盲目加索引或抬 timeout。

## 后续事项

- 下一次自然成功提交必须证明：父任务仅一条轻 JOIN 快照、无完整 `params/result`、文件探测与 broker 阶段
  Session 为 0、broker ID 与首事务一致、成功路径无 `ef9898665a613dd7` 或第二次 commit。
- 统计新端点请求量、成功/失败、累计、P50/P95/max，并与本轮 4 次/25.910 秒/P95 11.115 秒基线对比；
  同时对账 child intent，确认没有丢合法横竖屏/账号提交，也没有真正重复 delivery。
- 完整 24 小时旧宽读和 broker 后 UPDATE 指纹为 0、失败率为 0、无长 Session/5xx，并在下一后续窗口
  再次确认前，不得标记 CLOSED。PgBouncer/网络根因继续 `OPEN / REPEATED`。
