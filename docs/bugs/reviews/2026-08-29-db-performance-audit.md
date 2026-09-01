# 2026-08-29 每日数据库性能与错误巡检

> 说明：正文先保留 06:02:19 固定窗口的原始基线；用户随后授权执行“方案 1”。本文末尾的
> “方案 1 根因闭环实施补记”更新了 DDL、运行配置、重启和真实验收状态，执行结论以补记为准。

## 根因闭环结论

- 固定窗口：`2026-08-28 06:02:19 +0800` 至 `2026-08-29 06:02:19 +0800`；覆盖 `logs/slow_query.log`、`app.log`、`backend.log`、`scheduler.log`、`celery/*.log` 及轮转日志，并与上一固定窗口比较。
- 本轮不能判定数据库性能已解决。应用慢 SQL 215 次、累计 574.128 秒、P95 7.434 秒、最大 19.924 秒；虽然数量下降 2.3%，累计反而增加 1.1%。Session 长持有 33 次、P95 20.275 秒、最大 22.773 秒，分别较上轮增加 13.8%、14.8%、28.6%。
- 主系统根因仍是本机 PgBouncer 经 Tailscale 到远端 PostgreSQL 的建连/往返/commit-checkin 长尾：去重 connect/query timeout 为 10/2，且两次 query timeout 与真实 worker 20.251/22.247 秒 SQL 同秒。现场 executor 多为 0.026–102.923ms，无锁等待、无 idle-in-transaction，不支持把根因改写为缺索引。
- 新发现并修复两条可控根因：MP3 音频素材中文输入期间缺少请求所有权，导致 4 个搜索请求重叠；ZSXQ publish 在附件状态同步超时后吞异常却未清理 Session，继而丢失完成通知。前者静态资源已运行替换、待真实输入后窗；后者已由新 worker 加载但仍待真实发布/瞬断行为验收，均不是 CLOSED。
- 反复问题中，workflow_notify 再现 107 retry/29 terminal fail，跨 event provider 熔断和 durable pending 仍缺，保持 OPEN/REPEATED。MP3 jobs 请求从 212 降至 47、累计从 95.265 秒降至 25.812 秒，但服务端看不到页面 hidden/in-flight 状态，只能记改善并继续观察。
- Bilibili “双调度器 + 宽 identity read + 未变化 JSON 重写”在一个完整日约 23 个计划周期内旧指纹为 0、字段完整，代码根因转 `RUNTIME_ACCEPTED`；重复 bvid 唯一索引 DDL 是独立 OPEN 风险，未自动执行。
- 未执行 CREATE/DROP INDEX、ALTER TABLE、数据回填、生产配置或破坏性操作；历史 ZSXQ 附件状态/漏失通知也没有无人值守回填。

## 修复前后量化

| 指标 | 上一窗口 | 本窗口 | 变化 | 判定 |
| --- | ---: | ---: | ---: | --- |
| 应用慢 SQL | 220 | 215 | -2.3% | 数量小降 |
| 慢 SQL 累计 | 567.679s | 574.128s | +1.1% | 恶化 |
| SQL P50 / P95 / 最大 | 1.672 / 7.424 / 17.094s | 1.678 / 7.434 / 19.924s | P95 +0.1%、最大 +16.6% | 长尾恶化 |
| HTTP 请求 | 1,073 | 587 | -45.3% | 流量下降，不能直接当优化 |
| HTTP 成功 / 失败 | 1,072 / 1 | 585 / 2 | 失败率 0.09%→0.34% | 两条非数据库业务失败 |
| HTTP 累计 | 664.611s | 466.339s | -29.8% | 低于流量降幅 |
| HTTP P50 / P95 / 最大 | - / 2.960 / 19.380s | 0.0037 / 3.352 / 22.828s | P95 +13.2%、最大 +17.8% | 恶化 |
| Session 长持有 | 29 | 33 | +13.8% | 恶化 |
| Session P50 / P95 / 最大 | - / 17.656 / 17.702s | 9.416 / 20.275 / 22.773s | P95 +14.8%、最大 +28.6% | 恶化 |
| 持有期 SQL / 非 SQL | 259.928 / 22.281s | 304.950 / 47.997s | +17.3% / +115.4% | 后者含 fetch/commit/checkin |
| PgBouncer connect/query timeout | 10 / 0 | 10 / 2 | query 复发 | OPEN/REPEATED |
| Celery completed/retry/fail | 231 / 0 / 0 | 195 / 107 / 29 | 失败风暴复发 | workflow_notify 主导 |

托管应用未捕获 statement timeout、lock timeout、deadlock 或超过门槛的 idle-in-transaction。应用 SQL 错误 2 个，均是 query timeout 后 server closed；“没有捕获”不等于“没有发生”。

## 页面 / API 与 SQL 关联

按累计耗时、P95、最大耗时和失败率综合排序：

| 端点 | 请求 | 成功/失败 | 累计 | P95 / 最大 | 慢 SQL | 归因 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| GET MP3 audio-assets | 14 | 14/0 | 109.805s | 20.029 / 22.828s | 15 / 88.731s | IME 搜索重叠 + 远端链路；已补 composition + latest-only single-flight |
| GET MP3 detail | 21 | 21/0 | 40.587s | 7.359 / 16.477s | 多条 | `load_task` 大字段点查被远端链路放大 |
| POST MP3 Bilibili upload | 8 | 8/0 | 38.196s | 18.356 / 18.356s | 有 | 上传编排与数据库往返共同构成长尾 |
| GET Bilibili uploads | 23 | 23/0 | 35.162s | 5.600 / - | 有 | 继续观察窄读网络尾延迟 |
| POST 小军军封面重生成 | 4 | 4/0 | 26.462s | 17.055 / 17.055s | 有 | 低频高延迟 |
| GET MP3 jobs | 47 | 47/0 | 25.812s | 1.541 / 12.334s | 有 | 请求 -77.8%、累计 -72.9%；hidden 证据仍缺 |
| GET Celery status | 4 | 4/0 | 24.504s | 12.950 / 12.950s | 少量 | broker inspect 冷控制面仍 OPEN |
| POST continue-full-video | 4 | 4/0 | 20.113s | 13.107 / 13.107s | 有 | 低频高延迟 |
| GET ZSXQ browser history | 4 | 4/0 | 2.551s | 0.883 / 0.883s | 0 | 上轮 P95 17.691s 后改善，仍受样本限制 |

HTTP 两个失败为根路径 401 探测和一次 MP3 artifact 404，未与慢 SQL/DB traceback 对齐，不计数据库失败风暴。

### 特别链路与三类边界

- `zsxq-insights overview`、`topics/local`、`topics/local/tabs`、`sync/status` 本窗口没有自然业务样本，不能写成“未复发”；browser history 有 4 次成功但样本仍少。
- report-audio、report-script-material、Top10 的 PDF canonical 三入口没有足够自然样本；旧单项补查未捕获，但仍保持 OBSERVING。
- **低频高延迟**：Bilibili upload、两类封面/背景重生成、Celery status、continue-full-video。
- **页面很慢但无匹配慢 SQL**：POST MP3 jobs P95 2.477 秒、finance cover P95 4.757 秒等，可能是外部 I/O、未达 SQL 阈值或当前关联缺口，不能误报为无问题。
- **后台/连接很久但无前台**：33 次持有中 worker 14、scheduler 3、unknown 3；非 SQL 持有累计 47.997 秒。现有 telemetry 尚不能精确拆开 fetch、commit、rollback 与 checkin。

## 后台任务与失败风暴

