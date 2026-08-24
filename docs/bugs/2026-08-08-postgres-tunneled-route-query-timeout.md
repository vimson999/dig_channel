# PostgreSQL 长连接经代理虚拟隧道反复黑洞超时

## 基本信息

- 状态：公网物理绕过与 Tailscale 两轮隔离后仍有应用 query timeout 和 server socket crash；`RUNTIME_MITIGATED/OPEN`
- 严重级别：P0
- 影响模块：全部数据库页面/API / Celery / scheduler / PgBouncer / 本机代理网络
- 首次发现：2026-08-08 日巡检确认；同类 60 秒连接长尾此前已多轮出现
- 修复提交：不适用；需要人工调整本机代理/VPN 路由
- 根因分类：PostgreSQL 长连接经过 TUN 捕获路径、网络黑洞待隔离、PgBouncer query timeout、直连出口 HBA
- 复发键：`DB-SESSION-LIFECYCLE`
- 是否复发：是；2026-07-22 冷连接、2026-08-04/06 HBA 与 2026-08-07 60 秒长尾之后再次出现
- 关联记录：`2026-07-22-pgbouncer-cold-pool-connect-timeout.md`、`2026-08-04-postgres-hba-egress-ip-outage.md`、`2026-08-07-visual-storyboard-wide-polling-network-timeout.md`
- 防线层级：observability / operational_guardrail / runtime_acceptance

## 现象

2026-08-07 06:02:19 至 2026-08-08 06:02:19，PgBouncer 出现 12 个去重
`query timeout` 和 2 个 `connect timeout`，较上一窗口 10/1 恶化。应用出现 147 次超过
5 秒的连接持有，P95 60.485 秒；20 次托管进程长持有没有匹配的已完成慢 SQL。

用户可见样本包括 MP3 列表 request `4c3764ae` 返回 503/60.347 秒、PDF 资产列表
`da65bdfb` 返回 500/61.204 秒、平台凭据 `43f0462b` 返回 500/61.932 秒、PDF 证据图片
`1730dbc0` 返回 500/61.044 秒。后台 ZSXQ 与 watchdog 也在同一时段出现 60 秒持有。

## 影响

- 正确使用主键、唯一索引或小批量 ID 的查询也可能等待满 60 秒并返回 500/503。
- 同一网络抖动同时影响 Web、worker 和 scheduler，不能按单表分别增加索引。
- PgBouncer 保留热连接只能避免冷连接建立成本，无法修复已经被代理链路黑洞的数据库 socket。

## 根因定位与待隔离边界

本机到 PostgreSQL `121.4.126.31` 的实际路由为 `gateway=198.18.0.1`、
`interface=utun8`；系统 HTTP/HTTPS/SOCKS 代理同时指向 `127.0.0.1:7890`。PgBouncer
`SHOW SERVERS` 的六条远端连接也全部显示本地地址 `198.18.0.1`，证明数据库 TCP 长连接被
TUN 捕获，而不是由 PgBouncer 直接使用物理接口。

PgBouncer timeout 样本中的服务端连接年龄从数分钟到数小时不等，排除“只是一条过老连接”的单一解释；
03:54–04:12 还出现 query/connect timeout 簇，符合代理隧道短时黑洞。相同 ZSXQ 20-ID 快照查询
在 PostgreSQL 端使用 `uq_zsxq_topics_group_tab_topic`，executor 为 0.154ms，但应用日志最大
31.729 秒；`publication_tasks` 主键查询 executor 也仅 0.024ms。故障边界位于 PostgreSQL
executor 之外的本机 TUN/物理出口/传输路径，不是锁或缺索引。

2026-08-08 11:18 的跟进验收修正了最初过强的归因：Mihomo 运行日志显示数据库连接在调整前后
都命中既有 `GeoIP(cn) -> DIRECT`；普通 DIRECT 只决定 TUN 捕获后的出站策略，不会让 PgBouncer
socket 脱离 `utun8`。11:03 新建、明确命中 DIRECT 的连接仍在 11:05 出现一次 query timeout，
因此“代理节点导致黑洞”不能作为已证实根因。当前可证实的是 TUN/网络边界，仍需用
`route-exclude-address` 做物理绕过 A/B，才能区分 TUN 栈、物理出口和远端链路。

