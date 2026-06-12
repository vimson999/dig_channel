# 2026-06-02 工作归档

## 知识星球自动同步测试

- 目标：在 `zsxq-lab.html` 上测试外资研报与调研纪要 MP3 的自动同步效果。
- 测试约定：
  - 自动测试只跑 2 轮。
  - 第 1 轮立即提交，第 2 轮在 5 分钟后提交。
  - 每条内容最多下载 2 个附件。
  - `外资研报` 只下载 PDF。
  - `调研纪要` 只下载 MP3。
- 后端修改：
  - `/api/v1/zsxq/sync` 支持 `max_download_files` 参数。
  - `tasks.zsxq_sync.run_zsxq_sync` 将 `max_download_files` 传给同步服务。
  - `ZsxqSyncService.sync_tabs()` 增加单条内容的附件下载上限，达到上限后只停止当前内容的附件下载，但不会把剩余附件标记为 skipped。
  - 同步结果增加 `max_download_files` 与 `download_limit_reached`，tab 结果增加 `stopped_by_download_limit`。
- 前端修改：
  - `zsxq-lab.html` 新增 `自动测试 2 轮` 按钮。
  - 自动测试会查找官方 Tab 中的 `外资研报` 与 `调研纪要`。
  - 每轮分别提交两个异步同步任务：外资研报 PDF、调研纪要 MP3。
  - 运行中按钮变为 `停止自动测试`，可手动终止第二轮定时提交。
  - 同步日志中会提示 `有内容达到单条附件上限`。
- 验证：
  - `venv/bin/python -m py_compile src/backend/apis/v1/zsxq.py src/backend/tasks/zsxq_sync.py src/backend/services/zsxq_sync_service.py`
  - `node --check src/frontend/js/zsxq-lab.js`
- 生效要求：
  - 重启后端。
  - 启动知识星球专用 worker：`./scripts/dev/run-celery-zsxq.sh`。

## 自动同步测试 mode 字段过长修复

- 现象：点击 `自动测试 2 轮` 后，同步日志报错 `value too long for type character varying(20)`。
- 原因：`zsxq_sync_runs.mode` 字段长度为 20，前端传入的 `auto_test_foreign_report_round_1` 超长。
- 修复：
  - 前端自动测试 mode 改为短码：`auto_fr_1`、`auto_rm_1`。
  - 后端 `ZsxqSyncService` 增加 `_normalize_mode()`，所有同步 run 写库前统一裁剪到 20 字符，避免调用方再次传入超长 mode。
- 生效要求：重启后端后重新点击 `自动测试 2 轮`。

## 播客视觉化任务重新投递入口修正

- 现象：`podcast-visualizer.html` 任务卡在 `processing 18% / lyrics`，页面操作列仍显示 `等待`，无法自助重新投递。
- 结论：
  - 后端已有 `POST /api/v1/documents/podcast-visualizer/requeue/<task_id>`。
  - 前端 JS 已有非完成任务的重新投递逻辑，但 HTML 仍引用旧版本 `podcast-visualizer.js?v=20260520-004`，浏览器可能继续使用缓存，导致页面没露出按钮。
- 修复：
  - 操作按钮文案改为 `重新投递`，颜色改为蓝色，更容易识别。
  - 成功/失败提示同步改为 `重新投递`。
  - `podcast-visualizer.html` 的 JS 版本号升级到 `20260602-001`，强制浏览器加载新逻辑。
- 验证：
  - `node --check src/frontend/js/podcast-visualizer.js`
  - `curl http://127.0.0.1:8888/podcast-visualizer.html` 确认引用 `podcast-visualizer.js?v=20260602-001`。
  - `curl http://127.0.0.1:8888/js/podcast-visualizer.js?v=20260602-001` 确认包含 `podcast-visualizer/requeue` 与 `重新投递`。

## Celery Redis Broker 连接丢失频繁告警

- 现象：worker 日志频繁出现 `consumer: Connection to broker lost` 与 `redis.exceptions.TimeoutError: Timeout reading from socket`。
- 日志特征：
  - 业务任务已正常完成：`Task tasks.publisher.run_publication_pipeline ... succeeded`。
  - 约数秒后 worker 在 Redis broker 读响应/PING 时超时，然后自动重连。
  - 随后出现 Celery 5.x 到 6.x 的 deprecation warning。
- 根因：
  - 当前 `CELERY_BROKER_URL` 指向远程 Redis：`101.35.56.140:6379/15`。
  - worker 是长驻连接，远程公网 Redis 会有网络抖动。
  - 此前为避免 HTTP 入队请求长时间卡死，把 broker `socket_timeout` 设为 5 秒，导致 worker 对短暂抖动过于敏感。
