# 2026-10-31 数据库性能与错误巡检

## 巡检窗口与结论

- 固定窗口：2026-10-30 06:00:00 至 2026-10-31 06:00:00（Asia/Shanghai）。
- 基线：2026-10-30 报告、Bug 记录、Session 生命周期纪律与 recurrence registry。
- **本轮没有可标记 CLOSED 的主要根因。** 新增的 TTS 请求风暴及复发的 B站 DNS 故障已完成代码修复、完整测试、
  标准重启和短窗运行态验收，状态为 `RUNTIME_REPLACED/OBSERVING`；尚缺下一自然周期和完整后窗。
- 未执行任何生产 DDL、数据回填、破坏性操作或原始 JSON 清理。

按累计耗时、失败率、P95/max、用户影响和复发次数排序，首要问题为：TTS 历史轮询/音频 404 风暴、audio
catalog executor 外网络接收尾延迟、B站情报 DNS 故障、YouTube 固定两源第 28 窗失败、ZSXQ 1059 复发。

## 与上一轮的量化对比

| 指标 | 2026-10-30 | 本轮 | 变化/判断 |
|---|---:|---:|---|
| 慢 SQL 数量 | 15 | 39 | +160.0%，恶化 |
| 慢 SQL 累计 | 27.651s | 71.738s | +159.4%，恶化 |
| SQL P50/P95/max | 1.256/2.018/9.288s | 1.349/2.726/9.885s | P95 +35.1%，max +6.4% |
| SQL/statement/lock/connection timeout | 0 | 0 | 无新增，但不代表页面无慢请求 |
| 完成 HTTP | 635 | 1577 | 覆盖扩大 |
| HTTP 成功/失败 | 629/6 | 709/868 | 失败率 55.04%，由 TTS 音频 404 主导 |
| HTTP P50/P95/max | 3.74ms/1.477s/49.271s | 330ms/1.057s/62.643s | max 恶化；分布被 404 流量改变 |
| 远端旧 Mac DB 会话快照 | 6 | 0 | 改善，但不是完整窗物理隔离证据 |
| B站 competitor 成功 | 168/168 | 138/168 | 末段 DNS 故障复发 |
| ZSXQ upload 1059 | 3 | 8 | 恶化；其中 1 次 attempt 2 |

## 慢 SQL、Session 与 PostgreSQL/PgBouncer 分层

### 主要 SQL 指纹

| 排名 | 指纹/来源 | 次数 | 累计 | P95/max | 根因判断 |
|---|---|---:|---:|---:|---|
| 1 | `4ff19d3bf2cee9f4`，`api/tts_v2.py:get_history` | 25 | 33.352s | 1.830/2.726s | 无界轮询 + 132KB 宽 JSON 网络传输；executor 0.066ms，不是缺索引 |
| 2 | `be891...`，MP3 audio catalog | 2 | 19.212s | 9.885/9.885s | 1,558 行/约 891KB 的 driver/Tailscale 接收尾部；既有缓存/投影有效 |
| 3 | notification due | 3 | 4.822s | — | executor 毫秒级，远端往返尾部 |
| 4 | ZSXQ Insights overview | 2 | 4.100s | 2.062/2.062s | executor 40.802ms，未恢复全量正文读取 |
| 5 | Publisher cleanup | 2 | 3.527s | — | 既有索引命中，无锁/扫描证据 |

应用 SQL error、statement timeout、lock timeout、connection timeout、server closed、deadlock 均为 0。
12 个瞬时 idle observation 的最大连接数 3、最大事务龄 0.275 秒、最大 idle 0.246 秒；没有持久
`idle in transaction`。两个长 hold 都属于 audio catalog，SQL 合计 19.212 秒、非 SQL 仅 137ms，未见
Session 跨外部 I/O 持有。

