# 两个不可用 YouTube 频道持续制造每日同步失败

## 基本信息

- 日期：2026-09-20
- 状态：OPEN / REPEATED / RUNTIME_REPLACED / WAITING_MANUAL_CONFIG / WAITING_NATURAL_RUNTIME
- 严重级别：P1
- 影响模块：YouTube 频道快照同步、最近三天视频同步、工作流通知
- 根因分类：已失效的外部来源仍保持本地启用；失败通知丢弃具体频道身份
- 复发键：`EXTERNAL-CALL-RESILIENCE` / `REGRESSION-ACCEPTANCE-INTEGRITY`
- 是否复发：是；频道同步自 09-10 起每天固定失败 2 个，视频同步自 09-09 起每天固定 error 2 个
- 防线层级：durable_source_liveness / observability / contract_test / manual_config_pending

## 修复前证据与影响

2026-09-20 00:03 的频道同步处理 51 个频道，成功 49、失败 2、耗时 13.57 秒；
01:00 的视频同步处理 49 个频道、errors=2，服务耗时 39.75 秒。两条独立调度链已经
连续多日以相同数量失败，但旧调度器只保留汇总计数，通知和完成日志均没有失败频道身份，
使每日巡检只能看到症状而无法直接追到来源。

只读数据库对账定位到两个仍为 `is_active=true` 且没有本窗快照的频道：

- `channels.id=21`，`Smartpet Video AI`，外部 ID `UCWzdWJ9tpBEd2BwMtkbWS-A`，最后快照 2026-09-09；
- `channels.id=56`，`数字指南针`，外部 ID `UCi6kbBNPjb7F-6LcHqUdTiA`，最后快照 2026-06-18。

2026-09-20 使用生产凭据对两个外部 ID 做只读批量 `channels.list`，YouTube Data API 返回
`pageInfo.totalResults=0` 与 `items=[]`。因此当前已证根因不是 PostgreSQL executor、索引、
Session 持有或 PgBouncer，而是远端频道已删除、私有或不可访问，本地来源生命周期没有停用。

## 已落地修复

- 频道同步与视频同步的 partial/failed 通知新增最多 10 条结构化失败详情：本地频道 id、名称与错误文本；同样写入调度器 warning，下一次失败可直接归因。
- 两条 TDD 回归分别锁定 `results` 与 `details` 的失败身份，修复前均因 `summary.failures` 缺失红灯，修复后相关 15 项测试通过。
- 生成 `docs/sql/disable_unavailable_youtube_channels_20260920.sql`：带精确三元组保护、5 秒锁超时、30 秒语句超时、预检、RETURNING、验证与回滚。脚本默认 `ROLLBACK`，未无人值守修改生产数据。

## 为什么仍未闭环

停用业务来源属于生产配置变更，且外部平台短时异常与永久删除需要人工最终判断；本轮没有
擅自把两个频道停用。即使观测代码已通过测试，也只有下一次正常调度器启动后才进入运行态，
且必须等 00:03 与 01:00 两个自然周期验证，不能以测试替代。

## 下一轮验收条件

1. 人工重新调用 YouTube API 复核两频道仍为 0 results，并在 DBeaver 审核后执行受保护 SQL；
2. 安全门通过后加载新调度代码，确认两个调度器 PID/started_at 已更新；
3. 下一次频道同步没有这两个来源后应为 49/49、失败 0；视频同步应 errors=0；
4. 若仍有失败，通知和日志必须给出具体频道身份与错误，按新指纹继续追根因；
5. 至少再观察一个后续自然周期无相同失败，方可从 OPEN/OBSERVING 改为 CLOSED。

## 2026-09-20 运行态替换

