# Project Status 2026-06-03

## 知识星球附件下载失败标记

- 页面：`zsxq-lab.html`。
- 需求：
  - 已经下载成功的附件列表里已有 `下载过` 标记。
  - 需要对下载失败的附件增加可见标记。
  - 失败附件仍然可以单个重新下载。
- 后端：
  - `ZsxqSyncService.get_attachment_statuses()` 现在会返回 `downloaded` 和 `failed` 两类终态记录。
  - 跨 Tab fallback 查询也包含 `failed`，避免同一主题在不同入口展示时丢失失败状态。
  - 新增 `mark_browser_download_failed()`，浏览器单个下载失败时会写入 `download_status=failed` 和 `error_message`。
  - `/api/v1/zsxq/download` 下载失败时尝试记录失败状态；记录失败本身不会覆盖原始下载错误响应。
- 前端：
  - 附件行新增 `下载失败` 红色标记。
  - 失败时展示简短错误原因。
  - 失败附件的按钮文案改为 `重新下载`，按钮保持可点击。
  - 单个重新下载成功后，缓存状态更新为 `downloaded`，失败标记消失并显示 `下载过`。
  - 本地网络失败这类后端无法落库的情况，也会先在当前页面缓存失败状态。
  - `zsxq-lab.html` 脚本版本号升级为 `20260603-download-failed-badge`。
- 验证：
  - `venv/bin/python -m py_compile src/backend/apis/v1/zsxq.py src/backend/services/zsxq_sync_service.py`
  - `node --check src/frontend/js/zsxq-lab.js`
  - `git diff --check`
  - Flask test client 确认 `/api/v1/zsxq/attachments/status` 参数校验正常。
- 生效要求：
  - 重启后端，使 `/api/v1/zsxq/download` 的失败落库逻辑生效。
  - 刷新 `zsxq-lab.html`，确保加载 `js/zsxq-lab.js?v=20260603-download-failed-badge`。

## 外部 PDF 工具平台菜单级集成

- 来源项目：`/Users/v9/Documents/3---work/06---dev---python/pytest`。
- 读取结论：
  - 该项目是独立 Flask + Celery 的 PDF 智能处理平台，不是 pytest 测试项目。
  - 核心能力包括 PDF 智能清洗/去水印、深度解析、批量清洗、工具箱、音频裁剪和审计仪表盘。
  - 当前服务默认地址为 `http://127.0.0.1:5005`。
- 当前系统集成：
  - 新增页面 `pdf-tools.html`。
  - 新增脚本 `js/pdf-tools.js`，用于轻量检查外部服务是否可访问。
  - `shared-navigation.js` 工具箱新增 `PDF 工具平台` 菜单入口。
  - 页面提供外部平台首页、深度解析、批量清洗、工具箱、音频裁剪、审计仪表盘入口。
- 集成边界：
  - 本次不修改 `/pytest` 项目。
  - 不迁移代码，不共享 Celery 队列，不改 Redis。
  - 页面只做菜单级轻集成和状态提示。
- 风险记录：
  - `/pytest/run.py` 存在硬编码 Redis 地址，后续深度集成必须迁移到环境变量。
  - 深度集成时 PDF 任务应使用独立队列，不能和视频合成、知识星球同步、播客视觉化混用。
- 验证：
  - `node --check src/frontend/js/shared-navigation.js`
  - `node --check src/frontend/js/pdf-tools.js`
  - `git diff --check`

## 知识星球附件列表只看 MP3

- 页面：`zsxq-lab.html`。
- 需求：
  - 调研纪要 Tab 内 PDF 和 MP3 混在一起时，用户只关心 MP3。
  - 需要一个前端控制功能，只看 MP3。
