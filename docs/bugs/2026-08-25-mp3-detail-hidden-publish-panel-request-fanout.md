# MP3 详情页隐藏发布面板扇出凭据与上传记录请求

## 基本信息

- 状态：RUNTIME_REPLACED / OBSERVING；静态资源已替换，待真实浏览器流量与下一完整窗口验收
- 严重级别：P0
- 影响模块：MP3 视频编排详情 / 平台凭据 / 抖音与视频号上传历史 / PostgreSQL
- 首次发现：2026-08-25 每日数据库性能巡检
- 修复提交：当前工作区
- 根因分类：隐藏面板提前挂载、请求扇出、同源凭据重复读取
- 复发键：`HOT-PATH-PAYLOAD-POLLING`
- 是否复发：是；同属隐藏页面/隐藏面板无用户收益却持续访问热点 API 的复发模式
- 关联记录：`2026-08-20-report-script-material-hidden-worker-polling.md`、`2026-08-16-hidden-status-pages-unbounded-polling.md`、`2026-08-02-request-auth-duplicate-user-query.md`
- 防线层级：lazy_mount / request_budget / behavioral_contract / runtime_observation

## 现象与影响

固定窗口 `2026-08-24 06:02:19` 至 `2026-08-25 06:02:19` 内：

- `GET /api/v1/platform-credentials` 188 次、累计 142.744 秒、P95 2.648 秒、最大 16.993 秒；
- MP3 job detail 36 次、累计 59.943 秒、P95 6.918 秒、最大 8.388 秒；
- 隐藏发布面板仍产生抖音上传历史 50 次、视频号上传历史 50 次、B站上传历史 34 次；
- 平台凭据同形 SQL 在仅 19 行的表上 executor 现场仅 0.067ms，但应用 SQL P95 4.451 秒、最大 14.687 秒，排除缺索引为主因。

真实 request cluster `b4243f35` 显示：详情请求完成后，页面在约一秒内再并发发起三条平台凭据请求，并读取当时不可见的抖音、视频号上传历史。详情页自己的 `loadBiliCredentials()` 还与 B站面板的 `loadCredentials()` 重复读取同一凭据集合。连接与网络长尾使一次无界扇出被放大到秒级。

## 根因

`Mp3VideoWorkflowDetailPage.render()` 无条件执行 `mountBilibiliPanel()`、`mountDouyinPanel()` 和 `mountWechatChannelsPanel()`。共享标签页只通过 CSS 隐藏非活动面板，没有控制其数据加载生命周期；因此“未展示”不等于“未请求”。B站面板又不知道页面已在同一加载批次取得凭据，形成第四条同源凭据请求。

这不是 PostgreSQL executor 慢、索引不足或字段裁剪问题，而是前端组件生命周期违反“只为当前可见能力付出 I/O”的请求预算。

## 实际修复

- 新增活动发布面板分发器，初次渲染只挂载默认 B站面板；抖音和视频号只在用户切换标签时惰性挂载。
- 切换标签前保存当前面板草稿，重新进入标签时恢复草稿，不牺牲字段或交互完整性。
- 将详情页已经加载成功的 B站凭据注入 B站面板并标记已加载；首次读取失败时不标记成功，仍允许面板重试。
- 更新详情页静态资源版本，避免新页面继续命中旧脚本。
- 新增真实类行为契约，验证只挂载活动面板、切换前保存草稿，以及凭据不得重复加载。

## 验证

- TDD 红灯：旧实现因不存在活动面板分发器而明确失败；修复后 `tests/unit/test_publish_platform_tabs.py` 6 项通过。
- Node 行为契约 `mp3_publish_lazy_mount_contract.js` 通过；`node --check` 通过。
- 本地运行服务返回的新 HTML 已包含 `20260825-publish-panel-lazy-mount-v1`，证明静态运行边界已替换；本轮无需重启 backend/Celery/scheduler。
- 尚未取得真实浏览器打开新版本后 request id 级别的“1 个详情 + 1 个 B站凭据、0 个隐藏平台历史”证据，因此不能标记 CLOSED。

## 复发预防与下一轮验收

- 标签页、折叠区或隐藏面板的数据请求必须由可见/活动生命周期驱动；CSS 隐藏不能作为 I/O 门禁。
- 页面级请求预算契约：首次 MP3 详情加载最多 1 次任务详情和 1 次 B站凭据请求；未切换平台前抖音/视频号凭据与历史请求必须为 0。
- 下一轮按时间与 request id 聚类；若仍出现一次详情后 3–4 条凭据或隐藏平台历史请求，继续追静态资源版本、旧浏览器实例和其他 mount 入口。
- 后续完整窗口确认上述扇出指纹为 0，且平台凭据请求量相对详情访问量符合真实标签切换后，方可 CLOSED。
