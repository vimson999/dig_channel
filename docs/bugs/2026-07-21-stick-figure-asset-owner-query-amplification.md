# 火柴人资产入队和媒体请求放大父任务查询

## 基本信息

- 状态：已部署并完成真实 31 镜头页面、跨进程缓存与二次刷新验收
- 严重级别：高
- 影响模块：火柴人视频 / publication_tasks / 分镜资产 / 媒体文件接口
- 首次发现：2026-07-21 新 PID 真实生图请求
- 修复提交：待提交
- 根因分类：commit 后 ORM 过期刷新、媒体 N+1 权限点查、完成态仍读 JSON、结构缓存仅限单进程
- 复发键：`DB-SESSION-LIFECYCLE` / `HOT-PATH-PAYLOAD-POLLING`
- 是否复发：是；属于多次出现的过期 ORM 整行刷新与页面级查询放大
- 关联记录：`2026-07-16-zsxq-expired-orm-full-row-refresh.md`、`2026-07-10-report-audio-projection-and-parent-roundtrips.md`、`2026-07-21-zsxq-sync-point-query-amplification.md`
- 防线层级：shared_guardrail / sql_contract_test / runtime_acceptance / observability

## 现象与影响

- 31 个分镜图片的入队 API 耗时 `53.5s`。
- `_enqueue_image_asset` 在 commit 后读取过期的 parent/asset，触发包含 `params/result` 和所有宽列的 `publication_tasks` 主键刷新，已记录 2 次 `1.07s-1.12s`。
- 返回页面后，浏览器为每张图片单独请求文件，每次都调用 `_owned_parent`查数据库，使一个页面形成最多 31 次相同权限查询。
- 首轮修复部署后又发现视频文件请求仍调用 `_latest_video_task` 并读取 `params`：新 PID 记录 11 次相同 payload 查询；字幕接口还从 `result` 读取固定的 `subtitle_path`。
- 分镜结构只缓存 30 秒且仅存在当前进程，后端一重启就重新查询 31 段长文本；真实冷加载达到 `1.22s`。

## 根因

1. 循环内每个资产都 commit，但 parent、storyboard 和 asset 仍以 ORM 对象在后续循环中传递。
2. 所有场景端点复用同一个宽 `PARENT_COLUMNS`，没有为权限与文件路径设置窄快照。
3. 媒体文件权限结果没有请求组级复用，一个页面的并发资产请求被直接放大到数据库。
4. 视频和字幕产物路径本来由 workflow ID 唯一确定，接口却继续绕回任务 JSON 取路径。
5. 已完成分镜是不可变结构，但只做进程内缓存，把每次重启都当作首次加载。

## 实际方案

- 进入可能 commit 的流程前，将 parent、storyboard 和全部 asset 转为 `SimpleNamespace` 轻量快照。
- 图片、语音和视频入队在首次 commit 后只使用标量，禁止再访问 ORM parent/asset。
- 资产权限查询改用不含 `params/result` 的 `SCENE_PARENT_COLUMNS`，并按“可见用户 ID 集合 + workflow ID”缓存 30 秒正向快照。每次请求仍经过登录验证，不跨用户复用。
- 场景媒体按 workflow 一次集合加载并形成 30 秒轻量快照；并发首读使用单飞锁，重试写接口显式绕过缓存读取最新状态。
- 视频、字幕文件接口改用授权父任务和确定性产物路径，不再查询最新视频子任务；`VIDEO_TASK_COLUMNS` 删除 `params`。
- 已完成视频详情优先读取本地有界 manifest；只有缺少 manifest 的历史任务才冷回退 `result`，未完成任务才按 ID 单点读取提交参数，业务元数据保持完整。
- 已完成分镜结构写入 Redis，TTL 24 小时；新进程可恢复 storyboard ID 和 31 段轻量快照。强制刷新仍绕过缓存并覆盖新值。

## 验证

- 并发行为测试：31 个媒体读取并发请求只执行 1 次 workflow 集合查询。
- 新 PID `13804` 真实加载任务 `48c6592c-082a-438c-be4c-fe0c81342284`：31/31 图片成功、31 段音频与 1 个视频可用，浏览器无业务错误。
- 真实媒体接口均返回 `206`，单次约 `0.45-1.29ms`；验收窗口没有再出现父任务或场景资产点查慢 SQL。
- 契约断言：入队 commit 后不得出现 `parent.`/`asset.`；权限列禁止 `params/result`；缓存键必须包含可见用户集合。
- 完整已知问题门禁：`741 passed, 5 skipped, 42 subtests passed`，TypeScript、Python 编译、Alembic 单头和 `git diff --check` 均通过。
- 统一重启后新后端 PID `5273`，17 个 Celery 服务、watchdog 和 2 个 scheduler 全部启动成功，bootstrap `errors=[]`。
- 真实任务 `48c6592c-082a-438c-be4c-fe0c81342284` 连续加载两次：31/31 图片、31 音频、1 视频可用；新 PID 窗口慢 SQL `0`、错误 SQL `0`、`publication_tasks` payload 专项告警 `0`。
- Redis 结构键已存在，大小 `21166` bytes，验收时 TTL `86337s`；清空进程缓存后恢复行为由单测覆盖。

## 复发预防

1. 循环中存在 commit 时，循环依赖必须在第一次 commit 前整体快照，不允许每轮临时读 ORM。
2. 页面后续并发加载的文件、图片和分片必须有页面级权限查询预算，默认不得按资产点查父记录。
3. 权限缓存必须包含用户作用域，只缓存正向快照且设置短 TTL。
4. 可由业务 ID 确定的文件路径禁止再从 JSON 读取；完成态详情优先读取有界 manifest 或结构化列。
5. 不可变完成态结构必须有跨进程缓存验收；仅验证同一 PID 的第二次请求不算完成。
