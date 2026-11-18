# B站情报独立调度器整点外部连接连续失败

## 基本信息

- 日期：2026-09-18
- 状态：OPEN / REPEATED / WAITING_NETWORK_TRACE
- 严重级别：P1
- 影响模块：B站情报独立调度器、外部 HTTP/DNS/代理链路
- 根因分类：外部连接链路不可用；具体断点待同周期现场追踪
- 复发键：`EXTERNAL-CALL-RESILIENCE`
- 是否复发：同一完整窗口连续 24 个整点周期失败
- 防线层级：observability / runtime_acceptance_pending

## 现象与影响

2026-09-17 06:00–09-18 06:00 的独立调度器日志有 24 个真实整点失败周期；六个
competitor 请求均报 `Cannot connect to host space.bilibili.com:443` 和
`nodename nor servname provided`。另有两个 `partial` 行来自单元测试污染，不能计作生产部分成功。
01:36 周期还遇到配置 SQL 6.118 秒，但其余 23 个失败周期说明外部链路故障并非仅由该次数据库瞬断引起。
受影响的是整点情报同步的时效性，不能因调度器仍在运行而视为成功。

## 根因与解决思路

故障位于调度器到 B站的 DNS、代理或网络连接路径；当前系统 DNS 给出 `198.18.0.23` fake IP，
系统代理指向 `127.0.0.1:7890`，巡检时该端口无监听。尚缺 aiohttp 实际连接目标、代理使用方式和
TUN 路由的同秒证据，不能断言单一故障点。应在下一次真实周期同步采集 DNS、代理/TUN、
连接目标、HTTP 结果和失败分类，再修真正断点；不能靠无限重试掩盖持续失败。

## 实际方案与验证

本轮完成日志归因和独立异常登记，未更改生产网络、调度器频率或外部调用策略。
下一轮要求至少一次真实 6/6 同步成功，并观察完整窗口无整点失败风暴；同时区分测试日志与真实周期。
直到有运行证据前维持 OPEN。

## 2026-09-19 后窗复核

09-18 07:18 至 09-19 05:24 独立进程记录连续 23 次真实整点周期，每次 6/6 completed、单轮
7.558–19.822 秒；与前窗 24/24 失败相比有实质改善。06:09–06:16 的 server-closed/SQL
traceback 与 partial 则来自单测日志污染，另见
`2026-09-19-scheduler-test-logs-contaminated-production.md`；不得混算为新的真实连接失败。
由于失败时的 DNS/代理/实际连接目标尚无同秒证据，保持 OPEN/OBSERVING；下次真实异常同步采链路
断点，并复核完整后窗，而不以本次恢复反推故障根因。

## 2026-09-20 完整后窗复核

固定窗 2026-09-19 06:00–09-20 06:00 共 24 个真实整点周期，全部为 6/6 completed、
失败 0；累计耗时 199.888 秒，P50/P95/max 为 8.219/9.661/9.919 秒。相较最初
24/24 失败已连续第二个窗口改善，且本次覆盖完整 24 小时。窗口内的 `for test`、
fake timeout 与 SQLite 建表文本仍是 09-19 06:07–06:12 的旧测试污染，不计入生产周期。

但最初故障的 DNS/代理/TUN 同秒断点从未捕获，恢复原因仍未知；因此状态继续为
OPEN/OBSERVING，不能用两个绿窗反推已根治。新一轮正常进程加载导入期日志隔离修复后，
若再次失败必须同时记录 DNS 结果、实际连接目标、代理/TUN 路由与 HTTP 分类。

## 2026-09-21 后窗复核

固定窗内因 06:22 整栈重启错过 06 点周期，07:23 至次日 05:28 共 23 个自然周期，仍全部
6/6 completed、失败 0；累计 215.660 秒，P50/P95/max 为 9.293/11.119/11.409 秒。独立日志中
没有再出现 `for test`、假 task id、SQL traceback 或 server-closed 测试污染，日志隔离得到一个自然窗口证据。
最初外部连接失败的物理断点依然没有在故障现场捕获，故保持 OPEN/OBSERVING；若下一次失败，必须按既定
DNS/代理/TUN/实际连接目标合同取证，不能用三个绿窗反推根因消失。

