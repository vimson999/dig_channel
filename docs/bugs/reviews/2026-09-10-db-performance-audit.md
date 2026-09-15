# 2026-09-10 数据库性能与错误巡检

## 结论

固定窗口为 `2026-09-09 06:00:00` 至 `2026-09-10 06:00:00`（Asia/Shanghai）。数据库错误、
traceback SQL episode、锁等待、死锁、statement/lock/query/connect timeout 与 PgBouncer 六类错误均为 0；
149 条应用慢 SQL 累计 `244.641s`，P50/P95/max 为 `1.300/2.938/9.645s`。相较 2026-09-08
基线的 280 条/`702.212s`，数量下降 46.8%、累计耗时下降 65.2%、P95 下降 63.2%；但相较桥接窗口
2026-09-08 06:00 至 09-09 06:00 的 27 条/`45.481s`，数量与累计耗时分别回升 451.9%/437.9%。

没有证据支持新增索引、调高超时或修改 PgBouncer 参数。代表 SQL 的 PostgreSQL executor 为
`0.027–35.522ms`，而应用端可达数秒；共同根因仍是远端链路/驱动往返长尾与少量应用连接租约放大。
网络簇保持 `OPEN / REPEATED / OBSERVING`。

本轮唯一代码修复是巡检完整性：分析器漏掉了 runtime guard 的连字符 offender 格式。TDD 修复后，
同窗正确报告 31 个短暂观测，最大 3 连接、事务年龄 `0.779s`、idle 年龄 `0.562s`；没有准入 defer，
不能把它们误写成长事务事故。

## 数据范围与基线完整性

- 主窗口：09-09 06:00 至 09-10 06:00；桥接窗口：09-08 06:00 至 09-09 06:00。
- 当前分支缺少 09-06 至 09-08 的 review 文件，因此跨日比较使用自动化 memory 中 09-08 的已核验基线，
  并额外扫描桥接窗口，避免直接把缺档解释为业务归零。
- 来源覆盖 app/backend/scheduler/Celery、publication 专用 SQL、PgBouncer 轮转日志和只读生产快照。
- 所有数据库检查使用只读事务；未执行 DDL、DML、VACUUM FULL、REINDEX 或配置修改。

## 慢 SQL 与请求长尾

### 慢 SQL

- 149 条、累计 `244,641.43ms`，全部来自托管应用，无临时诊断混入。
- `publication_tasks` 87 条；其余指定目标表没有超过阈值的慢 SQL。
- 10、12–16 时共 139/149 条，是主要峰段。
- 最大聚合来源包括：火柴人分镜资产投影 13 次/`26.489s`，Publisher pending repair
  17 次/`22.790s`，通知 due provider 14 次/`22.452s`，ZSXQ all-tabs 配置 8 次/`16.416s`，
  MP3 load-task 9 次/`16.036s`。平台凭据窄更新有一次 `9.645s`，是单条最大值。

### HTTP

- 928 请求，926 成功、2 个 4xx，服务端失败率 0；全口径非 2xx 为 0.22%。P50/P95/max 为
  `7.29ms/3.135s/10.379s`。
- 两个非 2xx 分别为 render 安全重启冲突 `409` 和 B 站 seasons 参数错误 `400`，均非 DB 失败。
- `POST /content-pipeline/web-submit` 4 次全部 202，累计 `29.575s`，但没有超过 1 秒的 SQL；
  主要时间在文件处理、通知与多次远端往返，不是单条 executor 或缺索引。

## 后台任务与业务终态

- Celery：236 完成、4 失败、0 retry。失败均为 B 站外部业务/凭据问题：充电档位不可用、403 风控/登录、
  两次缺少 `SESSDATA/bili_jct`。四条 Publisher 数据库记录均已 durable 收口为 `failed/failed`，没有假成功或悬挂。
- 业务任务：音频清洗 2、PDF 清洗 2、MP3 workflow 5、PDF merge 1、Publisher 19 完成/4 失败/2 取消、
  ZSXQ publish 4 完成。
- 清洗资产：音频 35/35、PDF 49/49 均 `cleaned`，84 个唯一 hash，owner 缺失 0。
- 通知：97/97 `sent`，97 个唯一 dedupe key，未发现重复副作用。
- 本窗 Publisher 来源全部为 MP3，没有自然 report-audio 样本；父任务完成唤醒旧宽指纹
  `71b8b6d6cc87d1f3` 为 0，但该专项继续 `OBSERVING`，不能用“没有目标事件”宣告闭环。

## Content Pipeline O(1) 验收

四个 Web 批次分别接收 19、24、16、24 个资产，均严格为 1 次 canonical SELECT + 1 次多行 INSERT，
共 83 个 Web 资产全部提交成功；数据库窗口总计 84 个清洗资产全部完成。批量去重查询预算获得自然运行态验收，
状态提升为 `RUNTIME_ACCEPTED / OBSERVING`。但日志仍没有直接的“哈希入口
`in_transaction=false`”标记，事务外哈希只能由 SQL 时序、无 Web 长 hold 与终态旁证，独立记录继续观察。

## Session、PgBouncer 与网络

- 连接长 hold 5 次，P50/P95/max `6.757/9.938/9.938s`；SQL `29.158s`、非 SQL `8.217s`。
  相较 09-08 的 49 次与 `495.579s` 总持有，次数下降 89.8%、总量下降 92.5%。
- 最大非 SQL 放大来自 credential resolve 与火柴人分镜路径；credential update 和 MP3 详情则主要等待 SQL/驱动。
- PgBouncer 1,440 个采样，288 次 server connection（12/h）；wait P50/P95/max
  `25.32/100.31/1885.34ms`，query `53.92/178.26/2771.91ms`。六类错误与 route fallback 均为 0。
