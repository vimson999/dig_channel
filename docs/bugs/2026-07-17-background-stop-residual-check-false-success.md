# 2026-07-17 后台停止残留检查失败却报告已清理

## 基本信息

- 状态：已修复并完成真实重启验收
- 严重级别：高
- 影响模块：开发栈重启 / Celery / watchdog / scheduler
- 根因分类：运行时拓扑、启动验收、失败开放
- 复发键：`restart-residual-check-failed-open`
- 防线层级：restart_guard / contract_test / observability

## 现象

完整重启时，`stop-all-background-jobs.sh` 的最终 `awk` 残留检查在 macOS 上发生语法错误，但脚本随后仍输出“已停干净”，使检查失败被误判为没有残留进程。

## 影响

如果当时确有旧 worker 或 scheduler 残留，重启流程仍会继续启动新实例，可能造成同一队列多版本代码并存、重复消费、调度器重复运行，以及“代码已重启但旧 SQL 仍出现”的假象。

## 根因

1. 最终 `awk` 条件使用了 macOS `awk` 无法解析的跨行布尔括号形式。
2. 脚本没有把该命令的失败状态作为验收失败处理，空输出被当成“无残留”。

## 实际方案

- 将残留匹配条件改为兼容 macOS `awk` 的单行表达式。
- 使用 `if ! remaining="$(...)"` 显式检查命令状态；检查失败时立即退出，禁止报告环境已清理。
- 增加静态契约测试，锁定 fail-closed 行为和兼容表达式。

## 验证

- `bash -n scripts/dev/stop-all-background-jobs.sh` 通过。
- 修复后的 `awk` 表达式在本机解析执行成功。
- 完整重启后，后端 PID `23778` 独占 5001；16 个 Celery 服务、watchdog 和配置要求的两个 scheduler 全部存在，bootstrap `errors` 为空。

## 复发预防

1. 停止、迁移、清理和拓扑验收脚本必须 fail closed；检查器自身失败不得等价于检查结果为空。
2. 重启成功必须同时满足新监听 PID、服务注册表完整性和残留进程检查，不能只依赖脚本退出码或绿色文本。
3. Shell 中用于安全判定的 `awk`、`grep`、`ps` 管道必须在目标操作系统上单独验证，并由契约测试锁定失败处理。
