# ZSXQ Insights 冷查询把全量正文跨网络搬到应用

## 基本信息

- 状态：代码已部署并完成真实冷/热 API 验收；`RUNTIME_REPLACED/OBSERVING`
- 严重级别：P1
- 影响模块：生财有术 Insights overview / `zsxq_topics` / PostgreSQL 网络传输
- 首次发现：2026-08-02 的 overview 冷请求长尾；2026-08-09 定位根因
- 修复提交：当前工作区
- 根因分类：大字段读取 / 超大结果集 / PostgreSQL executor 与应用耗时分离
- 复发键：`HOT-PATH-PAYLOAD-POLLING`
- 是否复发：是；此前冷请求曾达 8.179s、8.582s，本轮真实请求达 14.920s
- 防线层级：data_access / pagination_guardrail / runtime_acceptance

## 现象

真实 request `25115ce7` 的 `/api/v1/zsxq-insights/overview` 冷请求耗时
14.920s，其中 `_load_selected_topics` 单条 SQL 为 12.319s，连接持有 14.890s。
同样参数的 SQL 返回 396 行、约 758,615 字节；页面最终只展示 30 条、响应约 45KB。

## 影响

- 冷缓存或进程重启后的首个用户承担整批正文传输成本，并逼近 PgBouncer 20 秒 query timeout。
- 数据库连接在整个传输和 Python 处理阶段被占用，放大并发请求的连接池等待。
- 仅看应用 SQL 耗时容易误判为 regex 或缺索引，导致无效 DDL。

## 根因

`_load_selected_topics` 在 PostgreSQL 内用完整 `title/content/question/answer/article` 文本做
主题、需求、成果和搜索匹配，这是保持语义完整性所需；但查询随后把每个匹配主题的多段截断正文
全部传回应用，再在 Python 中统计趋势、子主题、作者并分页。

`EXPLAIN (ANALYZE, BUFFERS)` 显示 executor 仅 85.817ms、无磁盘读，应用却耗时
13.973s；故根因是约 759KB 大字段跨 Tailscale/PgBouncer 传输，不是 executor、锁或缺索引。

## 解决思路

保持全文只在 PostgreSQL 内参与完整匹配，第一条查询只返回 topic id、作者、时间和数据库计算的
需求/成果/子主题布尔摘要；分页后第二条查询仅按当前页 topic ids 读取详情。所有正文和原始审计字段
继续保存在数据库中，详情接口不变，完整结果可逐页取得。

## 实际方案

- `_load_selected_topics` 改为返回轻量摘要，需求/成果/子主题标记仍基于数据库中的完整正文计算。
- 新增 `_load_topic_page_details`，只为当前页读取详情字段。
- Insights `page_size` 硬上限由 5000 收紧为 100，防止调用方绕过分页重建大传输。
- `_subtopic_breakdown` 使用数据库完整正文生成的匹配标记，避免缩短正文造成语义损失。

## 验证

- 修复前同 SQL：executor 85.817ms；应用 13.973s；396 行；758,615 bytes。
- 修改后只读服务冷构建：2.863s；轻量摘要 392 行/484.11ms，当前页 30 行/207.19ms；
  输出 28,641 bytes；热构建 0.743s。
- 新 PID `56236` 的真实 API：冷请求 request `919cb94c` 为 2.793s（curl 2.854s），
  热请求 request `40651068` 为 2.32ms（curl 113.9ms）；均 HTTP 200，30 条页面数据完整返回。
- 分页契约：page 1/2 各 30 条且 topic id 重叠为 0；`page_size=5000` 被收紧为 100。
  page 2 在底层网络抖动时仍耗时 5.577s（其中用户鉴权 2.565s、页详情 2.876s），证明
  本次大字段根因已替换但公共数据库网络问题仍需独立观察。
- 定向单测 14 passed；完整验证见当日巡检报告。

## 复发预防

- 热点页面的“筛选全集”和“展示当前页”必须拆开预算；完整匹配不等于传输完整正文。
- executor 小于 100ms而应用超过 1s且结果集含文本/JSON时，必须记录行数与传输字节，再判断索引。
- Insights 单页最多 100 条；前端继续使用 30 条分页，禁止以扩大 page size 代替翻页。
- 下一完整 24 小时必须复核 overview 冷请求与翻页 P95/max、该 SQL 返回规模和 PgBouncer timeout；
  未经后续窗口不标记 CLOSED。
