# 研报基础文案物料页在后台持续刷新 Celery 状态

## 基本信息

- 状态：CLOSED；2026-08-25 后续完整窗口确认隐藏态分钟级序列未复发
- 严重级别：P0
- 影响模块：研报基础文案物料台 / Celery status / Page Visibility / PostgreSQL
- 首次发现：2026-08-20
- 修复提交：当前工作区
- 根因分类：隐藏页无界轮询、共享防线横向覆盖遗漏
- 复发键：`HOT-PATH-PAYLOAD-POLLING`
- 是否复发：是；视频编辑器等页面已使用共享 Visibility controller，但本页仍保留直接 `setInterval`
- 关联记录：`2026-08-18-video-editor-hidden-polling.md`、`2026-08-16-hidden-status-pages-unbounded-polling.md`、`2026-08-11-celery-status-auth-session-held-across-inspect.md`
- 防线层级：shared_guardrail / contract_test / runtime_observation

## 现象

固定 24 小时窗口内 `/api/v1/celery/status` 从上轮 5 次增至 809 次，累计
1750.319 秒，P95 9.352 秒、最大 21.372 秒；其中 109 次匹配慢 SQL，累计
210.626 秒。页面 17:27 打开后约每 12 秒轮询，浏览器隐藏节流后仍稳定为每小时
60 次，直至次日巡检窗口结束。

## 影响

- Celery 控制面成为本轮 HTTP 累计耗时第一名，占全部 HTTP 累计耗时 77.6%。
- 单次最慢 request `777c1124` 为 21.372 秒；鉴权 SQL 约 1.480 秒，其余约
  20 秒是控制面、普通 SQL、连接/网络等待，不能归因于单条 PostgreSQL executor。
- 隐藏页没有用户可见收益，却持续放大数据库、Redis/Celery 状态汇总和网络长尾。

## 根因

`ReportScriptMaterialWorkflowPage.resetPollTimer()` 直接创建 12 秒 `setInterval`，
不检查 `document.hidden`，没有 visible immediate refresh、single-flight 或 unload cleanup。
此前共享防线只覆盖了已知状态页和视频编辑器，没有契约扫描本页，属于复发防线横向遗漏。

## 解决思路

- 复用共享 `VisibilityPolling` controller，隐藏时完全暂停，重新可见时立即刷新。
- 将 worker status 和仅在 active job 时需要的 history 刷新合并到同一 single-flight poll。
- 页面销毁时停止 controller，避免旧 timer 泄漏。

## 实际方案

- HTML 在业务脚本前加载 `js/visibility-polling.js`，并更新 cache-bust 版本。
- 新增 `startReportScriptMaterialPolling()`，由共享 controller 统一管理 12 秒周期、
  visibility 事件、single-flight 与 beforeunload cleanup。
- 新增针对真实 HTML 与业务脚本的 Node 契约：hidden 不触发 API/定时器，visible
  只触发一次并恢复周期，避免以后再次绕过共享防线。

## 验证

- TDD：新契约先因函数缺失失败，修复后通过。
- 真实 backend `/health` 为 healthy；Celery status 显示 broker 正常、17/17 服务运行。
- 页面、共享 helper 与业务脚本均由新 backend 返回 200；真实页面渲染出“物料队列在线”。
- 安全门通过后执行标准整栈重启，新 backend 与 worker 已加载。
- 旧浏览器实例在重启后仍继续分钟级请求，因为后端重启不会替换已加载到页面内的旧 JS；
  四个可控 Chrome 配置中均未暴露该标签页，故本轮不伪造 hidden 运行态通过证据。

## 复发预防

- 所有新增周期页面必须使用共享 Visibility controller；禁止直接新增无 visibility、
  single-flight、unload cleanup 的 `setInterval`。
- 前端契约必须读取实际 HTML 与实际业务脚本，而非仅测试复制出来的 helper。
- 下一轮按页面路径、request id 与时间序列优先复核 Celery status；旧实例退出后 hidden
  请求应为 0，visible 每 12 秒且不重叠。

## 后续事项

- 旧页面未退出前状态保持 OBSERVING；不能把代码测试或新页面加载写成已解决。
- 下一完整 24 小时要求 `/api/v1/celery/status` 不再出现隐藏态每小时 60 次的序列；若仍出现，
  继续追调用页和静态资源版本，而不是继续调整 API 超时。

## 2026-08-25 后续窗口验收

固定 24 小时窗口只记录 3 次 `/api/v1/celery/status`，累计 6.237 秒、P95/最大
2.369 秒，时间点为 09:18、10:34、17:37；不再存在上一轮从页面打开后每 12 秒、
隐藏后每分钟一次的连续序列。相对上一轮 809 次/1750.319 秒，次数下降 99.6%、
累计下降 99.6%。后续完整窗口已满足“不复发”条件，本记录关闭；共享热点簇仍因其他
页面扇出问题保持 OPEN。
