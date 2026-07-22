# B站上传 JSON 轮询与在线 DDL 锁队列

## 基本信息

- 状态：主迁移已应用且进程已替换；真实负载发现上传 worker 仍读任务 JSON，增量执行投影代码与迁移已准备，待管理员执行、新 PID 和真实投稿验收
- 严重级别：P0
- 影响模块：MP3 API / report audio API / B站上传 / publisher watchdog / PostgreSQL
- 首次发现：2026-07-18 14:06 后专项日志复盘
- 修复提交：未提交
- 根因分类：重复能力实现、热点 JSON 轮询、提交后 ORM 过期刷新、在线 DDL 锁队列
- 复发键：`HOT-PATH-PAYLOAD-POLLING` / `DB-SESSION-LIFECYCLE`
- 是否复发：同类机制再次复发
- 关联记录：`2026-07-13-mp3-hot-path-json-payload-regression.md`、`2026-07-16-zsxq-expired-orm-full-row-refresh.md`、`2026-07-18-publication-task-progress-json-write-amplification.md`
- 防线层级：shared_guardrail / schema_migration / actual_sql_contract / runtime_gate / observability

## 现象

2026-07-18 14:06 至服务停止前，去除重复日志和测试 SQL 后，专项日志仍有以下生产路径：

- MP3 B站上传历史轮询 51 次读取 `PublicationTask.params/result`，平均 1.78 秒，P95 4.80 秒，最大 32.89 秒。
- B站上传提交入口有 2 次提交后整行回读，平均 0.98 秒。
- publisher watchdog 的 B站孤儿修复存在提交后整行回读，现场达到 1.04 秒。
- report audio 豆包终态/异常收口仍有 4 次子任务大载荷读取，平均 0.94 秒。
- 15:23 在线执行 `ALTER TABLE publication_tasks ADD COLUMN ...` 等待 20.52 秒。等待中的 AccessExclusive 锁把同一时段的轻量主键查询拖到 5-17 秒，并使研报列表 count 在 5.05 秒触发 statement timeout。
- 16:50 主迁移后启动的新 `platform_upload` worker（PID `46137`）在 16:51:39 执行 `run_bilibili_upload_only` 时仍读取 `publication_tasks.params/result`，耗时 1.83 秒。它证明第一版结构表只替换了创建、列表、取消和 watchdog，没有替换真正执行投稿的 worker。

同类轻量 SQL 的现场执行计划为 0.024-0.142ms，说明这批尖峰不是缺索引；主要问题分别是大 JSON 搬运和在线 DDL 形成的锁队列。

## 为什么历史修复没有封住

1. report audio 已有共享 B站 helper，但 MP3 API 复制了第二套创建、列表和取消实现。
2. 旧纪律测试只检查 `services/bilibili_publish.py`，没有执行 MP3 API 的真实 SQL，因此“候选 ID 查询 + 循环按 ID 整行读取”仍被测试接受。
3. B站上传记录是稳定业务实体，却只存在于 `publication_tasks.params/result`；列表、取消、上传结果和 watchdog 都不得不重新解释 JSON。
4. watchdog 虽改成轻量候选查询，但 `commit` 后日志和调用方继续访问过期 ORM 实例，SQLAlchemy 隐式补发整行 SELECT。
5. 结构迁移曾在应用连接仍在线时执行。PostgreSQL 的公平锁队列会让后续普通 SELECT 排在等待中的 DDL 后面，造成“所有查询突然都慢”的假象。
6. pytest SQL 与生产 SQL 写入同一专项日志，旧分析器未区分进程角色和数据库方言，放大了人工复盘噪声。
7. 第一版验收覆盖创建、列表、取消、watchdog 和终态同步，却没有捕获 `run_bilibili_upload_only` 的实际 SQL。结构表保存了历史展示字段，但缺少视频路径、简介、标签、分区、转载策略和投稿声明等完整执行命令，worker 只能继续打开 JSON。

## 实际方案