- 现场为 transaction pool，`default/min/reserve=15/6/5`，`query/query_wait/connect timeout=20/15/10s`；
  六条 server socket 走 Tailscale `100.92.99.39`。诊断时 `cl_waiting=1` 但 `maxwait=0.036s`，是恢复快照，
  不代表峰时容量耗尽。
- 09-08 桥接窗口 PgBouncer wait/query P95 仅 `55.39/88.84ms`；主窗回升至
  `100.31/178.26ms`，与慢 SQL 回升同向。因缺少峰时 route/server/activity 同步快照，网络根因不能关闭。

## 只读物理审查与 EXPLAIN

- `publication_tasks` 约 132MB，live/dead 为 13,374/1,751；dead 较 09-05 的 1,945 下降约 10%。
  其他目标表索引扫描占比 98.79%–99.95%，没有精确重复索引。
- 无 waiting lock、无 idle-in-transaction；`statement_timeout=15s`、`lock_timeout=5s`、
  `idle_in_transaction_session_timeout=60s`；`pg_stat_statements` 未安装。
- 代表 EXPLAIN：publication PK `0.032ms`、chart `3.300ms`、Publisher list `0.067ms`、
  Content task list `0.225ms`、ZSXQ system runs `1.755ms`、Insights canonical `35.522ms`。
- 同形专项：平台凭据读 `0.104ms`、分镜资产 43 行 `17.990ms`、内容音频目录 500 行 `1.871ms`、
  通知 due `0.163ms`。数秒应用耗时无法由这些 executor 解释，因此不新增或删除索引。

## 页面/API 端到端验收

窗口后只读 smoke：Insights 冷请求 200/服务端 `3.227s`，其中 SQL `1.695s`；立即热请求命中本地缓存，
服务端 `0.67ms`。topics tabs 200/`498ms`、topics local 200/`1.247s`、sync status 200/`496ms`、
all-tabs config 200/`0.92ms`、正确 health `/api/data-collection/health` 200/`1.237s`。
这些仅证明当前运行可用；首次误探 `/api/v1/health` 得到 404 是窗口后的诊断路径错误，不计入业务失败率。

## 新发现与修复：巡检观测假绿

旧分析器没有匹配 `[TASK_GUARD] idle-in-transaction offenders count=N details=[...]`，因此把 31 个
生产观测漏成 0。新增回放测试先在旧实现上 RED，再加入结构化 JSON 解析、签名去重、最大连接与年龄汇总。
修复后分析器完整套件 30/30 通过，固定窗回放为
`episodes=31 max_connections=3 max_xact_age_seconds=0.779 max_idle_age_seconds=0.562`。
该问题登记为 `2026-09-10-db-audit-missed-task-guard-idle-observations.md`。

## 根因闭环状态

| 根因簇 | 本轮证据 | 状态与下一步 |
| --- | --- | --- |
| 远端 DB 网络/驱动长尾 | timeout 为 0、executor 快，但 PgBouncer P95 与应用慢 SQL 较桥接窗同步回升 | `OPEN/REPEATED/OBSERVING`；下次尖峰同步保存 route、server socket、pool 与 activity |
| 应用 Session 生命周期 | hold 5 次且较基线大降；无锁、无超时 | 改善，继续按 origin 观察；不因一次好窗关闭 |
| Content canonical N+1 | 4 个自然批次均 1 SELECT + 1 INSERT | `RUNTIME_ACCEPTED/OBSERVING`；继续守 O(1) 合同 |
| 文件哈希跨事务 | 无旧 6.1 秒时序或 Web 长 hold，但缺直接入口标记 | `OBSERVING`；补自然 `in_transaction=false` 证据后再关闭 |
| 父任务完成唤醒宽读 | 旧指纹 0，但无 report-audio 自然完成样本 | `OBSERVING`；等待目标自然 Publisher completion |
| 巡检漏报 offender | TDD 修复并回放 31 个观测 | `CODE_CHANGED/REPLAY_ACCEPTED`；下一完整窗验证新口径 |

## 变更与验证

- 只修改离线分析器、单元测试、Bug/规则/复发登记与本报告；无需重启在线服务。
- 保留用户已有的 ZSXQ 上传配置和 audio cleaner 相关未提交改动，不混入本轮实现。
- TDD：新增 offender 回放在旧实现 RED；修复后分析器 30/30 通过。
- 完整 unit 在受限沙箱运行 1,666 项，只有 34 项因禁止绑定 `127.0.0.1` 报
  `PermissionError`；在允许 loopback 的环境原样重跑为 `Ran 1666 tests in 79.909s / OK`。
- `compileall -q src/backend scripts/diagnostics scripts/dev tests` 通过；worker app factory 27 文件、
  Celery revoke 317 文件门禁通过；registry JSON 与 Bug strict 196/196 通过。
- 最终 `git diff --check` 通过；工作区仍包含用户原有的两个 service 修改与一个 ZSXQ runtime config 测试。

## 下一轮重点

1. 固定同一窗口持续比较慢 SQL、HTTP、Celery、hold、PgBouncer P95/max 与六类错误，不采样最顺眼的短窗。
2. 尖峰时同时落 route、`SHOW POOLS/SERVERS`、远端 activity，区分网络、池等待与 executor。
3. 继续采集 Content Pipeline 直接事务边界标记与 report-audio 自然父唤醒样本。
4. 分别报告 idle 异常事件、准入 defer 和短暂 offender 观测，禁止三种口径互相替代。
