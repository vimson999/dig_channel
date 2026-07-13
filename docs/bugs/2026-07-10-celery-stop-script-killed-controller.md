# 2026-07-10 Celery 停止脚本误杀控制进程

## 基本信息

- 状态：已修复
- 严重级别：高
- 影响模块：Celery worker restart / report audio / Doubao browser TTS
- 首次发现：2026-07-10 14:46 专项部署
- 修复提交：待提交
- 根因分类：进程识别与运行拓扑
- 复发键：`RUNTIME-TOPOLOGY-RELOAD`
- 是否复发：同类进程识别问题多次复发
- 关联记录：`2026-06-29-feishu-cookie-import-stale-celery-workers.md`、`2026-07-07-platform-upload-worker-restart-orphan.md`
- 防线层级：shared_guardrail / contract_test / observability

## 现象

执行 report audio 与 Doubao worker 的组合重启命令时，停止脚本把包含脚本名称的控制 shell PID 当成 Celery worker。控制 shell 被终止后，后续启动命令没有执行；report audio worker 已停止但未恢复。

## 影响

- 组合重启可能只执行停止阶段，造成队列暂时无人消费。
- 命令输出在控制 shell 被杀后中断，容易误判为完整重启成功。
- 本次有一条 report audio 任务在窗口内进入队列；随后通过受管启动接口恢复，新 PID 接手任务，未丢失业务数据。

## 根因

停止脚本使用 `ps aux | grep celery | grep <service>`。当外层控制命令同时包含 `stop-celery-...` 和 `run-celery-...` 时，控制 shell 的命令行也同时含有 `celery` 与服务名，满足了宽泛匹配条件。

## 解决思路

进程识别必须依据真实 Celery 可执行文件和明确的 `-Q` 队列参数，不能依赖脚本文件名或多个模糊关键词。启动恢复统一使用已有受管接口，以维护 PID 文件、日志和 watchdog 状态。

## 实际方案

- report audio 与 Doubao 停止脚本只匹配 `/venv/bin/celery` 且包含准确 `-Q <queue>` 的进程。
- 两个脚本共用相同的精确匹配原则，并保留温和停止与残留强制清理流程。
- 增加契约测试，禁止重新引入 `grep 'celery'` 宽泛匹配。
- report audio 通过 `/api/v1/celery/start/report_audio_workflow` 恢复为受管 PID；Doubao profile pool 两个 worker 重新启动成功。

## 验证

- 修正后的 Doubao 停止脚本只识别 PID 44234、44282，没有终止控制 shell或其他队列。
- 新 Doubao worker PID 84384、84444 均连接 broker 并 ready。
- report audio 受管 worker PID 87422 启动成功，队列由 1 清零，并继续处理任务 `d639ba5f-50c9-46e1-8790-7123d8afb6be`。
- 运行时契约测试覆盖两个停止脚本的可执行文件和队列参数匹配。
- 2026-07-11 全栈重启暴露通用 `stop-celery.sh` 仍使用 `ps | grep celery`，误杀控制脚本并产生 `Killed: 9`；已改为只匹配当前项目 `/venv/bin/celery` 且命令包含 `worker` 的真实进程，并纳入同一契约测试。

## 复发预防

- 所有 worker stop/restart 实现禁止使用服务脚本名做进程身份判断。
- 受管服务优先通过 Celery monitor 服务控制接口启动和重启。
- 组合重启必须验收新 PID、启动时间、ready 日志和队列消费，不能只看停止命令退出码。

## 后续事项

- 继续横向收敛其他停止脚本的宽泛进程匹配到统一进程管理 helper。
