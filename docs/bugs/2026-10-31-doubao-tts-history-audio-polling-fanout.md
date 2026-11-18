# 2026-10-31 豆包 TTS 历史轮询触发音频请求风暴

## 基本信息

- 状态：RUNTIME_REPLACED / OBSERVING
- 严重级别：P1
- 影响模块：豆包 TTS 历史页、`/api/v2/tts/history`、历史音频资源接口
- 首次发现：2026-10-31
- 修复提交：当前工作区
- 根因分类：频繁轮询 / 大字段传输 / 浏览器媒体预加载 / 失效资源投影
- 复发键：`TTS-HISTORY-POLLING-AUDIO-FANOUT`
- 是否复发：首次按该指纹登记
- 关联记录：`2026-10-16-content-pipeline-statistics-polling-query-timeout.md`
- 防线层级：shared_guardrail / contract_test / runtime_acceptance

## 现象

固定窗内 `GET /api/v2/tts/history` 52 次、累计 65.424 秒，触发 25 条同形慢 SQL、累计
33.352 秒；页面同时发出 890 次历史音频请求，其中 862 次返回 404，失败率 96.85%。该组合使全站
HTTP 固定窗失败率达到 55.04%。

## 影响

无活动任务时页面仍每 5 秒读取最近 20 条任务的 `params/result`，并重复创建音频元素；浏览器随即访问已经被
保留策略清除的历史文件。结果是数据库/网络大字段传输、无效资源请求和前端噪声被持续放大，但原始任务 JSON
和审计数据本身没有丢失。

## 根因

前端构造器无条件 `setInterval(loadHistory, 5000)`，历史渲染又为每个结果生成 `<audio src>`。浏览器会主动
探测资源，失效历史路径因此形成 404 风暴。生产只读 `EXPLAIN (ANALYZE, BUFFERS)` 使用
`ix_publication_tasks_feature_user_created_desc`，20 行 executor 仅 0.066ms，但载荷 132,143 bytes；所以
主因不是 PostgreSQL executor 或缺索引，而是无界轮询、大 JSON 传输和媒体请求扇出。API 还把磁盘不存在的
`audio_path/subtitle_path` 投影为可访问 URL，使失效状态继续暴露给浏览器。

## 解决思路

保留任务原始 JSON 审计字段，只收紧读取与公开投影：轮询只服务活动任务，后台页停止；媒体不预加载；资源不存在
时仍保留成功计数，但不发布失效 URL；数据库分页完成后立即释放 Session，再做文件系统检查和序列化。

## 实际方案

- `src/frontend/js/doubao-tts.js` 复用 visibility-aware、single-flight 轮询器；仅在存在
  `pending/processing` 项时启动，全部终态立即停止。
- 历史与详情音频元素统一 `preload="none"`。
- `src/backend/api/tts_v2.py` 只为真实存在的文件生成 URL，并在文件系统 I/O 前 `db.session.remove()`；不删除、
  不回填原始 `params/result`。
- `src/frontend/tts.html` 显式加载共享轮询门禁并更新静态版本。

## 验证

- 定向契约覆盖活动/终态轮询、页面可见性、single-flight、`preload=none`、资源存在性与 Session 释放。
- 完整测试：1,844 passed、10 skipped、374 subtests；`compileall` 与代码门禁随后复核。
- 标准重启前安全门确认无不可中断任务；重启后 backend health 正常、抽查 worker running 且队列为 0。
- 真实浏览器打开 `/tts.html`：终态历史只显示 2 个真实存在的音频控件，均为 `preload=none`，旧任务不再显示
  失效音频；页面仅请求一次 history，未再发出对应 segment audio 请求。

## 复发预防

历史/状态页不得用固定定时器无条件读取宽 JSON；必须以活动状态、可见性和 single-flight 为门禁。媒体资源接口
不得把不存在的本地文件投影成 URL，且任何文件系统检查必须发生在 Session 释放之后。

## 后续事项

当前只能标记 RUNTIME_REPLACED/OBSERVING。下一完整 24 小时窗要求：无活动任务时 history 不持续轮询、历史
音频 404 风暴为 0、该 SQL 指纹不再由页面周期放大；若仍出现，按 request id 继续追踪调用页与缓存版本。

## 2026-11-01 后窗复核

固定窗内仅捕获 06:08 上一轮真实浏览器验收产生的 1 次 history，请求 972.23ms，其中同指纹 SQL
821.61ms、未达到慢 SQL 阈值；之后 history 周期请求、segment audio 请求和音频 404 均为 0，上一窗的
52/890/862 风暴没有在已捕获流量中复发。

但固定窗没有新的 TTS 自然用户任务，页面是否长期保持打开也没有独立证据；零请求可能是低暴露，不足以把修复
升级为 CLOSED。状态保持 **RUNTIME_REPLACED / OBSERVING**；下一轮优先在真实活动任务和终态停留场景复核
active→terminal 后只允许一次最终刷新、随后 history/segment fan-out 均为 0。
