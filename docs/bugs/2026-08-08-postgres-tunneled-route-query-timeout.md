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

### 2026-08-29 query timeout 与连接持有长尾复发

- 固定窗口 PgBouncer 去重 `connect/query timeout=10/2`，server wait P95 90.94ms、最大 1822.05ms；source 仍为 Tailscale `100.96.3.12` 到 `100.92.99.39`，未见 proxy TUN fallback。
- 两次 query timeout 均与真实 worker SQL 同秒：MP3 任务点查 20.251 秒后由现有数据库重试恢复；ZSXQ publish 来源任务点查 22.247 秒后污染 Session 并导致完成通知漏投，后者已单独修复生命周期级联。
- 只读现场无 waiting lock、无 idle-in-transaction；代表性 publication 主键/配置/列表 executor 小于 0.14ms，audio-assets 搜索 executor 102.923ms，而应用慢 SQL P95 7.434 秒。没有新增索引依据。
- 状态保持 `RUNTIME_MITIGATED / OPEN / REPEATED`。下一轮仍要求 connect/query timeout、应用失败 SQL 同时为 0，并把 executor、PgBouncer wait、commit/checkin 与 request/task id 分段闭环。

### 2026-08-29 server_lifetime 900/1800 真实 A/B 与运行替换

300 秒轮换、6 条热后端连接对应的基线约为 72 次新 server connection/小时；它减少陈旧 socket
复用，却把远端 TLS/Tailscale 建连频率放大。本轮没有调高 query/connect timeout，而是用同一
只读 `SELECT 1` 客户端和 PgBouncer 日志口径依次跑两个完整候选窗口：

- 900 秒：1,001.157 秒、185/185 成功，探针 P50/P95/max 为
  241.178/1508.540/5424.375ms；6 次新连接，即 21.575 次/小时，较基线下降 70.03%；
  connect/query/query-wait/server-crash/TLS/login failure 全为 0，source 仅
  `100.96.3.12`。
- 1800 秒：1,904.879 秒、352/352 成功，探针 P50/P95/max 为
  239.650/1433.797/4719.955ms；4 次新连接，即 7.560 次/小时，较基线下降 89.50%；
  同六类错误全为 0，source 仍仅 `100.96.3.12`。PgBouncer wait P95/max 为
  13.083/23.937ms，query P95/max 为 124.712/143.657ms。

两个候选都达到预先锁定的“成功率 100%、错误为 0、churn 至少下降 70%”门槛；选择器再按探针
P95、建连率排序，确定 1800 秒。生成器默认值和契约测试已改为 1800，标准整栈重启后
`SHOW CONFIG` 确认 `server_lifetime=1800`、`server_idle_timeout=600`、
`min_pool_size=6`、`query_timeout=20`、`server_check_delay=5`、仅 `tcp_keepalive=1`；
`SHOW POOLS` 为 `cl_waiting=0/sv_used=6`，六条 server 均从 Tailscale
`100.96.3.12` 连接 `100.92.99.39`。重启后短窗口六类故障均为 0。

这项变更根治了“300 秒固定轮换导致的可控建连 churn”，但没有证明 Tailscale/远端网络长尾在
完整业务日消失。固定窗已有 connect/query timeout 10/2，真实 audio-assets 重启后冷请求仍为
3.604 秒、暖态三次为 0.630/0.786/0.693 秒；因此网络问题保持
`RUNTIME_REPLACED / OPEN / OBSERVING`。下一完整 24 小时仍要求 connect/query/query-wait/
crash/TLS/login failure 和应用失败 SQL 同时为 0，才允许关闭。

### 2026-08-29 17:54 业务冷启动长尾再次复发

- 17:52:34–17:57:19 新 backend 真实页面窗口出现 7 条慢 SQL、累计 40.669 秒、P95 16.354 秒和
  2 条长连接持有；目录 `7c7c4e07` 11.812 秒、jobs `f84a8c62` 16.918 秒、全量 Celery status
  `092d1145` 14.404 秒。jobs 中 `SET LOCAL statement_timeout=5000` 本身等待 16.354 秒，进一步
  排除具体表 executor 或缺索引是共同根因。