全量 1695/1695 通过后，重启安全门读取 46 条近期任务与 0 条活动 ZSXQ sync 并放行；
标准脚本冻结 17 个队列、关闭 API 入队并通过第二次门禁，未使用 FORCE 完成整栈替换。
新频道同步 PID 24049 于 06:22:50 启动，新视频同步 PID 24056 于 06:22:53 启动；
backend bootstrap 17/17 worker、6/6 scheduler、errors=[]，health 200。观测代码已进入运行态，
但来源配置仍未人工变更，且下一自然周期分别在 09-21 00:03 与 01:00，根因状态仍为 OPEN。

## 2026-09-21 自然周期复核

运行态观测已经生效并直接给出相同身份：00:03 频道同步 51 个，成功 49、失败 2，失败仍是
`id=21 Smartpet Video AI` 与 `id=56 数字指南针`；01:00 视频同步处理 49 个频道、errors=2，仍是
同两项。频道任务总耗时 16.22 秒，视频任务服务耗时 45.22 秒。说明观测修复通过自然周期验收，
但根因配置没有执行，故状态仍为 OPEN/REPEATED/WAITING_MANUAL_CONFIG。受保护 DBeaver 脚本继续保留，
不得用重复重试或忽略错误代替人工停用与后续双自然周期验收。

## 2026-09-22 自然周期复核

人工停用脚本仍未执行，两个失效来源继续启用并第三次自然复发。00:03 channel sync 处理 51 个来源，
成功 49、失败 2、耗时 14.18 秒；01:00 video sync 处理 49 个可枚举来源，新建 34、更新 42、errors=2，
耗时 43.20 秒。失败身份仍是 `id=21 Smartpet Video AI` 与 `id=56 数字指南针`，没有转化为数据库错误、
锁等待或重试风暴。

因此状态保持 OPEN/REPEATED/WAITING_MANUAL_CONFIG。责任动作不变：人工复核远端仍为 0 results 后在 DBeaver
逐段审核受保护脚本；随后必须由两个自然周期证明 channel 49/49、video errors=0，再观察一个后续周期。

## 2026-09-24 自然周期复核

人工停用脚本仍未执行，固定失效源继续第五个巡检窗口复发。`00:03` channel sync
处理 51 个来源，成功 49、失败 2、业务耗时 19.48 秒；`01:00` video sync 处理 49 个可枚举源，
新增 29、更新 60、`errors=2`、耗时 42.45 秒。两条链的失败身份仍是 `id=21 Smartpet Video AI`
与 `id=56 数字指南针`，原因分别为无法获取频道统计和上传播放列表。它们没有转化为 DB executor 慢、
Session hold 或无界重试，但每日都在制造必然失败和失败通知。状态保持
**OPEN / REPEATED / WAITING_MANUAL_CONFIG**，下一轮不得继续把相同计数当成新根因。

## 2026-09-25 自然周期复核

人工停用脚本仍未执行，第六个巡检窗口继续同一复发键。`00:03` channel sync 为 49/51，
业务耗时 16.56 秒（调度总耗时 18.82 秒）；`01:00` video sync 处理 49 个频道、新增 30、更新 53、
`errors=2`，耗时 52.42 秒。失败仍精确为 `id=21 Smartpet Video AI` 与 `id=56 数字指南针`。
没有数据库错误、长 Session 或无界重试，但两个确定失败源持续制造失败通知。状态保持
**OPEN / REPEATED / WAITING_MANUAL_CONFIG**；验收条件不变。

## 2026-09-26 自然周期复核

人工停用脚本仍未执行，第七个巡检窗口继续命中同一复发键。`00:03` channel sync 处理 51 个来源，
成功 49、失败 2，业务耗时 17.18 秒（调度总耗时 19.30 秒）；`01:00` video sync 处理 49 个可枚举来源，
新增 22、更新 57、`errors=2`，耗时 47.81 秒。失败仍精确为 `id=21 Smartpet Video AI` 与
`id=56 数字指南针`。未见数据库 timeout、Session 长持有或无界重试，但确定失败源仍持续制造失败通知。
状态保持 **OPEN / REPEATED / WAITING_MANUAL_CONFIG**；受保护 DBeaver 脚本与验收条件不变。

