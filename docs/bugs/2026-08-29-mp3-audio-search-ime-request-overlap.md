# MP3 音频素材搜索在输入法组合期间并发叠加

## 基本信息

- 状态：RUNTIME_REPLACED / OBSERVING
- 严重级别：高
- 影响模块：MP3 视频编排音频素材搜索 / `content_pipeline_assets` / `publication_tasks`
- 首次发现：2026-08-29 每日数据库性能巡检
- 修复提交：当前工作区；v4 静态资源和 catalog snapshot 已由运行中 backend 提供
- 根因分类：输入法组合事件 / debounce 不等于 single-flight / 慢网络请求重叠
- 复发键：`HOT-PATH-PAYLOAD-POLLING`
- 是否复发：是；属于前端请求缺少背压的同类入口
- 关联记录：`2026-08-28-mp3-history-polling-overlap.md`、`2026-08-25-mp3-detail-hidden-publish-panel-request-fanout.md`
- 防线层级：shared_guardrail / contract_test / runtime_observation

## 现象与影响

固定窗口 `2026-08-28 06:02:19` 至 `2026-08-29 06:02:19` 中，`GET /api/mp3-video-workflows/audio-assets` 14 次、累计 109.805 秒、P95 20.029 秒、最大 22.828 秒，关联 15 条慢 SQL、累计 88.731 秒，是本轮页面/API 累计耗时与最大耗时最高的端点。

18:25 的真实输入序列在前一请求尚未返回时依次发出 `q=t`、`q=tian'feng`、`q=天风`、`q=天`。基础与搜索两类主要 SQL 指纹共 12 次、累计 83.861 秒，最慢 19.924 秒。多次请求又因 canonical 文件过滤继续取下一 chunk，放大了远端数据库往返和连接占用。

## 根因

旧实现只有 350ms debounce 和响应序号丢弃。debounce 只能合并短时间内尚未发出的 timer，不能取消已经发出的请求；响应序号只能防止旧响应覆盖 UI，也不能阻止旧 SQL 继续执行。中文输入法在 `compositionstart` 到 `compositionend` 之间产生多个 input 事件，叠加 10–20 秒远端数据库长尾后形成并发请求。

现场 `q=天风, limit=40` 的 `EXPLAIN (ANALYZE, BUFFERS)` 返回 25 行，executor 102.923ms；同形应用 SQL 可达 14.684 秒。根因不是缺索引，而是输入事件所有权缺失和 PgBouncer/网络长尾叠加。

## 实际方案

- 组合输入期间清除搜索 timer，不发请求；`compositionend` 后只调度最终搜索词。
- Enter 在 `event.isComposing` 或本地 composing 状态时不触发搜索。
- `loadAudioAssets` 增加单一 drain owner；同一时刻最多一个请求在途，等待期间的新搜索只保留最新参数，旧响应完成后直接执行最新请求。
- 后端同时把 authoritative cleaned asset 目录构建为有界、加锁 single-flight、默认 300 秒 TTL 的快照；搜索等待期间不会因为首个关键词再次扫描远端目录。用户刷新显式传 `refresh=1`，文件存在性仍逐请求复核。
- 保留完整字段、canonical 文件过滤、分页和搜索语义；没有增加 timeout、删除字段、伪造资产或增加索引。后端根因详见 `2026-08-29-mp3-audio-asset-stale-candidate-scan.md`。
- 静态版本更新为 `20260829-cleaned-audio-assets-v4-catalog-snapshot`。

## 验证与后续

- TDD 红灯：旧类缺少 composition handler，契约明确失败。
- 修复后契约验证组合输入不发请求、完成后只发最终关键词，且慢请求期间多个搜索只串行执行首个与最后一个。
- JavaScript 语法和既有 cleaned asset/lazy mount 契约通过；真实 backend 已返回 v4 HTML 与 JS。
- 新 browser 实例中执行“先发强制冷刷新，再连续输入多个中间词并以 `天风` 结束”：只出现首请求 `request_id=499a067d`（1.219 秒）和最终请求 `request_id=faabd527`（1.53ms），中间词没有发出；最终请求在首请求完成 94ms 后开始，同页无重叠，最终显示 2 个真实文件。
- 当前 in-app Browser 控制层不能原生注入 macOS 输入法 composition 事件，因此真实中文候选窗仍是观测边界；compositionstart/end 的行为由前端契约覆盖，不能把契约测试冒充真实输入法证据。下一完整窗口仍要求同时在途不超过 1、组合期间为 0、只执行首尾关键词且 P95 明显低于 20.029 秒；未达到不得 CLOSED。