- PgBouncer 同窗 `cl_waiting=0`、6 条 server 就绪，source 仅 Tailscale `100.96.3.12` 到
  `100.92.99.39`，proxy TUN fallback 为 0；server wait 最大约 80ms，没有 query/connect/
  query-wait/crash/TLS/login failure。慢 SQL 可同时落在不同新旧 server socket，不能只归因于一次
  lifetime rotation。
- 事后 Tailscale ping 为 direct UDP、约 27ms，TCP 5432 可连，只能证明复核时链路恢复，不能否认
  17:54 短时传输/驱动等待。历史 21,600 秒 lifetime 曾产生陈旧 socket timeout，因此本轮没有凭
  一次 spike 盲目加长；1800 秒 A/B 选择和 timeout 均保持不变。
- 应用侧已消除页面放大器：MP3 页不再为两个 worker 徽标调用跨业务表全量 status，而使用 runtime-only
  进程/精确队列端点。最终 18:06:17 后 12/12 请求成功、P95 377.78ms、最大 1.503 秒，0 慢 SQL、
  0 长持有、六类 PgBouncer 错误为 0。这证明降载根因已替换，但不是底层网络不复发证据。

状态保持 `RUNTIME_REPLACED / OPEN / REPEATED / OBSERVING`。下一完整 24 小时必须同时满足六类
PgBouncer 错误为 0、应用失败 SQL 为 0、20 秒级 hold 为 0，并将任何新长尾按 request/task id、
SQL origin、server socket 创建时间和 execute/fetch/commit/checkin 分段；一次健康 ping、一次页面
快窗或 executor 毫秒级都不能单独关闭问题。

### 2026-08-29 18:27 scheduler 配置点查再次出现网络尾部

- 标准整栈重启期间，B站充电 scheduler 的 `load_schedule_config` 点查耗时 2,081.37ms；同一 SQL
  自 8 月 5 日以来至少 14 次超过 1 秒，最新两次重启分别为 1,029.03ms 和 2,081.37ms，属于反复
  后台尾延迟而非单次噪声。
- 真实表只有 18 行/64KB；`EXPLAIN (ANALYZE, BUFFERS)` 顺扫 1 个 shared buffer，planning
  0.029ms、execution 0.014ms。8 次同连接端到端点查为 27.7–31.4ms。优化器选择顺扫小表合理，
  现有 `(key, scope)` 唯一索引完整，不新增索引。
- 同分钟 PgBouncer 平均 wait 13.12ms、query 116.70ms，六类连接错误为 0；2.081 秒应用执行时间与
  0.014ms executor 的差值仍落在远端 PostgreSQL 返回/网络传输边界。该 scheduler 启动最终成功，
  不涉及失败重试风暴或 Session 跨外部 I/O。

状态继续为 `RUNTIME_REPLACED / OPEN / REPEATED / OBSERVING`。下一完整窗口除六类错误、失败 SQL、
长 hold 外，必须单列该配置指纹的次数、累计、P95/max及 scheduler 启动影响；不得以小表新增索引掩盖
链路问题。

### 2026-08-31 Tailscale 直连丢包与真实 20 秒 query timeout

- 固定窗口慢 SQL 为 110 条/262.811 秒/P95 5.501 秒，较上一轮分别改善 33.7%/43.6%/47.5%；
  PgBouncer 六类错误为 0、建连率从 36.333/h 降到 12/h。这只是窗口改善，不能关闭问题。
- 巡检真实页面随后捕获 `POST /api/v1/zsxq/topics/local` request `8c73fe09`：用户点查
  927.08ms、主题查询 28.82ms，第三条 SQL 等待 20,013.12ms 后接口以 502/21,088.98ms 结束；
  PgBouncer 在 06:11:35 同秒明确记录 server/client `closing because: query timeout`。
- 同一页面下一次 request `bd3d49cf` 以 200/1,636.88ms 恢复，后续真实筛选返回 205 条本地结果并
  渲染首批 20 条完整主题；失败不是稳定 SQL 语义或数据缺失。
- 精确 SQL 现场：日期范围 count distinct 使用既有
  `ix_zsxq_topics_group_create_synced_desc`，execution 741.058ms；分页 SQL 同索引 0.079ms；MP3
  canonical 目录顺扫 726 行、返回 280 行，execution 0.626ms，而巡检冷请求却为
  18.386s/SQL 17.433s。没有缺索引能共同解释两个不同表的秒级尾部。