- 前端：
  - 主题工具栏新增 `列表筛选` 下拉框。
  - 选项包括 `显示全部`、`只看 MP3`、`只看 PDF`。
  - `只看 MP3` 时，只展示包含 MP3 附件的主题，并且主题内只展示 MP3 附件。
  - 切到 `只看 MP3` 或 `只看 PDF` 时，同步把批量下载附件类型切到对应类型，避免列表只看 MP3 但按钮仍显示下载 PDF。
  - 全选和批量下载统计基于当前可见附件。
  - 筛选为空时保留筛选控件，用户可以切回 `显示全部`。
  - `zsxq-lab.html` 脚本版本号升级为 `20260603-mp3-filter`。
- 验证：
  - `node --check src/frontend/js/zsxq-lab.js`
  - `git diff --check`

## 知识星球当前页面热度排行

- 页面：`zsxq-lab.html`。
- 需求：
  - 在页面右侧增加当前页面热度排行 list。
  - 第一阶段只基于当前已加载页面数据计算，不入库、不使用 Redis。
  - 主要按附件下载次数排序，方便快速发现热门 MP3/PDF。
- 前端：
  - 右侧新增 `热度排行` 卡片。
  - 根据当前已加载、当前筛选后可见的附件生成排行。
  - 排序依据为附件 `download_count/downloads_count/downloadCount/downloadsCount/statistics.download_count`。
  - 只显示下载次数大于 0 的附件，最多展示前 20 个。
  - 排行项展示排名、附件名、附件类型、下载次数、作者、时间和所属主题标题。
  - 点击排行项会滚动定位到主列表对应附件，并短暂高亮。
  - 当列表筛选为 `只看 MP3` 时，排行自动只统计 MP3；切回 `显示全部` 时恢复全部附件排行。
  - `zsxq-lab.html` 脚本版本号升级为 `20260603-hot-rank`。
- 缓存/入库说明：
  - 本阶段未入库，也未使用 Redis 缓存。
  - 后续如果需要跨分页、跨刷新、全 Tab 维度排行，可再增加 Redis 10 分钟缓存和手动清缓存接口。
- 验证：
  - `node --check src/frontend/js/zsxq-lab.js`
  - `git diff --check`

## Render Celery 积压处理

- 现象：
  - `celery-monitor.html` 显示主视频合成 worker 在线，但 `render` 队列存在积压。
  - 页面显示 worker 未领取任务，用户要求立即开始消费积压。
- 排查：
  - `logs/celery/render.log` 显示 worker 与云 Redis broker 多次出现 `Broken pipe` 后重连。
  - 控制台重启前旧 worker 进程仍存在，但没有领取新任务。
  - 启动脚本存在隐患：`run-celery*.sh` 会在 shell 层默认设置 `REDIS_URL=redis://localhost:6379/0`，如果启动环境没有显式传入云 Redis，会阻止 `celery_config.py` 从 `.env` 读取云 Redis。
- 修复：
  - 移除 `scripts/dev/run-celery.sh`、`scripts/dev/run-celery-tts.sh`、`scripts/dev/run-celery-zsxq.sh`、`scripts/dev/run-celery-podcast.sh` 中的本地 Redis 默认值。
  - 统一让 `src/backend/celery_config.py` 通过 `.env` 或自身默认值决定 broker。
- 处置：
  - 通过 `/api/v1/celery/restart/render` 定向重启 `render` worker。
  - 旧 PID：`59750/59745`。
  - 新 PID：`19196`。
  - 重启后新 worker 已连接云 Redis `101.35.56.140:6379/15`，并收到 `tasks.publisher.run_publication_pipeline`。
  - `render` 队列长度从 `4` 降到 `3`，说明已开始消费积压任务。
- 验证：
  - `bash -n scripts/dev/run-celery.sh scripts/dev/run-celery-tts.sh scripts/dev/run-celery-zsxq.sh scripts/dev/run-celery-podcast.sh`
  - `tail -120 logs/celery/render.log`
  - `curl --noproxy '*' -X POST http://127.0.0.1:5001/api/v1/celery/restart/render`
  - `curl --noproxy '*' http://127.0.0.1:5001/api/v1/celery/status`
