# 生产级演进路线图

本文档记录项目向更健壮生产系统演进的优化方向。它不是单次重构清单，而是按风险和收益排序的持续路线图。

相关基线：

- 已有运行纪律：`docs/WORKFLOW_RUNTIME_OPERATIONS_PLAYBOOK.md`
- 已有连接和任务准入策略：`docs/production-hardening.md`
- 编排任务标准：`docs/WORKFLOW_ORCHESTRATION_STANDARD.md`
- 项目地图：`docs/PROJECT_CONTEXT_MAP.md`

## 优先级定义

- P0：降低生产事故概率或显著缩短恢复时间，优先做。
- P1：提升可观测性、可维护性和交付稳定性，应进入近期迭代。
- P2：中长期结构治理，避免系统继续变复杂后失控。

## 2026-06-23 运行态复盘结论

近期事故说明，系统主要风险不只是“某个 worker 没启动”或“某个连接池参数太小”，而是缺少持续对账机制：

- DB 业务表显示 `pending/processing`，但 Celery `active/reserved/scheduled/queue` 中没有对应消息。
- 父编排已经 `completed/failed/canceled`，强绑定子任务仍显示业务进行中。
- 任务因为准入保护进入 `deferred_*`，但延迟消息丢失后没有自动恢复。
- 发布链路把 `submitted`、`reviewing`、`visible` 混在一起时，用户会看到“号称成功但页面不可见”。
- DB 压力、业务并发、队列积压是三套控制面，不能靠反复调一个并发数字解决。

因此 P0 的第一目标不是继续加按钮，而是建立运行时不变量和自动 reconciliation：所有 active 业务任务必须能解释清楚“谁在执行、谁在等待、谁负责恢复、什么条件下终止”。

## P0：先降低运行风险

### P0-A：重复 Bug 根因治理

状态：已启动。首轮回顾见 `docs/bugs/reviews/2026-07-10-recurring-bug-review.md`。

现状：

- 2026-06-23 至 2026-07-10 共记录 50 条 Bug，已识别 6 个复发根因簇。
- 父子任务状态机、DB Session 生命周期、热点 JSON 查询和产物语义校验已经多次换入口复发。
- 现有规则和单模块测试不足以阻止复制式实现再次遗漏同类保护。

建议：

- 每 7 天或每新增 5 条 Bug 做正式回顾；同一复发键第二次出现时立即回顾。
- 第二次复发必须做同类模块横向排查、共享防线和跨入口契约测试。
- 第三次复发自动升为 P0 架构债，在共享防线完成前停止复制同类实现。
- 用 `docs/bugs/recurrence-registry.json` 和 `scripts/dev/review_bug_records.py --strict` 保证所有记录均被归类。
- 优先关闭 `ORCH-PARENT-CHILD-STATE`：子任务显式声明 `bound/post_completion/independent` 生命周期策略，repair 不再依赖 feature allowlist。

验收：

- 所有新增 Bug 都有复发键、关联记录和防线层级。
- 已复发问题不能以单点补丁或新增 allowlist 作为系统性完成条件。
- 复发根因簇连续两个回顾周期没有新增同类事故，且共享契约测试稳定通过。

### 0. 运行时事实对账和任务生命周期不变量

状态：已启动，继续完善。

现状：

- 已有 watchdog 进程守护、consumer-stuck 检测、业务任务丢失 Celery 消息后的自动重投。
- 已补 `publisher` 子任务父编排终止后的自动收口。
- 已补 watchdog 与页面一致读取业务任务快照：业务任务近期仍在推进时，不因队列积压或 Celery inspect 暂时为空误杀 worker。
- 已补 pause 语义贯穿 business repair：暂停 `render` 时，publisher 子任务 repair 不再绕过 pause 自动重投或降级。
- 仍需完善完整 active task reconciliation 规则，尤其是 `processing` 长时间无心跳、`deferred_*` 延迟消息丢失、父子任务级联终态等边界。

建议：

- 建立统一 active 任务不变量：
  - `pending`：必须能在 Celery 队列、scheduled/reserved 中找到，或处于可解释的 deferred 状态。
  - `processing`：必须有 worker active 记录或最近心跳/进度推进。
  - `deferred_*`：必须有下一次恢复计划、Celery 可见消息或 watchdog 可重投记录。
  - 父任务终态后，强绑定子任务不得继续 active。