- `SHOW CONFIG` 确认 `pool_mode=transaction`、`query_timeout=20`、`query_wait_timeout=15`、
  `server_lifetime=1800`、`default/min pool=15/6`；`SHOW POOLS` 当时 `cl_waiting=0`，6 条 server
  均为 used。目标 `100.92.99.39` 经 `utun5`，Tailscale 显示 active direct endpoint
  `121.4.126.31:41641`。20 包现场探测丢包 30%，RTT 27.098–184.053ms、stddev 59.952ms，
  5432 仍可建立 TCP；这把剩余共同根因收敛到 Tailscale/物理出口/远端网络质量，而不是 executor、
  锁、池排队或应用 CPU。

状态保持 `RUNTIME_REPLACED / OPEN / REPEATED / OBSERVING`。未提高 timeout、未加重试、未改
PgBouncer/Tailscale 配置；这类动作不能根治丢包且属于人工基础设施变更。下一轮必须同时取得 24 小时
丢包/路径质量、六类 PgBouncer 错误、20 秒 hold/502 与真实页面冷链指标，连续无复发后才可关闭。

### 2026-08-31 08:36 恢复快照（不构成关闭证据）

同一目标 `100.92.99.39` 的 20 包只读探测已恢复为 20/20、丢包 0%，但 RTT
min/avg/max/stddev 仍为 26.731/78.135/177.386/50.807ms。它与 06:11 的 30% 丢包、
20 秒 query timeout 共同证明链路呈间歇性抖动；一次恢复快照不能覆盖先前真实 502，也不能满足
连续后窗验收。状态保持 `OPEN / REPEATED / OBSERVING`。

### 2026-08-31 08:51 worker 连接再次中断

恢复快照后仅约 15 分钟，真实 PDF 清洗任务 `5341ebed-3ef5-4ce0-aa63-8cb1fe99ca1b` 在完成 31 份
文件、批量更新 31 个 `content_pipeline_assets` 的 commit/确认边界再次收到
`server closed the connection unexpectedly`。Celery 有界重试成功，但旧应用把全部 31 份 PDF 重做，
第二轮耗时 532.224 秒；用户流程直到 09:00:58 才完成。重启安全门在 08:52 的只读
`publication_tasks` 查询也曾等待 7,857.43ms，而 09:04 后同门查询恢复至 171.06ms。

这进一步证明 08:36 的 0% 丢包只是一段恢复，不是根因关闭。应用侧已新增 durable checkpoint 采用与
状态单调防线，避免网络瞬断再次放大为整批 OCR；底层网络仍为 `OPEN / REPEATED / OBSERVING`。
验收仍要求完整 24 小时六类 PgBouncer 错误、server closed、失败 SQL、20 秒 hold/502 均为 0。

### 2026-08-31 11:27 空列表再次出现 17 秒驱动等待

自然 PDF 合并列表 request `112cdfd9` 返回 200/18.024 秒，其中旧全实体任务 SQL
`358df92aaae36a2d` 为 17.328 秒但返回 0 行，连接持有 17.644 秒；非 SQL仅41.75ms。生产只读
EXPLAIN 对新等价条件 execution 0.201ms、planning 0.289ms、3 行、48 shared hit/0 read，同次应用
驱动仍等待 867.57ms，`SET TRANSACTION READ ONLY` 也曾等待 1.669 秒。现有
`(feature,user_id,created_at DESC)` 索引完整，进一步证明共同尾部不在 executor/索引。

应用放大器已收窄为一次轻投影窗口查询、查询后 release、终态/隐藏停止和 single-flight。11:21 标准
重启到 PID `82858` 后，真实 request `54fde6f1`/`7571d127` 为 459.39/370.30ms，各仅一条任务
SELECT 318.51/119.96ms；真实终态页面 26 秒仅请求一次，未再产生长 hold。该结果只证明应用放大器已
`RUNTIME_REPLACED / OBSERVING`；底层网络仍 `OPEN / REPEATED`，完整 24 小时与后续窗口验收条件不变。