## 解决思路

1. 先由数据库管理员允许稳定物理出口；本次直连尝试曾被 PostgreSQL 以
   `no pg_hba.conf entry for host "223.80.177.86"` 拒绝，未解决白名单前不能强切。
2. 在 Mihomo **实际加载的** `tun` 配置中加入 `route-exclude-address: [121.4.126.31/32]`；
   只加 `IP-CIDR,...,DIRECT` 不会绕过 TUN。重载后用 controller `/rules`、`route -n get` 验证。
3. 路由切换后重建 PgBouncer 六条服务端连接，避免复用旧 TUN socket。
4. 用 `route -n get`、PgBouncer `SHOW SERVERS/POOLS`、连续真实 API/worker 负载和下一完整 24 小时
   日志共同验收；不能只用一次连通测试或一次查询成功代替。

## 实际方案

### 2026-08-08 11:34 物理绕过已落地

- 确认 Clash Verge Rev 2.5.1 当前订阅绑定的 Merge 为 `m3rxIUqCsOcJ.yaml`；此前修改的
  通用 `Merge.yaml` 未进入当前运行配置。
- 在当前订阅 Merge 和 Clash Verge GUI 管理的 TUN 配置中加入
  `route-exclude-address: [121.4.126.31/32]`，重新激活订阅后，Mihomo 实际运行配置已包含该字段。
- `route -n get 121.4.126.31` 已从 `gateway=198.18.0.1/interface=utun8` 变为
  `gateway=192.168.12.1/interface=en1`。
- PostgreSQL HBA 已精确放行当时物理出口 `223.80.177.86/32`；配置校验通过并 reload，未重启数据库。
- 使用项目脚本重建本机 PgBouncer 后端连接后，`SHOW SERVERS.local_addr` 已从
  `198.18.0.1` 变为 `192.168.12.104`。PostgreSQL 端观测到的客户端地址为
  `223.80.177.86/32`。
- PostgreSQL 真实只读查询、Redis DB 和 Celery broker 的 `PING` 均成功。100 次、10 并发的
  正确事务归还 `SELECT 1` 测试全部成功，P50/P95/max 为 72.63/232.39/240.92ms；切换后的
  短窗口未出现 query timeout、connect timeout、HBA 或 server login failure。
- 当前状态由 `OPEN/ISOLATION_PENDING` 提升为 `ISOLATED/OBSERVING`。仍需后续完整 24 小时
  真实 API/worker 窗口为 0 timeout、0 个 60 秒 Session，才允许关闭问题。

### 2026-08-09 Tailscale 私网复核：第二轮隔离仍未闭环

- 15:12:59 后 PgBouncer 目标切换为 Tailscale `100.92.99.39`；当前路由为 `utun15`、
  MTU 1280，Tailscale 显示到数据库节点为 direct UDP、约 26ms，不经过 DERP。公网地址
  `121.4.126.31` 仍由物理 `en1` 路由。
- Tailscale 窗口应用慢 SQL 291 条，P50/P95/max 1.637/5.270/15.792s，总耗时
  650.760s；37 个真实 HTTP 请求全部成功，P95 12.059s、最大 32.041s。
- 仍有 5 个去重 query timeout：4 个来自一次性诊断进程，1 个来自真实 request
  `d48fbee4` 的 `/api/v1/celery/status`。该业务快照在应用等待 20s 后被 PgBouncer 取消，
  HTTP 虽返回 200但耗时 32.041s；同 SQL 当前 `EXPLAIN ANALYZE` executor 仅 0.086ms。
- PgBouncer 在私网窗口仍记录多次 `server conn crashed?`。因此 Tailscale 降低了失败率，
  但没有证明网络/长连接黑洞根因已消失；短时 API smoke 无新 timeout 只能作为观察证据。