PgBouncer 1,412 个样本中八类错误均为 0，286 个新连接来源均为当前节点 `100.96.3.12`，没有代理回退；
wait/query P95 为 45.18/71.08ms。这排除了本窗 pool wait 风暴，但不能排除 SQL 结果经公共 Tailscale/
psycopg2 接收的长尾。audio 同形 PgBouncer/直连 A/B 在上一轮均约 9.1 秒、executor 约 5–6ms，本窗再次
9.885 秒，故该复发仍为 `OPEN/REPEATED/WAITING_INFRASTRUCTURE`，不支持索引或超时掩盖。

## 页面/API/后台任务与 SQL 关联

| 页面/API/任务 | 请求/结果 | 累计、P95、max | SQL/调用链关联 | 状态 |
|---|---|---|---|---|
| TTS segment audio | 890；28 成功/862 个 404 | 353.692s；625.9ms/1.666s | history 重绘 `<audio src>` 后浏览器扇出；非 DB executor | 已修复并运行替换，待后窗 |
| TTS history | 52 | 65.424s；1.845/3.047s | 25 条 `4ff19...` 慢 SQL/33.352s | 已修复并运行替换，待后窗 |
| `/audio-assets` | 3；全成功 | 20.709s；10.568/10.568s | 2 条 catalog SQL/19.212s | 网络接收根因 OPEN |
| videos sync | 2 | 119.765s；57.122/62.643s | 无匹配慢 SQL，外部平台 I/O/编排 | 低频高延迟 |
| channels sync | 2 | 49.395s；23.073/26.322s | 无匹配慢 SQL，外部 I/O | 低频高延迟 |
| web submit | 1 | 10.379s | 无匹配慢 SQL，文件/broker/编排 | 未捕获阶段埋点 |
| charging options | 6；4 成功/2 个 502 | 4.168s | 外部 credential/provider 短暂失败，随后恢复 | OBSERVING |
| ZSXQ Insights overview | 2 个慢 SQL | SQL 4.100s | executor 40.802ms，缓存/远程往返 | OPEN/OBSERVING |

页面/API 很慢但无匹配慢 SQL：videos sync、channels sync、web submit。后台任务/连接持有很久但无前台端点：
Publisher pipeline 4 次/698.327 秒、upload-only 4 次/535.947 秒、ZSXQ publish 1 次/503.068 秒、audio clean
1 次/226.328 秒、TTS 2 次/191.939 秒、MP3 17 次/186.055 秒、notification 67 次/160.286 秒。Celery
固定窗 96 个任务完成、0 retry/failed；任务壁钟包含外部 I/O 和父子等待，不能等价为数据库时间。

## 新增根因：TTS 历史轮询与失效资源扇出

修复前证据链：终态页面仍固定每 5 秒调用 history；每次重绘最近 20 条任务的音频元素；52 次 history、25 条
慢 SQL、890 次音频请求和 862 个 404 在同一调用链出现。只读 EXPLAIN 命中
`ix_publication_tasks_feature_user_created_desc`，20 行 executor 0.066ms，payload 132,143 bytes，明确区分了
executor 与应用/网络放大。

已落地：前端改为 active-only、Page Visibility、single-flight、终态停止；音频统一 `preload=none`；API 只为
真实存在的音频/字幕生成 URL，并在文件系统检查前释放 Session，原始 `params/result` 与成功计数完整保留。

验证：定向测试红转绿；全量 1,844 passed、10 skipped、374 subtests；标准重启后真实浏览器页只显示 2 个真实
音频控件，均 `preload=none`，旧任务不再显示失效资源。reload 后只发生一次 history，未再发出 segment audio。
这证明新代码已替换旧运行体，但未覆盖完整后窗，状态不得高于 `RUNTIME_REPLACED/OBSERVING`。

## 复发问题与外部调用链

### B站情报 DNS/TUN

