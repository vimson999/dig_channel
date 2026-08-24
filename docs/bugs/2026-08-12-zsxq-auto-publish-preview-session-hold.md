# ZSXQ 自动发布预览跨远程上传持有数据库 Session

## 状态

`CLOSED`（2026-08-13）。代码、定向回归、完整重启、真实自动发布和下一完整窗口不复发证据均已完成；该单一 helper 根因闭环，不代表全局 PgBouncer/Tailscale 问题闭环。

## 现象与影响

2026-08-11 09:00 的内容流水线自动发布进程在生成发布预览后执行七牛上传和重试时，数据库连接分别被持有 `41.059s` 和 `88.557s`。对应 SQL 仅为 `104.94ms` 和 `115.39ms`，非 SQL 持有达到 `40.954s` 和 `88.441s`；同一时间段出现 PgBouncer query timeout。

该问题占用内容流水线进程的数据库连接，在外部存储抖动时会把远程 I/O 时长直接放大为连接池压力，并可能影响其它页面、任务和调度器。

## 根因

`preview_payload()` 为展示可用发布账号数调用 `_db_publish_cookie_account_count()`。该 helper 返回的是纯整数，但查询结束后没有归还 Flask-SQLAlchemy scoped Session。随后同一调用链进入七牛上传/重试，导致只读 checkout 跨外部 I/O 保留。

同模块的账号 DTO helper `_db_publish_cookie_accounts()` 已有 `db.session.remove()`，只有 count helper 漏掉了相同边界。这是 `DB-SESSION-LIFECYCLE` 复发簇、以及 2026-07-18 “ZSXQ 发帖凭据会话跨远程等待”问题的同类新入口。

## 修复

- `_db_publish_cookie_account_count()` 在 `finally` 中归还 Session；成功、空结果和异常路径均覆盖。
- 只让整数穿过预览到远程上传边界，不删除字段、不改变凭据选择、发布或审计语义。
- 增加真实 SQLite 模型测试，验证查询返回正确计数且在 helper 返回前恰好调用一次 `db.session.remove()`。

## 证据链

1. 修复前：`logs/scheduler.log` 中 checkout origin 为 `_db_publish_cookie_account_count`，两次持有 `41.059s/88.557s`，非 SQL 占 `99.7%/99.9%`，并与七牛上传重试、PgBouncer timeout 时间重叠。
2. 定向 TDD：新增测试在旧实现上失败（期望 remove 1 次，实际 0 次），修复后通过。
3. 相关回归：`tests.unit.test_zsxq_publish_state_updates` 与 `tests.unit.test_zsxq_auto_publish_service` 共 11 项通过。
4. 运行替换：重启安全门确认无不可中断任务后，完整 dev stack 重启；backend health 返回 `healthy`，worker/调度器重新加载。
5. 未完成：尚无修复后真实自动发布批次，不能以测试、重启或未捕获日志代替运行态不复发证据。

## 防复发契约

- 凭据/账号查询若只向外部 I/O 传递 scalar/DTO，helper 必须在返回前显式结束只读 Session。
- 新增任何 preview/status/count helper 时，必须有 release-before-I/O 顺序测试，不能依赖请求 teardown 或任务结束回收。
- 每日巡检按 checkout origin 与七牛/发布时间线优先复核，不把“本窗口没捕获”解释为“没有发生”。

## 下一轮验收

- 捕获至少一条真实 ZSXQ 自动发布任务，并确认 count 查询之后、七牛上传之前已 check-in。
- 同 origin 长持有、idle in transaction、server closed 和 PgBouncer query timeout 均为 0。
- 发布结果、账号数和原始审计字段完整；如远端上传失败，数据库连接仍必须在失败/重试期间保持释放。

## 2026-08-13 下一窗口闭环证据

- 固定窗口 `2026-08-12 06:02:19` 至 `2026-08-13 06:02:19` 捕获 29 个真实 `run_rule_start`（PDF 20、audio 9）、26 个发布流水线完成和 384 条七牛上传事件。
- `_db_publish_cookie_account_count()` 仍有 3 次真实慢查询（约 `1.035–1.860s`），但 checkout 长持有中不再出现该 origin，七牛上传/重试期间也没有 count-helper Session 跨界持有。
- 业务进程 checkout 最大值由上一窗口该问题造成的 `88.557s` 降至 `23.006s`，降幅 `74.0%`；`idle in transaction=0`。远端仍存在独立的 query/connect timeout，已归入隧道链路 Bug，不能反向否定本 helper 的边界闭环。
- 发布账号数、上传文件和原始审计 JSON 未删减。该记录满足“修复前证据 → 代码与契约 → 完整回归 → 新 PID → 真实任务 → 下一窗口未复发”，因此只关闭此问题指纹。