- 下一轮必须将诊断进程与托管应用分栏，并要求完整 24 小时托管应用 query/connect timeout=0、
  server crash=0、无 20/60 秒连接持有后，才能恢复 `ISOLATED` 或进一步关闭。

### 原始巡检阶段

- 本轮只完成只读定位、Bug/recurrence 登记与人工动作说明。
- 未修改系统路由、代理规则、生产连接串或 PgBouncer 运行配置；这些操作超出无人值守项目巡检权限。
- 未添加索引、未调高超时、未用重试或假数据掩盖失败。
- 用户随后把 `IP-CIDR,121.4.126.31/32,DIRECT,no-resolve` 写入 Clash Verge merge 配置；
  Mihomo controller 的 577 条运行规则中没有该条目，新连接仍命中第 433 条 `GeoIP(cn) -> DIRECT`。
- 在 `sv_active=0` 时已执行 PgBouncer `RECONNECT show_ytb_data`。六条后端 socket 于 11:18
  重建，但 local address 仍为 `198.18.0.1`；20 次 `SELECT 1` P50/P95/max 为
  26.153/135.760/159.705ms。该短测只证明当时可用，不构成不复发证据。

## 验证

- `route -n get 121.4.126.31`：`gateway 198.18.0.1`、`interface utun8`。
- `ifconfig`：`utun8` 地址为 `198.18.0.1`；系统代理端口为 `127.0.0.1:7890`。
- PgBouncer：`min_pool_size=6`、当前 `cl_waiting=0`、六条 server socket 本地地址均为
  `198.18.0.1`；本窗口 12 个 query timeout、2 个 connect timeout。
- PostgreSQL 实时无 waiting lock、无 idle-in-transaction；目标表索引扫描占比 99.31%–99.95%。
- ZSXQ 20-ID 真实查询 `Index Scan using uq_zsxq_topics_group_tab_topic`，execution 0.154ms；应用
  同指纹 P95 31.346 秒、最大 31.729 秒。
- 调整窗口 10:57:51–11:22:00：95 条应用慢 SQL，P95/max 7.909/19.161 秒；1 个去重
  query timeout、20 个 HBA 拒绝、20 个 server login failure、21 个 cached login failure；
  ZSXQ task `d995d65e-...` 在 11:05:17 持有 Session 63.134 秒，其中非 SQL 60.195 秒。
- 11:18 重连后的短窗口：102 个真实请求全部成功，但应用 SQL P95 9.184 秒；query/connect/HBA
  暂为 0。窗口过短且仍经过 TUN，只能记为观察，不得据此关闭。

## 复发预防

- 每日 DB 巡检必须记录数据库目标的本机 route gateway/interface，并与 PgBouncer server 本地地址关联。
- `DIRECT` 规则与“绕过 TUN”必须分开验收；需要物理绕过时使用 `tun.route-exclude-address`，并在
  变更前验证直连出口 HBA/防火墙，避免把网络黑洞替换为认证失败风暴。
- executor 小于 10ms 而应用超过 500ms 时，继续禁止加索引，优先核对 route、PgBouncer socket 与网络事件。

## 后续事项

- 保持 `route-exclude-address` 和当前 `/32` HBA，后续由 Tailscale 私网替代动态公网出口，避免
  公网 IP 再次变化导致 HBA 拒绝。
- 每轮验证 route/`SHOW SERVERS` 均不再使用 `utun8`/`198.18.0.1`；订阅更新或 Clash Verge
  重载后必须复核实际运行配置。
- 连续 24 小时要求 query/connect timeout=0、60 秒 Session=0，MP3/PDF/凭据和 ZSXQ 真实负载均成功；
  在此之前保持 `ISOLATED/OBSERVING`，不得写成已解决。

### 2026-08-12 长寿命后端 socket 缓解与 SSH 隧道回滚

