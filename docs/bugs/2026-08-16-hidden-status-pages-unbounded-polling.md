# 后台状态页持续轮询并放大 API 与数据库往返

## 基本信息

- 状态：CODE_CHANGED / OBSERVING
- 严重级别：P0
- 影响模块：Celery Monitor / ZSXQ 系统同步页 / ZSXQ Lab / publication_tasks / zsxq_sync_runs / PgBouncer
- 首次发现：2026-08-16 日常数据库巡检
- 修复提交：当前工作区，前端静态资源已由真实页面加载
- 根因分类：后台标签页轮询、请求重叠、跨端点扇出
- 复发键：`HOT-PATH-PAYLOAD-POLLING`
- 是否复发：是；与 `2026-08-05-report-audio-hidden-tab-polling-n-plus-one.md` 同类，新的状态页面未复用 Page Visibility 门禁
- 关联记录：`2026-08-03-zsxq-scheduler-config-polling-roundtrip.md`、`2026-08-11-celery-status-auth-session-held-across-inspect.md`
- 防线层级：shared_guardrail / contract_test / runtime_acceptance

## 现象与排序依据

固定窗口 `2026-08-15 06:02:19` 至 `2026-08-16 06:02:19`：

- `/api/v1/celery/status` 783 次，累计 1835.735s，P95 10.117s，最大 26.202s；同时 `/api/v1/celery/watchdog/status` 778 次。Celery 页面两个端点来自同一轮刷新，浏览器跨域预检还产生同量级 OPTIONS。
- ZSXQ 系统同步页四组轮询端点合计 1475 次：`system-sync/status` 369、`batches` 370、`auto-publish/status` 368、`auto-publish/runs` 368；累计约 1417.754s。
- Celery 两个端点在 12.8 小时内约 780 次成对出现，日志中位间隔 60s；系统同步页有一段 278 次、持续约 4.3 小时且中位间隔 60s，另有前台窗口恢复为 15s。Chrome 对后台 timer 的节流解释了 60s 间隔，但没有停止网络请求。
- `/api/v1/celery/status` 单次最大 26.202s、系统同步 POST 最大 17.197s，超过 15s 的固定 interval；旧实现没有 in-flight 门禁，可在网络长尾时重叠。

按累计耗时、P95、最大耗时和受影响页面排序，该问题高于本轮任何单一 SQL 指纹。所有请求成功并不代表没有问题：它们持续消耗 HTTP、鉴权、业务快照、PgBouncer 和远端数据库预算。

## 根因

三个页面分别直接使用 `setInterval`：Celery Monitor 每 15 秒刷新完整状态与 watchdog；ZSXQ 系统同步页每 15 秒并行调用四组接口；ZSXQ Lab 也独立轮询 scheduler 状态。它们没有共享 Page Visibility 生命周期，也没有等待上一轮完成。

因此一个用户留在后台的页面仍可无限期发请求；多个页面或同页长尾又会重叠。PostgreSQL executor 现场为毫秒级，主要成本是跨页面的请求扇出、broker inspect、鉴权/业务 SQL 与 PgBouncer/网络等待，不是缺索引。

## 实际修复

- 新增 `src/frontend/js/visibility-polling.js`，统一实现隐藏页停止 timer、恢复可见时立即刷新、同 controller in-flight 合并和 unload 清理。
- Celery Monitor、ZSXQ 系统同步页与 ZSXQ Lab 改用该 controller；系统同步的一轮四组请求以 `Promise.allSettled` 作为同一 in-flight 生命周期。
- 三个 HTML 入口显式加载带版本号的新 helper 和页面脚本，避免浏览器缓存继续执行旧轮询。
- 不牺牲任何响应字段，不删除原始 JSON，不改变前台可见时 15 秒刷新语义。

## 验证

- TDD：`tests/frontend/visibility-polling.test.cjs` 在 helper 不存在时先失败；修复后验证 hidden 不建 timer、visible 立即刷新、并发 tick 合并、再次隐藏清理和 stop 解绑。
- 定向测试：Node 生命周期测试通过；四个 JavaScript 文件 `node --check` 通过；数据库投影与前端契约 `99 passed, 3 subtests passed`。
- 运行态：真实 Chrome Celery Monitor 页面已重新加载 `visibility-polling.js?v=20260816-db-polling-guard` 与新页面脚本，状态接口返回 200，页面正常渲染。前端静态资源无需重启 backend/Celery/scheduler。
- 未闭环：修复发生在本窗口结束后；浏览器自动化无法可靠把被接管标签页置为 `document.hidden=true`，hidden 行为由确定性 Node 测试覆盖。尚无完整后续窗口证明后台请求归零。

## 防复发契约

- 所有周期页面轮询必须使用共享 Visibility controller，禁止页面直接新增无门禁 `setInterval`。
- controller 必须同时具备 hidden pause、visible refresh、in-flight single-flight 和 stop/unload cleanup。
- 巡检按页面产生的全部端点总累计耗时排序，并统计 OPTIONS；不能只看单个 GET 成功率或单条 SQL。

## 下一轮验收

- 以新静态资源加载时点为界，后台 Celery/ZXSQ 页面对应业务 GET/POST 和 OPTIONS 应停止增长；恢复前台后只允许一次立即刷新，再保持不快于 15s 且无 overlap。
- `/api/v1/celery/status` P95 需低于 10s；若单次刷新仍超过 10s，继续拆分 broker inspect、进程扫描和业务 SQL，不得用本次轮询降频掩盖单次长尾。
- 观察完整 24 小时无后台轮询复发后，才允许由 OBSERVING 转 CLOSED。
