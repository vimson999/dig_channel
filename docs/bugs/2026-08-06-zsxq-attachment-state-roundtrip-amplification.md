# ZSXQ 附件下载状态逐条往返放大

## 基本信息

- 状态：代码、回归与新 worker 边界证据已验证；继续观察新增下载长尾
- 严重级别：P1
- 影响模块：ZSXQ 同步 / `zsxq_attachments` / 下载状态 / Session / PgBouncer
- 首次发现：2026-08-06 10:12–10:25
- 修复提交：2026-08-06（当前工作树）
- 根因分类：数据库往返放大 / 远端连接等待 / 下载状态热写
- 复发键：`DB-SESSION-LIFECYCLE`
- 是否复发：与 2026-07-21 ZSXQ 点查放大、2026-07-22 发布附件状态 N+1 属同一热路径家族

## 现象

新 PID `47594` 启动的旧 ZSXQ worker 在一次自动同步中，`_update_attachment_download_state`
逐附件执行状态 UPDATE。10:04:21–10:25:10 分析窗口共 219 条慢 SQL，其中附件状态写 74 条、
累计 234,370.98ms，P50/P95/最大 3,360.26/5,303.02/5,989.57ms；另有 19 次连接持有来自
该入口，累计 112,009.70ms。SQL 只是按主键窄更新，但远端 PgBouncer/网络等待被每个附件重复支付。

## 影响

- 连接持有 P50/P95/最大为 6,491.95/15,323.20/33,141.78ms；附件逐条状态写与失败重试互相
  放大，页面/API 会看到同步进度长期不变。
- 这不是缺失索引证据：条件是附件主键，且慢度在分钟级 1–6 秒波动；同一窗口还出现 HBA 拒绝和
  cached server-login failure，不能把网络登录故障误报为 executor 慢。

## 根因

`_download_topic_attachments` 已在外部下载前提交并释放 Session，但下载完成/失败后仍逐条回写
`download_status/local_path/error_message`，每个状态转换一次远端 UPDATE 和一次 commit。新主题与附件
也在每个主题内 `flush()`，使 INSERT round-trip 按主题重复。原始 JSON 与业务字段必须保留，不能通过
删载荷或跳过状态解决。

## 解决思路

保留外部 I/O 前的 Session 边界，把状态转换收集为不可变标量映射，按字段形状使用 Core
`executemany`，在下一次外部下载前或批次阈值到达时提交；为新主题/附件预分配 UUID，页边界统一 flush，
并禁止外链扫描 SELECT 自动逐条 autoflush。失败时先 rollback，当前附件保持可重试的 pending 状态。

## 实际方案

- 新增 `_apply_attachment_download_state_updates`，按状态字段形状批量 UPDATE，并保留
  `IS DISTINCT FROM` 无变化保护。
- 自动下载循环不再逐附件 `_load_attachment_for_download_update`/commit；成功、失败、复用和跳过状态
  进入批次队列，外部下载前强制 flush/commit，禁止跨 I/O 持有连接。
- 新主题和附件在自动同步路径预分配 UUID，`_upsert_attachments(..., defer_flush=True)`，页末提交；
  不执行 DDL、回填、删除原始 JSON 或改变字段完整性。手动附件计划保留原有窄路径。

## 验证方式

- 定向 ZSXQ/投影纪律测试 115 tests 通过，包含“两个附件状态只发一条 UPDATE”与数据库断连后仍可重试，
  以及下载提交边界不提前刷主题 UPDATE 的回归。
- `compileall`、完整 unit、Bug 严格归类和 `git diff --check` 已在最终重载前重跑；最新一次完整
  unit 为 1,033 tests（11:52:34），定向 115 tests、Bug 严格检查 148/148 均通过。
- 页内新主题边界又补了一层 `no_autoflush`/一次性提交保护：无附件下载的新主题不会被评论或外链
  扫描逐条 flush；页心跳从快照提交后计时，保留最多 30 秒心跳和页末强制提交。
- 11:07 门禁全绿后曾重启到后端 PID `55059`/ZSXQ worker `55454`，但追补的显式
  `flush/no_autoflush` 代码是在该 worker 启动后才加入；旧 worker 的正向
  `f3545365-4503-4dc0-93fe-80d396497d2d` 轮次（4 页/78 topics，下载 17）捕获了 25,091.23ms
  隐式 `INSERT ZSXQ_ATTACHMENTS` 与 26,449.39ms 连接持有。
- 第二次重启后的 worker `58697` 已载入显式边界；11:32–11:34 的任务没有再出现
  `INSERT ZSXQ_ATTACHMENTS` 或 26 秒持有，附件状态 UPDATE 多次可影响 9 行，但该轮下载数为 0、
  复用 137，未覆盖新增附件 INSERT。11:47–11:49 又完成安全门禁与重载（新后端 PID `60726`，
  worker 11:49:15 ready），当前代码已包含主题下载边界批量修复；下一轮必须用真实新增下载确认
  “先 flush 新行→状态批量 UPDATE→commit”仍无隐式 INSERT 错位。
- 新 worker 随后运行任务 `e5ce5e4f-38f3-456b-987f-b0b763c1c55d`（11:50:43–11:52:21）：附件 503，
  已下载/复用 137，本轮新增下载 0、失败 2，因此没有产生可用于验收的新增附件 INSERT。主题投影
  已从 78 次单行写降为 7 次批量写，说明下载开启时的页边界不会再把主题批量强制拆散；但不能将此
  结果扩大解释为附件新行路径已验收。
- 12:14:18 新 worker 运行态已捕获新增路径的窄 INSERT：`INSERT ZSXQ_ATTACHMENTS` 影响 1 行、
  33.02ms，同时出现 `INSERT EXTERNAL_DOCUMENTS` 2 行、`INSERT ZSXQ_TOPICS` 2 行和关系表 1 行；
  没有旧轮次 25,091.23ms 的隐式 INSERT 或 26 秒连接持有。后续 `a931ec17` 轮次完成 4 页/78
  topics、附件 503、下载 5、失败 0；这条正向证据与 12:32 新 worker 的页级评论批量重载一起保留，
  但外部下载本身的时延仍按网络 I/O 单独观察。
- 12:45:54 重载后的最终 worker 又完成任务 `a4388ac3-405e-47bf-9055-3eed019b7c45`（38.7318s，
  4 页/78 topics，附件下载 1、失败 0）。该轮没有再出现 25 秒隐式附件 INSERT 或 26 秒连接
  持有；唯一附件快照慢读为 3,431.14ms，另有批量主题 UPDATE 1,043.09/1,537.22ms，精确窗口
  connection hold、SQL 错误、timeout、idle-tx 均为 0。外部 I/O 与远端窄投影仍需按网络等待单独
  观察，未凭应用耗时加索引。

## 复发预防

- ZSXQ 自动同步每页只允许有限次主题/附件状态写；分析器按 origin、累计耗时、P95、最大值和
  `non_sql_hold_ms` 报警。
- 任何新增附件状态写必须先通过“外部 I/O 前释放 Session、批量状态回写、失败可重试”契约测试。
- 远端 PostgreSQL HBA/出口 IP 作为独立 P0 readiness 门禁；在登录失败时禁止重启和索引 DDL。