- 新窗口仍出现 6 个去重 query timeout 和多次 `server conn crashed?`；异常 PgBouncer server socket 年龄集中在约 10 分钟以上，旧 `server_lifetime=21600` 会让网络漂移前建立的连接继续复用。
- 同形 MP3 audio-assets 查询服务端 executor 仅 55.619ms，关联正文最大仅 92 字节，进一步排除 executor、索引和大字段传输作为共同主因。
- SSH 隧道候选在隔离 A/B 中表现较好，但完整 Worker 启动后的真实直连发生 5 秒 timeout；系统按门禁回滚到 Tailscale，停止 LaunchAgent 并删除服务器受限公钥，没有把失败候选留作生产依赖。
- 最终保持 6 条热连接，把 PgBouncer `server_lifetime` 调整为 300 秒；100 次、10 并发只读请求全部成功，P95 338.6ms、最大 371.3ms、`cl_waiting=0`。
- 后续窗口仍记录 4 次 `server conn crashed?`，其中两次是 TLS receive timeout，socket 年龄约 196 秒，另两次约为 46/426 秒；300 秒轮换因此只是缓解，不是根因修复。
- 无 TLS 候选池 A/B 为 0/100；PostgreSQL 按 HBA 加密要求拒绝 `100.96.3.12` 的 `no encryption` 连接。候选池已停止，未放宽 HBA，正式 TLS 链路未变更。
- 状态保持 `RUNTIME_MITIGATED/OPEN`；仍需完整 24 小时 query/connect timeout=0、server crash=0 才能转入观察闭环。

### 2026-08-09 应用 Session 生命周期修复

- 报告截止后仍观测到 PgBouncer timeout/server connection failure，故 Tailscale 私网本身不能闭环。
- 真实 MP3 worker 栈确认子任务等待路径把数据库 Session 跨磁盘扫描、`ffprobe`、Celery inspect
  和 sleep 持有约 75 秒；现已在外部等待前提取轻量快照并主动归还 Session。
- Celery 服务停止管理路径也在安全查询后跨进程等待持有 Session 约 20.9 秒；现已在 stop guard
  后统一释放，包含允许停止、强制停止和安全门拦截返回路径。
- 相关回归测试 125 passed；生产 Web 已发布
  `7f2b032+worktree-20260809-db-audit-r2` 并通过 DB、Redis、HTTP/HTTPS 与容器健康检查；
  本机 MP3 worker、Flask 管理进程和 Celery Watchdog 已分别重启加载对应修复，未停止其他 worker。
- 这些证据把一部分长持有归因从纯网络问题细化为“应用持有放大网络故障”，但不否定仍存在的
  PgBouncer/server socket 异常。24 小时无复发验收从本次发布时点重新计时。
- 本机 `CELERY_RESULT_BACKEND` 的旧公网 Redis 残留也已改为 Tailscale 地址；三项 Redis 运行
  依赖预检全部通过，Flask 新进程健康且启动日志不再出现 result backend timeout。

### 2026-08-13 300 秒轮换仍复发

- 固定窗口内 PgBouncer 有 8 个去重 `query timeout`（上一窗口 6，+33.3%）、7 个 `connect timeout`（上一窗口 0）和 132 次 `server conn crashed?`；300 秒 `server_lifetime` 后大量失败 socket 年龄仍落在约 25–299 秒，排除“只要缩短旧连接寿命即可根治”。
- 唯一应用 SQL 错误 `fd910014d5877aa5` 来自 `tasks/report_audio_workflow.py:3021:_wait_child`，耗时 `20.153s`，与 02:31:38 的 PgBouncer query timeout 同秒；它是连接/网络等待，不是 executor 慢。
- 只读 PostgreSQL 现场无 waiting lock、无 idle-in-transaction；代表性 `EXPLAIN (ANALYZE, BUFFERS)` 为 `0.023–34.606ms`，而应用 SQL P95 为 `7.477s`。目标表索引扫描占比 `98.87%–99.95%`，没有证据支持新增索引作为根治。
- 状态保持 `RUNTIME_MITIGATED/OPEN`。下一轮优先按 server socket 创建时间、Tailscale path 与 query/connect timeout 做关联 A/B；在完整 24 小时 `query/connect timeout=0`、`server crash=0` 前不得关闭。

