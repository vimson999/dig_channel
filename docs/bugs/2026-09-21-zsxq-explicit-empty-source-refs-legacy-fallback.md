# ZSXQ 发布把明确为空的来源映射误作历史缺字段

## 基本信息

- 日期：2026-09-21
- 状态：CLOSED / RUNTIME_ACCEPTED（2026-09-22：三个自然 present-empty 任务与后续完整窗口均未复发）
- 严重级别：P1
- 影响模块：ZSXQ 清洗产物发布 / 附件发布状态回写 / `publication_tasks` 大 JSON
- 根因分类：present-empty 与 absent 语义混淆，错误触发历史兼容回退
- 复发键：`SESSION-LIFECYCLE-EXTERNAL-IO` / `HOT-PATH-QUERY-AMPLIFICATION`
- 是否复发：是；2026-07-22 已要求只有历史缺字段任务允许回退，本窗新任务仍因空数组进入回退
- 防线层级：structured_snapshot / query_budget / contract_test

## 修复前证据与影响

固定窗口 `2026-09-20 06:00:00–2026-09-21 06:00:00` 内有 7 条
`zsxq_clean_output_publish` 任务，全部来自当前 `content_pipeline_web` 链路，且
`params.zsxq_publish.metadata.source_refs` 都是明确存在的空数组。空数组表示该批次没有需要映射回
ZSXQ 下载附件的来源，并不是旧任务缺少结构化快照。

旧代码用 `_publish_task_source_refs(task)` 的真假值决定是否读取历史 clean task。空数组为 falsy，导致
每个完成/失败发布任务都额外读取 source task 的 `params/result`。固定窗捕获指纹
`f8b9a0ddf265ae34` 3 次，累计 6.702 秒，P50 1.783 秒、P95/max 3.508 秒；三次回写最终均为
`matched=0 updated=0`。真实任务 `params+result` 载荷为 8.9–34.7KB，额外读取既无业务效果，又放大远程
PgBouncer 往返和 JSON 传输。

实时同形 EXPLAIN 与全库证据没有指向 executor、锁或索引：目标表点查约 0.041ms，waiting lock、长事务与
idle in transaction 均为 0。根因在应用回退判据，而不是缺索引。

## 根因

结构化 `source_refs` 有三种语义：非空数组表示有映射；空数组表示权威地确认“无映射”；字段缺失表示历史任务，
才允许从 clean task 的 `params/result` 兼容回退。旧实现只区分“解析结果非空”和“解析结果为空”，把后两者合并，
破坏了 2026-07-22 建立的“只有历史任务允许 JSON 回退”合同。

## 已落地修复

- 新增 `_publish_task_has_source_refs_snapshot`，按字段是否为数组判断结构化快照是否存在，不再用数组内容真假值判断。
- 单任务状态回写与最近发布上下文批量路径都只在字段缺失时读取 clean task；明确空数组直接作为权威结果。
- 保留历史缺字段任务的一次性兼容回退，不裁剪 `params/result`，不删除原始 JSON 审计数据。
- 回归分别锁定单任务路径与最近上下文路径：明确空数组时前者不得产生 source task SELECT，后者只允许发布任务
  本身的一次有界查询。

## 验证

- TDD RED：新增单任务合同后，旧实现实际产生 `SELECT publication_tasks ... params, result`，断言失败。
- GREEN：present-empty 判据修复后，3 个定向测试通过。
- `test_zsxq_sync_query_discipline` 与 `test_publication_task_queue_repair` 联合 93/93 通过。
- 完整 unit 1698/1698 通过；`compileall`、`git diff --check`、registry JSON、Bug strict 和 Celery revoke
  boundary 门禁均通过。
- 06:26 双重重启安全门均确认无不可中断任务，标准脚本未使用 FORCE，06:27 新 backend PID 6472、17/17
  Celery service、Watchdog 与 6/6 scheduler 全部启动且 bootstrap `errors=[]`。health、`topics/local`、
  `topics/local/tabs`、sync status 与 Insights overview 均为真实 200 响应。
- 代码已经运行替换；但重启后尚无新的 `source_refs=[]` 自然发布任务，因此只能标记 RUNTIME_REPLACED /
  OBSERVING，不能标记 CLOSED。

## 复发预防

- 结构化快照判据必须区分 present-empty、present-nonempty 与 absent；只有 absent 允许历史 JSON 回退。
- 新生产者必须显式写入 `source_refs: []`，消费者不得用 `if not source_refs` 推断 schema 缺失。
- 状态回写 SQL 预算必须覆盖空数组和非空数组两种新任务；历史兼容测试单独保留，避免优化时误删兼容能力。
- 下一自然任务必须按 SQL origin 复核 `update_attachment_publish_status_from_publish_task` 不再出现 source task
  `params/result` 点查；至少一个后续完整窗口无同指纹，才能进入 CLOSED 评估。

## 下一轮验收条件

1. 安全门放行后替换 ZSXQ publish worker 与调用该服务的 Web/后台进程；
2. 用真实 web content pipeline 产生至少一条 `source_refs=[]` 发布任务；
3. 任务完成或失败状态回写仍正确，日志为 `matched=0 updated=0`，且没有对应 clean task 大 JSON SELECT；
4. 固定窗口内指纹 `f8b9a0ddf265ae34` 归零，或仅有明确缺字段的历史任务并能逐条解释；
5. 再观察一个后续完整窗口无复发，方可关闭。

## 2026-09-22 自然运行验收

修复后的完整固定窗口内，三个自然 `zsxq_clean_output_publish` 任务均携带显式 `source_refs=[]` 并完成；
对应 source clean 任务存在且含 17–57KB 的 `params/result`，因此足以触发旧回退，但本窗慢 SQL 中旧指纹
`f8b9a0ddf265ae34` 为 0，也没有 source task 大 JSON 回读。状态回写保持 `matched=0 updated=0`，符合
“明确无附件映射”的业务语义；没有通过删除原始 JSON 或伪造数据绕过。

证据链已达到修复前 3 次/6.702 秒无效查询 → 字段存在性修复 → 定向查询预算 → 1698 项前轮全量测试 →
新 worker → 三个自然业务任务 → 后续完整窗口不复发，故本记录可标记 CLOSED/RUNTIME_ACCEPTED。历史任务若
缺少字段仍保留窄兼容回退；下一轮巡检继续把该指纹作为 recurrence sentinel，重新出现时必须按任务逐条解释。