- 把 reconciliation 固化为服务，而不是散落在页面按钮和人工脚本里。
- repair 动作必须原地修复同一个业务 ID；除非用户显式重做，不新建重复任务。
- repair、cleanup、requeue、auto-cancel 都要写入 `result.queue_repair` 或 `result.orphan_child_cleanup` 等结构化字段。
- Celery Monitor 增加“运行时不一致”风险区：孤儿 pending、孤儿 processing、父终态子 active、deferred 丢消息。
- watchdog 重启 worker 前必须同时满足进程/队列异常和业务任务无近期推进；业务任务仍在推进时只提示观察，不执行重启。
- 人工 pause 必须约束所有自动动作：进程自愈、业务 repair、孤儿重投、processing 降级都不得绕过 pause。
- 子任务必须声明生命周期策略：强绑定父任务、允许父完成后继续、或完全独立；cleanup/reconciler 根据策略和父状态决策，禁止继续扩展 feature allowlist。

验收：

- 监控页不再出现“业务进行中 1，但 queue/active/reserved/scheduled 全为 0”的幽灵状态。
- 任意 active 任务都能被解释为：正在执行、等待可见 Celery 消息、受控延迟恢复、人工暂停、或已被自动收口。
- 单测覆盖：Celery 消息不可见重投、父任务终态子任务收口、deferred 消息丢失恢复、processing 超时不误杀。
- watchdog event 能说明自动做了什么，而不只显示 worker 重启。
- 有活跃业务任务且 `updated_at` 仍在推进时，watchdog 不得自动重启对应 worker。

### 1. 长等待父编排状态机化

现状：

- 研报音频、MP3 视频等父编排已经开始把重子任务等待改为短 inline 等待 + 延迟恢复。
- 仍有部分流程本质上靠父 worker 等待子任务，容易占坑、放大 DB 连接和 worker 资源压力。

建议：

- 所有父编排提交重子任务后进入 `waiting_*` 状态。
- 用延迟任务、reconciler 或子任务完成事件唤醒下一阶段。
- 父编排跨等待只保存 `task_id`、阶段码和恢复次数。
- 将最大恢复次数、恢复间隔和超时策略统一配置化。

验收：

- 父 worker 不会为了等待 publisher、Gemini、豆包朗读等子任务长时间占用。
- 失败续跑可以从 `waiting_*` 阶段恢复。
- 单测覆盖：子任务完成、失败、丢失、超时、重复恢复。

### 2. 数据库容量模型和连接治理

状态：已启动，继续落地。

现状：

- 已区分 Web、Worker、Scheduler 的 SQLAlchemy 连接策略。
- 已有 `runtime_guard` 基于 DB pressure、同类任务运行数和 backlog 做准入保护。
- 本地和生产都需要从“调并发数字”升级为“容量预算 + 连接池 + PgBouncer + 退避”的固定模型。

建议：

- 明确每类进程的连接预算：Web、Celery worker、scheduler、watchdog、一次性排障脚本。
- 生产优先使用 PgBouncer transaction pooling；本地如继续连接云 PostgreSQL，也应尽量使用同样连接路径或等价保护。
- 保持运行并发和 backlog 容量分离：不能因为 DB 被打爆就把正常排队能力一刀切死。
- DB pressure 触发时只延迟准入，不把任务直接判失败；延迟消息必须纳入 reconciliation。
- 所有长等待阶段必须释放 DB session，跨等待只保存 ID。

验收：

- 可以解释“当前最多会打开多少 DB 连接”，而不是靠猜。
- PgBouncer/数据库侧没有长期 `cl_waiting`、长 idle-in-transaction 或连接槽耗尽。
- 调整业务吞吐时有容量表和压测/冒烟依据，不再在“打爆 DB”和“勒死业务”之间来回摇摆。

### 3. 热路径禁止读取大 JSON

现状：

- `PublicationTask.params/result` 承载大量业务上下文。
- 近期已出现列表接口因为重新读取 `result` 大 JSON 导致 15 秒超时的问题。

建议：

- 建立 `docs/DATA_MODEL_HOT_PATHS.md`，列出列表页、轮询页、监控页允许读取的列。
- 为任务标题、封面字段、输出路径、子任务关键状态继续增加投影列。
- 在热点接口 review 中明确禁止 `PublicationTask.result` 全量加载。
- 为关键列表接口增加冷请求耗时基准。

验收：