| 任务族 | 完成 | 累计 | P95 / 最大 | retry/fail | 结论 |
| --- | ---: | ---: | ---: | ---: | --- |
| ZSXQ publish | 8 | 2,178.261s | 788.469 / 788.469s | 0/0 | 外部发布主导；一次 DB timeout 级联丢通知 |
| PDF clean | 5 | 1,429.641s | 566.934 / 566.934s | 0/0 | 媒体处理主导 |
| Publisher | 10 | 1,117.917s | 157.661 / - | 0/0 | 继续观察 Session 归还 |
| MP3 workflow | 45 | 897.635s | 86.135 / 156.667s | 0/0 | 一次任务点查 timeout 由 2/3 DB retry 恢复 |
| Bilibili upload | 8 | 636.614s | 119.081 / - | 0/0 | 无任务级失败风暴 |
| Audio clean | 3 | 168.568s | - | 0/0 | 样本少 |
| Workflow notify | 116 success | - | 1.947s | 107/29 | 18 时集中复发，跨 event provider 背压未闭环 |

ZSXQ delivery `4f2878f1...` / publication task `739f6bbf...` 的发布本体成功，但附件状态同步读取来源任务 `456ab87b...` 用时 22.247 秒后超时；旧 wrapper 吞异常后留下失败 Session，`publish_completed` 随即因 `PendingRollbackError` 得到 `queued=False`。这条 request/task/SQL origin 链已直接驱动代码修复。

MP3 delivery `e845f693...` / publication task `cc8bfb06...` 的全载荷主键点查用时 20.251 秒后超时；现有 `_db_retry` rollback/remove 后第二次 718ms 成功，任务最终继续完成。它证明有界重试可止住单次瞬断，却不能替代 PgBouncer/网络根治。

## 主要 SQL 指纹与 executor 归因

| 指纹 | 来源 | 次数 / 累计 | P95 / 最大 | executor 证据 | 状态 |
| --- | --- | ---: | ---: | --- | --- |
| `323ce0e8...` | audio-assets 基础查询 | 9 / 66.380s | 19.924 / 19.924s | 同族搜索 102.923ms | 输入重叠已修，网络 OPEN |
| `861525...` | audio-assets 搜索 | 3 / 17.481s | 14.684 / 14.684s | 25 行、5,304 buffers、102.923ms | 不加索引 |
| `60ff7467...` | scheduler config | 27 / 61.524s | 4.128 / 7.579s | 0.026ms | 远端链路反复 |
| `590220bb...` | MP3 `load_task` | 9 / 49.995s | 13.606 / 13.606s | 主键点查 <0.14ms | 次数 13→9、累计 -40.3%，仍 OPEN |
| `859092...` | MP3 worker 全载荷点查 | 1 / 20.251s | 20.251s | query timeout 同秒 | retry 恢复，链路 OPEN |
| `f8b9...` | ZSXQ attachment source task 点查 | 1 / 22.247s | 22.247s | query timeout 同秒 | Session 级联已代码修复 |

PgBouncer server wait P50/P95/最大为 27.98/90.94/1822.05ms，query P95/最大为 140.71/1536.88ms；source 为 `100.96.3.12`、remote 为 `100.92.99.39`，没有 proxy TUN fallback。应用 20 秒级等待远高于 executor，继续指向连接/网络/事务归还边界。

## 重点表、索引与写放大

| 表 | 总大小 | live/dead | 累计 update / HOT | 索引扫描占比 | 判断 |
| --- | ---: | ---: | ---: | ---: | --- |
| `publication_tasks` | 128MB | 12,359 / 156 | 240,754 / 1.29% | 99.77% | update +620；大 JSON 与状态写放大继续治理 |
| `zsxq_sync_runs` | 37MB | 1,062 / 46 | 111,710 / 低 | 99.93% | 无新缺索引证据 |
| `zsxq_topics` | 129MB | 12,637 / 1,584 | 174,041 / 0.71% | 99.58% | toast 79MB，保留原始 JSON；不盲删字段 |
| `zsxq_topic_comments` | 118MB | 41,333 / 5,644 | 237,197 / 70.46% | 99.91% | HOT 较好，继续看 vacuum |
| `zsxq_attachments` | 78MB | 33,906 / 37 | 264,485 / 低 | 99.95% | 写多但 dead 低 |
| `external_documents` | 7MB | 1,817 / 146 | 6,265 / - | 98.80% | 非本轮热点 |

只读现场没有 waiting lock、idle-in-transaction 或目标表完全重复索引；`pg_stat_statements` 未安装。低 scan 索引统计期短，不能仅凭 scan=0/1 删除。代表 publication 主键、配置和全列表执行均小于 0.14ms，ZSXQ run 1.566ms，insights 35.748ms；没有新索引收益证据。

待人工 DDL 有两项：[`docs/sql/bilibili_competitor_bvid_index_cleanup_dbeaver_20260819.sql`](../../sql/bilibili_competitor_bvid_index_cleanup_dbeaver_20260819.sql) 用于 Bilibili 等价 bvid 唯一索引；[`docs/sql/comment_hash_index_cleanup_dbeaver_20260724.sql`](../../sql/comment_hash_index_cleanup_dbeaver_20260724.sql) 用于尚未登记 migration 且仍存在的 `ix_zsxq_topics_group_tab_type_create_desc` / `ix_zsxq_topics_group_type_create_desc` 重叠索引。两者均须 DBA 在维护窗口逐段执行并保存 lock/statement timeout、owner、行数、重复数据、EXPLAIN、验证与回滚证据。未取得新的明确 DDL 授权，本轮没有运行。

## 已落地修复与验证

1. MP3 audio-assets：增加 IME composition 门禁、Enter composing 拦截、单一 drain owner 和 latest-only pending 请求；保留分页、canonical 过滤和完整字段。旧契约先因 handler 不存在 RED，修复后通过；运行 backend 已返回 `20260829-cleaned-audio-assets-v3-ime-singleflight` 和新 JS。
2. ZSXQ attachment status：数据库异常后 rollback，每次尝试 finally remove；瞬断最多重试一次，成功路径也在通知前释放 Session。两个测试在旧实现明确失败，修复后文件 7 tests 通过。
3. Bilibili：完整日旧宽读/逐行更新指纹 0，约 23 个计划周期，最新两行各 38 个 raw keys；代码根因转 RUNTIME_ACCEPTED，DDL 风险另行 OPEN。
4. 最终完整测试、compileall、前端契约、diff、registry 与 Bug 严格门禁以及重启/运行态结果见本文末尾“最终验收补记”。

