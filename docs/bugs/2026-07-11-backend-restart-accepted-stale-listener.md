# 2026-07-11 后端重启误把旧监听进程判定为启动成功

## 基本信息

- 状态：已修复，待用户执行真实全栈重启验收
- 严重级别：高
- 影响模块：Flask backend / development restart scripts / Celery bootstrap
- 首次发现：2026-07-11 慢查询新旧 PID 复盘
- 修复提交：待提交
- 根因分类：进程识别与启动验收
- 复发键：`RUNTIME-TOPOLOGY-RELOAD`
- 是否复发：同类进程识别问题再次复发
- 关联记录：`2026-07-10-celery-stop-script-killed-controller.md`、`2026-06-29-feishu-cookie-import-stale-celery-workers.md`
- 防线层级：shared_guardrail / contract_test / observability

## 现象

用户多次执行 `restart-dev-stack.sh`，Celery worker 的 PID 已变化，但监听 `5001` 的 Flask 进程仍为 2026-07-10 启动的 PID 15354。专项慢查询日志持续显示旧 API 代码路径。

## 影响

- 已完成的查询优化没有加载到 Web 进程，页面继续批量读取 `publication_tasks.params/result`。
- 重启脚本打印成功，使部署状态被误判，导致代码修复与运行验收脱节。
- 旧 Flask 进程继续启动或管理 worker，进一步混淆新旧运行拓扑。

## 根因

1. `run-backend.sh` 用 shell 启动 `python app.py`，PID 文件记录 shell PID，而监听端口的是 Python 子进程。
2. 停止流程只短暂跟踪直接子进程；子进程收到信号后若被重新挂载到 PID 1，后续强制终止阶段可能丢失目标。
3. `restart-dev-stack.sh` 使用 `stop-backend.sh || true`，明确吞掉停止失败。
4. 启动验收只检查端口是否存在监听者，没有确认监听 PID 属于本次启动。

## 解决思路

启动器必须让 PID 文件直接指向服务进程；停止失败必须阻断重启；启动成功必须同时满足新进程存活、端口已监听、监听 PID 等于本次启动 PID。

## 实际方案

- `run-backend.sh` 改为 `exec python app.py`，shell 被实际 Flask 进程替换。
- `stop-backend.sh` 在发送信号前递归快照后代 PID，等待最多 10 秒，未退出则按已记录 PID 强制终止，避免重新挂载后丢失目标。
- `restart-dev-stack.sh` 不再忽略停止失败；启动前再次检查旧监听是否清空。
- 启动完成时校验 `5001` 监听 PID 必须等于本次后台启动 PID，否则清理失败进程并返回非零。
- 增加 `BACKEND_PID_FILE` 隔离入口，支持不接触真实服务的进程生命周期测试。

## 验证

- 三个 shell 脚本通过 `bash -n`。
- `RuntimeInitializationIdempotencyTest` 共 12 项测试通过，包含停止失败不可吞掉、新 PID 必须拥有端口和 `exec` 启动契约。
- 在临时端口 59123 启动独立 HTTP 服务后，修复版停止脚本成功终止该 PID并释放端口。
- `git diff --check` 通过。
- 按用户要求未执行真实全栈重启，当前业务任务和旧 `5001` 进程未被触碰。

## 复发预防

- 任何 restart 脚本不得以 `stop-command || true` 吞掉服务停止失败。
- 端口存在监听不等于启动成功；必须验证监听 PID 和进程启动边界。
- PID 文件必须记录实际服务进程，不能记录仅负责拉起服务的短生命周期 shell。
- 每次部署验收必须记录 Web/worker PID 与启动时间，再按该边界审计日志。

## 后续事项

- 当前任务结束后由用户执行 `./scripts/dev/restart-dev-stack.sh`。
- 重启后确认 `runtime/backend.pid`、`lsof :5001` 和 `ps` 三者 PID 一致且启动时间为本次操作时间。
- 用新 PID 的真实页面流量重新验收 `json_predicate=0`、`multirow_payload=0` 和数据库错误为 0。
