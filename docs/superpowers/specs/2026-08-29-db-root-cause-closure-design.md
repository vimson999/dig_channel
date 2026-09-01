# DB 根因闭环与通知可靠性设计

日期：2026-08-29

## 目标

在不删除业务原始 JSON、不牺牲字段完整性、不用虚假成功掩盖外部故障的前提下，闭环当前四个开放根因：

1. workflow notification 在 SMTP 故障时按 event 独立重试，形成跨 event 建连风暴并丢失终态通知。
2. 本地 PgBouncer 通过 Tailscale 访问远端 PostgreSQL 时存在连接长尾；300 秒 `server_lifetime` 又把 6 个热连接放大为约 72 次/小时 TLS 重连。
3. Bilibili `bvid` 与同源复核发现的 `competitors.uid` 均存在两个等价唯一索引。
4. comment-hash 历史维护工具默认即写库、批量处理 13 个索引，且 runtime schema 对 Alembic 后继版本判断失真。

代码加载、测试通过或短窗口暂时无错误都不是 CLOSED。每项都必须保留“修复前证据 → 变更 → 定向测试 → 完整测试 → 真实运行验收 → 后续窗口复核”的证据链。

## Durable notification outbox

新增 `workflow_notification_deliveries`：

- `id`：UUID，稳定 event id。
- `dedupe_key`：provider、event type、workflow identity 与阶段组合后的 SHA-256，唯一约束。
- `provider`、`event_type`、`workflow_id`、`payload`、`payload_hash`。
- `status`：`pending`、`leased`、`retry_wait`、`sent`、`dead_letter`、`disabled`。
- `attempt_count`、`available_at`、`lease_owner`、`lease_expires_at`。
- `celery_task_id`、`last_error_type`、`last_error_message`。
- `first_attempt_at`、`last_attempt_at`、`sent_at`、`created_at`、`updated_at`。

新增 `workflow_notification_provider_states`：

- provider 为主键。
- `state`：`closed`、`open`、`half_open`。
- `consecutive_failures`、`open_until`、`probe_owner`、`probe_expires_at`。
- 最近成功、失败、错误类型与更新时间。

所有 DB 操作都是短事务。SMTP、Celery broker、sleep 和故障退避前必须 commit/rollback 并 remove Session。

### 入队

`enqueue_workflow_event()` 计算稳定 dedupe key，在短事务中执行 insert-or-load。相同业务事件只得到同一 delivery。事务提交并释放 Session 后，才投递 drain task。broker 投递失败不会丢 event；delivery 保持 pending，由周期 reconciler 恢复。

### 发送与租约

drain task 在短事务中检查 provider state，并用 `FOR UPDATE SKIP LOCKED` 取得一个到期 delivery 的有界租约。释放 Session 后执行 SMTP。发送完成后开启新短事务，按 delivery id 和 lease owner CAS 写 `sent` 或 `retry_wait`。

临时 provider 错误第一次出现即打开共享熔断。熔断期间其他 event 不连接 SMTP，只保持 durable pending。到期仅允许一个 half-open probe。成功关闭熔断并继续 drain；失败扩大退避窗口。

永久配置、认证或收件人错误进入 dead letter，并完整记录，不伪造 sent。临时故障不再通过 Celery `self.retry` 为每个 event 建立独立重试时钟。

### SMTP 尝试预算

配置为 465/SSL 时只执行 SSL；配置为 587/STARTTLS 时只执行 STARTTLS。只有明确的协议/端口不匹配才允许一次 fallback，`SMTPServerDisconnected`、timeout、网络错误不得触发 SSL 与 STARTTLS 双建连。

SMTP 不提供服务端幂等键，因此“SMTP 已接受、进程在 sent 状态提交前崩溃”无法证明 exactly-once。系统提供稳定 `X-Workflow-Event-ID`、单活动租约、payload hash 与审计状态，把该极小歧义显式暴露；不得将其描述为绝对 exactly-once。

## PgBouncer 连接策略

固定 300 秒轮换在 8.5 小时产生 618 次后端连接和 5 次 10 秒 connect timeout。新策略通过可复现 A/B 决定，而不是直接把 timeout 调大：

- 候选 `server_lifetime` 为 900 与 1800 秒。
- 保留 transaction pool、`min_pool_size=6`、`server_check_query=SELECT 1`、`server_check_delay=5`。
- 显式记录 TCP keepalive 配置。
- A/B 分别记录后端建连次数、失败率、query/connect timeout、server crash、TLS error、client wait、热连接数和请求 P95。
- 选择建连量下降至少 70%、热池稳定、无 query timeout 的候选。

若完整窗口仍出现 Tailscale connect/query timeout，状态继续 OPEN，责任上移为应用与 DB 网络共置或稳定隧道基础设施；禁止用无限 SQL 重试或索引改动掩盖网络故障。

## 索引维护

### Bilibili

保留约束所有者 `bilibili_competitor_videos_bvid_key` 与
`bilibili_competitors_uid_key`，并发删除独立索引
`ix_bilibili_competitor_videos_bvid` 与 `ix_bilibili_competitors_uid`。工具必须对每个
目标在 apply 前后验证 row count、重复组、等待锁、owner、valid/ready/unique、约束归属和点查
EXPLAIN；任何 post-check 失败时重建对应独立索引。历史 migration 与 runtime schema warning
名单同时禁止新环境重新生成这两个独立索引。

### ZSXQ comment-hash

旧工具改为默认 dry-run、显式 `--apply`，且不再默认处理 13 个索引。

- `ix_zsxq_topics_group_tab_type_create_desc` 自 2026-08-08 统计周期累计 2,990 次扫描，最近 2026-08-20 使用，必须保留。
- `ix_zsxq_topics_group_type_create_desc` 累计 1 次，最近 2026-07-09 使用，只是候选。仅在真实调用 SQL、pg_stat 和带/不带候选索引的 EXPLAIN 证明替代计划可靠后才允许删除。
- 已存在且完整的 hash 列不再 ALTER 或全表回填。
- Alembic 状态按 revision ancestry 判断；当前 head 包含旧 migration 时不得误报“未登记”，更不得把 version_num 倒退到旧 revision。

## 验收

- 通知故障注入：20 个并发 event，首次临时失败后熔断窗口总 SMTP 建连数为 1；恢复后 20 个 delivery 最终 sent，业务 dedupe key 唯一。
- DB/worker 故障注入：租约竞争、broker 投递失败、DB 瞬断、worker 崩溃均不会丢 pending event。
- 完整 unit、compileall、前端契约、`git diff --check`、Bug strict 与 registry JSON 全部通过。
- additive migration 和索引 DDL 均保存 pre/post/rollback 证据。
- 重启前通过安全门；重启后 backend、broker、全部 worker ready。
- 真实页面/API/worker 验收通过，并在后续完整窗口确认 notification terminal failure、PgBouncer timeout 与对应复发键没有再次出现，才允许 CLOSED。