## 根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 复发 | 剩余风险 / 责任动作 | 下一轮验收条件 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| PgBouncer connect/query timeout 10/2 | Tailscale 远端建连/传输/归还长尾 | 既有路由、pool、重试和诊断防线 | 两个真实 worker 20.251/22.247s 失败；executor 毫秒级 | 是 | 运维继续查热池、链路、commit/checkin | 完整日 timeout=0、应用失败 SQL=0 | OPEN / REPEATED |
| audio-assets `323ce/861525` | IME input + 已发请求无 single-flight | composition + latest-only drain + TDD | backend 已提供新 HTML/JS | 旧入口本窗复发，新代码待后窗 | 缺真实中文输入 request-id | composing 期间 0 请求、同时在途≤1、P95 显著下降 | RUNTIME_REPLACED / OBSERVING |
| ZSXQ `f8b9` + PendingRollback | best-effort DB 异常吞噬后未清理 Session | rollback/remove + 一次有界 retry + TDD | 新 worker PID 14749 ready；历史任务只读确认为 completed/done | 是 | 历史状态/通知待只读对账，不能自动回填 | 真实 retry 后附件一致、queued=True、无 PendingRollback | RUNTIME_REPLACED / AWAITING_REAL_TASK |
| workflow_notify 107 retry/29 fail | 跨 event 无 provider 熔断和 durable pending | 仅既有单 event 指数抖动 | 18 时失败风暴 | 是 | 需 durable outbox、全局预算、幂等补发 | 故障注入下建连受限、event 最终送达、terminal fail=0 | OPEN / REPEATED |
| MP3 jobs 47 次 | 既有隐藏页/轮询重叠 | Visibility + single-flight 已加载 | 请求 -77.8%、累计 -72.9% | 未见显式复发 | 无 hidden/in-flight telemetry | 隐藏态 0、可见在途≤1、按活跃分钟对比 | OBSERVING / IMPROVING |
| Bilibili 旧宽读 `2a9b...` | 双实例与宽读/未变化 JSON 写回 | OS 锁 + 窄 identity + 批量条件更新 | 完整日旧指纹 0、约23周期、raw字段完整 | 否 | 重复索引另行 OPEN | 后续日继续 0；字段完整 | RUNTIME_ACCEPTED |
| Bilibili 重复 bvid 索引 | 模型历史形成等价唯一索引 | 只读维护工具/人工 SQL 已备 | 两索引仍存在 | 持续风险 | DBA 明确授权并执行 | post-drop 目录/行数/唯一性/EXPLAIN/回滚证据 | OPEN / MANUAL DDL |
| PDF canonical miss | 缺失态与未查询态混淆 | sentinel/O(1) 既有修复 | 本窗自然样本不足 | 未捕获 | 三入口样本不足 | 各≥5次、旧指纹0、字段完整 | OBSERVING |
| Celery status cold | broker inspect 冷启动 | 既有短事务/single-flight | 4次，P95 12.950s | 是 | 控制面仍慢 | ≥5次冷请求 P95<5s、无 Session 跨 inspect | OPEN |

## 未捕获边界

- `pg_stat_statements` 缺失，服务器全历史 SQL 不可得；应用日志仅覆盖达到阈值/被记录的语句。
- 应用 SQL timer 与现场 EXPLAIN 不是同一次执行；只能用数量级和计划排除 executor/索引，不能逐条替代服务端 trace。
- Session 的 non-SQL 时间尚混合 result fetch、commit、rollback、checkin 与应用代码。
- 服务端日志不知道浏览器 Page Visibility、IME composition、多标签与同时在途数；前端修复必须以后续 request-id 窗口验收。
- ZSXQ insights/topics/tabs/sync 和 PDF 三入口自然流量不足；未捕获不能写成未发生。
- 后台任务统计来自 Celery success/retry/failure 日志；外部 provider 最终送达、漏通知和未记录的 broker 丢失需 durable event 对账才能证明。

## 下一轮明确验收计划

1. 优先按两个 query timeout 的 task id 复核 PgBouncer server 建连、wait、source address、应用 execute/fetch/commit/checkin；要求 connect/query timeout 和失败 SQL 同时清零。
2. 用真实中文输入触发 audio-assets，按 request id 证明 composition 期间 0 请求、同页同时在途不超过 1、等待时只执行首个和最后一个关键词，并对比 P95 20.029 秒基线。
3. 新 zsxq_publish worker 下完成真实发布；若自然出现瞬断，必须有一次有界 retry、附件最终一致、`publish_completed queued=True` 且无 `PendingRollbackError`。历史任务先只读对账，任何回填/重发需人工确认。
4. workflow_notify 按 event id 建立失败、pending、补发、最终送达对账；实施跨 event provider 熔断/全局预算前保持 OPEN。
5. MP3 jobs 继续按活跃分钟、隐藏时段和并发 request id 复核，不用总量下降冒充闭环。
6. PDF 三入口各积累至少 5 次自然样本；Bilibili 继续抽查旧指纹和 raw JSON 完整性。
7. DBA 若明确安排窗口，再逐段执行既有 bvid 索引脚本；未授权不自动 DDL。

## 最终验收补记

- 定向：`test_zsxq_publish_state_updates` 7 tests；MP3 audio search backpressure、canonical cleaned asset、lazy mount 三个 Node 契约和 JS syntax 全部通过。
- 完整 unit 在沙箱内因 34 个 loopback bind `PermissionError` 得到预期环境失败；在允许本地测试 socket/Redis 的相同命令复跑为 **1411 tests / OK**。
- `compileall -q src/backend`、`git diff --check`、`jq empty recurrence-registry.json` 通过；Bug strict 为 176/176 已归类、9 个复发簇、最近回顾 2026-08-29。
- 重启安全门真实读取 47 条 publication task、0 条活动 ZSXQ sync run，确认无不可中断任务；标准整栈重启成功。backend PID 14264、health healthy、broker OK；17 个服务全为 running，队列 0、无 paused/active/reserved/scheduled。zsxq_publish worker PID 14749 于 06:22:20 ready。
- 真实 audio-assets API：`q=天风` 返回 HTTP 200、0.810 秒、2 条有效资产，扫描 27、缺失 canonical 跳过 25；真实 HTML/JS 为最终版本并包含 IME/single-flight 代码。它证明新资源与 API 可用，但单请求不替代真实 IME 并发验收。
- 受影响 ZSXQ 历史任务只读确认为 `completed/done` 且 source task id 正确；未执行附件回填或通知重发。新 worker 尚无新的真实发布/自然瞬断样本，因此 Session 修复只能标 `RUNTIME_REPLACED / AWAITING_REAL_TASK_ACCEPTANCE`。
- 06:24 的历史任务只读对账产生一条 3.072 秒 adhoc 宽 ORM 点查，发生在固定窗口之后，不混入本轮 215 条托管应用基线；它再次显示全载荷读取经远端链路的成本，后续诊断应继续使用窄投影。

## 方案 1 根因闭环实施补记

### 修复前后量化证据

| 问题 | 修复前证据 | 已落地变更 | 本轮运行态证据 | 当前状态 |
| --- | --- | --- | --- | --- |
| workflow notify 跨 event 风暴 | 116 success / 107 retry / 29 terminal fail，18 时集中 | durable outbox、唯一 dedupe、provider 租约/熔断、watchdog 对账、单层 SMTP 预算 | 同一真实 event enqueue 2 次→1 delivery；sent、attempt=1、provider closed；仅一次 SMTP send | RUNTIME_REPLACED / OBSERVING |
| PgBouncer 300 秒建连 churn | 约 72 次新 server connection/小时；固定窗 connect/query timeout 10/2 | 900/1800 只读 A/B，默认选择 1800；不提高 timeout | 900：185/185、21.575/h、P95 1508.540ms；1800：352/352、7.560/h、P95 1433.797ms；两窗六类错误均 0 | churn 已替换；网络 OPEN / OBSERVING |
| Bilibili 等价唯一索引 | video bvid 两个唯一索引；competitor uid 同类两个唯一索引 | 保留 constraint owner，并发删除两个独立索引；历史 migration 与 runtime warning 防复发 | video：1128 行/重复 0/DDL 0.0364s/点查 0.066ms；uid：6 行/重复 0/DDL 0.5165s/点查 0.020ms | INDEX_CLEANUP_APPLIED / OBSERVING |
| ZSXQ topic-type 索引清单 | hot 索引 2990 scans；另一候选仅 1 scan、缺 SQL origin 与 with/without EXPLAIN | 工具默认 audit-only、精确 allowlist、证据 JSON、post failure 并发回建；历史 migration 不再批删 | `group_tab_type` 明确保留；`group_type` 未执行 DDL | KEEP / OPEN-EVIDENCE_MISSING |

### 通知代码、事务和后台调用链

