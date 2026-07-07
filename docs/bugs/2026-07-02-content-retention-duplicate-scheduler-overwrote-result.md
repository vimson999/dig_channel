# 2026-07-02 历史文件清理重复调度器覆盖执行结果

## 基本信息

- 状态：已修复，已重启历史文件清理调度器生效
- 严重级别：中
- 影响模块：`content_pipeline` 历史文件清理调度器 / 运行态状态展示 / 邮件通知
- 首次发现：2026-07-02 用户询问“昨天和今天清理了吗”
- 修复提交：待提交

## 现象

用户检查每日历史文件清理时，运行时状态显示最后一次结果为：

```text
last_run_at=2026-07-02T02:20:04Z
candidate_count=0
deleted_human=0 B
```

但日志里实际存在两次相邻执行：

```text
2026-07-02 10:19:45 trigger_done candidates=273 deleted_size=2.6 GB errors=0
2026-07-02 10:20:04 trigger_done candidates=0 deleted_size=0 B errors=0
```

也就是说，当天清理已经成功释放空间，但后一次空跑把 `runtime/content_retention_cleanup_config.json` 中的 `last_result` 覆盖成 `0 B`，导致页面/状态看起来像“没清”。

同一时间还发现两个调度器进程：

```text
80108 ... run_content_retention_cleanup_scheduler.py start
80425 ... run_content_retention_cleanup_scheduler.py start
```

## 影响

- 用户无法从状态面板判断真实清理量，会误判计划任务没有执行。
- 多个调度器同时存在时，到点可能重复执行清理和重复发送邮件。
- 由于第二轮通常已经没有候选文件，它会把真正释放空间的结果覆盖掉。
- 重启或人工排查时，PID 文件只记录其中一个进程，另一个进程容易被漏掉。

## 根因

旧实现只依赖 PID 文件判断调度器是否运行：

1. `start_scheduler()` 先读 PID 文件，再启动子进程。
2. 并发启动或旧进程未被 PID 文件管理时，两个调用都可能认为“当前未运行”。
3. 子进程自身没有单实例锁，被绕过服务层直接 `start` 时也不会拒绝第二个进程。
4. `stop()` 会清空 `next_run_at`，旧进程收尾时可能影响新进程已经写好的计划状态。

本质问题是：调度器这种长期驻留进程不能只靠 PID 文件做互斥；PID 文件是状态记录，不是并发锁。

## 解决思路

- 服务层启动时加非阻塞启动锁，避免并发请求同时拉起进程。
- 调度器进程自身加非阻塞进程锁，防止手工命令或绕过服务层重复启动。
- 停止调度器时不再清空 `next_run_at`，避免旧进程收尾破坏下次计划展示。
- 调度器启动时优先沿用已持久化的 `next_run_at`，支持重启后继续按原计划执行。
- 对僵尸 PID 做识别，避免 PID 文件指向 defunct 进程时误判仍在运行。

## 实际方案

`src/backend/services/content_pipeline/retention_scheduler_service.py`：

- 新增 `content_retention_cleanup_scheduler_start.lock` 启动锁。
- 新增 `content_retention_cleanup_scheduler_process.lock` 进程锁。
- `start_scheduler()` 获取启动锁后才检查状态并拉起子进程。
- 新增 `acquire_scheduler_process_lock()` 和 `release_file_lock()`，供调度器入口使用。
- `stop_scheduler()` 停止成功后只清 PID 文件，不清空 `next_run_at`。
- 新增 `scheduled_next_run_datetime()`，优先使用持久化计划时间。
- `_is_pid_running()` 增加 zombie 判断。

`src/backend/run_content_retention_cleanup_scheduler.py`：

- `start` 命令启动时先获取进程锁，锁已存在则直接退出并记录 warning。
- `refresh_schedule()` 使用持久化 `next_run_at` 设置 `schedule` 的下一次执行时间。
- 执行完计划任务后，从实际 job 中回写新的 `next_run_at`。
- `stop()` 不再把 `next_run_at` 写成 `null`。

`tests/unit/test_content_retention_scheduler_service.py`：

- 增加持久化 `next_run_at` 的未来/过期场景测试。
- 增加无持久化时间时按当前时间加间隔的测试。
- 增加 zombie PID 不视为运行中的测试。
- 增加启动锁被占用时不再拉起新进程的测试。

## 现场处理

确认实际清理记录：

```text
2026-07-01 10:19:35 scheduler deleted=488 deleted_size=5.8 GB errors=0
2026-07-02 10:19:45 scheduler deleted=273 deleted_size=2.6 GB errors=0
```

清理重复调度器：

```text
kill -9 80108 80425
```

重启修复后的单实例调度器：

```text
pid=47621
next_run_at=2026-07-03T03:19:07Z
```

按用户要求手动真实执行一次清理：

```text
2026-07-02 11:19:07 manual_cli deleted=52 deleted_size=1.6 GB errors=0
```

重复启动验证：

```text
已有历史文件清理调度器进程运行，退出本次启动
```

最终进程确认：

```text
47621 ... run_content_retention_cleanup_scheduler.py start
```

## 邮件通知确认

历史文件清理的里程碑邮件默认开启，除非显式设置：

```bash
CONTENT_RETENTION_EMAIL_NOTIFY=0
```

当前代码会发送：

- `started`：清理开始
- `completed`：清理完成且无错误
- `completed_with_errors`：清理完成但有错误
- `failed`：清理异常

现场日志已确认发送成功：

```text
[RETENTION_CLEANUP] email sent | event=started
[RETENTION_CLEANUP] email sent | event=completed
```

邮件发送失败只记录日志，不阻断文件清理。

## 验证

单元测试：

```bash
PYTHONPATH=src/backend venv/bin/python -m unittest tests.unit.test_content_retention_scheduler_service
```

结果：

```text
Ran 8 tests in 0.003s
OK
```

语法检查：

```bash
venv/bin/python -m py_compile src/backend/services/content_pipeline/retention_scheduler_service.py src/backend/run_content_retention_cleanup_scheduler.py
```

结果：通过。

运行态验证：

```text
scheduler.running=true
scheduler.pid=47621
config.enabled=true
config.interval_hours=24
config.dry_run=false
config.next_run_at=2026-07-03T03:19:07Z
```

## 复发预防

- 长驻调度器必须有“服务层启动锁 + 进程自身锁”，不能只依赖 PID 文件。
- 每次判断“计划任务是否执行”，不能只看 `last_result`，还要看日志里的 `trigger_done` 记录。
- 状态里显示 `0 B` 不等于当天没清，可能是后续重复空跑覆盖了结果。
- 停止旧进程不应清空未来计划时间；停止动作和执行计划状态要解耦。
- 排查定时任务固定四件套：配置、PID 文件、实际进程数、日志里的 `trigger_start/trigger_done`。

## 后续事项

- 明天 `2026-07-03 11:19:07` 北京时间应按当前规格执行：
  - 每 24 小时执行一次。
  - `dry_run=false`，真实清理。
  - 默认保留 3 天。
  - 开始/完成/异常里程碑发送邮件。
  - 单实例调度器运行，重复启动会退出。
