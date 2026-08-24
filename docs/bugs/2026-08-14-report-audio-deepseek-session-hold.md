# 研报音频 DeepSeek 结构化跨外部调用持有数据库 Session

## 基本信息

- 状态：`RUNTIME_REPLACED/OPEN`，新 worker 已加载；待真实任务与下一完整窗口验收
- 严重级别：P0
- 影响模块：report audio workflow / DeepSeek normalizer / PostgreSQL / PgBouncer
- 首次发现：2026-08-14 日巡检
- 修复提交：当前工作区
- 根因分类：外部 I/O 与数据库 Session 交叉持有
- 复发键：`DB-SESSION-LIFECYCLE`
- 是否复发：是；同簇 Session 跨 broker、浏览器、下载和远程上传之后再次出现
- 关联记录：`2026-07-24-db-session-crossed-broker-and-remote-io.md`、`2026-08-12-zsxq-auto-publish-preview-session-hold.md`
- 防线层级：local_patch / contract_test / observability / runtime_acceptance

## 现象

固定窗口内 3 个 report-audio worker 连接持有分别为 `122.171s`、`110.381s`、`83.161s`，其中 SQL 仅 `6.249s`、`8.288s`、`1.263s`，非 SQL 为 `115.922s`、`102.093s`、`81.898s`。checkout origin 均为 `tasks/report_audio_workflow.py:1007:_process_job`，任务为 `e28724cb-...`、`0a6e0be0-...`、`829bcdb2-...`。

## 影响

- 三个连接累计被非数据库工作占用约 300 秒，压缩 PgBouncer 可用连接并放大已有网络 timeout。
- 该问题没有表现为 PostgreSQL executor 慢；如果只按慢 SQL 或加索引会漏报根因。
- 受影响任务处于研报音频 Gemini 完成后的 DeepSeek 结构化阶段，页面轮询会同步感受到连接池长尾。

## 根因

`_process_job` 先用 `_load_task(..., *TASK_CHILD_COLUMNS)` 读取 Gemini 子任务结果并开启只读事务，随后 `_structure_gemini_response` 直接调用远程 `normalize_for_tts`。DeepSeek 网络等待期间 Session 没有显式归还，直到后续更新任务状态才释放。PostgreSQL 现场无 waiting lock、无 idle-in-transaction；代表性 EXPLAIN 为毫秒级，因此根因是应用事务边界而不是 executor、索引或锁。

## 解决思路

在任何 DeepSeek 兼容/回退路径开始前显式归还只读 Session；只把已物化的字符串、整数和 metadata 传给远程调用。保留全部原始 JSON 审计数据，不改变模型输出语义，不以提高 timeout 或重试掩盖问题。

## 实际方案

在 `_structure_gemini_response` 调用 `normalize_for_tts` 前执行 `release_db_session_quietly()`。Gem V2 直接路径不需要外部 DeepSeek，因此不额外建立/归还事务。

## 验证

- 先新增真实 SQLite 事务测试，旧实现观测到 DeepSeek side effect 执行时 Session 仍在事务内，得到预期 RED。
- 修改后同一测试观测到 Session 已归还；兼容路径、shadow、direct-valid/invalid 4 条定向测试通过。
- report audio workflow/finalization/database lifecycle 相关模块共 80 项测试通过。
- 重启安全门禁两次确认无不可中断任务；完整开发栈重启后 backend PID `5599` 健康，report-audio worker PID `5875` 于 06:19:04 ready。
- 未完成：没有新真实任务走到 DeepSeek 结构化阶段，故仍保持 OPEN。

## 复发预防

- 外部 AI、浏览器、broker、磁盘扫描与 sleep 前必须结束只读事务；调用参数只能是标量或不可变快照。
- Session 生命周期测试必须在 side effect 内观察真实事务状态，不能只断言 mock 调用顺序。
- 每日巡检继续单列“连接持有很久但没有匹配慢 SQL”，并按 checkout origin 追到外部调用。

## 后续事项

- 先运行重启安全门禁；只有无不可中断任务时才替换 backend/worker/scheduler。
- 新 worker 上用真实 report-audio 任务验收 DeepSeek 阶段，要求相同 origin 的非 SQL hold 不超过 5 秒。
- 下一完整 24 小时要求该调用链无超过 5 秒的 Session hold；一次测试或一次任务成功不能关闭记录。

## 2026-08-15 运行窗口复核

固定窗口未再捕获 `_process_job` DeepSeek origin 的 80–122 秒 Session hold，但也没有新的
真实 report-audio 任务走到 DeepSeek 结构化阶段。当前 backend/worker 健康不能替代目标路径
证据；因此不把“未捕获”写成“不复发”，状态保持 `RUNTIME_REPLACED/OPEN`。下一轮仍要求
至少一条真实任务完成 DeepSeek 阶段、相同 origin 非 SQL hold <5s，并在后续完整窗口无复发。