### 2026-08-13 10:02 PgBouncer keepalive 参数修复与短窗复核

- 新鲜日志把 127 次 TLS socket crash 进一步定位为 PgBouncer 空闲后端连接的
  `tls_sbufio_recv: read failed: Operation timed out`；同一窗口中 crash socket 年龄为
  25–299 秒，且没有对应客户端请求，PostgreSQL `idle_session_timeout=0`、直连 TLS 空闲
  40 秒稳定，故不是 executor、锁或服务端主动 idle 断开。
- 修复前本地生成器强制写入 `tcp_keepalive=1`、`tcp_keepcnt=3`、`tcp_keepidle=10`、
  `tcp_keepintvl=5`。这些平台相关探测值只在 PgBouncer 生效，直连 PostgreSQL 使用 OS
  默认值；本轮改为默认只保留 `tcp_keepalive=1`，三个 tunable 仅通过
  `LOCAL_PGBOUNCER_TCP_KEEPCNT/KEEPIDLE/KEEPINTVL` 显式注入，保留可重复 A/B 能力。
- 证据链：先新增默认配置“不出现 tunable”的测试，在旧代码上得到预期 RED；改动
  `scripts/dev/setup-local-pgbouncer.py` 后定向 4/4、`compileall`、`git diff --check`
  通过。随后对 report-audio 窄投影改动重新执行完整测试，用仓库根作为 top-level 运行
  `1165` 个用例，`OK (skipped=2)`。
- 已运行配置生成器并通过 `scripts/dev/run-local-pgbouncer.sh` HUP 重载；现场
  `SHOW CONFIG` 为 `tcp_keepalive=1`、`tcp_keepcnt/idle/intvl=0`，`cl_waiting=0`、6 条
  后端服务连接正常。未重启 backend/Celery/scheduler，未执行生产 DDL、HBA 或路由变更。
- 重载后截至 10:20:08 的 PgBouncer 日志为 `server conn crashed=0`、TLS receive
  timeout `0`、connect timeout `0`；6 个真实 PgBouncer 客户端空闲 90 秒后全部成功。
  同窗另有 1 次独立业务 `query timeout`，与这次空闲验收客户端不相同，证明 query timeout
  复发键仍开放，不能把短窗当作根治；自然 `server lifetime over` 5 次属于 300 秒轮换。
- 该 query timeout 对应的上游大字段放大点也已修复：`tasks/report_audio_workflow.py` 的
  `_resolve_scheme3_visual_inputs` 原先按主键加载 `PublicationTask` 全行，现只投影
  `id/user_id/report_audio_pdf_attachment_id/report_audio_pdf_filename/report_audio_pdf_bytes`；
  先写契约测试并在旧代码上 RED，修复后定向 report-audio/MP3 共 `160` 个用例通过。
- `venv/bin/python scripts/dev/check-restart-safety.py` 已实际读取远端状态，但仍阻止替换进程：
  2 个不可中断任务（`report_pdf_audio_workflow`、`report_pdf_gemini`）运行中；未使用
  `FORCE_RESTART_ACTIVE_TASKS=1`。因此窄投影的 worker 运行态验收待安全窗口。
- 隔离的一次性只读进程已用真实任务 `d859b7d7-866f-485e-9a9e-d8871ad12295` 调用窄投影解析：
  首条 `SELECT publication_tasks` 228.18ms、总解析 905.96ms、返回空工作流且无写入/投递；
  这证明新代码路径可运行，但不替代新 worker 加载后的真实任务验收。
- 状态仍为 `RUNTIME_MITIGATED/OPEN`：keepalive 行为已落地并短窗改善，但 Tailscale
  私网 path、query timeout 和 connect timeout 必须在下一完整 24 小时托管 Web/worker/
  scheduler 窗口中同时为 0，且真实页面/API/任务成功后，才可进入 `OBSERVING`/关闭。

