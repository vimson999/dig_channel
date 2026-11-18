# 2026-10-28 数据库性能与错误巡检

## 根因闭环结论

固定窗口为 2026-10-27 06:00:00 至 2026-10-28 06:00:00（Asia/Shanghai；数据库 UTC-naive 边界为
2026-10-26 22:00:00 至 2026-10-27 22:00:00）。本轮**不能写成已解决**：应用慢 SQL 由 19 条/43.958 秒变为
18 条/58.064 秒，数量改善 5.3%，但累计耗时恶化 32.1%，P50/P95 从 1.335/8.692 秒升到
1.560/8.938 秒；音频素材目录四次冷建占 37.591 秒，是主要数据库链路长尾。生产同形查询 executor 仅
5.864ms，应用最大 11.133 秒，约相差 1,898 倍，证据不支持加索引。

最严重新增问题是 YouTube 两个自然调度全量失败：频道同步 0/51、视频同步 0/51。旧实现把 Google API 的安全错误
原因吞成统一文案，无法从历史日志判定是 quota、权限、频道状态还是网络；本轮已落地无密钥泄漏的结构化原因传递，
完成安全重启和当前凭据只读连通验证，但修复后的 00:03/01:00 自然周期尚未发生，因此状态只能是
**RUNTIME_REPLACED / OBSERVING**。远端 MacBook 仍持有 5 个空 `application_name` 生产连接，物理隔离也仍是
**OPEN / REPEATED**。

本轮还修复了巡检器把“当前根任务含历史 `duplicate_of` 引用”误计为 old-root 的诊断缺陷；分类已改为互斥的
old/current/mixed/unclassified，并用生产只读回放确认实际是 1 个 mixed、0 个 old，不修改原始 JSON 审计数据。
没有执行生产 DDL、回填、索引增删或破坏性操作。

## 修复前后量化基线

| 指标 | 上一窗口 | 本窗口 | 变化与判断 |
| --- | ---: | ---: | --- |
| 应用慢 SQL | 19 / 43.958s | 18 / 58.064s | 数量 -5.3%，累计 +32.1%，总耗时恶化 |
| 慢 SQL P50 / P95 / max | 1.335 / 8.692 / 11.341s | 1.560 / 8.938 / 11.133s | +16.9% / +2.8% / -1.8% |
| HTTP 完成/成功/失败 | 893 / 882 / 11 | 1,078 / 1,072 / 6 | 失败率降至 0.56%；六个失败均为 favicon 404 |
| HTTP P50 / P95 / max | 3.62ms / 1.355 / 66.697s | 2.95ms / 1.023 / 55.073s | 三项改善；仍有低频外部同步长尾 |
| Celery 完成/重试/失败 | 220 / 0 / 0 | 149 / 0 / 0 | 无任务终态失败；样本量下降 |
| YouTube channel / video | 49/51；errors=2 | 0/51；errors=51 | 新增全量失败，最高优先级 |
| B站情报周期/来源 | 24/24；168/168 | 23/23；161/161 | 成功率稳定；重启少一个周期 |
| PgBouncer wait/query P95 | 51.65 / 82.76ms | 49.55 / 81.31ms | 小幅改善，八类连接错误仍为 0 |

## 主要问题排序

父子任务壁钟存在包含关系，下表只作同类排序，不跨层相加为用户等待时间。

| 排名 | 指纹/复发键 | 累计、P95、最大、失败与影响 | 状态 |
| ---: | --- | --- | --- |
| 1 | YouTube sync diagnostic loss | channel 0/51、video 0/51，失败率 100%；两个每日任务全部用户来源受影响 | RUNTIME_REPLACED / OBSERVING |
| 2 | MP3 workflow | 3 个 completed，累计 14,570.324s，P95 5,409.856s，max 5,488.903s；主要是媒体/外部编排 | OPEN / OBSERVING |
| 3 | runtime topology | Publisher 15 个：12 completed、3 stale child canceled；远端 MacBook 仍有 5 个生产连接 | OPEN / REPEATED |
| 4 | audio `be891b5c1af362da` | 4 次/37.591s，P95/max 11.133s；影响 audio-assets 页面；executor 5.864ms | OPEN / REPEATED |
| 5 | ZSXQ publish 1059 | 1 个任务最终成功/625.837s；5 次 upload failure 1059、1 次 verify pending、attempts=6 | OPEN / REPEATED |
| 6 | Insights `674016d39deab3f5` | 2 次/3.981s；overview 4.228s；executor 41.265ms | OPEN / REPEATED |
| 7 | B站情报与上传 | 情报 23/23、161/161；6 个 upload 与 6 个 supplement 均成功，无 21173/21523/耗尽 | OBSERVING，未复发 |

