# Database audit fixes implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 09-02 至 09-04 已定位的数据库应用层问题形成可测试修复，独立提交并推送远端。

**Architecture:** 保留任务与审计数据，缩小查询投影、明确文件 I/O/数据库事务边界，并维持 CAS 与去重唯一约束。日志按完整事件解析，用精确时间和事件上下文区分副本与独立故障。网络排队未定位的底层原因单独保留，不用应用测试冒充生产根因闭环。

**Tech Stack:** Python、Flask、SQLAlchemy、Celery、PostgreSQL/PgBouncer；unittest + 隔离 SQLite 回归。

**Spec:** `docs/bugs/reviews/2026-09-04-db-performance-audit.md`；`docs/rules/database-session-lifecycle-discipline.md`。

## Global Constraints

- 不执行 DDL、生产数据回填或生产网络/连接池配置修改。
- 不删除原始审计 JSON；不将无自然样本或一次测试成功视为运行态完全闭环。
- 保留其他功能的未提交改动；仅按明确文件清单提交本次修复。
- 测试固定 `CELERY_BROKER_URL=memory:// CELERY_RESULT_BACKEND=cache+memory://`，不投递真实业务。
- 当前修复分支：`codex/db-audit-fixes-20260904`；不强推，不创建 PR，不重写已有提交。

---

## Task 1: 父任务唤醒查询与失败恢复

**Files:**
- Modify: `src/backend/services/report_audio_parent_wakeup.py`
- Test: `tests/unit/test_report_audio_parent_wakeup.py`
- Document: `docs/bugs/2026-08-10-report-audio-finalization-json-polling-n-plus-one.md`

**Interfaces:** `wake_report_audio_parent_for_child(child_task_id: str) -> dict` 保持原返回契约；失败仍抛出 broker 异常。

- [x] 保留窄候选查询与主键、版本校验、行锁；先确认已有 SQL 测试通过。
- [x] 增加失败回归：在 broker 回调中用另一 Session 追加审计键并提交，然后抛出异常；恢复后必须保留该键。

```python
assert saved.result["concurrent_audit"] == {"delivered": True}
assert saved.celery_task_id == "old-delayed-delivery"
```

- [x] 增加终态/推进后的失败恢复回归：broker 等待中父任务被取消或推进，不得改回 processing/pub_waiting。
- [x] 运行 `venv/bin/python -m unittest tests.unit.test_report_audio_parent_wakeup`，确认新增回归在现状失败。
- [x] 失败分支按主键及 claim 字段锁定最新快照，恢复仅由本次 claim 修改的字段；合并 waiting_child 键并保留最新其他审计字段。终态或已推进的父任务不恢复；准入守卫自身写入的三个 deferred 步骤仍可恢复旧延迟投递。

```python
RECOVERABLE_CLAIM_STEPS = (
    WAKE_STEP, "deferred_db_pressure", "deferred_backlog", "deferred_capacity",
)
restore_query = PublicationTask.query.filter(
    PublicationTask.id == parent_id,
    PublicationTask.status.in_(ACTIVE_PARENT_STATUSES),
    PublicationTask.current_step.in_(RECOVERABLE_CLAIM_STEPS),
    PublicationTask.celery_task_id == broker_task_id,
    PublicationTask.waiting_child_updated_at == wake_token,
)
```

- [x] 重跑本模块；检查无匹配/重复零 JSON、有效主键读取、broker 前无事务、并发审计和失败恢复。

## Task 2: 内容准入批量去重与文件事务边界

**Files:**
- Modify: `src/backend/services/content_pipeline/dedupe_service.py`
- Modify: `src/backend/services/content_pipeline/audio_clean_task_service.py`
- Modify: `src/backend/services/content_pipeline/pdf_clean_task_service.py`
- Modify: `src/backend/apis/v1/content_pipeline.py`
- Test: `tests/unit/test_content_pipeline_dedupe.py`
- Test: `tests/unit/test_content_pipeline_submission_query_discipline.py`
- Test: `tests/unit/test_content_pipeline_web_submit_api.py`
- Document: 09-02 去重、09-03 哈希 Bug 记录

**Interfaces:** `fingerprint_files(files) -> list[dict]` 在文件阶段执行；`reserve_fingerprints(task_id=..., media_type=..., fingerprints=..., source_mode=...) -> dict` 只做数据库阶段，返回格式不变。

- [x] 新增测试，先执行真实 SELECT 打开上游事务，再调用 PDF/audio submit；在真实 fingerprint_file 入口断言无事务。
- [x] 新增待提交写入回归，确保不会为哈希默默提交或丢弃上游未提交改动。

```python
db.session.query(PublicationTask.id).all()
assert db.session().in_transaction()
# fingerprint_file 的包裹回调：assert not db.session().in_transaction()
```

- [x] 新增网页上传回归：真实 DB 认证读取之后，FileStorage.save 入口无事务，暂存文件内容与提交参数不变。
- [x] 红灯后复用 `end_db_transaction_before_external_io` 在服务指纹入口和网页 multipart/暂存入口释放只读事务；拒绝未提交 ORM 写入。

```python
end_db_transaction_before_external_io("content pipeline file fingerprint")
fingerprints = fingerprint_files(normalized_files)
```

