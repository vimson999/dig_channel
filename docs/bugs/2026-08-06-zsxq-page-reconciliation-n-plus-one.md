# 2026-08-06 ZSXQ 页内评论与外链对账 N+1 持有数据库连接

## 基本信息

- 状态：代码、回归与新 worker 页级评论路径已验收；外链真实链接样本待持续观察
- 严重级别：P1
- 影响模块：ZSXQ 同步 / `zsxq_topic_comments` / `external_documents` / PostgreSQL Session
- 首次发现：2026-08-06 12:13–12:21 运行态复盘
- 修复提交：2026-08-06（当前工作树）
- 根因分类：页内 N+1 读 / Session 持有过久 / 远端往返放大
- 复发键：`DB-SESSION-LIFECYCLE`
- 是否复发：与 2026-07-21 ZSXQ 点查放大、2026-07-24 评论重写复发属同一热路径家族
- 关联记录：`2026-08-06-zsxq-page-update-roundtrip-amplification.md`、`2026-08-06-zsxq-attachment-state-roundtrip-amplification.md`
- 防线层级：local_patch / contract_test / runtime_acceptance

## 现象

旧 worker 任务 `823fc84f-ac5d-457f-b454-c83c42a5dbd0`（12:13:44–12:21:30，34 页、610 条主题，
其中 597 条已有主题）在 `services/zsxq_sync_service.py:5434:_upsert_topic_comments` 产生 15 次
长连接持有，累计 128,676.43ms，P50/P95/最大为 8,198.24/15,237.13/16,753.64ms；单次
`sql_count` 30–87，说明一页内逐主题评论 SELECT 反复支付远端往返。

同一轮 `external_document_service.py:838:_upsert_document_shells` 还出现 2 次持有，累计
15,187.00ms（最大 8,288.71ms），原因是外链扫描按主题调用文档 shell/关系对账，而不是页级批量。
分析窗口 12:13:44–12:21:32 共 35 条慢 SQL，应用部分 32 条/50,467.81ms，P50/P95/最大
1,279.26/2,759.67/4,131.68ms；无 SQL 错误、statement/lock/query/connect timeout 或
idle-in-transaction。

## 影响

- 评论 SELECT N+1 把 34 页同步的远端连接持有放大到 128 秒，拖慢后台同步并扩大 PgBouncer
  抖动影响面；这不是“评论表缺索引”的证据。
- 外链 shell/关系逐主题对账额外持有 15 秒；重复 URL 仍会反复走同一批查询。
- 当前慢度同时包含 PostgreSQL/PgBouncer 远端等待，不能仅用应用端总耗时判断 executor。

## 根因

`_sync_one_tab` 已按页加载主题和附件投影，但评论对账仍在主题循环内用
`ZsxqTopicComment.query ... comment_id.in_(...)` 逐主题读取；外链扫描也在循环内调用
`scan_topic_record`，每个主题分别查询 `external_documents` 和
`zsxq_topic_external_documents`。Session 在整个主题循环中保持活动，远端每次往返叠加成
长 checkout。

## 解决思路

- 复用上游已经完成的主题评论 payload，按页收集 comment ID，一次读取轻量标量快照；变化行用
  Core UPDATE 按字段形状回写，原始 JSON 只有 hash 变化时才更新。
- 在页级冻结外链扫描 DTO，批量查询文档 shell 和主题关系；保留单主题 API 给其他调用方使用。
- 提交/下载边界仍由既有 Session 纪律控制，不把 ORM parent 或大字段跨外部 I/O 搬运。

## 实际方案

- `ZsxqSyncService._load_existing_topic_comment_snapshots` 按页、按最多 1000 个 comment ID
  分块读取 `TOPIC_COMMENT_SNAPSHOT_COLUMNS`，不读取 `raw_payload`。
- `_upsert_topic_comments(..., existing_records=...)` 支持标量快照；新增评论继续进入 ORM 批量
  flush，已有评论由 `_apply_topic_comment_updates` Core executemany 写回，保留 `raw_payload`、hash
  和 `last_synced_at` 语义。
- `_sync_one_tab` 将外链扫描改成冻结 DTO 后页末调用 `scan_topic_batch`；文档服务一次查 shell、
  一次查关系，仍不删除任何审计字段。
- 未执行 CREATE/DROP INDEX、ALTER、数据回填、生产配置或破坏性操作。

## 验证方式

- 新增回归验证两主题只发一条评论 SELECT；变化评论仍只发一次 UPDATE 并保留原始 payload/hash；
  外链快照验证完整链接列表跨 commit 保留且不携带 raw JSON。
- 评论/投影/工作流/外链定向测试 56 项通过；全量 unit 1,037 项通过，另有 compileall、diff check、
  Bug strict 150/150 和 recurrence registry JSON 校验通过。
- 12:45:54 新 `zsxq_sync` worker ready 后，任务 `a4388ac3-405e-47bf-9055-3eed019b7c45`
  于 12:46:17–12:46:55 完成 4 页/78 topics、更新 77、附件 503/下载 1/失败 0。窗口仅有
  `_apply_existing_topic_updates` 两次与 `_load_existing_attachment_snapshots` 一次慢 SQL，
  连接持有、SQL 错误、timeout、idle-in-transaction 均为 0；没有再出现逐主题
  `_upsert_topic_comments` 或外链 shell/关系 N+1 持有。
- 本轮没有可识别的新增外链链接样本，因此外链 DTO 的数据完整性由单测/代码合同确认，后续仍需含
  真实链接的同步样本；不能把未捕获误报为没有外链。

## 复发预防

- 每页对账入口必须有集合查询预算；分析器按 origin、累计耗时、P95、最大值和 SQL/non-SQL
  持有分别排名。
- 页面 DTO/标量快照通过后才允许 commit 或外部 I/O；禁止在循环中重新读取 ORM parent。
- 没有新的 `pg_stat_user_indexes`、`EXPLAIN (ANALYZE, BUFFERS)` 和真实 SQL 谓词证据前，不以
  N+1/远端等待为理由增加索引。