## 2026-09-29 固定窗复核

固定窗共 22 个真实周期：21 个为 6/6 completed，1 个在 05:47 为 5/6 partial；累计记录耗时
201.212 秒、最大 14.136 秒。失败 competitor 的错误为
`Cannot connect to host api.bilibili.com:443 ssl:default [None]`，其余五个 competitor 同轮继续完成，
没有演变为数据库错误、Session 长持有或失败重试风暴。

这说明逐 competitor 隔离有效，但外部连接物理断点再次出现。状态继续 **OPEN / REPEATED / OBSERVING**；
下一次失败必须同秒记录 DNS、系统代理/TUN、实际连接目标和 HTTP 分类。21/22 成功不能反推根因已消失。

## 2026-10-14 DNS 失败风暴复发

03:32、04:32、05:32 三个自然整点周期均为 0/7，累计 21 个 competitor 请求失败；错误一致为
`Cannot connect to host space.bilibili.com:443` 与 `nodename nor servname provided`。同窗没有匹配慢 SQL、
数据库 Session 长持有或 PgBouncer 失败，说明故障仍在 DNS/代理/TUN/外部 HTTP 边界，而不是数据库 executor。

相较 09-29 的单 competitor 失败，本窗恶化为整轮失败风暴。状态保持
**OPEN / REPEATED / WAITING_NETWORK_TRACE**；下一次失败必须在同秒保存 DNS 结果、系统代理/TUN、aiohttp
实际连接目标与 HTTP 分类。一次手工解析成功或一次 7/7 不能关闭，要求完整 24 小时自然周期全成功。

## 2026-10-15 全窗 24/24 失败并恶化

固定窗 24 个真实整点周期全部 `status=failed total=7 succeeded=0 failed=7`，累计 168 个 competitor 请求
失败；错误仍一致为 `space.bilibili.com` 的 `nodename nor servname provided`。相较上一窗仅 3 个失败周期，
本窗扩大为完整 24 小时失败风暴。代码在外部 fetch 前已释放 DB Session，失败后的状态写入是短事务；窗口内
没有匹配慢 SQL 或连接长持有，数据库不是根因。

窗后 06:06 的 DNS/route/代理快照显示 fake-IP 经 TUN，系统代理端口有监听，代理与 no-proxy curl 均返回 200；
这只证明当前恢复，故障发生时的 DNS/TUN 断点没有留存。状态继续
**OPEN / REPEATED / WAITING_NETWORK_TRACE**。下一次失败必须同秒保存 DNS、代理进程/端口、TUN route、aiohttp
实际目标和 HTTP 分类；关闭条件仍是完整 24 小时所有自然周期 7/7，不能以一次手工 200 代替。

## 2026-10-16 失败风暴缓解但窗口前段仍有 5 个失败周期

固定窗共捕获 23 个自然周期。06:37–10:38 的前 5 个周期仍为 0/7，累计 35 个 competitor 请求报
`space.bilibili.com` DNS `nodename nor servname provided`；11:42–05:23 随后 18 个周期连续 7/7。
这比上一窗 24/24 失败明显改善，但没有覆盖完整 24 小时全绿，且恢复时没有同秒 DNS/代理/TUN 证据。

状态保持 **OPEN / REPEATED / OBSERVING / WAITING_NETWORK_TRACE**。下一轮验收仍是完整 24 小时所有自然周期
7/7；任何新失败必须同秒保留 DNS、代理进程/端口、TUN route、aiohttp 实际目标与 HTTP 分类。

## 2026-10-17 完整自然绿窗

固定窗 24/24 个自然周期全部 completed，168/168 competitor 成功；周期 P50/P95/max 为
13.084/14.934/14.966 秒，没有失败、重试风暴、慢 SQL 或 Session 长持有。相较上一窗前 5 个周期失败，
这是明确改善并满足完整绿窗数量条件。

