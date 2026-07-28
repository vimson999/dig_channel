# Watchdog 停止脚本误匹配管理命令自身

## 基本信息

- 状态：已修复并完成进程边界回归
- 严重级别：P1
- 影响模块：Celery Watchdog / 本地运行维护脚本
- 首次发现：2026-07-27
- 修复提交：当前工作区
- 根因分类：进程发现使用模糊命令文本匹配
- 复发键：`RUNTIME-TOPOLOGY-RELOAD`
- 是否复发：同类停止脚本误杀控制器再次复发
- 关联记录：`2026-07-10-celery-stop-script-killed-controller.md`
- 防线层级：shared_guardrail / contract_test

## 现象

重载 Watchdog 时，`stop-celery-watchdog.sh` 除真实 Watchdog PID 外，还把包含
`celery_watchdog_loop.py` 文本的当前维护命令识别为目标并发送 `SIGTERM`，导致整条
后端重载命令在停止 Watchdog 后提前退出。

## 影响

- 维护编排中途终止，后端没有按计划继续重载。
- 如果调用者没有重新核对现场，可能留下 Watchdog 停止、后端仍为旧代码的半完成状态。
- 模糊匹配扩大了停止范围，违反进程控制最小目标原则。

## 根因

脚本采用 `ps | grep celery_watchdog_loop.py | grep -v grep`。这只能排除 grep 自身，
不能排除命令行中恰好包含目标文件名的 shell、测试器或维护控制器。

## 解决思路

目标进程的稳定合同是“完整命令行以 Watchdog Python 入口文件结尾”。使用锚定的
`pgrep -f` 只匹配该入口，不再按任意子串扩大停止范围。

## 实际方案

- 启停前后的进程发现统一使用
  `pgrep -f 'scripts/dev/celery_watchdog_loop[.]py$'`。
- 逐 PID 发送信号，保留温和停止后再强制清理的原有语义。
- 增加静态契约测试，禁止恢复无锚点 grep。

## 验证

- 定向测试覆盖锚定进程匹配合同。
- 真实 Watchdog 已由后端 bootstrap 重新启动为 PID `90211`。
- 5 个 `report_pdf_gemini` Worker PID 在维护前后保持不变。

## 复发预防

- 停止脚本不得使用无锚点的 `ps | grep` 作为破坏性目标解析。
- 进程匹配必须绑定入口文件结尾、PID 文件或已验证的进程组。
- 停止后必须分别验收目标已退出和无关 Worker PID 未变化。

## 后续事项

- 后续统一审计其他 `stop-celery-*.sh` 是否仍使用同类模糊匹配。

## 2026-07-28 同类入口补强

新增 YouTube 视频 scheduler 现场验收时，发现 Celery Monitor 的通用
`_scheduler_processes()` 仍使用 `pattern in command`，会把命令正文中提到 runner
文件名的诊断 shell 短暂识别成第二个调度器进程。现已改为用 `shlex` 解析 argv，
只在某个独立参数的 basename 与 runner 文件名完全相等时匹配，并增加“真实 runner
命中、诊断 shell 不命中”的契约测试。该修复把原先只落在 stop 脚本的防线提升为
Monitor 所有 scheduler 共用的进程识别防线。
