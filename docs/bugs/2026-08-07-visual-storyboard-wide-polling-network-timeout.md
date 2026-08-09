# 火柴人分镜轮询误回读任务大字段并放大网络超时

## 基本信息

- 状态：新后端已加载并通过真实 API 验收，待下一完整日志窗口确认不复发
- 严重级别：P0
- 影响模块：火柴人视觉视频详情 / publication_tasks / PgBouncer / PostgreSQL
- 首次发现：2026-08-07
- 修复提交：当前工作区
- 根因分类：高频状态轮询、错误的任务类型判定、大字段兼容回退、隐藏标签页未停轮询、PgBouncer/网络长尾
- 复发键：`HOT-PATH-PAYLOAD-POLLING`
- 是否复发：是；既有防线已经要求轮询使用窄投影、single-flight 和 Page Visibility，但 workflow 分镜使用了不同的 `parent_feature`，绕过了轻量分支
- 关联记录：`2026-07-21-visual-plan-completed-detail-repeat-query.md`、`2026-07-22-terminal-polling-image-repair-payload-recurrence.md`、`2026-08-05-report-audio-hidden-tab-polling-n-plus-one.md`
- 防线层级：local_patch / contract_test / runtime_verification / observability

## 现象

- 2026-08-06 12:55:42 至 2026-08-07 06:02:19，分镜 job 端点 1,759 次，成功/失败 1,757/2，P50/P95/最大 0.329/2.505/62.447 秒，累计 1,529.272 秒，占该窗口 HTTP 累计耗时 47.1%。
- `api/report_visual_plan.py:824:_get_owned_visual_task` 的宽 `publication_tasks.params,result` 指纹 48 次，P50/P95/最大 3.579/29.418/60.192 秒，累计 318.223 秒。
- request `f3a215e9` 和 `8b7976fc` 均在约 60 秒后因 `server closed` 返回 500；同秒 PgBouncer 记录 query timeout，Session 持有约 61 秒。

## 影响

- 活动分镜详情页每 4 秒轮询一次，单个长任务可累计上千次请求；后台标签页也持续占用连接。
- 60 秒网络/PgBouncer 长尾被重复宽字段回读放大为用户可见 500，并挤占其他 API 和 worker 的连接预算。
- 实时 `EXPLAIN (ANALYZE, BUFFERS)` 对相同主键全行查询只有 0.032ms，增加索引不能解决该问题。

## 根因

分镜任务实际以 `parent_feature=report_stick_figure_video_workflow` 创建，但 job 详情路由只把字面值 `storyboard` 视为轻量分镜。输出尚未生成时，路由先读一次窄任务投影，再执行兼容回退，重新物化 `params/result`。前端同时以 4 秒间隔持续轮询，且未在页面隐藏时暂停。真实请求的 SQL executor 为毫秒级，60 秒失败与同秒 PgBouncer query timeout 对齐，说明数据库索引不是主因；错误分支和轮询乘数是应用可修根因，剩余链路长尾属于连接/网络层风险。

## 解决思路

- 按实际业务 feature 集合识别分镜任务，活动态始终使用窄投影，禁止兼容回退物化 JSON。
- 轮询改为 single-flight、页面隐藏暂停、可见后恢复，并降低活动分镜轮询频率。
- 保留所有业务字段和原始 JSON；不通过删除审计数据或虚构结果规避问题。

## 实际方案

- 后端新增 `_STORYBOARD_PARENT_FEATURES`，同时覆盖 `storyboard` 与 `report_stick_figure_video_workflow`；无输出时直接用 `_storyboard_job_payload(..., include_segments=False)`。
- 前端增加 `pollInFlight`、`visibilitychange` 门禁，并把活动分镜轮询间隔从 4 秒调整为 15 秒。
- 更新静态资源 cache-bust；增加行为测试，若 workflow 分镜触发 `_job_payload` 或第二次宽任务查询则立即失败。

## 验证

- 定向 150 tests 通过，前端 JavaScript 语法检查通过。
- 重启安全门检查 47 个活动 publication task 和 0 个活动 ZSXQ sync，无不可中断任务；开发栈完成重载，backend health 正常，两个 report visual plan worker 与 ZSXQ worker 均 ready。
- 真实调用 `GET /api/report-visual-plans/jobs/e1916b67-c852-4de4-9251-a6d5fa9b0133` 返回 200；应用耗时 898.91ms，SQL 仅为 users 546.51ms、publication_tasks 窄投影 26.09ms、output 26.18ms，没有 `params/result` 回读。
- 该证据只证明新进程已替换和单次真实 API 路径正确；下一完整窗口前保持 OBSERVING，不能标记 CLOSED。

## 复发预防

- 分镜任务所有合法 parent feature 必须共享同一个轻量状态 DTO；新增 workflow feature 必须更新行为测试。
- 所有持续轮询必须同时具备 single-flight、Page Visibility 暂停、终态停止和不短于 10 秒的默认周期。
- 每日巡检按端点累计耗时、宽 SQL origin、PgBouncer timeout 和 Session 持有联合验收，不能只看慢 SQL 数量。

## 后续事项

- 下一完整 24 小时窗口确认该端点不再出现 `api/report_visual_plan.py:_get_owned_visual_task` 的宽 `params/result` 指纹，P95 不再出现 60 秒长尾。
- 若轻量端点仍有 60 秒失败，继续检查 PgBouncer server connection、网络出口和 PostgreSQL socket，不以重试或新增索引掩盖。
- 在下一条真实活动分镜上用可见/隐藏页面验证 15 秒周期和隐藏暂停；当前历史失败任务只能验收 API 查询形态。
