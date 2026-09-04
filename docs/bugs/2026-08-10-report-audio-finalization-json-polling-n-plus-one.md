# Report audio finalization 周期 JSON 扫描与详情 N+1

## 基本信息

- 状态：2026-09-04 父任务唤醒查询形态再次检出；RUNTIME_REPLACED / OBSERVING，待自然完成事件与后续窗口
- 严重级别：P0
- 影响模块：研报音频 finalization watchdog / 详情列表 / `publication_tasks`
- 首次发现：2026-08-10 日巡检
- 修复提交：当前工作区
- 根因分类：JSON predicate polling / N+1 / multirow payload / over-fetching
- 复发键：`HOT-PATH-PAYLOAD-POLLING`
- 是否复发：是；同簇此前已多次出现任务表 JSON 轮询和列表逐项大字段读取
- 关联记录：`2026-08-05-report-audio-hidden-tab-polling-n-plus-one.md`、`2026-07-22-terminal-polling-image-repair-payload-recurrence.md`
- 防线层级：data_access / contract_test / runtime_acceptance

## 现象

### 2026-09-04 提交前并发恢复补强

独立 Session 回归确认：broker 等待期间新增的审计字段会被旧失败恢复整份 result 覆盖；父任务被取消或
进入新步骤后，也可能被改回 processing/pub_waiting。本次失败恢复改为主键 + claim token/broker ID +
活动状态 + 可恢复步骤锁定最新快照；可恢复步骤为 WAKE_STEP 及准入守卫写入的三种精确 deferred 步骤。
仅恢复 claim 修改的字段；waiting_child 仅回退五个被修改键，保留其他新增审计信息，不再回写旧
status/error/completed_at。真实准入延后后 broker 失败（三种原因）、并发追加、取消、推进及原路径
11 项通过。该补强尚未执行运行替换；06:10 的 ready 证据只属于先前窄查询版本。

### 2026-09-04 父任务唤醒复发与延迟物化

固定窗口 09-03 06:00 至 09-04 06:00，`71b8b6d6cc87d1f3` 在新 worker PID7759
再次出现 8 次 `multirow_payload`，累计252.80ms、P95/max42.28ms。虽未过1秒阈值，
仍违反查询形态门禁；不能因 executor 快而关闭。真实 child `3bfb3376-2469-4a09-9ec3-45ba49dd8766`
的原查询命中 waiting_child 索引，executor0.034ms、返回0行；无新增索引依据。

根因是完成事件先按 waiting_child 等结构化字段找父任务，却在判断是否存在/已唤醒前读取完整 result。
现在候选查询改为不可变窄 Row；只有匹配且未唤醒的单一父任务才按主键与 updated_at 读取完整回滚快照，
短事务内用行锁防止筛选与更新间版本漂移。原始 JSON、CAS token、稳定 broker ID、失败回滚全部保留。
这采用既有“先结构化判断、命中主键才延迟物化”的纪律，不机械删除 JSON 或改变业务字段。

新增两项实际 SQL 回归先红后绿：无匹配与重复事件零 JSON 读取；有效唤醒恰好一次主键载荷查询，
broker 入口无活动事务。父唤醒8项（含失败恢复、终态父、重复事件）通过；完整unit1593项通过。
新候选查询executor0.014ms。06:07安全门及标准脚本两次门禁均无不可中断任务，冻结17队列后重启至
backend38129；06:10:05 render、06:10:15 report_audio均ready，bootstrap17/17、errors=[]，health healthy。
自然 publisher completion 与下一完整窗口旧指纹0
才可进一步验收，不能用本次EXPLAIN或单测代替业务验收。

固定 24 小时内，`repair_pending_finalizations_once` 指纹 `6b1c434d80a9291e`
约执行 938 次，平均 35.76ms、P95 113.28ms、最大 357.77ms、累计约 33.5s，频率约每
90 秒一次。查询永久在 `publication_tasks.result` 上判断 `completion_contract_version`。

详情列表对每个父任务调用 `finalization_summary_for_audio`；指纹 `af15f...` 本窗口 274 次、
平均 56.21ms、P95 194ms、最大 491.8ms、累计约 15.4s，并读取完整 `params/result`。

## 影响

- 无业务变化时 watchdog 仍每天产生约 960 次 JSON 条件查询。
- 详情页每增加一个父任务就增加一次完整 JSON 查询，放大网络抖动和连接占用。
- 原始 JSON 是审计数据，不能靠删除字段或假数据缩小问题。