## 2026-09-27 自然周期复核

人工停用脚本仍未执行，第八个巡检窗口继续命中同一复发键。`00:03` channel sync 处理 51 个来源，
成功 49、失败 2，业务耗时 18.43 秒（调度总耗时 20.66 秒）；`01:00` video sync 处理 49 个来源，
新增 27、更新 49、`errors=2`，耗时 38.88 秒。失败仍精确为 `id=21 Smartpet Video AI` 与
`id=56 数字指南针`。数据库错误、长 Session 和无界重试均未捕获，但确定失败源继续制造每日失败通知。
状态保持 **OPEN / REPEATED / WAITING_MANUAL_CONFIG**；受保护 DBeaver 脚本与双自然周期验收条件不变。

## 2026-09-28 自然周期复核

人工停用脚本仍未执行，两个失效来源在第九个巡检窗口继续启用。`00:03` channel sync 处理 51 个来源，
成功 49、失败 2，业务耗时 14.17 秒（调度总耗时 16.21 秒）；`01:00` video sync 处理 49 个来源，
新增 21、更新 44、`errors=2`，耗时 38.11 秒。失败仍精确为 `id=21 Smartpet Video AI` 与
`id=56 数字指南针`，生产库 `is_active` 也仍为 true。状态继续
**OPEN / REPEATED / WAITING_MANUAL_CONFIG**；不得用重试或忽略错误替代人工停用与后续双自然周期验收。

## 2026-09-29 自然周期复核

人工停用脚本仍未执行，第十个巡检窗口继续精确命中同一两个失效源。`00:03` channel sync 为 49/51，
业务耗时 15.28 秒（调度总耗时 17.34 秒）；`01:00` video sync 处理 49 个来源、`errors=2`、耗时
42.99 秒。失败仍是 `id=21 Smartpet Video AI` 与 `id=56 数字指南针`，没有 DB timeout、长 Session
或重试风暴。状态保持 **OPEN / REPEATED / WAITING_MANUAL_CONFIG**；下一步仍是人工复核并执行受保护停用
脚本，随后以 channel 49/49 和 video errors=0 的两个自然周期验收。

## 2026-10-14 自然周期复核

人工停用脚本仍未执行，第十一次巡检窗口继续命中 `id=21 Smartpet Video AI` 与 `id=56 数字指南针`。
00:03 channel sync 共 51 个来源，45 成功、6 失败；01:00 video sync 处理 49 个来源、新增 20、更新 66、
`errors=2`。固定两源仍包含在失败详情中；其余新增失败与同窗 Google API SSL EOF/读超时相关，必须按瞬时外部
链路与永久不可用来源分开统计，不能扩大停用范围。

状态保持 **OPEN / REPEATED / WAITING_MANUAL_CONFIG**。责任动作仍是人工只读复核 21/56 后在 DBeaver
执行受保护脚本；随后以 channel 49/49、video errors=0 连续两个自然周期验收。

## 2026-10-15 第十二个巡检窗口复核

人工停用脚本仍未执行，`id=21 Smartpet Video AI` 与 `id=56 数字指南针` 继续作为固定失败源出现。
本窗两个 `videos/sync` 请求分别耗时 48.451/89.596 秒，一个 `channels/sync/all` 为 31.618 秒；它们都没有
匹配慢 SQL，额外失败明确来自 Google API 30 秒 read timeout。首轮视频同步处理 49 个来源，次轮因瞬时外部
超时只处理 45 个；固定两源与瞬时网络失败仍须分开，禁止扩大停用范围。

状态保持 **OPEN / REPEATED / WAITING_MANUAL_CONFIG**。责任动作仍是人工复核 21/56 后在 DBeaver 执行
`docs/sql/disable_unavailable_youtube_channels_20260920.sql`，随后以 channel 49/49、video errors=0 连续两个
自然周期验收；外部 read timeout 另按瞬时链路观察。