新增 migration `20260829_workflow_notification_outbox`，生产创建
`workflow_notification_deliveries` 与 `workflow_notification_provider_states`。业务事务未提交时
拒绝产生幽灵 outbox；干净/Core 事务先提交释放 Session，再由独立短事务插入 delivery，之后才做
broker I/O。broker 失败保持 durable pending；watchdog 查询 due provider 后释放 Session 再投递。
worker 按 provider→delivery 固定锁序获取租约，SMTP 期间不持有数据库 Session，完成/失败再用短事务
收口。临时错误进入共享 retry_wait/open window，永久认证/收件人/5xx 进入 dead letter；普通断连
不再双协议重试，只有明确 SSL protocol mismatch 才允许 STARTTLS fallback。租约按完整 SMTP 预算
计算，默认 126 秒；邮件带稳定 `X-Workflow-Event-ID`。

真实 event `db-audit-runtime-034bd8a9-16f9-48f9-8507-f55a7a3cbb24` 对应 delivery
`9cdd2f9d-45fa-4b9c-8276-d30e474acf70`。两次 enqueue 返回同一 ID、matching rows=1；worker
日志只有一次 `durable delivery sent`，随后重复 drain 均为 `no_due_delivery`。最终
`sent_at=2026-08-29T08:31:12.670047`、attempt=1、无错误/租约/熔断。数据库 sent 是 SMTP
调用成功证据，不等价于邮箱客户端人工回执，`mailbox_receipt_verified=false`。

固定窗口的 29 个历史 terminal failure 没有完整 durable payload，未自动重建、回填或重发；这避免
用假数据或猜测收件内容绕过审计。SMTP “服务端接受后、sent commit 前进程崩溃”仍可能造成
at-least-once 重复歧义，稳定 event header 与审计行只能暴露，不能宣称绝对 exactly-once。

### 索引与数据库变更证据

两次 Bilibili DDL 前均重新核对主库 `show_ytb_data`、角色 `cappa_rw`、owner、行数、重复组、
waiting lock、valid/ready/unique 和 constraint ownership；只删除精确独立索引
`ix_bilibili_competitor_videos_bvid` 与 `ix_bilibili_competitors_uid`，没有改表数据、约束或
原始 JSON。post-check 均保留行数/唯一性，约束索引有效；维护工具任何 post-check 失败都会并发
重建目标独立索引。下一窗口仍需确认目录未被旧环境重新制造，故暂不 CLOSED。

`zsxq_topics` 的 `ix_zsxq_topics_group_tab_type_create_desc` 已 2990 scans、约 5.07MB，是业务热
索引，明确 KEEP。`ix_zsxq_topics_group_type_create_desc` 只有 1 scan、约 3.81MB，但生产无
`pg_stat_statements`，也没有真实 SQL origin 和同参数 with/without-target EXPLAIN；本轮没有盲删。
当前没有待执行的 Bilibili 人工 SQL；若以后补齐 comment 候选证据，仍须在 DBeaver 逐条执行、设置
lock/statement timeout，并保存验证/回滚结果。

### 完整测试、重启与端到端验收

- 通知定向 39/39、Bilibili 工具 11/11、comment 工具 6/6、PgBouncer 8/8；最终完整 unit
  **1453/1453**，`compileall`、`git diff --check`、recurrence JSON 均通过。
- 重启前门禁读取 47 条 processing 记录和 0 条活动 ZSXQ sync；冻结消费后第二次门禁仍为 0，
  未使用 FORCE。backend PID 44341 健康、broker OK、17/17 服务 running，workflow_notify
  PID 44815 ready，watchdog 已运行新 outbox reconciler。队列长度控制面本次返回 Redis socket
  timeout，但 worker process/inspect 均正常、无 active/reserved/scheduled；该冷控制面风险继续 OPEN。
- runtime schema 校验通过；显式只读比较确认数据库 current 与代码 head 均为
  `20260829_workflow_notification_outbox`。PgBouncer 实际为 lifetime 1800、min pool 6、`cl_waiting=0`，六条
  Tailscale server 就绪。重启后短窗口 connect/query/query-wait/crash/TLS/login failure 均 0。
- 真实 DB-backed audio-assets `q=天风` 返回 2 项、HTTP 200；冷请求 3.604 秒，暖态三次
  0.630/0.786/0.693 秒。它证明 backend/API/数据库链路可用且明显低于固定窗 P95 20.029 秒，
  但四个探针不能替代真实 IME 并发和完整业务日。

### 根因闭环状态表（方案 1 后）

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险 / 责任动作 | 下一轮验收条件 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `WORKFLOW-NOTIFY-SMTP-RETRY-STORM` | event 各自重试、无共享 provider 状态/持久待发 | outbox + dedupe + provider lease/breaker + watchdog | 真实重复入队一行、一次 sent、attempt=1 | 旧窗是；新代码短窗否 | 历史 29 条不可安全补发；SMTP accept/commit 歧义；责任：应用/运维 | 完整 24h terminal fail=0；受控瞬断只 1 probe；pending 最终 sent；event id 不重复 | RUNTIME_REPLACED / OBSERVING |
| `POSTGRES-TUNNELED-ROUTE-TIMEOUT` | 远端 Tailscale/PG 链路长尾；300 秒轮换另增 churn | 1800 秒默认值 + A/B 契约；timeout 不变 | churn -89.5%、352/352、错误 0；运行池 6/等待 0 | 固定窗 10/2；A/B/短窗否 | 完整日网络仍未知；责任：运维/应用共同复核 | 六类 PgBouncer 错误、应用失败 SQL、20 秒 hold 全为 0 | OPEN / OBSERVING |
| Bilibili bvid/uid exact duplicate | 历史 migration 在列 UNIQUE 后又建独立 UNIQUE | 两个并发 DROP + fresh-install/runtime guard | 行数/重复/约束/点查 post-check 全过 | 尚未见重建 | 下一固定窗目录复核；责任：DB 巡检 | 两独立索引仍不存在、constraint valid、重复 0、点查不退化 | APPLIED / OBSERVING |
| ZSXQ topic-type overlap | 历史批删清单缺真实工作负载证据 | 工具 fail-closed、hot index 保护、migration 防批删 | hot=2990 scans；candidate=1 scan 且证据缺失 | 风险仍在 | DBA 只有证据完备才执行 | 真实 SQL + pg_stat reset time + with/without EXPLAIN + replacement | OPEN / EVIDENCE_MISSING |
| audio-assets IME overlap | composition 期间多请求且无 latest-only owner | composition + single-flight（既有本轮修复） | API 冷/暖 200；未模拟真实 IME | 旧窗是；新入口未捕获 | 前端真实输入 telemetry | composing=0 请求、同时在途≤1、只执行首尾关键词 | OBSERVING |
| ZSXQ poisoned Session | DB timeout 被吞后未 rollback/remove | rollback/remove + 一次有界 retry（既有本轮修复） | 新 worker ready；无新真实发布瞬断 | 旧窗是；新样本缺 | 不擅自发布/故障注入外部内容 | 真实任务附件一致、queued=True、无 PendingRollback | AWAITING_REAL_TASK |

### 未捕获边界、诊断噪声与下一轮

- 生产仍无 `pg_stat_statements`；应用 timer 与现场 EXPLAIN 不是同一次执行，未捕获不能当未发生。
  Session non-SQL 仍混合 fetch/commit/rollback/checkin；页面 visibility、IME、多标签和邮箱收件回执也
  不在服务端观测范围。
