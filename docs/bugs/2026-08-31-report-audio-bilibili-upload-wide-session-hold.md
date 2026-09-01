# 报告音频 B站上传提交宽读父任务并跨文件探测持有 Session

## 基本信息

- 状态：RUNTIME_REPLACED / OBSERVING；代码、完整测试和标准安全重启已完成，待下一次自然成功提交与完整 24 小时后窗
- 严重级别：P0
- 影响模块：报告音频工作台 / `POST /api/report-audio-workflows/jobs/<id>/bilibili-upload` / `publication_tasks` / platform_upload broker
- 首次发现：2026-08-31 对 MP3 B站提交根因做全调用点复核时
- 修复提交：当前工作区
- 根因分类：父任务大 JSON 宽读、Session 生命周期、文件 I/O 交叉持有、重复数据库往返
- 复发键：`DB-SESSION-LIFECYCLE`、`HOT-PATH-PAYLOAD-POLLING`
- 是否复发：是；报告音频入口复制了历史 B站提交的完整 ORM 父任务、凭据点查和 Session 跨文件探测模式
- 关联记录：`2026-07-18-bilibili-upload-json-polling-and-online-ddl-lock-convoy.md`、`2026-07-24-db-session-crossed-broker-and-remote-io.md`、`2026-08-31-mp3-bilibili-upload-wide-parent-double-commit.md`
- 防线层级：narrow_parent_snapshot / joined_credential_projection / detached_path_candidates / session_release_before_probe / bounded_child_fallback / sql_order_contract / runtime_observation

## 现象

MP3 B站上传提交完成窄化后，调用点审计发现报告音频的同名入口仍通过 `_get_owned_task` 加载完整
`PublicationTask.params/result` 与全部媒体投影；随后在同一请求中解析视频、查找 visual/publisher child、
探测多个视频和封面文件，并另做凭据显示名点查。旧实现的真实 route 契约测试记录到 11 次文件探测，
11/11 次 `db.session().in_transaction()` 都为 `True`：读事务与连接租约跨越了文件系统 I/O。

生产只读样本任务 `38414dee-1ab9-43f0-966e-2c8a7ce5d4e1` 的 `params=2,409B`、
`result=18,644B`，合计 `21,053B`。旧完整 ORM 点查驱动耗时 `606.98ms`；其 PostgreSQL planning
仅 `0.159ms`、execution `0.041ms`，3 shared hit/0 read。说明等待不在 executor、锁或磁盘，而在
PgBouncer/网络与宽载荷传输，并被应用延长为文件探测期间的 Session 持有。

## 影响

- 每次报告音频 B站提交都搬运与命令无关的原始审计 JSON 和媒体投影，放大远端数据库尾延迟。
- 视频/封面文件探测和 child fallback 期间占用数据库 Session；并发提交会增加连接租约与排队风险。
- 凭据显示名独立点查，父/child/凭据多次往返会把一次网络抖动叠加到用户等待。
- 若只修 MP3 入口而不复核报告音频同构调用点，同一根因会以另一页面继续复发。

## 根因

1. 路由为鉴权和少量路径/标题字段加载完整 ORM 父任务；`params/result` 的审计载荷被无条件物化。
2. 路径解析 helper 在数据库 Session 尚未释放时调用 `_path_exists`，将文件 I/O 与读事务交叉持有。
3. visual/publisher child fallback 查询后直接继续文件探测，没有明确 rollback/remove 边界。
4. 凭据名通过独立查询获取，增加一次远端数据库往返。
5. 旧 helper 隐含依赖请求 `params.report_audio_workflow` 与 result PDF/视频多个兼容位置；直接删字段或只保留
   单一路径会破坏历史任务、result-only PDF 元数据和专用 B站封面优先级，因此必须以候选快照兼容。

生产证据进一步排除了索引误诊：新父任务+凭据轻投影 planning `0.300ms`、execution `1.684ms`、
118 shared hit/0 read，驱动 `105.85ms`；相对旧完整 ORM 驱动改善约 `82.6%`。同轮只读诊断中连
`SET TRANSACTION READ ONLY` 都出现 `98–420ms` 等待，child fallback 驱动 `69.42ms`（数据库日志
query `28.43ms`）。底层仍是网络/PgBouncer 长尾，应用根因是宽读、多往返和跨 I/O 持有；没有
CREATE/DROP INDEX 证据。

## 解决思路

把提交所需的父任务身份、PDF 元数据、标题、视频/封面候选和凭据显示名冻结成一次 detached 命令快照；
数据库阶段只收集字符串候选，不做文件 I/O。每个必要 fallback 查询结束后立即 rollback/remove，随后
再按原业务优先级做文件探测。保留全部原始 JSON 审计数据、历史兼容位置和专用封面语义。

## 实际方案

- 新增 `_report_bilibili_upload_parent_snapshot`：一条父任务+凭据 LEFT JOIN，只投影鉴权 scalar、必要
  JSON scalar 和路径候选；正常路径不加载完整 `params/result`。
- 兼容真实嵌套键 `params.report_audio_workflow`，同时保留 result-only `pdf.filename`，避免历史任务标题中的
  机构和日期丢失。
