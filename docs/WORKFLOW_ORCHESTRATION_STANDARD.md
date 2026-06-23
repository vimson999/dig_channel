# 编排任务开发标准

本文档约定项目里所有“编排任务”的统一做法，适用于知识星球编排、研报转音频/视频编排、MP3 视频编排，以及后续新增的自动化流程。

## 目标

- 每条编排都能在 Celery Monitor、历史列表、邮件通知里用同一种方式被观察。
- 失败后支持从失败处续跑，已完成且产物完整的节点必须复用。
- 父编排只负责串联节点和记录状态，具体执行交给清晰的子任务或服务函数。
- 新增编排时不再“各展神通”，而是按同一套状态、结果和接口约定实现。

## 后端基础结构

每个编排必须有一个稳定的 `feature`，例如 `report_audio_workflow`、`mp3_video_workflow`。父任务统一使用 `PublicationTask`：

- `params.feature`: 编排 feature。
- `params.user_id`: 当前用户。
- `params.<feature>`: 本编排输入、配置、metadata。
- `result.logs`: 最近日志数组。
- `result.children`: 子任务摘要，key 使用稳定语义名，例如 `landscape_video`。
- `result.notifications`: 已入队通知事件，避免重复发邮件。
- `result.error`: 当前失败原因。
- `result.retry_requested_at`: 最近一次续跑请求时间。
- `result.output_path`: 最终 JSON 结果文件。

父任务状态统一：

- `pending`: 已入队。
- `processing`: 正在执行。
- `completed`: 全部完成。
- `failed`: 编排失败，可从失败处续跑。
- `canceled`: 用户取消，可按业务决定是否允许续跑。

前端对外状态统一映射为：

- `queued`
- `running`
- `succeeded`
- `failed`
- `canceled`

## 阶段设计

每个阶段都必须有稳定的 step code，日志和 UI 都使用它：

- `*_started` 或业务动作名：阶段开始。
- `*_reused`: 续跑时复用已有产物。
- `*_submitted`: 已提交子任务。
- `*_completed`: 阶段完成。
- `failed`: 编排失败。
- `done`: 编排完成。

进度值要按阶段区间固定下来，不要在不同运行中漂移。例如 MP3 视频编排：

- 2: start
- 8-54: Gemini 上传、解析和信息图
- 58: DS 规划
- 68: 四张封皮/背景图
- 78-90: 横屏视频
- 91-99: 竖屏视频
- 100: done

## 失败续跑

续跑不是“重新跑一遍”。`/retry` 接口必须保留已有 `result`，只清理运行态字段：

- 保留 `result.gemini`、`result.plan`、`result.artifacts`、`result.children`、已完成视频摘要等。
- 追加 `retry_requested` 日志。
- 写入 `result.retry_requested_at`。
- 清空 `result.error`、`error_message`、`completed_at`。
- 把父任务改回 `pending/queued` 并重新入队。
- 已完成任务返回 400：`任务已完成，无需续跑`。

执行函数必须在每个阶段入口做幂等检查：

- Gemini 阶段：必须同时存在第一轮返回文本和信息图文件，才可复用。
- 规划阶段：必须存在标题和音频时间戳，才可复用。
- 图片阶段：必须四张图片都存在且非空，才可复用。
- 图片模板发生变化时，图片 metadata 必须带 `template_version`，续跑检查要拒绝旧版本产物并重生成。
- 视频阶段：必须子任务 `completed` 且视频文件存在且非空，才可复用。
- 失败、取消、缺文件、半成品都不能复用，必须重建该阶段。

视觉产物优先复用已有前端生成器：

- 如果项目里已经有成熟的封面/背景图页面，编排应通过浏览器自动化调用页面 canvas 导出。
- 后端不再重复实现一套视觉模板，除非页面不存在或明确作为兜底。
- 前端页面应暴露稳定的 `window.renderWorkflow...()` 方法，接收结构化 payload，返回 `canvas.toDataURL()`。
- 编排侧只负责传字段、上传图片 dataURL、保存导出的图片。

续跑日志要明确写出复用动作，例如：

- `gemini_reused`: 复用已完成的 Gemini 第一轮返回和横向信息图。
- `generate_visual_assets_reused`: 复用已生成的四张封皮/背景图。
- `landscape_video_reused`: 复用已完成的横屏视频。