## 2026-10-16 第十三个巡检窗口复核

人工停用脚本仍未执行。自然 channel sync 为 51 个来源、49 成功/2 失败、业务耗时 12.87 秒；自然 video
sync 处理 49 个来源、新增 21、更新 58、`errors=2`、耗时 39.20 秒，固定失败仍是 `id=21` 与 `id=56`。
同窗手工页面同步分别出现 49.581 秒和 62.791 秒的 Google SSL EOF/30 秒 read timeout，且没有匹配慢 SQL；
这些瞬时网络失败与两个永久不可用来源继续分开管理。

状态保持 **OPEN / REPEATED / WAITING_MANUAL_CONFIG**。责任动作仍是人工在 DBeaver 审核并执行
`docs/sql/disable_unavailable_youtube_channels_20260920.sql`，随后以 channel 49/49、video errors=0 的连续
两个自然周期验收。

## 2026-10-17 第十四个巡检窗口复核

人工停用脚本仍未执行。00:03 自然 channel sync 为 51 个来源、49 成功/2 失败，业务耗时 21.74 秒；
01:01 自然 video sync 处理 49 个来源、新增 22、更新 56、`errors=2`、耗时 59.65 秒，固定失败仍为
`id=21 Smartpet Video AI` 与 `id=56 数字指南针`。同窗页面手工同步 max 60.294 秒且无匹配慢 SQL，
继续按外部 I/O 与永久不可用来源分开处理。

状态保持 **OPEN / REPEATED / WAITING_MANUAL_CONFIG**。责任动作仍是在 DBeaver 只读复核后执行
`docs/sql/disable_unavailable_youtube_channels_20260920.sql`，随后以 channel 49/49、video errors=0 连续
两个自然周期验收。

## 2026-11-01 第二十九个巡检窗口复核

人工停用脚本仍未执行。00:03 自然 channel sync 仍为 51 个来源、49 成功/2 失败，失败固定为
`id=21 Smartpet Video AI` 与 `id=56 数字指南针`；01:00 video sync 处理 49 个来源、新增 25、更新 54、
`errors=2`，失败仍精确为 21/56，耗时 67.32 秒且没有匹配慢 SQL或 Session 长持有。

状态继续 **OPEN / REPEATED / WAITING_MANUAL_CONFIG**。只允许人工在 DBeaver 复核后执行
`docs/sql/disable_unavailable_youtube_channels_20260920.sql`；验收仍是连续两个自然周期 channel 49/49、
video errors=0，不能把第 29 个同形窗口视为瞬时网络问题。

## 2026-10-18 第十五个巡检窗口复核

人工停用脚本仍未执行。00:03 自然 channel sync 为 51 个来源、49 成功/2 失败，耗时约 20.77 秒；
01:01 自然 video sync 处理 49 个来源、`errors=2`、耗时约 60.44 秒。固定失败仍精确为
`id=21 Smartpet Video AI` 与 `id=56 数字指南针`，且没有匹配慢 SQL、Session 长持有或重试风暴。

状态保持 **OPEN / REPEATED / WAITING_MANUAL_CONFIG**。责任动作仍是在 DBeaver 审核并执行
`docs/sql/disable_unavailable_youtube_channels_20260920.sql`，随后以 channel 49/49、video errors=0 的连续
两个自然周期验收。

## 2026-10-19 第十六个巡检窗口复核

人工停用脚本仍未执行。00:03 自然 channel sync 为 51 个来源、49 成功/2 失败；01:00 video sync 处理
49 个来源、新增 17、更新 62、`errors=2`。固定失败仍精确为 `id=21 Smartpet Video AI` 与
`id=56 数字指南针`，且没有匹配慢 SQL、Session 长持有或重试风暴。