- 新增 `publication_task_bilibili_uploads` 一对一结构化实体，保存父任务、来源、视频/封面、账号、充电、定时、合集、投稿结果、取消和队列修复状态。
- 两个 API 统一调用 `services/bilibili_publish.py`；创建任务时在同一事务写任务行和结构化行。
- 创建响应在 commit 前生成不可变 DTO；Celery ID 使用定向 UPDATE 写回，提交入口不再回读任务整行。
- 历史列表使用一次有界 JOIN，返回完整业务字段，但不读取任务 `params/result`，也没有 N+1。
- publisher 终态同步维护 `aid/bvid/title/credential`；取消和 watchdog 修复状态也写结构化表。
- 增量执行投影补齐 `video_path/cover_path/description/tags/tid/copyright/no_reprint/dynamic/neutral_mark/is_only_self/space_hidden/creation_statement_id`；上传 worker 以一次有界 JOIN 取得完整命令，不再读取任务 JSON。
- 上传成功和失败使用 PostgreSQL JSONB 原子 patch 保存审计结果，同时维护结构化 `aid/bvid/title/credential`，不再执行 SELECT-before-UPDATE。
- watchdog 在 commit 前构造不可变状态快照；commit 后日志和调用方不再接触 ORM 实例。
- 豆包终态、超时和 stale 分支按结构化 TTS 列读取，不再退回通用大载荷列集。
- DBeaver 迁移在事务开头检查 `cappa_rw` 活动连接，发现任何应用连接就整体失败；启动脚本同时检查表、列、权限、索引和历史回填覆盖。
- 专项日志新增 `role/process/dialect/process_started_at`，分析器默认排除 pytest/SQLite 记录并单独识别 DDL。

## 当前验证

- 生产主迁移已于 16:41 左右提交：清洗投影 386 条、B站结构记录 199 条、下载台账 3957 条；Web 随后恢复 healthy。16:50 本地 Web/Celery/watchdog/scheduler 完整替换为新 PID。
- B站创建与入队实际 SQL 测试：0 条 `publication_tasks` SELECT，只有 INSERT 和定向 UPDATE。
- 三条上传历史记录：固定 1 条 JOIN SELECT，且不含 `publication_tasks.params/result`。
- 完整调用上传 worker：执行上下文固定 1 条 JOIN SELECT，覆盖投稿调用全部参数，且不含 `publication_tasks.params/result`；PostgreSQL 终态结果编译为单条 JSONB merge UPDATE。
- watchdog 真实扫描与重入队：固定 2 条轻量任务 SELECT，且不含 `params/result`；旧实现会出现第 3 条整行刷新。
- publisher 终态更新已验证结构化 `aid/bvid/title/credential` 同步完整。
- 目标库只读回填预检：199 条历史 B站上传任务、90 个父任务、0 条缺失父关系；旧任务 JSON 合计 444502 bytes，最大单条 2990 bytes。1 条旧记录缺少账号显示名，迁移通过账号表补齐后未解析数为 0。
- 增量迁移前生产只读检查：当前 200 条结构记录，源视频路径缺失 0、无效标签 0、路径超长 0、账号/定时/合集投影不一致均为 0。
- 历史业务完整性检查：13 条失败投稿均有结构化 `error_message`，仅存在于 JSON 的错误为 0；上传结果不存在 `aid/bvid/url` 之外仍被裁掉的附加字段，标题和账号显示名缺失均为 0。
- B站完整 worker、豆包取消收口、数据库投影与相关运行时回归共 `201 passed, 4 subtests passed`。
- 当前生产表仍缺少上述 12 个执行列，运行中的 PID 也尚未加载增量代码；启动门禁已按预期返回 `BLOCKED`，因此本记录不能标记为已解决。

## 验收门槛

1. 管理员在所有应用连接为 0 后完整执行 `docs/sql/bilibili_upload_execution_projection_dbeaver_20260718.sql`，事务必须成功提交。
2. 启动门禁报告结构、权限、索引、清洗投影、B站历史投影全部完整。
3. 启动 Web、worker、watchdog 和 scheduler，并记录新 PID 与启动时间。
4. 真实创建并完成至少 1 个 B站上传；列表轮询至少 5 次；watchdog 至少执行 1 个检查周期。
5. 新进程窗口内，B站列表、提交和 watchdog 的 `params/result` 指纹为 0，`multirow_payload=0`，无 statement timeout。
6. 核对标题、账号、充电、定时、合集、取消、aid/bvid、重试和历史记录，不得因性能改造缺字段。

## 复发纪律

- 一个稳定业务实体第二次需要从 JSON 中筛选、关联或轮询时，必须建立结构化表/列，禁止继续增加 JSON helper。
- 一个业务能力只能有一个查询和状态转换所有者；API 只能委托共享服务，不得复制实现。
- 静态源码检查不能代替实际 SQL 契约。热路径测试必须采集完整调用产生的 SQL 数量、返回列和事务后的隐式刷新。
- 结构化业务实体的验收必须覆盖命令生产者、命令消费者和终态写回；只覆盖列表或监控不算完成。
- DDL、回填和重复索引清理只能在应用连接为 0 时执行；迁移脚本必须自行 fail closed。
- “代码已修改”“迁移已准备”“迁移已应用”“进程已替换”“真实负载已验收”是不同状态，禁止合并表述。
