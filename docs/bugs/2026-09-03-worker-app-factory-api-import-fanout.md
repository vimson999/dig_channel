# 后台任务完整注册 Web API 导致混版导入失败

## 基本信息

- 状态：RUNTIME_REPLACED / OBSERVING
- 严重级别：P0
- 影响模块：Celery 全任务 / Flask app factory / 发布、上传与 MP3 工作流
- 首次发现：2026-09-03 每日数据库性能与错误巡检
- 修复提交：当前工作区
- 根因分类：后台任务无关 Web API 注册、长驻进程混版导入、系统级防线缺失
- 复发键：`RUNTIME-TOPOLOGY-RELOAD`
- 是否复发：是；2026-09-01 仅修了通知 worker 单点，其他任务仍保留同一调用模式
- 关联记录：`2026-09-01-workflow-notification-mixed-version-import-storm.md`
- 防线层级：minimal_app_factory / full_tree_ast_gate / process_restart / runtime_observation

## 现象与影响

固定窗口 `2026-09-02 06:00:00` 至 `2026-09-03 06:00:00` 内，Celery 记录 30 次失败。
其中发布流水线 16 次、Bilibili upload-only 9 次、MP3 视频工作流 4 次都在业务处理前失败，公共指纹为
`ImportError: cannot import name 'ZsxqAnalysisItem' from 'models'`。调用链从任务中的默认 `create_app()`
进入 `register_all_apis()`，再导入与当前任务无关的 `zsxq_analysis` API。

长驻 worker 已缓存旧 `models` 包，而当天新增 API 从磁盘导入新模型符号，于是同一进程形成新旧模块图。失败使
已投递的发布、上传与 MP3 任务延迟，且把 Celery 失败率和运行日志显著放大。

## 根因

2026-09-01 的修复证明 notification worker 只需数据库上下文，却只在该单一任务改成
`create_app(register_apis=False)`。其余后台任务目录仍有 50 个默认 `create_app()` 调用；因此同一个架构缺陷会被
任意新 route/model/service 契约再次触发。缺失的是全目录自动门禁，而不是另一个 ImportError 特判。

## 实际方案

- 将 `src/backend/tasks/**/*.py` 中所有直接 app factory 调用统一改为
  `create_app(register_apis=False)`；Web 进程默认行为不变。
- 新增 AST 门禁 `scripts/dev/check-worker-app-factory-boundary.py`，扫描整个任务树并拒绝遗漏显式最小模式的
  直接 `create_app` 调用。
- 新增门禁自测：坏样本必须失败、正确样本必须通过、真实任务树必须整体通过。
- 更新旧的 report-video 测试替身，使其同时验证 `register_apis=False` 调用合同。

## 验证

2026-09-04后窗复核：发布9次、upload-only8次、MP3 delivery50次自然Celery成功，目标导入失败0。
数据库当前窗口新建任务中publisher16项、MP3父4项均completed/done；消息成功数与业务实体数口径不同。
09-03 09:00再次正常运行替换后的PID7759/8106已有真实完成样本。获得自然任务正向证据，仍保留
OBSERVING，下一完整窗口继续查新增route/model后的导入失败，不以一次成功关闭整个拓扑簇。

- TDD 红灯阶段，真实任务树门禁列出 50 个违规调用；修复后扫描 27 个 Python 文件通过。
- 目标交叉套件 40/40 通过；修复测试替身后获准环境完整 unit 为 1589/1589 通过。
- `register_apis=False` 只跳过 API 注册，不改变数据库、Celery 和模型 extensions 初始化。
- 重启安全门两次均读取 47 条近期任务、0 个活动同步并确认无不可中断任务；标准脚本冻结 17 个队列、关闭 API、
  二次检查后完成整栈重启，全程未使用 FORCE。新 backend PID 21618，health 为 healthy。
- runtime-only 状态确认 15/15 项核心 Celery service 均 running、queue=0；多实例池也全部重建，关键 worker 在
  06:24:21–06:24:45 之间 ready，切点后没有 `ZsxqAnalysisItem` ImportError。
- 当前只证明代码已运行替换；尚无切点后的自然发布/上传/MP3 业务任务，因此保持 OBSERVING，不标 CLOSED。

## 复发预防与验收

- 任何后台任务仅为 app context/数据库初始化创建应用时，都必须显式关闭 API 注册；完整 Web route 图只属于
  Web 进程。
- 每次完整回归、重启前和 CI 都运行 AST 门禁；人工 `rg` 不是可自动失败的替代品。
- route/model/service 契约变更后必须经安全门重启所有受影响长驻进程；磁盘 import 成功不能代表旧 worker 已加载。
- 运行替换后记录 backend/worker PID、ready 时间、切点后 ImportError 指纹与自然任务结果；下一完整窗口为 0
  才能进一步收口。