## 子任务约定

父编排创建子任务时必须写入可追溯 metadata：

- `parent_feature`
- `parent_task_id`
- `source_feature`
- `source_task_id`
- `metadata.role`
- `metadata.title`

父编排必须用 `result.children.<label>` 记录子任务公开摘要：

- `job_id`
- `celery_id`
- `status`
- `step`
- `progress`
- `feature`
- `error`
- `created_at`
- `completed_at`

等待子任务时，父任务应把子任务进度映射到自己的阶段进度区间，并定期刷新 `children`。

长耗时等待必须遵守数据库会话纪律：

- 跨等待阶段只保存子任务 ID，不保存 `PublicationTask` ORM 实例。
- 等待循环中需要读取状态时，用短生命周期查询重新加载子任务。
- 释放 SQLAlchemy session 后，禁止继续访问旧 ORM 对象的 `status`、`result`、`params` 等属性。
- 如等待时间可能达到分钟级，应优先设计为 `waiting_*` 状态 + 恢复调度，避免父 worker 长时间占坑。
- 豆包多分段朗读、publisher 视频合成这类重子任务默认只能短暂 inline 检查状态，随后通过父任务延迟恢复继续推进。
- 恢复调度必须写入 `result.waiting_child` 和 `result.deferred_waits`，并限制最大恢复次数，避免无限循环。
- 已知重子任务的默认策略必须偏生产：研报音频编排的豆包分段与 publisher 视频、MP3 视频编排的横屏/竖屏 publisher 视频，父编排默认只 inline 等几秒，后续由延迟任务恢复；本地调试如需拉长等待，必须通过显式环境变量覆盖。

## 通知标准

每条编排都要接入 `workflow_notification_service`：

- 阶段开始、阶段完成、最终完成、失败都应有事件。
- 事件名称使用 `<feature>_<stage>_<state>`，例如 `mp3_video_visuals_completed`。
- 通知入队前检查 `result.notifications[event_type].queued`，避免重复邮件。
- 通知 payload 必须包含 `stage_results.stages` 和 `stage_results.outputs`，让邮件能直接看懂当前产物。

续跑复用阶段时也可以调用 completed 通知；服务层会自动去重。

## Celery Monitor 标准

编排任务必须出现在两层监控里：

- Celery 队列：展示 worker、active/reserved/scheduled。
- 业务运行：从 `PublicationTask` 读取 `pending/processing` 的父任务和关键子任务，避免 Celery active 瞬时不可见时 UI 空白。

新增编排时要更新业务监控 feature 映射，至少包含：

- queue 名称。
- `PublicationTask.params.feature` 或相关 JSON feature。
- 页面跳转链接。
- 标题 fallback。

## 前端标准

编排页面必须提供：

- 输入列表或创建入口。
- 历史任务分页和跳页。
- 状态 pill、进度条、当前 step。
- 成功状态标绿，失败状态标红。
- 标题旁复制按钮。
- 失败/取消任务显示 `失败续跑` 按钮，按钮 title 为 `从失败处续跑`。
- 续跑前弹窗说明会复用已完成节点。
- 下载入口按产物语义命名，不直接暴露内部文件名作为主要按钮文案。

文案统一：

- 创建：`开始编排`
- 取消：`取消`
- 续跑：`失败续跑`
- 接口成功提示：`任务已从失败处续跑`
- 已完成续跑提示：`任务已完成，无需续跑`

## 新增编排检查清单

1. 定义 `FEATURE_NAME`、队列名、页面路径。
2. 新增 API blueprint，并注册到后端。
3. 新增 Celery task，并注册到 celery 配置和开发 worker 脚本。
4. 父任务 `PublicationTask` 的 `params/result` 按本标准落库。
5. 每个阶段都实现可复用检查点。
6. `/retry` 实现从失败处续跑，不能清空已有产物。
7. 子任务写入 parent/source metadata，并记录到 `result.children`。
8. 邮件通知事件注册到 `workflow_notification_service`。
9. Celery Monitor 增加业务运行映射。
10. 前端历史列表支持状态、复制标题、下载、取消、失败续跑。
11. 单元测试覆盖规划/产物判断/续跑复用的核心逻辑。
12. 验证项至少包括后端语法检查、前端语法检查、相关单元测试。
