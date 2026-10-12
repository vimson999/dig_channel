# DB 巡检漏计彩色控制台请求日志

## 基本信息

- 日期：2026-09-25
- 状态：CODE_CHANGED / PRODUCTION_REPLAY_ACCEPTED / OBSERVING
- 严重级：P1
- 影响模块：`scripts/diagnostics/analyze_db_performance_logs.py`、HTTP 全链路基线、慢 SQL 与 request id 关联
- 根因分类：生产日志格式演进未进入解析合同
- 复发键：`REGRESSION-ACCEPTANCE-INTEGRITY`
- 是否复发：是；属于巡检器因日志格式变化静默漏报、造成验收假绿的既有问题簇
- 防线层级：production_sample_replay / parser_contract / audit_baseline

## 现象与影响

09-24 报告只统计到 16 个 HTTP 请求，本轮初次扫描甚至只识别 1 个请求，但 `logs/backend.log`
中存在大量成对的 request start/end。当前生产控制台行以 `🟢 [2026-09-24 10:18:40]` 开头，
旧 `REQUEST_START`/`REQUEST_END` 只接受无 emoji 且必须带毫秒的
`2026-09-24 10:18:40,123`。解析器没有报错，只是静默丢弃，因此 endpoint 请求量、P95、失败率、
低频高延迟和 request→SQL 关联都被低估。上一轮 HTTP 基线不具可比性，不能据此声称本轮流量暴增。

## 根因与已落地修复

通用时间戳正则同样遗漏 `🟢`；请求专用正则还假设时间戳必有逗号毫秒。修复把彩色 emoji 前缀纳入
通用时间戳，并抽出请求时间戳前缀，同时兼容方括号、无毫秒、逗号或点号毫秒。没有放宽 request id、
method、path、status 或 duration 的业务字段约束。

## 验证

- TDD RED：真实彩色 start/end 样本均无法匹配。
- GREEN：新增生产样本合同通过；分析器完整单测 32/32 通过。
- 真实日志回放：固定窗从错误的 1 条恢复为 305 条完整请求、0 条 incomplete、88 个端点；303 成功、
  2 个失败均为 `/favicon.ico` 404，P50/P95/max 为 2.49ms/2.009s/6.040s。
- request `07585b10` 被正确关联到 `/api/mp3-video-workflows/audio-assets` 6.040s、同 request id 的
  canonical SQL 5.692s 和 Session hold 5.757s。

## 复发预防与后续验收

- 请求日志的 plain、ANSI/emoji、带/不带毫秒四类形态必须使用真实生产行进入合同测试。
- endpoint 总量为 0 或与 API access 日志数量级显著背离时，巡检必须显式报 coverage anomaly，不能生成“无问题”结论。
- 当前修复是按真实日志重放验收，分析器不是常驻服务，无需重启；下一完整窗口仍需确认请求覆盖持续非零，
  并把 access/API/request-logger 三路计数差异作为观测项。未完成自动 coverage 告警前保持 OBSERVING。

## 2026-09-26 覆盖复核

固定窗分析结果为 0 个 request start/end；原始 backend 日志同期持续输出 5 分钟 performance heartbeat，
但确实没有 request logger 或 Werkzeug 访问行。边界后 `06:04:09` 的 `/health` 被同一解析器正确识别为
200/2.45ms，窗后四个 ZSXQ 只读端点也能正常关联 request id，因此本窗 0 请求不是格式回归。

由于没有自然 HTTP 流量，本窗仍按 coverage anomaly 报告，不能生成页面/API 的 P50/P95 或把 0 样本解释为
无问题。修复保持 **PRODUCTION_REPLAY_ACCEPTED / OBSERVING**；下一轮仍须用自然 access/API/request-logger
三路数量级对账，自动 fail-loud coverage 门禁尚未落地。

## 2026-09-27 覆盖复核

固定窗识别到 6 个完整请求，但全部是上一轮在 06:04–06:13 主动执行的 health 与四个 ZSXQ 只读烟测，
不是自然业务流量；其后没有自然 request/Werkzeug 行。解析器能把 request `1f9c837b` 与 Insights
3.121 秒请求及 1.607 秒 SQL 正确关联，说明彩色格式漏计未复发；但自然页面/API 基线仍为空。

本轮 06:10 后再次执行只读运行态烟测：health 200/41ms，Insights 冷 MISS 200/3.111s、同参数暖 HIT
200/31ms，topics/local 200/474ms，tabs 200/236ms，sync/status 200/530ms。它们只证明当前链路可达和
缓存生效，不替代自然全站流量。状态保持 **PRODUCTION_REPLAY_ACCEPTED / OBSERVING**。
