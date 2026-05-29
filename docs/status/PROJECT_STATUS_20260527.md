# 2026-05-27 工作归档

## 知识星球同步器第一阶段

- 明确目标：为“前沿信息收录”等知识星球频道建立可手动触发、可增量、可追踪的本地同步能力，后续再复用同一套服务做计划任务。
- 新增数据库模型：
  - `zsxq_sync_runs`：记录每次同步的范围、状态、统计结果和错误信息。
  - `zsxq_topics`：按 `group_id + tab_id + topic_id` 保存主题元数据，避免同一主题出现在多个 tab 时互相误判。
  - `zsxq_attachments`：记录主题里的文件和图片附件元数据，并跟踪 PDF 本地下载状态。
  - `zsxq_sync_cursors`：记录每个频道 + tab 的最新主题和同步游标。
- 新增 `ZsxqSyncService`：
  - 支持同步一个或多个官方 tab。
  - 每次同步写入数据库，并把原始 topic JSON 保存到 `data/zsxq/{group_id}/{tab}/`。
  - 增量逻辑：遇到当前 tab 已存在的 `topic_id` 时停止继续向下翻页。
  - Cookie 仍由前端请求传入，不写入数据库或本地文件。
- 新增后端接口：
  - `POST /api/v1/zsxq/sync`：手动同步当前 tab 或全部 tab。
  - `POST /api/v1/zsxq/sync/runs`：查看最近同步记录。
- 前端 `zsxq-lab.html` 新增：
  - “同步当前 Tab”按钮。
  - “同步全部 Tab”按钮。
  - 同步结果和最近同步日志展示区域。
- 同步节奏：
  - 当前 tab 默认最多拉 3 页。
  - 全部 tab 第一版每个 tab 默认拉 1 页。
  - 多页和多 tab 请求之间随机等待 4.5-7 秒，避免过于密集。
- `.gitignore` 新增 `data/zsxq/`，避免同步出来的原始数据和附件进入代码仓库。
- 云端 PGSQL 权限修正：
  - 移除运行时自动 `CREATE TABLE` 行为，避免应用账号无建表权限时抛出 psycopg2 权限错误。
  - 新增 `docs/sql/zsxq_sync_tables.sql`，需要用有建表权限的数据库账号一次性执行。
  - 同步接口会检查表是否存在；缺表时返回明确初始化提示。
- 页面布局修复：同步按钮增加后，标题区被按钮挤压导致中文逐字换行；已把标题和操作按钮改成上下两行布局。
- 同步日志展示优化：
  - 选中星球后常驻展示“同步日志”面板，不再只在点击同步后临时出现。
  - 面板明确说明当前同步保存主题元数据、附件元数据、原始 topic JSON 和 PDF 附件文件。
  - 点击同步后保留本次同步结果，不再立刻被最近同步列表覆盖。
  - 最近同步记录和本次各 tab 结果改为列表展示，每条展示时间、范围、状态、新增/更新/已存在/附件数量和错误信息。
- 同步自动下载 PDF 附件：
  - 同步过程中会自动下载 PDF 到 `data/zsxq/{group_id}/{tab}/files/`。
  - 只自动下载 PDF，非 PDF 附件标记为 `skipped`。
  - 下载状态写入 `zsxq_attachments.download_status/local_path/error_message`。
  - PDF 下载之间随机等待 9-12 秒。
  - 下载完成后校验 PDF 文件头，非有效 PDF 会删除并标记失败。
  - 如果遇到已存在主题但 PDF 还没下载，会继续处理并补下载；后续 PDF 已补齐后才按增量逻辑提前停止。
- 主题列表附件状态展示：
  - 新增 `POST /api/v1/zsxq/attachments/status`，按当前可见 `topic_id` 批量查询附件下载状态。
  - 前端附件行增加“下载过”标签；`download_status=downloaded` 时显示。
  - 前端按 `group_id + tab_id + topic_id` 做内存缓存，避免切换/重绘时重复查库。
  - 同步成功后清空该缓存并刷新当前页标签。
  - 手动“下载”和“排队下载附件”也会写回 `zsxq_attachments.download_status=downloaded`，并立即刷新当前行的“下载过”标签。
- 知识星球页面视觉重构：
  - 页面从左右两栏改为三栏工作台：左侧账号/星球，中间主题流，右侧同步日志和发布。
  - 统一卡片、按钮、tab、主题条目、附件行的边框、圆角、阴影和 hover 状态。
  - 将主题操作按钮收束为独立工具条，避免功能增长后挤压标题区域。
  - 右侧发布区和同步日志区从主题流里拆出，降低主阅读区域噪音。

## 知识星球附件下载修复

- 定位问题：PDF 下载兜底逻辑过宽，主题详情里只要有 URL 的对象都可能被误判为附件对象，极端情况下会把 `group.background_url` 或作者头像当作 PDF 下载源。
- 修复 `ZsxqService.file_url_candidates()`：
  - 屏蔽 `avatar/background/thumbnail/image/cover/icon` 等图片类 URL 字段。
  - 只接受 `download/file/url/link/path` 等更接近附件下载的字段。
- 修复 `find_file_in_topic()`：
  - 只有看起来像附件 payload 的对象才参与匹配。
  - 移除“只有一个 URL 对象就当成附件”的危险兜底。
- 增加 PDF 响应校验：如果目标文件名是 `.pdf`，但上游返回 `image/*`，后端直接拒绝，不再把图片按 PDF 文件名返回给浏览器。

## 当前边界

- 第一阶段只自动下载 PDF；非 PDF 附件暂不自动下载。
- 计划任务尚未接入；后续应复用 `ZsxqSyncService`，不要另写一套同步逻辑。
- 计划任务要跑时，需要后端可读取的 Cookie profile；当前浏览器 localStorage Cookie 只适合手动同步。

## 知识星球同步日志和下载标签修复

- 修复同步日志时间显示：
  - 后端同步记录使用 UTC 裸时间写入数据库。
  - 前端展示时将无时区的 ISO 时间按 UTC 解析，并统一用 `Asia/Shanghai` 渲染。
  - 最近同步状态从英文 `completed/failed/running` 改为中文 `成功/失败/进行中`。
- 修复“下载过”标签漏展示：
  - 原查询只按 `group_id + tab_id + topic_id` 精确查询附件状态。
  - 若同一主题出现在多个官方 tab，或历史同步时 tab id 与当前展示 tab id 不一致，已下载记录会被漏掉。
  - 现在先查当前 tab，再按 `group_id + topic_id` 兜底合并其他 tab 的 `download_status=downloaded` 记录。
  - 前端仍保持可见主题批量查询和内存缓存，避免列表滚动时频繁查库。

## 验证

- `python3 -m py_compile src/backend/models/zsxq_sync.py src/backend/services/zsxq_sync_service.py src/backend/apis/v1/zsxq.py`
- `python3 -m py_compile src/backend/services/zsxq_service.py src/backend/apis/v1/zsxq.py`
- 单文件导入断言：确认 `background_url/avatar_url` 不会被当作附件下载候选，`download_url` 仍可用。
- `node --check src/frontend/js/zsxq-lab.js`
- `git diff --check`