状态保持 **OPEN / REPEATED / WAITING_MANUAL_CONFIG**。责任动作仍是在 DBeaver 审核并执行
`docs/sql/disable_unavailable_youtube_channels_20260920.sql`，随后以 channel 49/49、video errors=0 的连续
两个自然周期验收。

## 2026-10-20 第十七个巡检窗口复核

人工停用脚本仍未执行。00:03 自然 channel sync 处理 51 个来源，仅 43 成功、8 失败，失败率 15.69%、
耗时 68.73 秒；固定 `id=21/56` 仍在，并新增 64/31/34/24/2/20 六个瞬时失败。01:00 video sync 仍处理
49 个来源、`errors=2`、耗时 36.54 秒，失败精确为 21/56。固定永久失败与瞬时外部链路失败必须分开统计，
不得把新增六源一并停用。

状态继续 **OPEN / REPEATED / WAITING_MANUAL_CONFIG**，且 channel 链路较上一窗 49/51 明显恶化。
责任动作仍是人工在 DBeaver 只读复核后执行
`docs/sql/disable_unavailable_youtube_channels_20260920.sql`；随后要求连续两个自然周期 channel 49/49、
video errors=0。六个瞬时源若再次出现，应单独采 Google API 错误类型、目标与网络现场。

## 2026-10-21 第十八个巡检窗口复核

人工停用脚本仍未执行。00:03 自然 channel sync 为 51 个来源、45 成功/6 失败、业务耗时 43.28 秒；固定
`id=21 Smartpet Video AI` 与 `id=56 数字指南针` 仍在，另有 49/48/55/57 四个瞬时失败。01:00 video
sync 处理 49 个来源、新增 31、更新 56、`errors=2`，失败仍精确为 21/56。相较上一窗 channel 43/51
有所恢复，但永久两源第 18 窗复发，不能标改善闭环。

状态继续 **OPEN / REPEATED / WAITING_MANUAL_CONFIG**。责任动作仍是在 DBeaver 只读复核后仅对 21/56
执行受保护脚本；随后要求连续两个自然周期 channel 49/49、video errors=0。四个瞬时源不得扩大停用范围。

## 2026-10-22 第十九个巡检窗口复核

人工停用脚本仍未执行。00:03 自然 channel sync 为 51 个来源、49 成功/2 失败、耗时 14.84 秒；01:00
video sync 处理 49 个来源、新增 26、更新 65、`errors=2`、耗时 40.23 秒。失败仍精确为
`id=21 Smartpet Video AI` 与 `id=56 数字指南针`，本窗没有上一窗额外四个瞬时失败，链路有所改善，但
永久两源第 19 窗复发。

状态继续 **OPEN / REPEATED / WAITING_MANUAL_CONFIG**。仍只允许人工在 DBeaver 复核后对 21/56 执行
`docs/sql/disable_unavailable_youtube_channels_20260920.sql`；随后要求连续两个自然周期 channel 49/49、
video errors=0。

## 2026-10-23 第二十个巡检窗口复核

人工停用脚本仍未执行。00:03 自然 channel sync 为 51 个来源、49 成功/2 失败，耗时约 18.23 秒；01:00
video sync 处理 49 个来源、`errors=2`、耗时约 43.02 秒。失败继续精确为
`id=21 Smartpet Video AI` 与 `id=56 数字指南针`，且没有匹配慢 SQL、长 Session 或失败重试风暴。

状态继续 **OPEN / REPEATED / WAITING_MANUAL_CONFIG**。只允许人工在 DBeaver 复核后对 21/56 执行
`docs/sql/disable_unavailable_youtube_channels_20260920.sql`；随后要求连续两个自然周期 channel 49/49、
video errors=0。

## 2026-10-24 第二十一个巡检窗口复核