24 个周期共 138/168 成功；01:56 为 5/7，02:56–05:57 连续四周期 0/7，30 次失败均为
`space.bilibili.com` DNS `nodename nor servname provided`。进程环境已有 `https_proxy`，本地代理 7890 在线且
curl 成功，但 `bilibili_api` AioHTTP client 显式传空 proxy，未可靠继承环境代理，真实请求仍落入 TUN fake-IP
DNS。已在 fetch 前设置库级 proxy；真实凭据+competitor 只读探测成功，并随新 scheduler PID 运行替换。
下一自然小时周期尚未发生，保持 `OPEN/RUNTIME_REPLACED/OBSERVING`。

### YouTube 固定不可枚举来源

00:03 channel 49/51；01:00 video 处理 49 源、新增 26、更新 56、errors=2。失败仍为 id 21/56，已连续第
28 个窗口复发；上一窗全来源 SSL 故障本窗未复发。受保护人工脚本仍为
`docs/sql/disable_unavailable_youtube_channels_20260920.sql`，未无人值守执行。

### ZSXQ 1059

一个 durable publish 最终完成 503.068 秒，但记录 8 次 upload failure、1 次 attempt 2、0 verify pending，
较上一窗 3 次恶化。有界重试和幂等是容错，不是物理根治；数据库无锁、长 Session 或匹配 executor 故障。

### 分布式旧运行体

14/14 个目标 durable task 均为 current-root，Publisher 8/8 完成，old/mixed/unclassified、foreign actor 均 0；
当前快照旧 MacBook DB 会话从 6 降为 0。这是改善，但单次快照不证明完整窗无共享凭据 actor，保持
`OPEN/OBSERVING/WAITING_DISTRIBUTED_ISOLATION`。

## 表规模、扫描与索引复核

| 表 | 总尺寸 | live/dead | index usage | 判断 |
|---|---:|---:|---:|---|
| publication_tasks | 153MB（TOAST 120MB） | 14,751/5 | 99.84% | dead tuple 大幅改善；宽 JSON 仍需投影/缓存纪律 |
| zsxq_topics | 133MB | 13,981/1,665 | 99.51% | Insights executor 40.802ms，无新增索引依据 |
| zsxq_topic_comments | 119MB | 45,884/6,093 | 99.84% | 继续观察 vacuum，不手工 DDL |
| zsxq_attachments | 79MB | 35,988/75 | 99.94% | 无顺序扫描热点证据 |
| zsxq_sync_runs | 37MB | 1,077/61 | 99.90% | runs EXPLAIN 1.557ms |
| external_documents | 7MB | 1,817/146 | 98.73% | 无新热点 |

exact duplicate index 为 0、waiting locks 为 0；统计自 2026-08-08 server start 累计且未 reset，
`pg_stat_statements` 不可用，因此低使用/重叠索引仍不能据累计快照直接 DROP。代表 EXPLAIN：watchdog 0.039ms、
chart 3.119ms、all-tabs 0.022ms、Publisher list 0.048ms、content list 0.225ms、wide/narrow JSON
0.025/0.024ms、sync runs 1.557ms、Insights 40.802ms。**本轮没有 CREATE/DROP INDEX 建议**；所有主要慢点均
缺少“executor 慢 + 真实高成本扫描 + 索引条件匹配”的三重证据。

## 测试、重启与运行态验收

- 定向测试：TTS 轮询/资源投影/Session 释放与 B站代理合同全部通过。
- 全量测试：沙箱内因 loopback bind 权限产生 34 个环境失败；按相同命令在授权环境重跑为
  **1,844 passed、10 skipped、374 subtests**。
- 重启前 `check-restart-safety.py` 检查 46 个近期任务，0 个活动 ZSXQ/不可中断任务；未使用 FORCE。
- 标准 `restart-dev-stack.sh` 后 backend PID 73752；bootstrap 17/17 worker、Watchdog、6/6 scheduler 均 ok，
  `errors=[]`。health healthy；抽查 workflow_notify、MP3、platform_upload、doubao TTS 均 running、queue 0。
- 代理端口 7890 在线；B站只读真实凭据探测成功。自然小时周期和完整后窗仍是缺失证据。

