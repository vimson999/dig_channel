# ZSXQ Insights 冷聚合在查询后继续持有 Session

## 基本信息

- 状态：RUNTIME_REPLACED / OBSERVING；远端网络长尾仍 OPEN
- 严重级别：P0
- 影响模块：生财情报台 / zsxq_topics / PgBouncer / Session 生命周期
- 首次发现：2026-08-18 日常数据库巡检
- 根因分类：只读 Session 跨 CPU/缓存工作持有、远端结果抓取/回滚长尾
- 复发键：`DB-SESSION-LIFECYCLE`、`HOT-PATH-PAYLOAD-POLLING`
- 是否复发：是；是 `2026-08-09-zsxq-insights-cold-fulltext-transfer.md` 的同链路后续问题
- 防线层级：bounded_projection / explicit_read_scope / runtime_acceptance

## 修复前证据与根因

真实冷请求 `577298a3` 耗时 18.392s；同 request 的 connection lifecycle 为 13.790s，4 条 SQL 合计 4.363s、最大 2.162s，非 SQL 持有 9.427s，checkout origin 为 `_aggregate_themes`。现场同形 SQL `EXPLAIN (ANALYZE, BUFFERS)` 仅 34.558ms，排除 PostgreSQL executor 与缺索引为主因。

服务层虽然已把全文筛选留在数据库且仅为当前页加载截断详情，但同一 Flask scoped Session 在查询返回后仍贯穿 Python 聚合、排序、响应与缓存写入。

## 实际修复

新增显式只读 Session scope，并把主题统计、轻量匹配集、当前页详情、识别结果分别物化后立即 release；CPU 聚合和缓存写入只使用不可变 Row/dict。SQL `execute` 仍保留在各调用函数中，避免 origin 被公共 helper 吞掉。字段与当前页截断契约不变，未删除正文或审计数据。

## 运行态验证与剩余根因

- TDD 覆盖“只读 scope 最终 release 且 execute origin 保留调用者边界”；服务测试 10 个通过。
- 标准重启后真实冷请求 `b24437ad` 返回 200，SQL origin 正确落在 `_aggregate_themes`、`_load_selected_topics`、`_load_topic_page_details`；热缓存 HIT 为 3.70ms，health 0.51ms。
- 冷请求仍为 26.856s，并在当前页详情的结果抓取/事务回收阶段出现 13.253s checkout：SQL hook 3.684s、其余 9.569s。该段没有后续 CPU/缓存工作，且 executor 现场仅 0.137ms，证据指向 PgBouncer/远端网络传输或回滚往返；不能把它误写成 Session 代码已彻底解决。

## 防复发与下一轮验收

Insights 所有 DB 读取必须是显式有界 scope，scope 外禁止访问 ORM/Result。下一轮分别统计 execute、结果 fetch、rollback/checkin 和响应聚合耗时；要求应用 CPU 阶段 checkout=0，同时将远端 query/connect timeout 与 page-detail fetch 尾延迟降至零后才允许关闭网络根因。

## 2026-08-30 复发复核

真实冷请求 `21b8b432` 为 22.668 秒；详情 scope 持有 13.515 秒，其中对应 SQL 13.476 秒，
非 SQL 仅 38.53 毫秒。CPU 聚合/缓存阶段没有继续占用该 Session，说明显式 read scope 修复仍然有效；
但相同 SQL 的 executor 只有 1.069 毫秒，远端结果抓取/事务回收长尾再次出现。热请求
`29c7ab66` 为 0.81 毫秒只能证明 cache hit，不能证明冷链路根治。

状态保持 `RUNTIME_REPLACED / OBSERVING`，远端网络根因改记为 2026-08-30 再次复发并继续 OPEN。
下一轮必须以不同冷 key 分别捕获 execute、fetch 与 checkin；完整 24 小时无十秒级 fetch 尾部前，
不得以测试通过、热缓存或当前 PgBouncer 错误为零关闭。

## 2026-08-31 多冷 key 复核

真实页面依次切换 `ai`、`ai-content`、`ai-sales`、`agent-workflow`、`ai-coding` 五个冷 key，
端到端分别为 5.353/9.475/2.974/2.293/2.447 秒，P50/P95/max 为
2.974/9.475/9.475 秒；五次均返回完整真实数据。最慢 `ai-content` request `c51d2624` 中
selected SQL 为 6.761 秒、2085 行，详情 SQL 1.649 秒、48 行；连接持有 6.805 秒而非 SQL 持有
仅 43.76ms，证明 CPU/缓存阶段没有再次跨界持有 Session。相同 canonical 查询的现场 executor
为 36.513ms。

相较上一轮单冷 key 22.668 秒，最大值改善 58.2%，但 P95 仍超过 5 秒验收线，且同期其他真实页面
再次捕获 PgBouncer query timeout 与 Tailscale 30% 丢包。Session 边界代码保持
`RUNTIME_REPLACED`；网络相关冷链仍为 `OPEN / REPEATED / OBSERVING`，不得标记已解决。