人工停用脚本仍未执行。自然 channel sync 处理 51 个来源、49 成功/2 失败，业务耗时 22.51 秒；自然
video sync 处理 49 个可枚举来源、新增 24、更新 65、`errors=2`，耗时 49.08 秒。失败继续精确为
`id=21 Smartpet Video AI` 与 `id=56 数字指南针`，没有匹配慢 SQL、长 Session 或任务重试风暴。

状态继续 **OPEN / REPEATED / WAITING_MANUAL_CONFIG**。只允许人工在 DBeaver 复核后对 21/56 执行
`docs/sql/disable_unavailable_youtube_channels_20260920.sql`；验收仍是连续两个自然周期 channel 49/49、
video errors=0，不能把第 21 次同形失败当作瞬时网络问题。

## 2026-10-25 第二十二个巡检窗口复核

人工停用脚本仍未执行。自然 channel sync 处理 51 个来源、49 成功/2 失败，耗时 21.940 秒；自然 video sync
处理 49 个来源、新增 31、更新 54、`errors=2`。失败继续精确为 `id=21 Smartpet Video AI` 与
`id=56 数字指南针`，没有匹配慢 SQL、长 Session 或重试风暴。

状态继续 **OPEN / REPEATED / WAITING_MANUAL_CONFIG**。只允许人工在 DBeaver 复核后对 21/56 执行
`docs/sql/disable_unavailable_youtube_channels_20260920.sql`；验收仍是连续两个自然周期 channel 49/49、
video errors=0，不能把第 22 次同形失败归入瞬时网络问题。

## 2026-10-26 第二十三个巡检窗口复核

人工停用脚本仍未执行。自然 channel sync 处理 51 个来源、49 成功/2 失败，业务耗时 21.410 秒；自然 video sync
处理 49 个来源、新增 14、更新 70、`errors=2`，约 52.460 秒完成。失败继续精确为
`id=21 Smartpet Video AI` 与 `id=56 数字指南针`，没有匹配慢 SQL、长 Session 或任务重试风暴。

状态继续 **OPEN / REPEATED / WAITING_MANUAL_CONFIG**。只允许人工在 DBeaver 复核后对 21/56 执行
`docs/sql/disable_unavailable_youtube_channels_20260920.sql`；验收仍是连续两个自然周期 channel 49/49、
video errors=0，不能把第 23 次同形失败归入瞬时网络问题。

## 2026-10-27 第二十四个巡检窗口复核

人工停用脚本仍未执行。自然 channel sync 处理 51 个来源、49 成功/2 失败，业务耗时 24.32 秒；自然 video
sync 处理 49 个可枚举来源、新增 28、更新 57、`errors=2`。失败继续精确为 `id=21 Smartpet Video AI`
与 `id=56 数字指南针`，没有匹配慢 SQL、长 Session 或任务重试风暴。

状态继续 **OPEN / REPEATED / WAITING_MANUAL_CONFIG**。只允许人工在 DBeaver 复核后对 21/56 执行
`docs/sql/disable_unavailable_youtube_channels_20260920.sql`；验收仍是连续两个自然周期 channel 49/49、
video errors=0，不能把第 24 次同形失败归入瞬时网络问题。

## 2026-10-28 第二十五个巡检窗口复核

本窗不是固定两源的普通复发：00:03 channel sync 由 49/51 恶化为 **0/51**，业务耗时 12.83 秒；01:00
video sync 同样 **0/51**，耗时 21.92 秒。全部来源分别只留下“无法获取频道统计数据”和“无法获取频道上传
播放列表”，旧实现没有保留 HTTP status/provider reason，因此不能把这次全量失败归因到 21/56，也不能据此
扩大停用范围。08:33 当前凭据只读探测恢复成功，说明不是持续性不可用；历史精确上游原因保持 UNKNOWN。

已新增安全错误分类与密钥脱敏合同并标准运行替换，下一自然周期可直接观测 endpoint/status/reason 或网络异常
类型。固定 21/56 的人工停用动作仍不变；全量瞬时故障另保持 **OPEN / RUNTIME_REPLACED / OBSERVING**，要求
00:03/01:00 自然周期和随后后窗验证，不能以当前探测成功写成已解决。