- 早期只读诊断曾出现 shell/Python 引号 SyntaxError、缺 `pg_stat_statements`、读取受限
  `current_setting`、引用不存在列，以及一次 UID 审计 shell 引号错误；均未到达写库。重启后两次
  localhost curl 先被 `127.0.0.1:7890` 代理/沙箱 loopback 拒绝，`lsof` 与获准 loopback 复核证明
  backend 未退出。直接 `alembic -c migrations/alembic.ini` 因该 Flask-Migrate 配置没有独立
  `script_location` 而 fail-fast，随后显式目录与只读版本表比较通过；一次 watchdog 日志 glob 无匹配
  也仅是诊断命令噪声。以上不能算业务或数据库失败。
- 下一轮优先从 2026-08-29 16:29:08 新 backend 边界统计完整 24 小时：通知 delivery/retry/dead
  letter/最终 sent 和 event id；PgBouncer 六类错误、source、churn、wait 与应用失败 SQL；Bilibili
  两个独立索引是否重现；真实 IME 输入；新 zsxq_publish 自然任务。任何一项缺后窗都保持
  OPEN/OBSERVING，不得写“已解决”。

## MP3 authoritative catalog 根因闭环补记

### 为什么 v3 前端背压仍不足以闭环

方案 1 第一次重启后的单次 `q=天风` 探针虽为 0.810 秒，但随后用真实页面重新加载默认目录，
`request_id=53df69ee` 仍耗时 23.347 秒。该请求的 Session 持有 22.590 秒，7 条 SQL 累计
22.455 秒；三个 `zsxq_attachments` chunk 每次仅返回 40 行，却分别等待 3.432、16.844 和
1.151 秒。前端 v3 的 latest-only drain 能阻止等待期间的中间请求，却不能让首请求本身更快：
快速输入时首个 `q=t` 的 `request_id=b9ac5005` 仍耗时 20.108 秒，canonical SQL 等待
19.576 秒，Session 持有 19.838 秒。最终 `q=天` 在前端 20 秒 timeout 后才启动，尾部仍有约
0.19 秒重叠。因此 v3 只能算止住部分放大，不能当根因完成。

### 真实数据、SQL origin 与 executor 证据

`service.mp3_video_workflow.list_audio_assets` 的旧调用链以附件分页为候选源：

| 证据 | 数量 / 耗时 | 结论 |
| --- | ---: | --- |
| `zsxq_attachments` 总行 / 音频已下载候选 | 34,458 / 3,886 | 历史状态很多，但不是当前可播放目录 |
| `content_pipeline_assets` 总行 / cleaned audio | 726 / 280 | canonical cleaned asset 才是权威源 |
| 最近 120 个附件候选 exact / normalized canonical match | 0 / 0 | 旧候选源无法映射到 cleaned asset |
| 最近 120 个候选 cleaned ready / raw 文件存在 | 0 / 0 | 继续翻页不会产生展示项 |
| 280 个 cleaned 记录实际文件存在 / 缺失 | 126 / 154 | 返回 126；保留并过滤 154 条审计记录 |
| 旧 chunk offset 0/40/80 executor | 172.228 / 175.074 / 176.175ms | 远低于应用 3–17 秒等待 |
| 新 canonical 查询 executor | 1.875ms，280 行，72 shared hits | 无磁盘读/temp，不需要新增索引 |

旧 chunk 计划需处理 3,886 个附件候选并顺扫 12,637 个 topic，5,298 shared hits，但没有
temp spill 或 physical read；新 canonical SQL 的应用计时 19.576 秒又约为 executor 的一万倍。
这组 request id、SQL origin、Session 与 EXPLAIN 证据共同把根因定位为：候选数据所有权反转，
导致应用反复经 PgBouncer/Tailscale 拉取和后过滤；远端网络/驱动长尾放大每一次往返。没有
waiting lock、executor 重活或缺简单索引证据。

实现过程中，初版显式投影遗漏 `media_type`，真实只读 benchmark 立即暴露 280 次 deferred-column
lazy-load：共 283 条 SQL、Session 12.48 秒。查询预算测试随后先 RED，再补齐投影；这一缺陷在重启
前已经消除，未进入最终运行进程。这项诊断证据也被固化为“目录构建只能有一条 canonical SELECT”
的防复发契约。

### 最终代码/配置方案

- `content_pipeline_assets(status=cleaned, media_type=audio)` 成为唯一候选源；单次显式投影最多
  2,500 行。附件和 topic 只通过一条有界查询补充标题、作者、tab 等元数据，归一化绝对路径兼容
  `/var` 与 `/private/var`。
- catalog builder 验证 cleaned 文件实际存在；API 返回前再次校验当前页文件。154 条缺失记录仍保留
  作审计，不回填假文件、不删除原始 JSON，也不牺牲字段完整性。
- canonical 和 metadata 查询分别完成后立即结束只读事务并 remove Session，再做路径归一化与
  `os.path.isfile`；缓存命中同样先释放请求鉴权可能留下的 Session。数据库异常先清理失败 Session
  再抛出，禁止目录连接跨文件系统 I/O。
- 目录快照采用进程内 `RLock` single-flight，默认 TTL 300 秒、上限 3,600 秒；q/group/tab/hot、
  排序与分页在有界快照完成。普通搜索复用快照，页面显式刷新使用 `refresh=1` 强制重建；响应暴露
  catalog size/build time/cache hit/age/TTL。
- 前端继续使用 composition 门禁、单一 drain owner 和 latest-only pending；静态资源版本更新为
  `20260829-cleaned-audio-assets-v4-catalog-snapshot`。
- 未增加 timeout、重试或索引；没有 CREATE/DROP INDEX、ALTER TABLE、回填或破坏性操作。

### 修复前后量化与真实端到端验收

| 场景 | 修复前 | v4 修复后 | SQL / Session 结果 |
| --- | ---: | ---: | --- |
| 默认真实页面 | `53df69ee` 23.347s | `ea2496e3` 1.009s；最终 `3708b3b0` 1.094s | 前：7 SQL/22.455s、hold 22.590s；最终 2 SQL 562.22/248.34ms，无长 hold |
| 快速输入首请求 | `b9ac5005` 20.108s | 强刷 `499a067d` 1.219s | 前 canonical 19.576s、hold 19.838s；后仅一次冷构建 |
| 暖搜索 | 旧链路可达 14–20s | `15b783b2` 1.59ms；`faabd527` 1.53ms；最终 `108830e2` 3.02ms | 0 条 catalog SQL；最终 `q=天风总` 返回 1 个真实文件 |
| service 冷目录 | 旧实现最多扫描 121 条附件才命中 1 项 | 1.689s | 2 SQL，扫描 280 canonical、保留 126、跳过缺失 154 |
| service 暖搜索 | 每个 q 重查 280 canonical | `q=天` 0.980ms；`q=天风` 0.758ms | 均 0 SQL |
| service 强刷 | 无显式目录刷新所有权 | 1.231s | 2 SQL |

真实浏览器“先强制冷刷新、再快速输入多个中间词、最终停在 `天风`”总计约 1.66 秒：只发出
`499a067d` 与 `faabd527`，中间词没有请求；最终 GET 在首请求完成 94ms 后才开始，未重叠，页面显示
2 项。冷/暖请求均未再产生 Session 长持有告警。当前 in-app Browser 控制层不能原生注入 macOS
输入法 composition 事件，故 native composition 只由 JavaScript 契约覆盖，不能冒充真实候选窗证据。

安全门禁在第二次重启前读取 47 条 processing task、0 条活动 ZSXQ sync，确认没有不可中断任务；
未使用 FORCE。增加 Session/文件 I/O 边界契约后又按同一门禁完成最终标准重启；backend PID 52574、
health healthy，bootstrap 于 17:32:32 完成，17 个 Celery 服务全部 `ok=true`、watchdog 正常且
`errors=[]`，MP3/content pipeline/workflow notify worker ready。Bilibili intelligence scheduler
本机配置为 disabled（bootstrap `ok=true` 但 `started=false/running=false`），不是本次修复导致的启动失败；
该边界保留到下一轮配置复核。