## 页面、API、后台任务与 SQL 关联

### SQL、Session、PgBouncer、网络与 executor

- 18 条应用慢 SQL 累计 58.064 秒；另有 4 条 ad-hoc 诊断样本。应用 SQL error、statement timeout、lock
  timeout、连接 timeout、deadlock、server closed 与 waiting lock 均为 0。17:05 的一条 `UndefinedColumn` 来自本轮
  ad-hoc 巡检查询，是巡检自污染，不是业务故障。
- 指纹依次为 audio 4/37.591s、notification due 4/5.308s、Insights 2/3.981s、B站 bvid lookup
  2/2.158s，其余单条约 1–2 秒。
- 四次 web audio connection hold P50 9.396s、max 11.181s；匹配 SQL 37.591s，Session 内非 SQL 合计仅
  703.63ms。17 次 runtime guard 观察最大连接数 3、事务龄 0.273s、idle 0.242s、defer=0，未见 durable
  idle-in-transaction 或 Session 跨外部 I/O。
- PgBouncer 1,393 个样本、281 次连接（约 11.708/h），来源为当前 Mac mini `100.96.3.12`，proxy fallback=0，
  八类错误为 0；wait/query P95 为 49.55/81.31ms。当前证据把音频与 Insights 的未知段定位到 PostgreSQL
  executor 之外的 driver/PgBouncer/私网 fetch/反序列化路径，不能归因 executor。
- audio 生产 `EXPLAIN (ANALYZE, BUFFERS)`：有界 1,511 行、4570 行顺扫、sort 1,431KB、shared hit/read
  3/510、executor 5.864ms、payload 873,080 bytes；应用最大约为 executor 1,898 倍。Insights executor
  41.265ms，而应用 SQL 1.962–2.019s，约 49 倍。

### 全业务 HTTP 与三类异常

固定窗 1,078 个完成请求、1,072 成功、6 个 favicon 404，累计 336.201 秒，P50/P95/max
2.95ms/1.023s/55.073s。

- `POST /api/videos/sync` 1/55.073s、`POST /api/channels/sync/all` 1/25.367s、content-pipeline
  web-submit max 12.214s，均无匹配 >=1s SQL，是低频高延迟且页面/API 慢但无慢 SQL的重点样本。
- audio-assets 16 个请求累计 41.153s、P95 10.296s、max 11.699s；四个冷 request
  `a561cb5b`/`8c26dc4`/`cf30e21`/`a3077def` 分别关联 SQL 8.905/11.133/8.615/8.938s，热请求为
  1–18ms，5 分钟缓存有效。
- 固定窗重点 ZSXQ：overview 1 个 200/4.228s（SQL 1.962s）、topics/local 200/0.663s、tabs
  200/0.221s、sync/runs 200/0.326s。
- 后台很久但无前台端点：MP3 workflow max 5,488.903s、Publisher completed max 409.165s、ZSXQ publish
  625.506s、audio clean 245.852s。没有匹配慢 SQL只说明当前阈值没有捕获数据库段，不能误报为没有问题。

### 后台任务、外部 I/O 与运行体

- Celery 149 completed、0 retry task、0 failed task，累计 3,391.268s：Publisher pipeline
  9/1,107.238s，upload-only 6/846.351s，ZSXQ publish 1/625.837s，MP3 36/347.867s，audio clean
  1/244.457s，notification 96/219.519s。
- durable 任务：MP3 3/3 completed，Publisher 12 completed + 3 canceled，ZSXQ publish 1 completed，audio clean
  1 completed。3 个 canceled 是旧 child 在 parent 短暂失败后到达的 stale delivery；parent 随后完成，终态门禁正确拒绝旧投递，
  不是当前业务失败。Publisher foreign actor、素材缺失、upload missing 均为 0，notification 64/64 sent、max attempt=1。
- root 分类修正后：MP3 3 current、Publisher 15 current、ZSXQ publish 1 current；audio clean 1 个 mixed historical
  reference、old=0、current=0。mixed 来自历史 `duplicate_of`，实际输入输出均是 current-root，原 JSON 保留。