但此前 DNS/TUN 失败发生时的物理断点仍未取得，单个绿窗不能证明外部链路根因已消失。状态保持
**OPEN / OBSERVING / WAITING_NETWORK_TRACE**；下一完整窗继续要求 24/24，任何新失败必须同秒保存 DNS、代理/
TUN、实际连接目标与 HTTP 分类后再决定是否关闭。

## 2026-10-18 连续第二个完整自然绿窗

固定窗 24/24 个自然周期全部 completed，168/168 competitor 成功；累计 325.382 秒，周期
P50/P95/max 为 13.221/15.146/18.852 秒，没有失败、重试风暴、匹配慢 SQL或 Session 长持有。这是连续
第二个完整自然绿窗。

但历史 DNS/TUN 故障发生时仍没有物理断点证据，故保持 **OPEN / OBSERVING / WAITING_NETWORK_TRACE**；
下一次失败必须同秒记录 DNS、代理/TUN、aiohttp 实际连接目标和 HTTP 分类，不能用绿窗反推根因消失。

## 2026-10-19 成功率保持但外部尾延迟显著恶化

本窗因前一日 06:06 重启后的调度边界捕获 23 个自然周期，23/23 completed、161/161 competitor 成功，
累计 418.509 秒，周期 P50/P95/max 为 13.955/31.667/37.877 秒。相较上一窗 P95/max
15.146/18.852 秒，分别恶化 109.1%/100.9%。最慢周期的数据库 SQL 主要为 33–453ms，未匹配 >=1s
慢 SQL，credential/BVID 阶段之间存在约 5.3–12.7 秒日志空档，说明主要尾部仍在外部 HTTP/DNS/TUN 边界；
缺逐请求阶段埋点，不能臆断单一断点。

状态保持 **OPEN / OBSERVING / WAITING_NETWORK_TRACE**。下一次失败或 >30 秒周期必须同秒记录 DNS、代理/TUN、
aiohttp 实际连接目标和 HTTP 阶段；下一完整窗同时要求 24/24 成功和 P95 恢复，成功率不能掩盖性能恶化。

## 2026-10-24 成功率与尾延迟恢复

固定窗 24/24 个自然周期 completed、168/168 competitor 成功，累计 329.944 秒，周期
P50/P95/max 13.399/16.122/17.000 秒。相较上一窗 13.480/16.785/17.171 秒略改善，且没有失败风暴、匹配
慢 SQL 或 Session 长持有。

历史 DNS/TUN 物理断点仍未在故障现场捕获，故保持 **OPEN / OBSERVING / WAITING_NETWORK_TRACE**；下次失败
仍须同秒保存 DNS、代理/TUN、aiohttp 实际目标和 HTTP 阶段，不能用本窗全绿反推根因已根治。

## 2026-10-25 重启边界后的 23 个自然周期继续全绿

固定窗因 06:14 重启边界捕获 23 个自然周期，23/23 completed、161/161 competitor 成功，累计
302.132 秒，周期 P50/P95/max 为 13.100/14.260/14.295 秒；相较上窗尾延迟继续改善，没有失败风暴、
匹配慢 SQL 或 Session 长持有。

覆盖不足完整 24 个周期，且历史 DNS/TUN 故障发生时的物理断点仍未取得，故保持
**OPEN / OBSERVING / WAITING_NETWORK_TRACE**。下次失败必须同秒保存 DNS、代理/TUN、aiohttp 实际目标与
HTTP 阶段；下一完整窗仍需成功率和尾延迟同时稳定。

## 2026-10-26 完整自然绿窗但尾延迟小幅回升

固定窗 24/24 个自然周期 completed、168/168 competitor 成功，累计 321.868 秒，周期 P50/P95/max 为
13.269/14.854/15.208 秒。较上窗多一个周期，累计耗时 +6.5%；P95/max 分别 +4.2%/+6.4%，仍远低于
2026-10-19 的 31.667/37.877 秒。三条约 1.1 秒的应用 SQL 没有任务失败、锁或长 Session，代表 executor
仍为毫秒级，不能将周期尾部归因到缺索引。