最终进程的真实 UI 强刷 `3708b3b0` 为 1.094 秒，canonical 与 metadata 阶段分别 562.22ms 和
248.34ms；其后才进行文件校验。真实暖搜索 `108830e2` 为 3.02ms、0 条目录 SQL，页面显示 1 个
`天风总` 结果。两次请求均无 slow-query 或 connection-held-too-long 告警；结合 TDD 中
`os.path.isfile` 执行时事务状态由 `True` 变为 `False`，形成“代码边界 + 单测 + 新进程 API”证据链。

### 测试、索引结论与闭环状态

- TDD 四个失败证据依次覆盖：附件候选扫描 121 条、连续搜索重复 canonical SELECT、遗漏投影触发
  deferred-column SELECT、文件校验期间事务仍为 active；最终相应契约均通过。
- MP3 定向 186 项通过；canonical/backpressure 前端契约与 JavaScript syntax 通过。
- 一次遗漏项目 `PYTHONPATH` 的诊断命令跑过 1,452 项并出现 2 个 `services` 导入错误、0 个断言失败；
  按项目标准环境执行完整命令后为 **1,456/1,456 OK**。最终 compileall、JavaScript syntax、registry
  JSON、`git diff --check` 均通过；Bug strict 为 **177/177** 已归类、9 个复发簇、最近回顾
  2026-08-29。
- 两组 `EXPLAIN (ANALYZE, BUFFERS)` 均显示 executor 毫秒级，且无磁盘/临时文件/锁证据；本问题
  **不生成索引 DDL**。现存 ZSXQ topic-type 低使用候选仍因缺真实 SQL 与 with/without EXPLAIN 保持
  OPEN/EVIDENCE_MISSING，不能借本问题盲删或盲加。

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险 / 责任动作 | 下一轮验收条件 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| MP3 stale attachment chunk + canonical reread | 错误候选源优先级、失效后过滤、重复远端往返、Session 跨文件 I/O | authoritative catalog、O(1) metadata、TTL single-flight、显式 refresh、查询后 release/remove、文件二次校验 | 最终冷 `3708b3b0` 1.094s/2 SQL；暖 `108830e2` 3.02ms/0 SQL；文件检查事务单测为 false | 旧 v3 后复发；最终进程短窗否 | 进程内 cache 多实例各自冷建；154 个历史文件缺失；责任：应用继续观测 | 完整 24h 旧 chunk/逐项指纹=0、暖搜索 SQL=0、冷/强刷≤2 SQL、文件 I/O 无事务、无 20s hold、字段/文件完整 | RUNTIME_REPLACED / OBSERVING |
| MP3 IME/request overlap | composition 事件和已发请求没有统一所有者 | composition + drain owner + latest-only + catalog snapshot | 浏览器 burst 仅首尾请求且无重叠 | 旧窗是；普通 burst 新代码否 | native IME 未由控制层捕获；责任：前端/巡检 | 真实输入法 composing=0 请求、同时在途≤1、P95 显著低于 20.029s | RUNTIME_REPLACED / OBSERVING |
| PgBouncer/network tail | executor 与 driver/application 等待相差 10²–10⁴ 倍 | 1800 lifetime、热池与诊断；本分支减少往返，不抬 timeout | v4 冷 2 SQL、暖 0 SQL，无短窗长 hold | 固定窗与 v3 后仍有；v4 短窗未见 | 完整日仍未知；责任：运维/应用 | 六类网络错误、失败 SQL、20 秒 hold 全为 0 | OPEN / OBSERVING |

### 下一轮明确复核

下一轮必须从 2026-08-29 17:31:43 最终 backend 边界优先复核完整 24 小时，而不是重新从零扫描：

1. 按 `audio-assets` request id 统计请求量、成功/失败、累计、P50/P95/最大，并确认旧附件 chunk、
   逐项 canonical/deferred-column 指纹为 0；冷/强刷目录 SQL 不超过 2，暖搜索为 0。
2. 关联 Session lifecycle 与 PgBouncer，要求无 20 秒目录持有、无 query/connect/TLS/crash，若仍有
   秒级 SQL则继续拆 execute/fetch/commit/checkin 与 source/remote socket，并确认文件校验阶段无活动事务，
   不能回退为加索引。
3. 用真实 macOS 中文输入法复核 composition 期间 0 请求、同页同时在途≤1、只执行首个和最终词；
   多实例/重启后的第一次冷建也应各自 single-flight。
4. 抽查 126 个 live asset 的标题、作者、topic/tab、source、duration 与文件可访问性，复核 154 个
   缺失文件只被过滤且审计记录仍在；不得用字段缺失换取性能。
5. 同时继续方案 1 的其他后窗：通知 outbox 最终送达/terminal fail、PgBouncer 六类错误、Bilibili
   独立索引不重现、真实 ZSXQ publish Session 清理。未达到各自验收条件的一律保持 OPEN/OBSERVING。

## 方案 1 最终补记：请求所有权与 MP3 冷启动放大器

### 修复前后量化证据

最终 telemetry 运行进程先捕获一次真实复发。17:52:34–17:57:19 共 21/21 HTTP 成功，但 7 条慢 SQL
累计 40.669 秒、P95 16.354 秒，2 条长 Session：

| 页面调用 | 修复前 request id / 耗时 | 根因与变更 | 最终 PID 57273 运行态 |
| --- | --- | --- | --- |
| audio-assets | `7c7c4e07` 11.812s；canonical 11.423s；hold 11.461s | 目录已 O(1)，剩余为远端长尾；增加 client id/seq/trigger/inflight 可观测所有权 | `b95c18bf` 1.503s、2 SQL 829.17/392.35ms；暖 `7c4e30ab` 5.8ms/0 SQL |
| jobs | `f84a8c62` 16.918s；`SET LOCAL` 16.354s；hold 16.644s | 与目录同时冷启，网络长尾而非 executor | `45311060` 377.36ms，真实 3 条成功任务可见 |
| worker 徽标 | 全量 `/api/v1/celery/status`：`092d1145` 14.404s，跨 publication/sync/workflow 多表 | 页面只需两个 worker，却误用全量业务控制面；新增 runtime-only 指定服务接口 | `fbe6f3a8` 377.78ms、0 业务 SQL；MP3/render 徽标正常 |
| 页面整体 | 初次历史请求曾 timeout；三类冷请求同时放大 | 前端改用 v6 轻量 worker status；后端在进程/Redis I/O 前释放鉴权 Session，不取业务 snapshot/broker inspect | 18:06:17 后 12/12 成功、累计2.698s、P95 377.78ms、max1.503s；慢 SQL/长 hold/SQL error=0 |

PgBouncer 在复发窗仍为 6 条 Tailscale server、`cl_waiting=0`，wait 最大约 80ms且六类错误为 0；
事后 Tailscale direct ping 约 27ms。一次恢复后的 ping 不能覆盖 17:54 的传输长尾。历史 21,600 秒
lifetime 有陈旧 socket timeout，因此没有盲目延长或提高 timeout；1800 秒运行配置保持不变。

### 代码、测试与真实页面证据

- audio-assets 增加经过白名单/长度边界的 `client_id/client_seq/client_trigger/client_inflight`，日志只写
  所有权和 cache telemetry，不持久化 q。真实同页 seq 1/2/3 与 initial/debounce/refresh 均可关联，
  `client_inflight=1`。
- 新 `/api/v1/celery/runtime-status?services=mp3_video_workflow,render` 最多接受 8 个已知服务，只读取
  进程、精确队列、paused/PID；不读跨表业务任务快照、不做 broker inspect，并在外部 I/O 前释放
  请求 Session。MP3 页面不再调用全量 `/api/v1/celery/status`。