- 当前 DB snapshot 仍见 Mac mini 5 个、MacBook `100.119.238.48` 5 个空 `application_name` 连接及服务端
  gunicorn；一次 watchdog idle-in-transaction 仅 0.248s。远端共享凭据/actor identity 风险没有闭环。
- B站情报固定窗因重启为 23/23 周期、161/161 成功，累计 296.102s，P50/P95/max
  12.400/15.299/15.464s。上传 6 个、supplement 6 个均成功，本窗没有 21173、21523 或 chunk exhausted。
- ZSXQ 一条自然发布最终成功，但 1059 五次与 verify pending 一次说明外部失败仍复发；有界重试有效，不等于根因关闭。
- YouTube 00:03 channel 51/51 失败、01:00 video 51/51 失败。08:33 当前凭据只读连接成功，说明不是持续性
  quota/凭据失效；历史精确 provider reason 被旧代码吞掉，不能反推原因。固定的 21/56 不可用来源被全量事故遮蔽。

## 程序模式与表/索引复核

没有发现新增 N+1、无界分页、大字段全量搬运、重复去重聚合、缓存失效风暴、频繁轮询或 Session 与外部 I/O
交叉持有。audio 一次最多 1,511 行/873KB，投影、single-flight、5 分钟缓存和 Session 外文件检查有效；当前问题是
冷 miss 的 executor 外 fetch 长尾，不能靠扩大 timeout/TTL 掩盖。Insights 保持轻摘要和当前页详情，原始 JSON/TOAST
审计数据未删除。

| 表 | 总大小 | live/dead | index scan share | 判断 |
| --- | ---: | ---: | ---: | --- |
| `publication_tasks` | 152MB | 14,778 / 1,771 | 99.84% | PK 0.045ms、chart 2.979ms、列表 0.045–0.254ms |
| `zsxq_topics` | 133MB | 13,981 / 1,665 | 99.51% | Insights executor 41.265ms |
| `zsxq_topic_comments` | 119MB | 45,884 / 6,093 | 99.84% | 无本窗热点指纹 |
| `zsxq_attachments` | 79MB | 35,988 / 75 | 99.94% | 无本窗热点指纹 |
| `zsxq_sync_runs` | 37MB | 1,077 / 61 | 99.90% | 代表查询 1.544ms |
| `external_documents` | 7,016KB | 1,817 / 146 | 98.73% | 无本窗热点指纹 |

没有 exact duplicate index。低扫描候选仍包括 attachments PDF group-tab 与 topics group/type，但统计自 8 月 8 日
server start 累计，缺 reset-window 写放大和反事实执行计划；`pg_stat_statements` 不可用。**本轮不生成或执行
CREATE/DROP INDEX、ALTER、回填或生产配置脚本。** 盲目增加索引不会修复 executor 外长尾。

## 已落地修复、测试与运行态验证

- `youtube_api.py` 新增安全 HTTP 错误分类，保留 endpoint、HTTP status、provider reason 和去密钥 message；网络错误仅保留
  exception type + endpoint。线程本地 last error 会在每次调用前清空，并通过 channel/video result 传递；异常链使用
  `from None`，不会把含 API key 的 URL泄漏到日志。
- `youtube_sync_service.py` 将安全原因传递到调度结果；新增测试验证 403 quota reason 可见、假 key 与 `?key=` 不可见，
  video 结果携带原因且线程本地状态被清空。
- `daily_db_runtime_snapshot.py` 把 root 引用改为 old/current/mixed/unclassified 互斥分类；新增 mixed 历史引用回归，生产只读回放
  验证 old=0、mixed=1，避免下一轮重复误报。
- 重启前 `check-restart-safety.py` 确认无不可中断任务；标准 `restart-dev-stack.sh` 双门禁通过。重启后 backend PID
  68271，17/17 Celery、watchdog、6/6 schedulers 就绪，health healthy；YouTube 当前只读连接成功。
- 重启后真实 E2E：overview 200/1.500s、topics/local 200/0.747s、tabs 200/0.263s、sync/runs
  200/0.459s、system scheduler 200/0.384s。YouTube 新代码已进入运行体，但修复后的自然 00:03/01:00 尚未验收。
- 定向测试：YouTube 与 channel/video scheduler 12/12，通过；runtime snapshot 7/7，通过；Session-before-I/O 契约继续通过。
  完整 unit 1,747/1,747 通过，耗时 112.439s。测试不能替代下一自然周期运行态证据。