- 父快照返回 detached `SimpleNamespace`；路由取得快照后立即 `release_db_session_quietly()`，再探测
  视频或封面文件。
- standard/visual/child fallback 都只查询路径字符串候选；fallback 查询结束后再次释放 Session，文件
  探测期间没有活动事务。
- 保持默认封面优先级：父任务专用 B站封面优先；否则已完成 publisher child 的专用 B站封面优先于父任务
  generic cover；用户显式选择仍按原合同处理。
- 把 JOIN 得到的 `credential_name` 传给共享 child 创建 helper，不再额外点查凭据。
- 共享 B站 enqueue helper 已由同轮 MP3 根因修复为首事务预存 broker UUID、成功路径无第二次写；报告
  音频入口直接复用，没有再复制 broker 后 UPDATE。
- 未删除、压缩或改写任何原始 JSON；未执行生产 DDL、回填、timeout、限流或重试掩盖。

## 验证

- TDD 红灯：100KB 审计载荷的真实 route 测试在旧实现中业务返回成功，但 11/11 次文件探测都看到活动
  数据库事务；修复后所有探测均为 `False`，父任务/凭据恰好一条 JOIN，无完整 JSON alias、无独立凭据 SELECT。
- 新增 result-only PDF 元数据回归，确认标题仍含真实机构“广发证券”和日期 `2026-08-30`。
- 新增 child 专用 B站封面回归，确认 completed child cover 优先于父任务 generic cover，且 child fallback
  后所有文件探测仍无事务。
- 报告音频 API 文件 `79 tests OK`；报告音频、MP3、DB projection 与 task projection 交叉定向
  `189 tests OK`；PDF 合并下载响应的两处测试句柄也已显式关闭，`-W error::ResourceWarning` 6/6 通过。
- 完整 unit 首轮在受限沙箱仅因 loopback bind 出现 34 个 `PermissionError`；允许纯测试 loopback 后
  `Ran 1499 tests in 73.792s`、`OK`。
- `compileall -q src/backend src/frontend scripts/diagnostics scripts/dev`、registry JSON 与
  `git diff --check` 通过；最终 Bug strict 见本轮巡检报告。
- 生产只读样本：旧 JSON `21,053B`，新快照序列化 `2,517B`，减少约 `88.0%`；旧/新驱动
  `606.98/105.85ms`，改善约 `82.6%`，而 executor 均为毫秒级。
- 只读基线诊断在 12:37 人为产生 SQL 指纹 `fffe03373e38e2e6`，process=`-c`、origin=`unknown`；这是
  明确登记的诊断探针，不得误计为自然复发，也不能用其后的未捕获代替自然业务验收。
- 12:47 安全门读取 47 条近期任务、0 条活动 sync；标准脚本冻结前后二次门禁均放行，未用 FORCE。
  新 backend PID `92009` healthy；bootstrap 17/17 Celery service `ok=true`、`errors=[]`。
  platform_upload、report_audio_workflow、mp3_video_workflow 分别于 12:48:50、12:49:00、12:49:04 ready。
- 12:48 后短窗没有目标旧指纹、SQL error、PgBouncer timeout、server closed、长 Session 或报告音频
  B站提交 POST。没有制造外部上传或重复 child，因此只证明代码与 worker 已替换，不能证明自然成功路径已闭环。

## 复发预防

- 所有 B站上传提交入口必须共享“命令轻快照 → release/remove → 文件/网络/broker I/O”合同；不得逐页面
  复制完整 ORM 父任务读取。
- 路径 helper 只能收集字符串候选，不得在查询 helper 内做 `exists/is_file/resolve`；每次 fallback 查询后
  必须显式释放 Session，再进入文件系统。
- 历史兼容必须以有界 JSON scalar 投影实现；不得为了窄化删除原始 JSON、字段完整性或专用封面优先级。
- 测试必须对每次文件探测记录 `in_transaction()`，并锁定父任务/凭据 SQL 数量与完整 JSON alias 为 0。
- 运行态将自然 request id、SQL origin、指纹、Session hold、broker ID 与 child 终态联合对账；诊断进程
  `-c` 的探针必须单独标注，不能混入自然复发统计。

## 后续事项

- 下一次自然成功报告音频 B站提交必须证明：父任务仅一条轻 JOIN 快照，无完整 `params/result`；所有
  视频/封面探测与 broker 阶段 Session 为 0；child fallback 只在必要时发生且查询后立即释放。
- 对账标题中的机构/日期、横竖屏视频、用户选封面/默认专用封面、凭据显示名和 child 终态，确认字段完整性
  没有回归；共享 enqueue 成功路径不得出现 broker 后第二次 UPDATE/commit。
- 统计该端点请求量、成功/失败、累计、P50/P95/max，并与本轮只读驱动/载荷基线及 MP3 同类链路对比。
- 完整 24 小时自然旧宽读/跨文件 hold 为 0、失败率为 0，且后续窗口再次确认前，不得标记 CLOSED。
  PgBouncer/网络根因继续 `OPEN / REPEATED`。