- TDD 旧代码因缺 runtime-only/精确队列 helper、仍调用全量 status 而 RED；修复后
  `test_celery_monitor_watchdog` 38 项、MP3 定向 110 项、前端 ownership/runtime-status/canonical
  契约和 JS syntax 通过。最终完整 unit **1,459/1,459 OK**。
- 第二次安全门读取 47 条 processing、0 条活动 ZSXQ sync 且无不可中断任务；标准整栈重启未用 FORCE。
  backend PID 57273、bootstrap 18:05:49 完成、health healthy、17 个 Celery 服务 `ok=true`、
  `errors=[]`。真实页面显示 10 个文件（280 scanned/154 missing filtered）、3 条成功任务和两个正常 worker。
- 延长至 18:14:01 的分析窗为 17/17 请求成功、累计 3.461 秒、P95 723.55ms、最大 1.503 秒；
  workflow-stats `ad569087` 为 723.55ms、窄 publication 查询 604.96ms且返回 0 行。后续只读追根证明
  这不只是网络尾部：11/11 个历史 pipeline 任务的 45 字符引用被静默截成 36 字符，完整引用查询必然
  假 0；三种参数 EXPLAIN 都走现有索引，executor 仅 0.022–0.032ms。
- 创建端已规范为 UUID + `content_pipeline_asset`，读取端用一条窄投影兼容 UUID 与无歧义历史截断键，
  不扫 JSON、不回填历史数据。PID59946 真实页面恢复“编排1次/成功1”，4 次精确接口均返回 1 行，
  端点 176.60–544.38ms、SQL112.17–354.34ms；补齐 `source_feature` 防串单和完整类型引用回显后，
  最终 PID62310 的 `6487357a` 为178.68ms/SQL112.67ms/1行。五次均为0慢 SQL/长 hold/SQL error。

### 根因闭环状态表（最终运行边界）

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险 / 责任动作 | 下一轮验收条件 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| MP3 stale attachment/canonical reread | 候选所有权反转、失效后过滤、重复远端往返、Session 跨文件 I/O | authoritative catalog、O(1) metadata、TTL single-flight、查询后 release、文件二次校验 | 最终冷1.285s/2业务SQL；暖3.36ms/0目录SQL；字段/文件真实 | 旧 v3 后复发；最终进程未见旧指纹 | 多实例各自冷建、154历史文件缺失；应用继续观察 | 24h旧 chunk/lazy-load=0，冷≤2 SQL、暖0，文件 I/O 无事务 | RUNTIME_REPLACED / OBSERVING |
| MP3 client ownership/IME | debounce 不能取消已发请求，服务端缺页面 owner 证据 | composition + drain owner + latest-only + client id/seq/trigger/inflight | 同页 seq 单调、inflight=1；最终暖搜索3.36ms | 普通 burst 未复发；native IME 未捕获 | 真实 macOS 候选窗；前端/巡检 | composing期0请求、全请求inflight≤1、只首尾词、P95不反弹 | RUNTIME_REPLACED / OBSERVING |
| MP3 页误用全量 Celery status | 两个徽标触发跨业务表 snapshot 与 broker 控制面 | runtime-only 指定服务/精确队列，外部 I/O 前 release Session | 全量14.404s/多表慢 SQL → 377.78ms/0业务SQL；真实徽标正常 | 最终窗否 | 其他页面仍可能合理使用全量控制面；不得全局替换语义 | 该页24h全量status=0，runtime status无业务SQL、P95<5s | RUNTIME_REPLACED / OBSERVING |
| MP3 workflow-stats `b98866a...` | 45字符 `pipeline:<uuid>` 误入36字符 source列并静默截断，读取端却用完整引用 | 写入规范 UUID/source owner；读取端按 owner+key 兼容 UUID 与无歧义历史键；完整类型引用回显；窄投影、无JSON/回填 | `ad569087` 723.55ms/SQL604.96ms/假0 → 最终 PID62310 真实页编排1次；精确5次176.60–544.38ms/均1行 | 历史假0未复发；未来写入未有自然任务 | 下一真实任务与24h窗口；应用/巡检 | 新任务 UUID/source_feature 正确，JSON保留完整引用；历史11条无串单/假0 | RUNTIME_REPLACED / OBSERVING |
| PgBouncer/Tailscale tail | executor 毫秒级而 execute/driver 可达11–16s；瞬时传输长尾 | 1800 lifetime热池；本轮减少往返和并发，不抬timeout | 17:54明确复发；最终18:43–18:49短窗恢复且六类错误0 | 是 | 运维/应用按socket与request拆段；短窗不可闭环 | 完整24h六类错误/失败SQL/20s hold=0 | OPEN / REPEATED / OBSERVING |

### 最终未捕获边界与下一轮计划

native macOS composition 仍无法由当前 Browser 控制层注入；`pg_stat_statements` 仍未安装；一次
Tailscale ping、最终运行短窗口和测试通过都不能替代生产 24 小时。最终复核边界改为 backend
18:43:41 / 真实页面 18:44:48，下一轮必须优先延续而不是重扫：

1. 按 `[MP3_AUDIO_ASSET_CLIENT]` 聚合 client/seq/trigger/inflight，确认所有 in-flight≤1、native
   composition 为 0 请求、旧附件 chunk/deferred lazy-load 为 0。
2. 确认 MP3 页调用全量 Celery status 为 0；runtime-only 端点不出现业务 SQL或跨 Redis/进程 I/O 的
   Session，P95<5 秒；目录冷/强刷≤2 SQL、暖搜索0；workflow-stats 按真实量统计 P95/累计、返回行数与
   source key，历史11条不得再假0/串单；下一自然 pipeline 任务必须写规范 UUID 与正确 source_feature。
3. 对六类 PgBouncer 错误、失败 SQL、长 hold 保持零容忍；若新长尾发生，关联 request/task、SQL origin、
   server socket 与 execute/fetch/commit/checkin。17:54 已证明底层网络尚未闭环。
4. 同时继续 notification outbox、Bilibili 索引不重现、ZSXQ 自然发布和字段/文件完整性后窗。
   任一项未满完整窗口仍保持 OPEN/OBSERVING，不得写“已解决”。

## 方案 1 最终补记：source identity 与调度器网络尾延迟

### workflow-stats 修复前后

- 生产只读盘点：MP3 工作流 296 条，pipeline 来源 11 条；11/11 的 `source_task_id` 都是完整
  `pipeline:<uuid>` 的 36 字符截断前缀，且错误标成 `zsxq_attachment`。11 个当前键无碰撞，但不能把
  静默截断当成合法合同。
- 现有 `ix_publication_tasks_mp3_source_user_created_desc` 已有 6,208 次扫描；完整引用、历史键和 UUID
  的 EXPLAIN 都是索引扫描，执行 0.022–0.032ms。故不生成 publication_tasks DDL。
- 两个契约测试先在旧实现 RED：创建端得到截断前缀；历史 stats 返回0。修复后 API 文件7/7、MP3发现集
  116/116、完整 unit 1,461/1,461、compileall 和 diff check 通过；附加契约证明同 UUID 的不同 source owner
  不会串单，且列表 API 仍回显完整 `pipeline:` 引用。
- 两次标准重启前门禁均无不可中断任务，均未使用 FORCE。首次 PID59946 验证历史兼容；最终完整性补丁
  加载到 PID62310，18:44:25 bootstrap 完成，17个 Celery服务均`ok=true`、`errors=[]`，health healthy。
  真实页面精确资产历史恢复，未创建验收假任务。
