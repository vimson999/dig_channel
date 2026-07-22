# ZSXQ 调度器错误根目录导致重复实例

## 基本信息

- 状态：已修复并完成两次 backend bootstrap 唯一实例验收
- 严重级别：P1
- 影响模块：ZSXQ 自动调度器、后端 bootstrap、Celery monitor 一键重启
- 首次发现：2026-07-19
- 根因分类：同一服务使用了不同的项目根目录和 PID 文件
- 复发键：`RUNTIME-TOPOLOGY-RELOAD`
- 是否复发：同类问题复发
- 防线层级：shared_guardrail / contract_test / observability

## 现象

完整重启启动了调度器进程 `90156`，后端稍后又启动进程 `9738`。两个实例可能重复触发同一轮业务。
旧实例把 PID 写到项目上级目录：

```text
/Users/v9/Documents/3---work/06---dev---python/runtime/zsxq_auto_scheduler.pid
```

后端 bootstrap 检查的却是项目内路径：

```text
dig_ytb_channel/runtime/zsxq_auto_scheduler.pid
```

## 根因

`run_zsxq_auto_scheduler.py` 位于 `src/backend`，却按更深层模块的父目录层级推导项目根，导致默认根
多上移两级。shell/UI 启动入口也没有显式传 `ZSXQ_PROJECT_ROOT`，因此同一服务形成两套 PID 真相。

## 修复

- Python 默认项目根固定为仓库根 `BACKEND_DIR.parents[1]`。
- shell 启动入口显式导出 `ZSXQ_PROJECT_ROOT="$PROJECT_ROOT"`。
- Celery monitor 启动所有 scheduler 时显式传相同根目录。
- 维护性调度器重载支持跳过启动即触发，避免为了加载代码而制造新业务轮次。
- 启动器把 stdout 重定向到调度器日志时显式传递标记，Python 不再同时用 StreamHandler 重复写同一文件。
- 回归测试同时锁定 Python 根目录推导、shell 环境变量和监控 API 子进程环境。

## 验收标准

- 后端重启后 ZSXQ 自动调度器进程数必须等于 1。
- 项目内 PID 文件的 PID 必须与唯一运行进程一致。
- 项目上级旧 PID 文件只能被识别为历史残留，不能再被新进程更新。
- 维护重载不得创建新的同步轮次。

## 验证

- 首次维护重载后仅有调度器 PID `29643`；日志去重维护重载后仅有 PID `97315`，项目内 PID 文件一致，均未创建第 118 轮。
- 两次 backend-only 重启的 bootstrap 均返回 `zsxq_auto already_running`，没有生成第二实例。
- 最终后端 PID `83292` 独占监听 5001，`/health` 返回 `healthy`。
- 上级目录指向死亡进程 `90156` 的错误 PID 文件已清理。
- 新日志从 2026-07-20 00:04:12 起不再成对重复；`rounds_completed` 保持 117，`last_trigger_at` 未变化。
- 根目录、shell 入口、监控 API 环境变量和 stdout 去重均有契约测试；最终完整回归 `602 passed, 5 skipped`。

## 复发预防

- 每个托管服务只能有一个 canonical runtime 根目录和 PID 文件。
- 所有 shell、API、bootstrap 入口必须显式传递同一项目根，不能各自猜测。
- “启动成功”必须同时验证进程唯一性、PID 文件一致性和业务无意外触发。