## 待人工动作、风险与未捕获边界

1. 人工在 DBeaver 复核并逐条执行 YouTube 21/56 停用脚本；脚本内需遵守 lock/statement timeout、验证和回滚。
2. 旧 Mac 栈需要取得卸载/停用证据；否则轮换 PostgreSQL/Redis 凭据并隔离 broker namespace。当前会话为 0
   不能代替该动作。
3. audio catalog 需要同秒 socket/route/fetch bytes 的基础设施证据；不应新增索引、删字段、延长超时或降低完整性。
4. `pg_stat_statements` 不可用，索引统计为 server-start 累计；没有 reset 后写入与计划证据，禁止按低使用盲删。
5. 未被日志捕获不等于未发生：固定窗无完整自然 ZSXQ 页面组合、无修复后的 TTS 24h、无 B站新自然周期；
   request id 只能关联已记录请求，外部 provider 内部阶段仍可能不可见。
6. 巡检中的一次 B站模型字段名误用产生了自测 `InvalidRequestError`；已识别为诊断自污染，未写业务数据，不计为
   生产根因证据。

## 根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 复发 | 剩余风险/责任动作 | 下一轮验收条件 |
|---|---|---|---|---|---|---|
| `TTS-HISTORY-POLLING-AUDIO-FANOUT` | 终态无界轮询、宽 JSON、媒体预加载、失效 URL | active/visible/single-flight、preload none、存在性投影、Session 提前释放 | 新 PID + 真实页面单次 history、0 segment fan-out | 新增 | 缺完整后窗 | 24h 无空闲轮询、音频 404=0、指纹不再周期放大 |
| `be891...` audio catalog | driver/Tailscale 结果接收尾部 | 既有投影/TTL/single-flight；本轮无掩盖修改 | executor 毫秒级、应用 9.885s | 是 | 基础设施 owner 补 socket/route A/B | 自然冷 P95 显著收敛且同秒物理断点闭合 |
| `BILIBILI-DNS-TUN` | bilibili_api 未可靠继承环境代理，落入 TUN fake-IP DNS | 显式设置库级 proxy | 真实凭据只读探测、新 scheduler、代理在线 | 是 | 缺自然周期 | 下一周期 7/7，随后完整窗 168/168 |
| `YOUTUBE-21-56` | 两源永久不可枚举 | 受保护停用 SQL 待人工 | 第 28 窗仍 49/51、errors=2 | 是 | 人工 DBeaver | 连续两周期 49/49、errors=0 |
| `ZSXQ-UPLOAD-1059` | provider 上传 1059，物理原因未知 | 既有有界 retry/幂等/verify | 最终完成但 8/1/0 失败链 | 是且恶化 | 补 provider 阶段与 A/B | 完整窗 upload/retry/pending 全 0 且真实发布成功 |
| `REMOTE-OLD-RUNTIME` | 共享 DB/Redis/broker 未物理隔离 | 诊断/actor 门禁已有 | 14/14 current-root、远端会话快照 0 | 本窗未捕获 | 人工停旧栈或轮换凭据/namespace | 完整 24h remote/old/foreign=0 + 当前根自然链成功 |

## 下一轮明确验收计划

1. 先复核新 PID 后的 TTS history 次数、`4ff19...` 慢 SQL、segment audio 404 和活动任务状态，按请求流量归一。
2. 检查 B站 06:08 后首个自然小时周期是否 7/7，并确认日志中代理选择与 DNS 错误归零；继续看完整 24h。
3. 对 audio catalog 冷 miss 捕获 connect/acquire、execute/fetch、bytes、socket/route 与 executor 同秒证据。
4. 继续单列 YouTube 21/56、ZSXQ 1059/retry/pending、旧运行体会话/actor，不用最终成功或零快照代替闭环。
5. 复跑表规模/索引/EXPLAIN；只有真实 SQL、reset 后统计和 `ANALYZE BUFFERS` 同时支持时才生成 DDL 草案。