- 最终18:43:41–18:49:51窗口8/8请求成功、累计1.495秒、P95/max1.285秒；冷目录`7a303b88`
  1.285秒（两条业务目录SQL 602.14/202.76ms），cache-hit搜索`eb4e1d98`3.36ms/0目录SQL，精确stats
  `6487357a`178.68ms/SQL112.67ms/1行。0条慢查询日志、0长Session、0 SQL error、0六类PgBouncer错误；
  目录属于“低频>1秒但无匹配慢查询日志”，已单独保留，未用“slow_query=0”误报为无问题。

### 新增低频后台尾部

18:27:46 B站充电 scheduler 启动读取 `system_configs` 出现 2,081.37ms 慢 SQL；同源日志自8月5日起
至少14次超过1秒，属于反复问题。只读证据显示表仅18行/64KB，计划顺扫1个shared buffer，executor
0.014ms；8次同连接点查27.7–31.4ms。PgBouncer同分钟平均wait13.12ms/query116.70ms，六类错误为0。
因此该尾部归入既有 PgBouncer/Tailscale 远端返回抖动，绝非缺索引；顺扫18行是优化器正确选择，新增
索引只会写放大。网络根因继续 `OPEN / REPEATED / OBSERVING`，下一24小时需同时统计该指纹次数、累计、
P95/max和调度启动影响。

## 方案 1 后续补记：共享模型 fail-closed 与最终运行窗

### 新增根因防线

API 创建端规范化不足以阻止其他调用方再次把带类型长引用写入 36 字符列。TDD 新增共享模型契约后，
旧实现因未抛异常而 RED；`PublicationTask.sync_projection_fields` 现对所有新建/未知超宽
`source_task_id` fail closed，只允许已经持久化且精确命中 11 条已知 MP3 历史指纹的旧行保持原值。
全库按真实候选优先级盘点仍只有 `mp3_video_workflow/zsxq_attachment` 这 11 条超宽候选，11/11 为
45 字符 `pipeline:<uuid>` 被截成 36 字符，没有发现其他 feature 的未知污染。真实历史任务用新模型同步
前后值不变并回滚，证明兼容不是靠放宽未来写入合同。

定向模型/API 23/23、模型/MP3/PgBouncer/投影子集 108/108，完整 unit **1,462/1,462 OK**。安全门
确认 47 条 processing 中无不可中断任务、活动 sync 为 0；标准整栈重启未用 FORCE。最终 backend
PID **65497**，19:07:29 bootstrap 17/17 `ok=true`、`errors=[]`，health healthy。

### 最终页面、SQL 与网络反复证据

真实登录页面从 19:08:39 开始：目标资产只出现 1 条，显示“编排 1 次 / 最近已完成 / 成功 1”；历史
任务 `a344af22-a913-445e-8369-77b7a506aa56` 正确可见。暖搜索 `a4462def` 5.54ms、0 条目录 SQL；
workflow-stats `1bbf928e` 244.05ms、SQL177.05ms、1行，历史假0没有复发。

但本窗不能宣告数据库性能闭环。19:06:43–19:09:19 共 17/17 请求成功、累计23.399s、P50
3.93ms、P95 8.291s、最大13.260s；4条应用慢 SQL 累计22.111s、P50 5.726s、P95/max
12.362s；2条长 Session P50 8.309s、P95/max12.408s，SQL/non-SQL 分别20.586/0.130s。
无 statement/lock/connection timeout、idle in transaction、SQL error 或六类 PgBouncer 错误。

| 页面/API | request id | 总耗时 | SQL/Session 关联 | 结论 |
| --- | --- | ---: | --- | --- |
| audio-assets 首次目录 | `8a54a58e` | 13.260s | canonical SELECT 12.362s；hold12.408s/非SQL45.86ms | 低频高延迟；目录形态已根治，网络尾部反复 |
| jobs 首次历史 | `f36e0af5` | 8.291s | `SET LOCAL`5.726s、count1.716s、rows782.44ms；hold8.309s | trivial SET 同样慢，排除大字段/索引为主因 |
| runtime-only worker | `a5ba933d` | 725.99ms | 0业务SQL | 未回退全量 Celery status |
| audio-assets 暖搜索 | `a4462def` | 5.54ms | cache hit、0目录SQL、client seq2/inflight1 | backpressure/cache 合同正常 |
| workflow-stats | `1bbf928e` | 244.05ms | 窄 publication 查询177.05ms/1行 | source identity 历史兼容正常 |

生产 `content_pipeline_assets` 为 726 行、524KB表/856KB含索引，目标 280 行；现有 6 个索引中
status 单列累计仅 1 scan，但真实 EXPLAIN 三次选择顺扫+内存 quicksort，executor
0.396/0.484/0.568ms、64–67 shared-hit、0 shared-read/temp，计划估计289行与实际280行接近。直接链路
客户端仅30.4–31.9ms，10次 `SELECT 1` P50/P95/max 28.276/76.372/113.070ms。PgBouncer 同窗口
periodic stats wait P95/max36.20ms、query P95/max117.58ms，19:09周期均值升至 wait114.112ms/
query507.052ms；无 client wait/timeout。由此确认 12 秒来自远端传输/返回瞬时停顿，绝非 executor、锁、
磁盘或缺索引；本轮不生成 content-pipeline 索引 DDL。

### 根因闭环状态表（19:06:43 新边界）

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险 / 责任动作 | 下一轮验收条件 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| MP3 source identity | API带类型引用误入定长列，且共享模型静默截断 | UUID+owner规范化、历史无歧义兼容、完整JSON回显、模型层未知超宽fail closed | PID65497真实页1次/成功1；stats244.05ms/1行；全库仅已知11条 | 历史假0否；未来自然写入未捕获 | 下一真实任务与24h；应用/巡检 | 新任务UUID/owner正确、JSON完整；未知超宽拒绝；历史11条无串单 | RUNTIME_REPLACED / OBSERVING |
| MP3 authoritative catalog | 候选反转、重复往返、Session跨文件I/O | 单次有界catalog、TTL single-flight、查询后release、文件二次校验 | 冷仍≤2业务SQL；暖5.54ms/0目录SQL；真实字段完整 | 旧查询形态否；网络尾延迟是 | 多实例首次冷建；应用继续观察 | 24h旧chunk/lazy-load=0、暖0 SQL、文件I/O无事务 | RUNTIME_REPLACED / OBSERVING |
| PgBouncer/Tailscale tail | executor亚毫秒而同步返回可达5.7–12.4s | 1800 lifetime热池与分段观测；应用减少往返/并发，不抬timeout | canonical executor≤0.568ms vs API SQL12.362s；trivial SET5.726s | 本最终窗再次复发 | 外部网络/socket尚无根治权限与故障时分段遥测；运维/应用 | 完整24h六类错误0且失败SQL/长hold=0；冷P95<5s/max<10s | OPEN / REPEATED / OBSERVING |
| Native IME ownership | composition与已发请求所有权曾分离 | composition+latest-only drain+client telemetry | 本页seq1→2、inflight均1；普通输入只首尾 | 普通输入否；native IME未捕获 | 当前浏览器控制层不能产生真实候选窗；前端/巡检 | macOS中文候选期间0请求、完成后仅最终词、inflight≤1 | RUNTIME_REPLACED / OBSERVING |

下一轮必须从 backend **2026-08-29 19:06:43**、真实页面 **19:08:39** 继续，而不是重置基线：优先捕获
自然新 pipeline 任务、native macOS composition、24h source 写入审计和 PgBouncer 六类错误；按 request id
继续拆 execute/fetch/commit/checkin。网络长尾已在所谓“最终短窗”再次发生，因此不得把前一短窗恢复或
测试全绿写成“已解决”。