- 研报音频历史、MP3 编排历史、Celery Monitor、publisher 历史列表都使用投影列或轻量摘要。
- 典型列表冷请求有明确目标，例如 10 条任务小于 5 秒。
- 新增测试或脚本能检测响应体大小和是否误加载大字段。

### 4. 调度器和 worker 的生产恢复闭环

现状：

- 后端自举和 watchdog 已经能恢复托管 worker，并默认启动核心 `zsxq_auto` scheduler。
- 但 scheduler、worker、业务 backlog 三者之间仍需要更明确的恢复闭环。

建议：

- 为每个 scheduler 定义：是否核心、默认是否自启、负责哪些业务、关闭风险。
- Celery Monitor 同时展示 worker 状态、scheduler 状态、业务积压、最近失败原因。
- watchdog 不只看进程，还看消费者、队列积压、业务 pending age。
- 对人工 pause、排障关闭 scheduler、自动准入延迟做明确 UI 状态。

验收：

- 重启后可以一屏确认：核心 worker 在线、核心 scheduler 在线、积压是否健康。
- 人工 pause 后不会被自举或 watchdog 偷偷拉起。
- scheduler 未运行但核心业务依赖它时，页面有明确告警。

### 5. 平台凭据迁移完成

现状：

- 已有 `platform_credential_service` 和加密凭据表。
- 仍存在 `.env` 兼容回退和部分链路直接读环境变量的历史路径。

建议：

- 核心平台凭据统一走加密表：知识星球同步/发布、Bilibili 上传、后续浏览器自动化账号。
- `.env` 只保留开发兜底和迁移工具入口。
- 凭据解析必须记录 account_id、用途、最近使用、最近错误、是否过期。
- 认证失败与临时网络失败分开处理，避免误禁用账号。

验收：

- 生产路径不依赖 `.env` 中的 Cookie。
- 凭据页面能验证、导入、禁用、排序和查看脱敏摘要。
- 发布和同步邮件里能说明账号切换和凭据错误类型。

### 6. 发布成功判定可追踪

现状：

- 运行手册已要求不能把 submitted/reviewing 当作成功。

建议：

- 统一发布状态枚举：`submitted`、`reviewing`、`visible`、`failed`、`unknown`。
- 知识星球附件、Bilibili 上传、YouTube 上传都记录平台返回、验证尝试和最终确认状态。
- 邮件和 UI 只在 `visible` 或明确平台成功状态时写“成功”。

验收：

- 发布任务结果能说明提交数、已确认可见数、待审核数、失败数。
- 用户无需翻日志也能知道“提交了但还没确认”和“已确认发布成功”的区别。

## P1：提升可观测性和交付稳定性

### 7. 自动生成 API/队列/页面地图

建议：

- 生成 `docs/API_ROUTE_MAP.md`：从 Flask app 注册结果导出 endpoint、method、blueprint、url。
- 生成 `docs/CELERY_QUEUE_MAP.md`：队列、task name、worker 脚本、业务 feature、页面入口。
- 生成 `docs/FRONTEND_PAGE_MAP.md`：HTML、主 JS、API base 策略、导航入口。

验收：

- 新人不用 grep 全仓库也能找到入口。
- CI 或维护脚本能更新这些地图，避免文档漂移。

### 8. 统一任务事件日志

建议：

- 为所有编排写入统一 `result.logs[]` 结构：`time`、`step`、`message`、`level`、`artifact`。
- 子任务摘要统一字段：`job_id`、`feature`、`status`、`step`、`progress`、`error`。
- 邮件通知、前端历史、详情页复用同一套摘要函数。

验收：

- 任意父编排详情页都能按阶段读懂执行过程。
- 失败邮件和页面错误一致，不出现两个地方说法不同。

### 9. 测试分层和冒烟脚本标准化

建议：

- 每条核心业务线定义最小冒烟脚本：不依赖真实外部平台时用 mock，依赖真实平台时显式标记 manual。
- 对编排任务增加“续跑复用”和“子任务失败恢复”测试模板。
- 对热点接口增加响应体大小和冷请求耗时检查。

验收：

- 修改某条业务线时能快速知道该跑哪些测试。
- bug 修复记录中的验证命令可复用，而不是一次性文字。

### 10. 结构化观测指标

建议：

- 为 DB pressure、runtime guard deferred、队列长度、worker 消费者、任务 age、发布验证结果输出结构化指标。
- Celery Monitor 增加“风险面板”：老 pending、老 processing、deferred 任务、无消费者队列。
- 日志中关键事件使用稳定 tag，便于 grep 和后续接入 Loki/Promtail。