## 2026-08-29 重启后只读验收

第一次 v3 重启后，真实只读 `q=天风&page=1&per_page=40` 为 0.810 秒；随后真实页面又暴露默认请求 `request_id=53df69ee` 23.347 秒和快速输入 `q=t` 的 `request_id=b9ac5005` 20.108 秒。它证明前端 latest-only 不能单独根治慢目录，状态因此未提前关闭。

补齐 authoritative catalog snapshot 并第二次安全重启后，冷页面 `request_id=ea2496e3` 为 1.009 秒，暖搜索 `request_id=15b783b2` 为 1.59ms 且不访问目录 SQL；上述冷刷新/快速输入序列也已无请求重叠。代码与真实页面已运行替换，但 native composition 和完整 24 小时均未覆盖，继续保持 OBSERVING。

目录查询后的 Session/文件 I/O 边界进一步收紧并再次安全重启后，最终 backend PID 52574 上的真实强刷 `request_id=3708b3b0` 为 1.094 秒，随后暖搜索 `q=天风总` 的 `request_id=108830e2` 为 3.02ms、0 条目录 SQL，页面正确收敛为 1 项；仍未出现请求重叠或长 Session 告警。native composition 与完整 24 小时边界不变，状态仍为 OBSERVING。

## 2026-08-29 请求所有权 telemetry 与最终运行态复验

为消除“服务端只看见请求、看不见哪个页面事件拥有请求”的证据缺口，前端 v5 为每个页面实例生成
稳定 `client_id`，并给每次目录请求附加单调 `client_seq`、有限枚举 `client_trigger` 和
`client_inflight`。后端只记录经过白名单/长度校验的这些所有权字段及 cache telemetry，不持久化搜索词，
结构化标记为 `[MP3_AUDIO_ASSET_CLIENT]`。该 telemetry 只用于关联 request id、页面实例与触发来源，
不替代前端 single-flight 本身。

第一次加载 telemetry 的 backend PID 55314 上，同一页面实例
`mp3-mte7etld-yzghurae` 依次记录：`seq=1/initial` 的 `request_id=7c7c4e07`
耗时 11.812 秒，`seq=2/debounce` 的 `09a7c27c` 命中缓存并收敛为 1 项，
`seq=3/refresh` 的 `df491264` 强刷耗时 1.661 秒；三个请求的 `client_inflight` 均为 1。
这证明前端 owner 没有重叠，但也再次证明网络长尾可让单一合法首请求变慢，不能把“无并发”误写成
“底层数据库链路已解决”。

同一页面还暴露了独立冷启动放大器：构造器并发加载目录、任务历史和 worker 状态，而 worker 徽标旧代码
误用 `/api/v1/celery/status`，触发跨业务表完整快照。现已在 v6 改用仅查询
`mp3_video_workflow,render` 两个进程和精确 Redis 队列的
`/api/v1/celery/runtime-status`；后端在进程/Redis I/O 前释放鉴权 Session，并通过契约禁止
`_business_tasks_snapshot` 与 broker inspect。最终 PID 57273、真实页面边界 18:06:17 后：

- 初始目录 `request_id=b95c18bf` 1.503 秒，SQL 829.17/392.35ms；
- 轻量 worker 状态 `fbe6f3a8` 377.78ms、0 条业务 SQL；任务历史 `45311060` 377.36ms；
- 同一 client `mp3-mte7u2px-08oavkxj` 的暖搜索 `request_id=7c4e30ab` 为
  `seq=2/debounce/inflight=1`、cache hit、5.8ms、返回 1 个真实文件；
- 37 秒窗口共 12/12 请求成功，累计 2.698 秒、P95 377.78ms、最大 1.503 秒，慢 SQL、长 Session、
  SQL error 和六类 PgBouncer 错误均为 0；没有再调用全量 `/api/v1/celery/status`。

请求所有权与页面冷启动放大均已由新代码和真实页面替换，状态仍是
`RUNTIME_REPLACED / OBSERVING`：native macOS composition 尚无真实候选窗样本，且本轮 17:54 的
网络长尾发生在新 owner 已生效后。下一轮必须从最终边界验证完整 24 小时所有请求
`client_inflight<=1`、trigger/seq 可关联、composition 期间请求为 0、旧全量状态端点不再由该页调用；
任何一项缺失都不得 CLOSED。