- 修复：
  - broker `socket_timeout` 默认调到 30 秒，可通过 `CELERY_BROKER_SOCKET_TIMEOUT` 覆盖。
  - broker connect timeout 保持 5 秒，可通过 `CELERY_BROKER_CONNECT_TIMEOUT` 覆盖。
  - 打开 Redis timeout retry：`retry_on_timeout=True`。
  - 增加 `health_check_interval=30` 与 `visibility_timeout=3600`。
  - 显式设置 `broker_connection_retry_on_startup=True`，减少 Celery 6 迁移告警。
  - 显式保持 `worker_cancel_long_running_tasks_on_connection_loss=False`，避免连接短抖时取消正在执行的长任务。
- 验证：
  - `venv/bin/python -m py_compile src/backend/celery_config.py`
  - 配置检查确认 `socket_timeout=30`、`retry_on_timeout=True`、`broker_connection_retry_on_startup=True`。
- 生效要求：重启所有 Celery worker。
- 长期建议：Celery broker 最好使用本机或同机房 Redis；云端 PostgreSQL 适合存业务状态，但 broker 对网络时延和稳定性更敏感。

## 播客视觉化列表下载按钮修复

- 现象：`podcast-visualizer.html` 任务列表里已完成任务显示 `下载` 按钮，但点击无法下载。
- 原因：
  - 任务结果里的 `download_url` 是后端 API 路径：`/api/v1/documents/podcast-visualizer/task/<id>/download`。
  - 列表渲染时使用 `window.location.origin + download_url`，页面在 `localhost:8888`，导致请求打到 `localhost:8888/api/...`。
  - 实际后端 API 在 `API_BASE_URL`，开发环境为 `localhost:5001/api/v1`。
- 修复：
  - 新增 `resolveApiDownloadUrl()`，当路径以 `/api/` 开头时，用 `API_BASE_URL` 去掉 `/api/v1` 后拼接，生成 `localhost:5001/api/v1/...`。
  - `podcast-visualizer.html` JS 版本号升级为 `20260602-002`，避免浏览器继续使用旧缓存。
- 验证：
  - `node --check src/frontend/js/podcast-visualizer.js`
  - 本地前端服务确认 `podcast-visualizer.js?v=20260602-002` 已加载。
  - 本地 JS 确认包含 `resolveApiDownloadUrl()`。

## 闭门会封面底部字幕安全区

- 页面：`closed-door-card-cover-generator.html`。
- 需求：增加一个默认未选中的 checkbox；选中后截图区域里的封面主体内容整体上移，为视频底部字幕留空间。
- 实现：
  - 左侧表单新增 `为底部字幕留空间` checkbox。
  - `drawTexts()` 根据 checkbox 设置 `yShift=-74`，并通过 `ctx.translate(0, yShift)` 对文字层整体平移。
  - 主标题、高亮条、副标题、日期、免责声明作为同一组整体上移；背景不动。
  - 导出下载图与预览一致。
- 修正：
  - 最初用单个元素分别加 `yShift`，容易出现视觉错位和底部残留色块。
  - 已改为 canvas 坐标系整体平移，避免黄色卡片底部出现异常绿色区域。
- 验证：
  - 本地前端服务确认页面包含 `subtitleSafeSpace`、`为底部字幕留空间` 和 `yShift` 渲染逻辑。
  - 本地前端服务确认包含 `ctx.translate(0, yShift)`，且不再使用局部 `172 + yShift`。
  - `git diff --check`。

## 闭门会封面日期空值处理

- 页面：`closed-door-card-cover-generator.html`。
- 需求：日期输入为空时，不显示日期区域，也不保留白色圆角底色。
- 修复：
  - `drawDatePill()` 读取 `inputs.dateText.value.trim()`。
  - 日期文本为空时直接 `return`，不绘制日期文字和白色 pill。
- 验证：
  - 本地前端服务确认包含 `if (!text) return;`。
  - `git diff --check`。

## Celery 可视化控制台

- 页面：`celery-monitor.html`。
- 后端 API：
  - `GET /api/v1/celery/status`：返回 broker 状态、四个 worker 的在线状态、PID、运行时长、队列积压和日志文件路径。
  - `GET /api/v1/celery/logs/<service_id>`：读取指定 worker 最近日志。
  - `POST /api/v1/celery/start/<service_id>`：后台启动指定 worker。
  - `POST /api/v1/celery/stop/<service_id>`：停止指定 worker。
  - `POST /api/v1/celery/restart/<service_id>`：重启指定 worker。
- 服务白名单：
  - `render`：主视频合成。
  - `cloud_tts`：豆包 TTS。
  - `zsxq_sync`：知识星球同步。
  - `podcast_visualizer`：播客视觉化。
- 实现细节：
  - 后端新增 `src/backend/apis/v1/celery_monitor.py`，只允许控制白名单 worker，不接受任意 shell 命令。
  - 启动使用固定 `scripts/dev/run-celery*.sh`，通过 `subprocess.Popen(..., start_new_session=True)` 后台运行。
  - 日志写入 `logs/celery/*.log`。
  - 启动器 PID 写入 `runtime/celery/*.pid`。
  - 停止 worker 时不直接调用 broad 的 `stop-celery.sh`，而是按 queue/hostname 精确匹配对应 Celery 进程，避免误杀全部 worker。
  - 前端每 5 秒刷新状态，可查看日志并执行启动、停止、重启。
  - 导航栏“工具箱”新增 `Celery 控制台`。