## 根因

历史 completion contract 兼容修复被留在永久 watchdog 条件中。真实只读核验显示只有 4 条
completed finalization，待 v2 回填数量为 0；新完成路径也写 v2，历史条件已完成使命却无退出机制。

详情列表先批量取得父任务，随后 `_job_payload()` 对每项独立查 latest finalization；摘要只需
少量 JSON 子字段，却读取整个载荷。

## 解决思路

- 周期 watchdog 只处理 stale active；按 id 的显式 reconcile 仍保留旧完成记录兼容能力。
- 对有界页面最多 100 个 audio id 执行一次摘要查询，仅投影结构化列和所需 JSON 子字段。
- 保留全部存储 JSON 和接口 summary 字段，不执行 DDL。

## 实际方案

- 删除 completed + JSON contract 周期谓词，只查询 stale `pending/processing`。
- 新增 `finalization_summaries_for_audio_ids`，去重并硬限制 100 个 id。
- `detail=1` 列表一次装载 summary map 并传给 `_job_payload`；单任务详情仍可单条查询。

## 验证

- 两条行为测试先失败：旧完成记录仍被 reconcile；批量 loader 不存在且详情列表未调用。
- 修复后 4 条定向行为通过；相关模块 177 tests passed。
- 新 backend PID `64496`，report audio worker 06:18:10 ready。
- 真实 `GET /api/report-audio-workflows/jobs?detail=1&per_page=5`：HTTP 200/4.254s，5 项且
  finalization summary 字段完整；新窗口无 finalization JSON predicate 或逐项 full-payload 指纹。

## 复发预防

- 一次性历史兼容扫描必须有退出条件，不能永久放在 watchdog。
- 列表子任务摘要必须按页面 id 集合一次加载，禁止 `_job_payload` 内逐项查库。
- JSON 审计字段完整保存；热读只投影必要子字段，并以 SQL 形态测试拒绝整列回归。

## 后续事项

- `detail=1` 的父任务查询本身仍整页读取 `params/result`：真实 5 条响应 856,468 bytes，
  父查询 1.858s、端到端 4.254s。剩余 over-fetch 保持 OPEN，需分离列表摘要与单任务详情。
- 下一轮要求两个旧指纹在完整 24 小时新进程窗口均为 0；无真实负载不能据此关闭。

## 2026-08-14 单任务路径复发

P0 查询形态门禁在固定窗口检出 `services/report_audio_finalization.py:1193:latest_finalization_for_audio` 的 `multirow_payload` 指纹 `af15f9b3d2324c80` 共 365 次，P95 `264.20ms`、最大 `435.78ms`；另有 7 次慢样本，最大 `4.327s`。上一轮只把列表改为批量窄投影，单任务 `finalization_summary_for_audio` 仍委托 `latest_finalization_for_audio`，继续读取完整 `params/result`，所以原记录重新打开。

本轮先用真实 SQLite 和 SQLAlchemy statement capture 新增契约测试，旧代码因 SELECT 包含完整 `publication_tasks.params/result` 得到预期 RED；随后把单任务 helper 统一委托给有界批量投影 loader。新旧两条 finalization 投影测试通过，字段语义和原始 JSON 存储均未改变。安全重启后 backend PID `5599` 已加载；真实任务 `38414dee-...` 的列表与 status API 分别为 `HTTP 200 / 0.477s`、`HTTP 200 / 0.677s`，finalization 查询 `29.14ms` 且未再产生 `multirow_payload`/完整 `params,result` 告警。仍需下一完整窗口确认旧指纹为 0，因此保持 OBSERVING。

另有 `report_audio_parent_wakeup.py:83` 10 次 multirow payload 仍保持 OPEN：该 CAS/回滚路径当前需要精确 JSON 状态，不能在没有原子 JSON patch 与回滚测试时机械删字段；下一轮必须继续定位并设计 PostgreSQL JSONB 原子更新契约。

## 2026-08-15 下一窗口复核

固定窗口内旧单任务指纹 `af15f9b3d2324c80` 为 0，属于一整个后续窗口的正向证据；
但本轮没有真实 publisher child completion 足以验收 `report_audio_parent_wakeup.py:83` 的
CAS/回滚路径，后者仍 OPEN。旧 finalization 指纹继续保持 OBSERVING，不把单一窗口零样本
扩大解释为所有父子收尾路径均已闭环；下一轮要求真实 child completion、字段完整且 parent
wakeup 不再读取完整 JSON。