历史 DNS/TUN 故障的物理断点仍未在故障现场捕获，状态保持 **OPEN / OBSERVING /
WAITING_NETWORK_TRACE**。下一次失败必须同秒保存 DNS、代理/TUN、实际目标与 HTTP 阶段；成功率和尾延迟需继续
同时稳定。

## 2026-10-29 23 个自然周期继续全绿，max 小幅回升

固定窗受调度边界影响捕获 23 个自然周期，23/23 completed、161/161 competitor 成功，累计 290.868 秒，
周期 P50/P95/max 12.420/14.422/15.784 秒。相较上一窗 23 周期同口径累计 296.102 秒、P50/P95/max
12.400/15.299/15.464 秒，总耗时 -1.8%、P95 -5.7%，但 max +2.1%。未见 21173/21523、chunk exhausted、
stale tier、失败风暴、匹配慢 SQL 或长 Session。

历史 DNS/TUN 物理断点仍未在故障现场捕获，故保持 **OPEN / OBSERVING / WAITING_NETWORK_TRACE**；下次失败
仍须同秒保存 DNS、代理/TUN、aiohttp 实际目标和 HTTP 阶段，不能用连续绿窗反推历史根因已根治。

## 2026-10-30 完整 24 个自然周期继续全绿

固定窗 24/24 个自然周期 completed、168/168 competitor 成功，累计 305.007 秒，周期 P50/P95/max
12.481/14.196/15.433 秒；无失败风暴、匹配慢 SQL 或长 Session。相较上次同为 24 周期的窗口，尾部仍处于
稳定区间。

历史 DNS/TUN 故障的物理断点仍没有故障现场证据，因此保持 **OPEN / OBSERVING /
WAITING_NETWORK_TRACE**。下一次失败或周期超过 30 秒时必须同秒保存 DNS、代理/TUN、实际连接目标和 HTTP
阶段；连续绿窗是改善证据，不是根治证据。

## 2026-10-31 DNS 故障复发并完成显式代理运行替换

固定窗 24 个自然周期共 168 个 competitor 请求，138 成功、30 失败；01:56 周期仅 5/7，02:56–05:57
连续四周期 0/7，错误均为 `space.bilibili.com` 的 `nodename nor servname provided`。数据库操作为短事务，
无匹配长 Session 或慢 executor。故障现场确认进程已有 `https_proxy=http://127.0.0.1:7890`，代理端口在线且
代理 curl 返回 200；但 `bilibili_api` 的 AioHTTP client 显式传入内部 `proxy` 参数，默认空值，不会可靠继承
环境代理，因此仍落到间歇失败的 TUN fake-IP DNS 路径。

已 TDD 在 fetch 前把 `BILIBILI_INTELLIGENCE_PROXY`（优先）或现有 HTTPS/HTTP proxy 写入
`bilibili_api.request_settings`。使用现有凭据和真实 competitor 的只读探测经新路径成功返回 1 行；标准重启后
新 scheduler PID 已加载代码，代理端口仍在线。状态为 **OPEN / RUNTIME_REPLACED / OBSERVING**：下一自然小时
周期与完整后窗尚未发生，不能写成已解决；验收要求 7/7 且随后完整 24 小时 168/168，无 DNS 复发。

## 2026-11-01 后续自然窗验收：显式代理根因闭环

从 10-31 06:08 新 scheduler 加载显式代理修复后，固定窗捕获 07:08 至次日 05:15 共 23 个自然小时周期，
23/23 completed、161/161 competitor 请求成功，DNS/`nodename nor servname` 错误为 0；周期
P50/P95/max 为 11.657/14.646/14.725 秒。覆盖少一个 06 点周期是重启切点造成，不是失败或日志缺口。

该后窗同时具备根因代码、定向合同、标准重启、真实凭据探测和后续自然周期无复发证据，因此
`BILIBILI-DNS-TUN` 中“库内空 proxy 未继承进程代理”的根因升级为 **CLOSED / RUNTIME_ACCEPTED**。
仍保留外部 B站/DNS 可用性监控；若未来发生不同错误分类，应按新指纹重开，不把任何外部故障都归入本根因。
