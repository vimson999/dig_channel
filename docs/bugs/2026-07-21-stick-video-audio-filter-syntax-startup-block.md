# 火柴人视频音频滤镜拼接语法阻断后端启动

## 基本信息

- 状态：已修复、编译与相关测试通过，新后端启动成功
- 严重级别：高
- 影响模块：火柴人视频 / Flask 启动 / ffmpeg 音频变速
- 首次发现：2026-07-21 后端跨进程验收
- 修复提交：待提交
- 根因分类：动态字符串缺少显式拼接、回归后文件继续变化
- 复发键：`REGRESSION-ACCEPTANCE-INTEGRITY` / `RUNTIME-TOPOLOGY-RELOAD`
- 是否复发：是；属于“测试通过但待部署文件随后变化”验收失效簇
- 关联记录：`2026-07-18-regression-suite-false-green-and-live-side-effects.md`、`2026-07-11-backend-restart-accepted-stale-listener.md`
- 防线层级：contract_test / compile_gate / runtime_acceptance / observability

## 现象与影响

后端 PID `67745` 启动时在 `services/report_stick_figure_video.py:299` 抛出 `SyntaxError`，端口 5001 没有监听。Celery 与 scheduler 未被停止，但所有后端 API 暂时不可用。

## 根因

音频滤镜从固定字符串改为按 `speed` 动态拼接后，代码写成变量 `audio_filters` 紧邻 f-string。Python 只允许字符串字面量之间隐式拼接，变量与字符串之间必须使用 `+`。该未跟踪文件在完整回归结束后继续发生变化，因此先前的编译通过不能代表最终待启动内容。

## 实际方案

- 将滤镜表达式改为 `audio_filters + f"apad=..."`，保留原有 atempo、补齐和裁剪语义。
- 后端失败后没有强行接受 PID 文件；先修复、编译和跑相关测试，再创建新的受管进程。
- 新后端 PID `94639` 与 `runtime/backend.pid`、5001 监听 PID 一致。

## 验证

- `py_compile` 覆盖火柴人视频、视觉方案和 Redis 客户端。
- 火柴人 render、媒体缓存和视觉缓存相关测试 `8 passed`。
- 新后端成功监听，bootstrap 识别 17 个 worker、watchdog 与两个 scheduler 均已运行且无错误。

## 复发预防

1. 任何回归完成后的源文件变化都会使该次回归失效，最终启动前必须重新执行编译与全量门禁。
2. ffmpeg 动态滤镜组装必须有非 1.0 倍速测试，覆盖条件前缀和后续固定滤镜的完整拼接。
3. 启动验收必须同时验证进程存活、监听 PID 和 bootstrap 结果，不能只相信已写入的 PID 文件。