- [x] 保留每批一次 canonical SELECT + 一次 INSERT 的正常预算及 savepoint 竞争回退；为锁定/插入排序使重叠批次使用稳定哈希顺序，返回结果仍按输入顺序。
- [x] 回归正常批量、批内重复、已存在 canonical、可回收记录以及真实唯一冲突后的 savepoint 恢复；运行三个测试模块全绿。

## Task 3: 完整日志事件、独立故障与来源

**Files:**
- Modify: `scripts/diagnostics/analyze_db_performance_logs.py`
- Test: `tests/unit/test_db_performance_log_analyzer.py`
- Document: `docs/bugs/2026-08-18-managed-sql-error-source-misclassified.md`

**Interfaces:** `event_records(lines)` 仍产生 `(datetime, text)`；traceback 与结构化 SQL 错误保持独立流，不相加。

- [x] 新增红灯：同秒不同毫秒/不同 request 或 pid 的两次故障保留两次；两份相同日志只算一次；同文件重复事件不被全局 set 吞掉。
- [x] 新增窗口、毫秒精度、无时间戳前缀、测试来源和独立诊断来源回放，禁止生产统计混入测试。

```python
assert records[0][0] == datetime(2026, 9, 3, 8, 54, 10, 721000)
assert "sql_error_traceback_episodes count=2" in output
```

- [x] 保留毫秒时间、明确事件身份，跨文件副本去重保留同源多次发生；识别测试/诊断上下文，按来源分列，不输出 SQL 参数值。
- [x] 重跑日志模块与固定历史窗口；对账原始 timeout/traceback，不调整生产配置。

## Task 4: Worker 最小工厂与交付门禁

**Files:**
- Modify: `src/backend/tasks/*.py` 中本次已有的 `create_app(register_apis=False)` 修改（精确清单以提交 diff 为准）
- Create: `scripts/dev/check-worker-app-factory-boundary.py`
- Test: `tests/unit/test_worker_app_factory_boundary.py`
- Test: `tests/unit/test_report_video_parse_state_updates.py`
- Document: 09-03 worker 工厂 Bug、09-02/03/04 巡检、Bug registry 与验证记录

- [x] 确认 worker 不依赖 Web API 注册；不收录 ZSXQ analysis 新功能、上传提示音、PDF 前端或 publisher queue repair 的其他改动。
- [x] 用 requesting-code-review 只读复核，处理 Important/Critical 后复核最终 diff。
- [x] 全量执行（需要 localhost bind 的测试用获准权限运行）：

```bash
YOUTUBE_API_KEY=unit-test-placeholder GOOGLE_API_KEYS=unit-test-placeholder CELERY_BROKER_URL=memory:// CELERY_RESULT_BACKEND=cache+memory:// PYTHONPATH=src/backend:. venv/bin/python -m unittest discover -s tests/unit -p 'test_*.py'
venv/bin/python scripts/dev/check-worker-app-factory-boundary.py
venv/bin/python scripts/dev/check-celery-revoke-boundary.py
venv/bin/python scripts/dev/review_bug_records.py --strict
venv/bin/python -m compileall -q src/backend scripts/diagnostics
git diff --check
```

- [x] 显式暂存文件；从暂存树导出临时目录，用相同运行时复跑全部 unit，确认不依赖被排除的脏改动。
- [x] 核对提交清单及敏感信息，只提交代码、测试、计划和必要 Bug 文档，不上传 runtime/logs/outputs/原始业务 artifacts。
- [ ] `git commit` 后执行非强制 `git push -u origin codex/db-audit-fixes-20260904`；`git ls-remote` 对账 SHA。
- [ ] 交付提交号、分支、测试数量和保留风险；网络问题与运行态新自然事件仍列为待验收，不虚报关闭。

## 验收记录

- Task 1–3 已完成：实际 SQL/独立 Session 与日志回放红绿验证。第二次审查补出准入延后后 broker 失败无法恢复，三种准入原因均先红后绿；父任务模块现 11 项通过。
- 首轮当前工作区全量 1606 项通过（90.539s），随后增加上述准入回归；最终以干净暂存树复跑为准。
- 固定历史窗回放：慢 SQL 29 / 39.148s、HTTP 672、Celery 成功 205、连接长持有 1、断连 1、traceback 1；traceback 同时保留两个日志来源。
- worker 工厂 27 文件、revoke 314 文件、Bug 195/195、compileall 通过。暂存检查发现旧 09-03 报告尾部空行，已清理。
- 原始业务 artifacts、日志、outputs、配置以及其他未提交功能不进入提交；本次新补强未重启线上进程，运行态观察仍待安全部署后验收。
- 干净快照未复制本地 `.env`，首次发现 11 个旧 Web 模块导入错误；加入 YouTube 和 Google 的无效测试占位配置后，正常发现 1587 项测试、导入错误为 0。未读取或复制真实密钥，也未为此修改生产代码。
- 暂存树与干净快照中的 917 个 src/scripts/tests 文件逐个 Git blob 哈希核对一致，mismatches=0；避免测试混入原工作区其他功能。
- 最终干净暂存树全量 **1587 tests / 89.966s / OK**；包括最后准入延后失败恢复回归。相比原工作区数量较少，是因为主动排除了其他未提交功能及其测试。
- 最终只读审查：无 Critical/Important，72 项定向通过。干净树 worker gate 27 文件、revoke gate 310 文件、Bug strict 195/195 和编译检查通过。
- 上述实现与验证完成后执行提交/推送；提交 SHA 和远端 ref 对账以 Git 记录及本次任务交付消息为准。
