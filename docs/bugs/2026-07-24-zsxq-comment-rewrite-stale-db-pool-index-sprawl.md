# ZSXQ 评论重复重写与数据库连接、索引治理复发

## 基本信息

- 状态：2026-07-29 巡检重新打开；运行修复已上线，ORM 索引定义防回归已修复，生产索引待管理员复核
- 严重级别：P0
- 影响模块：ZSXQ 同步入库 / PgBouncer / PostgreSQL 重点表索引
- 首次发现：2026-07-24
- 修复提交：当前工作区
- 根因分类：热点载荷写放大、连接生命周期过长、索引只增不减
- 复发键：`DB-SESSION-LIFECYCLE` / `HOT-PATH-PAYLOAD-POLLING` / `RUNTIME-TOPOLOGY-RELOAD`
- 是否复发：第 N 次；主题表已采用载荷指纹，但评论表遗漏同类入库合同
- 关联记录：`2026-07-22-zsxq-topic-unchanged-payload-rewrite.md`、`2026-07-22-pgbouncer-cold-pool-connect-timeout.md`
- 防线层级：shared_guardrail / contract_test / observability

## 现象

- 2026-07-24 慢查询中，评论同步 flush 占 198 条；最高频 SQL 实际是逐条更新 `zsxq_topic_comments.raw_payload/last_synced_at`。
- 多条读取 SQL 独立执行仅 0.04-0.4ms，但应用端仍出现 1-30 秒延迟和 PgBouncer `query timeout`。
- 重点表存在完全重复、前缀重复、旧 JSON 表达式和长期零扫描索引，增加写入与 vacuum 成本。

## 影响

- 同步相同评论也产生 JSON 序列化、网络传输、WAL、索引维护和死元组。
- 长寿命远端连接遇到链路失效时，窄查询被放大到超时，业务层误判为 SQL 本身很慢。
- 无效索引让每次写入支付额外成本，并掩盖真正的连接和入库问题。

## 根因

1. 主题写入已经有 `raw_payload_hash`，评论写入仍无条件覆盖所有字段和完整 JSON。
2. PgBouncer 后端连接默认保留 6 小时，复用前没有主动健康检查。
3. 历次优化只新增索引，没有把等价定义、查询计划、累计扫描和写放大纳入退出条件。
4. 慢日志按 ORM flush 调用点归因，一度把评论 UPDATE 误认为附件写入。
5. 后端 bootstrap 在 daemon thread 中运行但未传播 Flask application context，导致调度状态查询错误回退到默认配置。

## 解决思路

- 完整 JSON 保留用于审计，但由轻量指纹决定是否更新。
- 单独验证 SQL executor 时间；若执行计划毫秒级而端到端超时，转查连接池和网络。
- 索引采用“查询证据、等价检查、使用量、写入代价”四项审计，建立明确的保留和删除清单。

## 实际方案

- 新增 `zsxq_topic_comments.raw_payload_hash` 并回填历史数据；相同评论二次同步不再产生 UPDATE。
- 外部文档 URL 幂等查询只加载 6 个轻量列，且值未变化时不再触碰 `updated_at`。
- PgBouncer 后端连接生命周期缩短到 15 分钟，空闲 3 分钟回收，复用前执行 `SELECT 1`；查询超时保留为 60 秒最终保护。
- 清理 `publication_tasks` 旧 JSON/前缀重复索引、`external_documents` 重复唯一索引、ZSXQ 长期零扫描及前缀重复索引。
- 模型定义、迁移、启动 schema 门禁和契约测试同步更新，防止旧索引被 ORM 再建。
- 后端 bootstrap 的同步和异步入口统一显式进入 `app.app_context()`，确保线程中的数据库调度配置查询使用真实配置。

## 验证

- 查询计划已验证：父子任务查询 0.234ms、同步摘要 0.388ms、外部文档 URL 查询 0.035ms。
- 全量回归：867 passed、5 skipped、42 subtests；针对启动上下文的补充回归 32 passed、5 subtests。
- 真实库回滚验收：重放最近主题的 4 条评论，`comments_updated=0`，实际评论 UPDATE SQL 为 0。
- 旧 worker 最后新增的 7 条和 24 条缺失哈希记录均已再次回填，运行 schema 检查确认缺失数为 0。
- `zsxq_sync` 新 worker 于 14:05 上线并正常 pong；Flask 新后端于 14:09 上线，`/health` 为 200/32ms，研报任务列表为 200/424ms。
- 新 bootstrap 已读取 `source=database` 的真实 ZSXQ 调度配置，`errors=[]`，不再出现 application-context 回退。

## 复发预防

- 同一类周期入库表一旦引入完整 JSON，必须同时提供变化指纹、历史回填和“不变零写入”测试。
- 索引整改不得以“创建成功”为完成；必须记录 EXPLAIN、等价索引、扫描统计、写热点和删除结论。
- executor 小于 10ms 而应用超过 500ms 时，禁止继续凭慢日志盲目加索引。
- 慢写归因必须读取实际 SQL 表名和语句，不能只采用 flush 所在源码行号。
- 启动门禁必须区分运行兼容性与管理员维护：缺字段、回填不全、缺权限、缺关键索引继续硬阻断；迁移标记延迟和纯冗余索引清理只告警，避免已修复代码被旧 worker 长期滞留。

## 后续事项

- 不再按旧清单批量删除剩余 3 个索引。管理员只评估
  `ix_zsxq_attachments_publish_status`；两个 topic-type 复合索引保留到有完整查询计划
  对照和更长观察窗后再决定。
- 使用
  `docs/sql/zsxq_attachment_publish_status_index_cleanup_dbeaver_20260729.sql`
  逐条复核并发删除候选，随后重跑 schema 维护检查。

## 2026-07-29 自动巡检复核

- 统计自 PostgreSQL 2026-07-17 启动以来累计：
  `ix_zsxq_attachments_publish_status` 扫描 0 次、352 KiB，且被
  `(publish_status, publish_updated_at DESC)` 复合索引左前缀覆盖。
- 原清理清单中的
  `ix_zsxq_topics_group_tab_type_create_desc` 已扫描 104 次、读取 2023 个索引项；
  `ix_zsxq_topics_group_type_create_desc` 扫描 1 次但读取 3380 个索引项。仅凭旧清单
  删除会违反“真实查询 + 使用统计 + EXPLAIN”纪律，因此本轮明确取消无人值守批量删除。
- ORM 模型仍把 `publish_status` 和 `topic_type` 声明为自动单列索引，并保留已废弃的
  `ix_zsxq_topics_group_tab_create_synced_desc` 名称；新库或 `create_all` 会重新制造
  已清理索引。
- 模型元数据已改为当前规范索引：
  `ix_zsxq_attachments_publish_status_updated_at` 和
  `ix_zsxq_topics_group_tab_create_time_desc`，并新增契约测试禁止旧索引名复发。
- 在下一轮真实自动同步后再次核对评论 UPDATE 数和哈希缺失数，并持续观察 30-60 分钟 PgBouncer 超时是否归零。
