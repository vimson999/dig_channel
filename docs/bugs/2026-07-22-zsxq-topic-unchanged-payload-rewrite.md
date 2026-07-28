# ZSXQ 已有主题无差别重写原始载荷

## 基本信息

- 状态：代码与全量回归已通过，管理员迁移脚本已就绪，待迁移、新 worker 和真实同步验收
- 严重级别：P0
- 影响模块：ZSXQ 同步入库 / zsxq_topics / PostgreSQL
- 首次发现：2026-07-22
- 修复提交：当前工作区
- 根因分类：热点载荷写放大、入库变更检测缺失
- 复发键：`DB-SESSION-LIFECYCLE` / `HOT-PATH-PAYLOAD-POLLING`
- 是否复发：第 N 次；此前主要治理读取端，本次确认已有数据的入库端仍重复搬运 JSON
- 关联记录：`2026-07-18-zsxq-run-result-clean-handoff-payload.md`、`2026-07-21-zsxq-sync-point-query-amplification.md`、`2026-07-22-terminal-polling-image-repair-payload-recurrence.md`
- 防线层级：shared_guardrail / contract_test / observability

## 现象

- 新 PID 真实同步负载中，`_update_existing_topic` 对一条已有 topic 执行整行更新，SQL 包含正文、问答、统计列和 `raw_payload`，耗时 `1063ms`。
- 代码每次同步都先写磁盘原始快照，再构造临时 ORM，并把除主键外的所有列写回；即使上游载荷完全不变也执行相同大更新。

## 影响

- 每页已有主题数会线性放大 JSON 序列化、网络传输、WAL、表膨胀和提交时间。
- 完整 JSON 的存在本身不是问题；缺少入库变更检测，导致审计载荷被当作心跳字段反复写入。
- 在公网 PostgreSQL 抖动窗口中，单条无业务变化更新也可能跨过慢查询阈值。

## 根因

1. `zsxq_topics` 没有保存原始载荷指纹，轻量快照为了避免读 JSON 也无法判断新旧载荷是否相同。
2. 更新函数使用“构造完整 ORM，再遍历全表列”的写法，没有区分结构化业务变化、审计载荷变化和同步心跳。
3. 原始磁盘快照也在每轮无条件重写，入库端没有幂等合同。

## 解决思路

- 保留完整 JSON 以满足审计和历史兼容，不牺牲业务完整性。
- 在结构化表上增加与 PostgreSQL `jsonb::text` 一致的 MD5 变化指纹；页面批量快照只读取 32 字节指纹，不读取 JSON。
- 将写入拆成结构化差异、原始载荷差异和同步心跳三组字段。

## 实际方案

- `zsxq_topics.raw_payload_hash` 保存原始载荷指纹；迁移使用 PostgreSQL `md5(raw_payload::jsonb::text)` 回填历史数据。
- 应用按 PostgreSQL JSONB 的“键字节长度、再字节序”规范递归排序，确保 Python 指纹与数据库回填一致；真实样本已验证匹配。
- 未变化载荷只更新 `last_sync_run_id/last_synced_at`；结构化列仅在值变化时进入 UPDATE。
- 只有指纹变化时才更新 `raw_payload/raw_payload_hash/raw_json_path` 并重写磁盘快照。
- 新建 topic 和所有 ORM 原始载荷写入由模型事件同步维护指纹，避免旁路漂移。
- 启动 schema 门禁升级到 `20260722_zsxq_topic_payload_hash`，同时验证列存在和全表指纹一致。

## 验证

- 真实 topic 样本：PostgreSQL 指纹与 Python 指纹均为 `3647783662da5e161c84ba86b20a698e`。
- 契约测试覆盖不变载荷不写 JSON/磁盘、变化载荷完整写 JSON/指纹/正文，以及页面快照不读取 `raw_payload`。
- 全量确定性回归 `793 passed, 5 skipped, 42 subtests`；数据库纪律门禁 `87 passed, 3 subtests`；编译、TypeScript、单一 Alembic head 和 diff 检查均通过。
- 使用应用账号执行迁移时因 `zsxq_topics` owner 为 `postgres` 被 PostgreSQL 拒绝；事务完整回滚，列不存在且 Alembic 仍为上一版本。管理员单事务脚本 `docs/sql/zsxq_topic_payload_hash_dbeaver_20260722.sql` 已绑定结构变更、回填、版本推进和一致性断言。

## 复发预防

- 大 JSON 入库必须声明变更指纹或版本号；周期同步不得把原始载荷当心跳字段无条件写入。
- 结构化投影、完整审计载荷和运行心跳必须分别计算更新集合，不得遍历整表列生成 UPDATE。
- 轻量快照允许读取载荷指纹，禁止为了比较变化重新读取完整 JSON。
- 测试必须同时证明“不变时不写大字段”和“变化时不丢完整数据”，单边性能测试不能作为完成条件。

## 后续事项

- 使用 postgres/admin 执行管理员单事务脚本，重启 ZSXQ worker，并用下一轮已有 topic 同步 SQL 验收。
- 继续观察附件和评论入库是否存在同类无变化 JSON 重写；同指纹再次出现直接复用此合同。