- 验证：
  - `venv/bin/python -m py_compile src/backend/apis/v1/celery_monitor.py src/backend/apis/v1/__init__.py`
  - `node --check src/frontend/js/celery-monitor.js`
  - `node --check src/frontend/js/shared-navigation.js`
  - Flask test client 确认 `/api/v1/celery/status` 返回 200，包含四个服务。
  - 本地前端服务确认 `celery-monitor.html` 与 `celery-monitor.js` 可访问。
- 生效要求：
  - 重启后端，使新 API 生效。

## Celery 控制台清空积压功能

- 需求：在 Celery 控制台中增加“剔除”能力，用于清理用户判断不再需要的队列积压消息。
- 后端：
  - 新增 `POST /api/v1/celery/purge/<service_id>`。
  - 只允许白名单服务：`render`、`cloud_tts`、`zsxq_sync`、`podcast_visualizer`。
  - 内部只对该服务对应 Redis 队列执行 `DELETE queue_name`。
  - 返回清理前数量、清理后数量、实际剔除数量。
  - 不修改数据库任务记录，不中断已经被 worker 取走的 processing 任务。
- 前端：
  - 每张 worker 卡片新增 `清空积压` 按钮。
  - 队列积压为 0 时按钮禁用。
  - 点击前二次确认，明确提示只清除 Redis 队列里尚未被 worker 取走的消息。
  - `celery-monitor.html` JS 版本号升级到 `20260602-002`。
- 验证：
  - `venv/bin/python -m py_compile src/backend/apis/v1/celery_monitor.py`
  - `node --check src/frontend/js/celery-monitor.js`
  - Flask test client 确认未知服务 purge 返回 404，白名单保护生效。
  - `git diff --check`。
- 生效要求：
  - 重启后端，使新 purge API 生效。

## Celery 控制台当前执行任务可见性

- 问题：iTerm 日志中可以看到 `render` worker 已经开始执行视频任务，但 `celery-monitor.html` 只展示 worker 在线、PID、运行时长和 Redis 队列积压，无法看到 worker 已经取走并正在执行的任务。
- 后端：
  - `GET /api/v1/celery/status` 增加 Celery inspect 结果。
  - 读取 `active`、`reserved`、`scheduled` 三类任务。
  - 根据 worker hostname 前缀归类到 `render`、`cloud_tts`、`zsxq_sync`、`podcast_visualizer` 四个白名单队列。
  - 每个 service 返回 `tasks`、`active_count`、`reserved_count`、`scheduled_count`。
  - inspect 失败时通过 `inspect_error` 返回，不影响 worker 基础状态展示。
- 前端：
  - 每张 worker 卡片新增 `执行中`、`已领取`、`定时` 三个计数。
  - 有任务时展示任务名、短 task id、参数摘要、已运行时间。
  - 左侧 worker 列表同步展示三类任务计数。
  - `celery-monitor.html` JS 版本号升级到 `20260602-003`，避免浏览器缓存旧脚本。
- 验证：
  - `venv/bin/python -m py_compile src/backend/apis/v1/celery_monitor.py`
  - `node --check src/frontend/js/celery-monitor.js`
  - Flask test client 确认 `/api/v1/celery/status` 返回 200，四个 service 都包含 `tasks.active/reserved/scheduled`。
  - 浏览器确认 `celery-monitor.html` 加载 `js/celery-monitor.js?v=20260602-003`，主视频合成卡片出现 `执行中 / 已领取 / 定时`。
- 生效要求：
  - 重启后端，使 `/api/v1/celery/status` 的 inspect 字段生效。
  - 刷新 `celery-monitor.html`，确保加载 `20260602-003` 版本脚本。

## Celery 控制台清空积压 405 诊断

- 现象：点击播客视觉化 worker 的 `清空积压`，页面提示 `请求方法 POST 不被允许`。
- 判断：
  - 当前代码路由表确认 `POST /api/v1/celery/purge/<service_id>` 已存在。
  - Flask test client 确认 POST 方法可匹配 purge 路由；在沙箱内因无法连接远端 Redis 返回 500，但不是 405。
  - 页面仍出现 405，说明运行中的后端进程尚未重启到包含 purge 路由的新代码。
- 前端补强：
  - `apiFetch()` 现在保留 HTTP status。
  - purge 遇到 405 或 `POST 不被允许` 时，明确提示需要重启后端服务，而不是泛化为清空失败。
  - `celery-monitor.html` JS 版本号升级到 `20260602-004`。
- 验证：
  - `venv/bin/python -m py_compile src/backend/apis/v1/celery_monitor.py`
  - `node --check src/frontend/js/celery-monitor.js`
  - `git diff --check`
- 生效要求：
  - 重启后端，使 purge 路由进入运行进程。
  - 刷新 `celery-monitor.html`，确保加载 `20260602-004`。
