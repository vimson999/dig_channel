# 日巡检数据库快照把本地时间误作 UTC

- 发现日期：2026-10-17
- 严重级别：P0（验收完整性）
- 状态：CODE_VERIFIED / PRODUCTION_REPLAY_ACCEPTED
- 复发键：`REGRESSION-ACCEPTANCE-INTEGRITY:daily-db-snapshot-local-boundary-as-utc`

## 现象与影响

`daily_db_runtime_snapshot.py` 接受每日巡检的 Asia/Shanghai 本地边界，却把无时区的字符串直接传给使用
UTC-naive 时间戳的任务表。固定窗 `2026-10-16 06:00:00..2026-10-17 06:00:00` 因而在数据库中整体后移
8 小时，可能漏掉清晨任务、混入下一窗口数据，并造成任务失败率、old-root 和运行态验收假绿。

## 根因

脚本使用 `datetime.fromisoformat()` 后没有附加巡检时区，也没有转换到模型约定的 UTC-naive 边界；输出又不显示
实际数据库边界，使错误无法从普通日志察觉。这是巡检器的时间语义错误，不是数据库数据或索引问题。

## 修复

- 新增 `utc_naive_database_boundary()`：无时区输入按显式 `--timezone`（默认 `Asia/Shanghai`）解释，再转换为
  UTC-naive；带时区输入也统一转换。
- 支持 `DAILY_DB_AUDIT_TIMEZONE`，并在查询前打印 `database_window_utc`，让报告可以审计实际边界。
- 未修改任务数据、生产表结构或业务服务；不需要重启 backend/Celery/scheduler。

## 验证

测试先断言本地 `06:00..06:00` 必须转换成前一日 `22:00..22:00` UTC，修复前因转换函数不存在而失败，
修复后定向 3 项通过。随后对生产只读快照真实回放，输出：

`database_window_utc=2026-10-15 22:00:00..2026-10-16 22:00:00`

纠正后识别到 Publisher 19 条、其中 3 条失败（15.8%），避免把原错误窗口当作运行态依据。

## 防复发与后续验收

所有按本地自然日查询 UTC-naive 模型的诊断脚本必须显式完成时区转换并打印数据库边界；跨午夜合同不得删除。
本项代码与生产只读回放已经接受，但后续巡检仍要核对报告窗口、日志窗口和数据库窗口一致，不能以测试通过代替
真实任务覆盖。