## 人工动作、未捕获边界与下一轮计划

- **P0 人工隔离：**在在线 MacBook 停止/卸载旧 backend、Celery、watchdog、scheduler 和脚本；若不能证明全停，轮换
  PostgreSQL/Redis/broker 凭据并隔离 Celery namespace，所有客户端设置不可伪造的 deployment identity。只断当前连接会被重连。
- YouTube 21/56 仍仅保留既有 DBeaver 人工脚本
  [`disable_unavailable_youtube_channels_20260920.sql`](../../sql/disable_unavailable_youtube_channels_20260920.sql)，按其中
  lock/statement timeout、验证和回滚逐段执行。本轮没有新的待人工 DDL。
- 未捕获边界：慢日志阈值为 1 秒；`pg_stat_statements` 不可用；旧 YouTube 统一文案无法恢复 provider reason；远端空
  `application_name` 无法归因进程；system ZSXQ sync 禁用；B站 invalid tier/chunk failure 本窗没有故障样本。
- 下一轮先验收 YouTube 00:03/01:00 自然周期是否输出安全精确原因且不泄密；复核 remote/old/foreign actor；在 audio/Insights
  下一个 cold miss 同时采 pool acquire、driver fetch bytes、deserialize、server socket、route 与响应 bytes；继续追踪 ZSXQ
  1059、B站分片/失效档位和 21/56。未获得后续窗口证据前不得标 CLOSED。

## 根因闭环状态表

| 问题指纹 | 根因 | 已落地修复 | 运行态证据 | 是否复发 | 剩余风险/责任动作 | 下一轮验收条件 |
| --- | --- | --- | --- | --- | --- | --- |
| YouTube all-source failure | 历史 provider reason 被统一文案吞掉；上游瞬时根因未知 | 安全结构化原因与链路透传、脱敏回归 | 新代码已重启；当前连接成功；自然周期未到 | 新增 | 等自然调度；保留完整安全原因 | 两个自然周期完成，原因可定位、key 不泄漏；按根因修复后再观察后窗 |
| runtime topology | 多主机共享生产 DB/Redis/broker，缺强 actor identity | root/actor/终态门禁与诊断 | 本窗 current-root；远端仍有 5 连接 | 风险反复 | 人工停旧栈或轮换凭据/namespace | 完整 24h remote/old/foreign=0，当前根自然链成功 |
| audio `be891...` | executor 外 driver/PgBouncer/私网 fetch 长尾 | 投影/上限/single-flight/cache/Session 外 I/O | 37.591s vs executor 5.864ms；暖态 1–18ms | 是 | 补 pool/fetch/bytes/deserialize/route 分段 | 冷暖自然配对，暖 SQL=0，冷链无秒级未知段 |
| Insights `6740...` | executor 外远程 fetch 尾部 | 轻摘要/当前页拆分 | 3.981s vs executor 41.265ms；E2E 1.500s | 是 | 补 bytes/driver/route 分段 | 多组自然冷暖样本，P95 稳定且无全文搬运 |
| root mixed false positive | 历史 JSON 引用被当作当前 lineage actor | 互斥 mixed 分类和回归 | 生产回放 old=0、mixed=1 | 诊断问题已替换 | 后窗防止重新误报；保留 JSON | 下一每日巡检分类稳定，mixed 不进入 old actor 告警 |
| Publisher stale child | parent 恢复后旧投递到达 | terminal guard 拒绝旧 delivery | 3 canceled child，parent 后续完成 | 是但门禁生效 | 继续监控重复投递与终态 | 后窗无终态回退、无重复上传，parent 正常完成 |
| ZSXQ 1059 | 外部上传/发布瞬时失败 | 有界 retry、原错保留 | 5 次 1059 + 1 verify pending 后完成 | 是 | 防无界/重复发布 | 后窗无风暴、预算内且无重复发布 |
| B站 upload/tier | 外部媒体与选项生命周期风险 | 有界分片、上传前档位校验 | 12/12 成功，本窗无故障样本 | 未捕获 | 等真实 invalid-tier/chunk 样本 | 可分类、无重复稿件；失效项上传前拦截且重选成功 |
| target indexes | 低扫描索引可能低价值 | 只读 stats/EXPLAIN 门禁 | 无重复索引、热点 executor 毫秒级 | 无新增 | 不盲增删；补 reset-window 证据 | 仅有真实 SQL+stats+EXPLAIN 才生成 DDL 提案 |