## 2026-10-29 第二十六个巡检窗口复核

人工停用 21/56 的脚本仍未执行。本窗 00:03 channel 再次 0/51，业务耗时 57.16 秒；01:00 video 同样
0/51，业务耗时 96.63 秒。上一轮安全错误合同已在两条自然任务中运行：全部样本精确为
`API network failure endpoint=channels type=SSLError`，且未输出 API key。由此可以接受“诊断合同已替换”，
但业务根因已明确转为全来源的间歇 TLS/代理/网络路径，而不是 21/56 的永久失效。

窗后经本机代理和绕过代理的 TLS 请求均得到预期无 key 403，项目真实凭据 `test_api_connection()` 也成功；
这只证明当前恢复，不能在没有故障同秒 A/B 的情况下判断本地代理、Google edge、证书路径或调度时网络谁是根因。
状态保持 **OPEN / REPEATED / OBSERVING / WAITING_NETWORK_TRACE**。下一次 SSL 复发必须同秒保留 proxy/direct
TLS 阶段、运行体 PID 与代理环境；不得用无界 retry 掩盖。21/56 的人工脚本范围不扩大。

## 2026-10-30 第二十七个巡检窗口复核

上一窗全来源 `channels/SSLError` 本窗未复发：00:03 channel 处理 51 个来源、49 成功/2 失败，业务耗时
15.72 秒；01:00 video 处理 49 个可枚举来源、新增 22、更新 56、`errors=2`。失败又精确回到
`id=21 Smartpet Video AI` 与 `id=56 数字指南针` 的永久不可枚举形态；没有匹配慢 SQL、长 Session 或
失败重试风暴。

这只构成全来源 TLS 故障的一个恢复窗口，不能标为根治；固定 21/56 则是第 27 窗复发。状态分别保持
**OPEN / OBSERVING / WAITING_NETWORK_TRACE** 与 **OPEN / REPEATED / WAITING_MANUAL_CONFIG**。人工动作仍仅限
DBeaver 复核并执行 `docs/sql/disable_unavailable_youtube_channels_20260920.sql`；随后需连续两个自然周期
channel 49/49、video errors=0，同时继续观察全来源 SSL 不复发。

## 2026-10-31 第二十八个巡检窗口复核

00:03 channel 仍为 49/51，01:00 video 处理 49 个可枚举来源、新增 26、更新 56、`errors=2`；失败继续
精确为 `id=21 Smartpet Video AI` 与 `id=56 数字指南针`。上一窗全来源 SSL 故障未复发，但固定两源已连续
第 28 个窗口复发，且无匹配慢 SQL、长 Session 或任务重试风暴。

状态保持 **OPEN / REPEATED / WAITING_MANUAL_CONFIG**。人工动作仍仅限 DBeaver 复核并执行
`docs/sql/disable_unavailable_youtube_channels_20260920.sql`；验收为连续两个自然周期 channel 49/49、video
errors=0。未执行脚本前不能把稳定的 2 个失败当成可接受基线。

## 2026-11-02 第二十九个巡检窗口复核

00:03 channel 仍为 49/51、业务耗时 10.75 秒；01:00 video 处理 49 个可枚举来源、新增 20、更新 58、
`errors=2`、耗时 32.55 秒。失败继续精确为 `id=21 Smartpet Video AI` 与 `id=56 数字指南针`，没有匹配
慢 SQL、长 Session 或任务重试风暴。

状态保持 **OPEN / REPEATED / WAITING_MANUAL_CONFIG**。人工动作仍仅限 DBeaver 逐条复核后执行
`docs/sql/disable_unavailable_youtube_channels_20260920.sql`；验收仍为连续两个自然周期 channel 49/49、
video errors=0。本轮不无人值守修改生产数据。
