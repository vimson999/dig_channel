# ZSXQ 同步逐条点查放大远端数据库抖动

## 基本信息

- 状态：页面级集合查询与集合更新已通过第 138 轮真实负载验收
- 严重级别：高
- 影响模块：ZSXQ 同步 / 附件入库与下载 / PostgreSQL / PgBouncer
- 首次发现：2026-07-21 新进程真实同步日志
- 修复提交：待提交
- 根因分类：N+1 查询、重复物化、延迟列隐式加载、验收窗口不足
- 复发键：`zsxq-sync-per-item-point-query-amplification`
- 是否复发：是；属于 `orm-expired-projection-refresh-after-commit` 与热点查询复发簇的新入口
- 防线层级：shared_guardrail / sql_contract_test / runtime_acceptance

## 现象与证据

- `14:13-14:20`，同一个旧 `zsxq_sync` worker 连续产生附件与 topic 点查慢 SQL，耗时 `1.12s-10.64s`。
- 最慢查询只返回 9 行；随后同形查询立即回落到 `30-180ms`。
- 生产库 `zsxq_attachments` 约 22974 行，索引命中率 `99.94%`，复合唯一索引覆盖 `(group_id, tab_id, topic_id, attachment_key)`；现场没有活动锁等待。
- PgBouncer 同期池等待为毫秒级，未出现池耗尽。表规模和执行计划不足以解释 10 秒点查，远端数据库或网络存在瞬时抖动。
- 旧代码仍对每个 topic 单独查询一次 `zsxq_topics`；对每个附件单独查询一次是否存在，随后下载阶段又按 topic 查询整批附件。
- `ZsxqAttachment.before_update` 访问未投影的 `local_path`，触发额外主键 SELECT；该签名在 7 月 20 日后重复出现 5 次。
- 第一轮部署后的 `14:55:25-14:55:35`，真实同步仍出现 3 次按 topic 的附件 SELECT 和 2 次 `raw_payload` UPDATE，耗时 `1.07s-1.38s`。这证明“每 topic 一查”仍会在远端链路抖动时复发，不能作为最终验收。

## 根因

1. 之前只控制了单条查询的列宽，没有约束一个业务批次的 SQL 总次数。
2. 入库和下载被当成两个独立阶段，各自重新读取同一批附件，轻量查询仍形成 N+1。
3. mapper 更新钩子读取延迟列，绕过调用方的 `load_only` 预算。
4. 远端链路抖动时，一次 10 秒等待本来只是偶发事件；逐条往返把发生概率和端到端耗时同时放大。
5. 旧验收只断言“不读 raw_payload”和“不整行刷新”，没有断言每页/topic 的查询次数上限，因此测试假绿。
6. 第一轮修复把附件从“每附件点查”降成“每 topic 批量查”，但一页仍有 19-50 次数据库往返；修复粒度停在了中间层。

## 修复

- 每页 topic 在任何数据库事务开始前完成上游评论补全，再用一个 `IN` 查询加载全部既有 topic 的结构化快照。
- topic 快照使用字典而不是 ORM 实体，循环内 commit 后不会过期或隐式刷新。
- 每页所有 topic 的附件用一个 `topic_id IN (...)` 查询加载为脱离 Session 的轻量快照，再按 `topic_id + attachment_key` 在内存匹配；循环内 commit 不会触发刷新。
- flush 后把附件转换成 `SimpleNamespace` 快照，下载阶段直接复用，不再执行第二次同 topic 查询。
- 附件批量投影显式包含 `local_path`，mapper 更新钩子不再为 `file_ext` 补查延迟列。
- 本地下载文件已经可复用时，不再重复执行无变化的 `download_status` UPDATE 和 COMMIT。
- 超过重试阈值的附件如果已经处于相同 `skipped/error_message` 状态，不再每轮重写；底层更新同时使用 `IS DISTINCT FROM`，相同值即使被重复调用也影响 0 行且不刷新 `updated_at`。
- watchdog 同时改为先检查无 JSON 候选行的 broker 可见性；只有确认消息丢失后才加载单条 `params/result`。

## 验证

- ZSXQ SQL 捕获测试断言：两个 topic 跨 commit 的入库与下载合计只执行一次页面级 `zsxq_attachments` 轻量 SELECT，且不读取 `raw_payload`。
- topic 页测试断言：两个 topic 只执行一次 `zsxq_topics` SELECT，commit 后读取快照不产生补查。
- watchdog 测试断言：broker 消息可见时只有一次无 `params/result` 的候选 SELECT。
- 第二轮定向回归：`122 passed, 26 subtests passed`；完整已知问题回归：`703 passed, 5 skipped, 39 subtests passed`。
- 第 138 轮新 worker 完成 4 页、78 topics、508 attachments，2 个下载、0 个失败；页面级附件集合 UPDATE 实测 `41.84ms` 和 `165.09ms`，不再出现此前每页 `3.18s-6.33s` 的 executemany 慢 SQL。
- 同轮仅有页面 topic/attachment 快照 SELECT `1.80s/1.71s` 各一次，以及单条下载状态和 topic 更新约 `1.03s/1.04s`；页面级查询次数预算已生效，未再放大为逐条点查。
- 首版 `UPDATE FROM VALUES` 在第 137 轮暴露 JSONB 类型错误，已按独立 P0 Bug 记录并在第 138 轮证明修复，不能从验收历史中抹去。
- 生产现场：索引完整、无锁等待、表扫描命中正常，因此本轮不新增索引或 DDL。
- 重复状态更新入口历史出现 12 次、单次最高 `2.31s`；生产附件无变化复验为 `33.35ms`、影响 0 行，`updated_at` 前后完全一致。
- 研报子任务汇总的现场 `EXPLAIN (ANALYZE, BUFFERS)` 为 `0.773ms`，命中 `ix_publication_tasks_parent_created` 与 `ix_publication_tasks_source_created`；应用侧单次 `581ms` 是连接往返抖动，不再通过重复索引掩盖。

## 复发预防

1. 热循环不仅要限制列宽，还必须限制查询次数；默认预算是每页一次集合查询，不允许每条记录点查。
2. 同一业务阶段的生产者和消费者必须共享结构化快照，禁止“刚写完又按同一条件重查”。
3. mapper 事件、property 和序列化函数不得读取未加载属性；SQL 捕获测试必须覆盖 flush/commit。
4. 真实验收必须等待完整同步、下载、清洗、发布和 watchdog 周期，空闲窗口不能判定修复完成。
5. 服务端执行计划正常且现场无锁时，优先减少往返和验证链路抖动，不得继续堆叠重复索引。