### 2026-08-14 keepalive 后约 20 小时复核

- 从 2026-08-13 10:03:54 重载到本轮窗口末，TLS receive timeout 与 `server conn crashed?` 均为 0，说明移除平台相关 keepalive tunable 的改善持续约 20 小时；因不足完整 24 小时，socket crash 只记 OBSERVING。
- 同一重载后仍有 10 个去重 query timeout 和 2 个 connect timeout；整个固定窗口为 14/3，上一轮为 8/7，即 query timeout `+75%`、connect timeout `-57%`。多个 query timeout 与应用 20 秒 SQL 失败同秒，网络/连接根因仍复发。
- PostgreSQL 现场无 waiting lock、无 idle-in-transaction，关键 EXPLAIN 执行 `0.030–37.504ms`；PgBouncer 运行配置仍为 `query_timeout=20`、`server_lifetime=300`、仅 `tcp_keepalive=1`。没有证据支持加索引或继续缩短连接寿命。
- 状态保持 `RUNTIME_MITIGATED/OPEN`。下一轮必须以完整 24 小时 `query/connect timeout=0`、TLS crash=0、托管应用失败 SQL=0 为验收条件。

### 2026-08-15 代理 TUN 路由回退复发

- 13:06:24 首次出现 pool `query_wait_timeout`；13:07:08 后 PgBouncer 新 server connection
  的本地 source 从 Tailscale `100.96.3.12` 变成代理 TUN `198.18.0.1`。至 13:10:56
  共 16 次回退连接，和 9 个去重 query-wait、18 个 server crash、3 个 TLS receive error
  及 16 条 scheduler repair SQL 失败对齐。
- 这次失败风暴根因是 PostgreSQL 链路错误进入代理虚拟网卡，不是 executor、锁或索引：
  scheduler config 同形查询 `EXPLAIN (ANALYZE, BUFFERS)` 仅 0.024ms，失败 SQL 却连续
  持有约 15.3s。
- PgBouncer 15:16 重启后 source 恢复为 `100.96.3.12`；当前 route 为 `utun5`，数据库
  `client_addr` 也为 `100.96.3.12`。但之后仍有 5 个客户端 query timeout、7 个 connect
  timeout（其中若干发生在路由恢复后），说明一次 TUN 回退解释了主要失败风暴，却没有
  解释或根治全部传输不稳定。
- 分析器已增加 query-wait/server crash/TLS/source-address 归因与契约测试。此变更补齐
  防线，不会自动修改系统路由。
- 状态保持 `RUNTIME_MITIGATED/OPEN`。下一轮要求完整窗口 proxy TUN source=0、
  query/connect/query-wait/server crash/TLS error/应用失败 SQL 全部为 0，并完成真实
scheduler/API/worker 验收；否则继续追代理配置和 Tailscale path。

### 2026-08-16 无 TUN 回退但 timeout 仍复发

- 固定窗口 proxy TUN fallback、query-wait、server crash、TLS receive error 均为 0，说明上一轮
  路由回退风暴改善；server source 保持 Tailscale `100.96.3.12`。
- 但去重 query timeout 为 7（上一轮 5，`+40%`），connect timeout 为 11（上一轮 7，
  `+57.1%`）。15:15:15 的 query timeout 与 request `03cc1336`、SQL origin
  `api/mp3_video_workflow.py:514:load_page` 同秒，应用最终记录 `server closed`，请求耗时
  20.169s；这仍是用户可见失败。
- PostgreSQL 只读现场无 waiting lock、无 idle-in-transaction；代表性 executor 0.023–35.916ms，
  应用慢 SQL P95 7.079s。故无证据转向索引或锁，剩余根因仍在 PgBouncer/网络传输边界。
- 状态保持 `RUNTIME_MITIGATED/OPEN`。下一完整窗口必须同时满足 query/connect timeout=0、
  proxy TUN/query-wait/crash/TLS=0、应用失败 SQL=0，并覆盖真实 API/worker 才能进入关闭观察。