验收：

- 看到页面或日志即可判断是 DB 压力、worker 缺失、scheduler 未运行、上游平台失败还是业务 bug。

### 11. 文件保留和磁盘预算闭环

建议：

- 在现有 retention cleanup 基础上增加磁盘水位策略。
- 大文件产物登记引用关系：任务、同步 run、附件、输出目录。
- 清理前展示“会影响哪些任务下载/预览”。

验收：

- 磁盘接近阈值时有预警。
- 自动清理不会删除 pending/processing/failed 排障所需文件。

## P2：中长期结构治理

### 12. API 目录收敛

现状：

- `src/backend/apis/` 和 `src/backend/api/` 并存。

建议：

- 新接口优先进入 `apis/v1/` 或明确的新版本目录。
- 对旧 `api/` 模块建立兼容清单，标注是否保留、迁移或废弃。
- 不急于一次性搬迁，先禁止继续扩大双目录不确定性。

验收：

- 路由地图能标出 legacy API。
- 新增接口时 review 明确注册位置和兼容策略。

### 13. 前端 API 基址收敛

现状：

- 多数页面用 `env.js` / `CONFIG.API_BASE_URL`，少数页面硬编码 `localhost:5001`。

建议：

- 统一提供一个 `resolveApiBase()`。
- 页面不直接拼 `localhost`，只读统一配置。
- 为本地 Flask 静态挂载、独立 `8888` 静态服务和生产同源部署分别定义策略。

验收：

- 端口变化或生产部署不需要逐页改 HTML/JS。
- 前端页面地图能标出仍未收敛页面。

### 14. 配置和启动文档现代化

建议：

- 更新 `docs/README.md` 的快速开始，让它反映当前真实系统，而不是早期 YouTube Analytics。
- `DEPLOYMENT_GUIDE.md` 区分旧一键发布台启动和当前多队列生产启动。
- 为本地开发、准生产、生产部署分别列出最小环境变量。

验收：

- 新机器按文档能启动核心链路。
- 不再出现文档写 `8000/8080/5000` 但实际是 `8888/5001` 的混乱。

### 15. 业务模块边界沉淀

建议：

- 对每条业务线写一页 owner 文档：输入、输出、任务表字段、队列、外部依赖、失败恢复。
- 优先补：知识星球系统同步、研报音频编排、MP3 视频编排、publisher、内容清洗。

验收：

- 修改某条链路前能先读一页文档理解边界。
- 新增功能不会随手跨模块读写内部字段。

### 16. 前端从页面集合走向轻量模块化

建议：

- 不必立刻改 SPA。
- 先把共享 fetch、toast、状态 pill、分页、复制按钮、下载按钮等抽成稳定 JS 模块。
- 高风险页面先保持原状，新增页面复用模块。

验收：

- 历史列表、状态 pill、失败续跑按钮在不同编排页面表现一致。
- 修一个通用交互不需要改多份页面。

## 建议落地顺序

近期 1-2 周：

1. 完成运行时 reconciliation 第一阶段：stale pending 重投、父终态子任务收口、deferred 丢消息恢复、processing 心跳/超时规则。
2. 固化 DB 容量模型：PgBouncer/连接预算/worker 并发/backlog 上限/准入退避一张表。
3. 把研报音频和 MP3 视频的剩余长等待路径继续状态机化。
4. 梳理热点接口，补 `DATA_MODEL_HOT_PATHS.md`。
5. 统一 Celery Monitor 风险面板：scheduler、worker、业务积压、运行时不一致同屏。

中期 1 个月：

1. 生成 API/队列/页面地图，降低入口认知成本。
2. 平台凭据生产路径彻底迁移到加密表。
3. 发布确认状态统一落库和展示。
4. 核心链路冒烟测试和续跑测试模板化。
5. 更新部署和快速开始文档。

长期：

1. API 双目录逐步收敛。
2. 前端共享模块抽取。
3. 业务线 owner 文档补齐。
4. 将结构化指标接入统一监控栈。

## 维护规则

- 每完成一个 P0/P1 项，更新本文档状态或拆成更具体的任务文档。
- 如果生产事故暴露出新风险，先写 bug 记录，再决定是否提升为本文档的 P0。
- 不做没有验证标准的“优化”。每项优化都必须能说明验收方式。
