# Mac Pro 开发机配置交接

本文用于把 DIG 开发环境从当前 Mac mini 扩展到一台 Mac Pro。目标是共享远端 PostgreSQL 和 Redis 服务，
但让两台开发机使用不同的 Redis 逻辑库，避免 Celery 消息、缓存和任务结果互相污染。

## 目标拓扑

| 组件 | Mac mini（当前机器） | Mac Pro（新机器） |
| --- | --- | --- |
| PostgreSQL 应用入口 | `127.0.0.1:6432/show_ytb_data` | `127.0.0.1:6432/show_ytb_data` |
| PostgreSQL 实际服务 | `100.92.99.39:5432/show_ytb_data` | `100.92.99.39:5432/show_ytb_data` |
| Redis 应用缓存 | `100.92.99.39:6379/4` | `100.92.99.39:6379/7` |
| Redis Celery broker | `100.92.99.39:6379/5` | `100.92.99.39:6379/8` |
| Redis Celery result | `100.92.99.39:6379/6` | `100.92.99.39:6379/9` |

Redis 的权威分配见 [`../REDIS_DB_ALLOCATION.md`](../REDIS_DB_ALLOCATION.md)。Mac Pro 不得临时复用 DB4-6；
即使队列名不同，共用 broker 仍可能让另一台机器消费任务。

## 交接包

- [`mac-pro.env.example`](mac-pro.env.example)：可复制成项目根目录 `.env` 的脱敏模板。
- [`../../rules/database-session-lifecycle-discipline.md`](../../rules/database-session-lifecycle-discipline.md)：数据库连接池、
  PgBouncer 和后台任务 Session 纪律。
- 项目启动入口：[`../../../scripts/dev/run-backend.sh`](../../../scripts/dev/run-backend.sh)。
- PgBouncer 生成器：[`../../../scripts/dev/setup-local-pgbouncer.py`](../../../scripts/dev/setup-local-pgbouncer.py)。
- Redis 预检：[`../../../scripts/dev/check-runtime-redis.py`](../../../scripts/dev/check-runtime-redis.py)。
- Schema 预检：[`../../../scripts/dev/check-runtime-schema.py`](../../../scripts/dev/check-runtime-schema.py)。

## 哪些内容可以复制

优先通过 Git 获取整个仓库。`requirements.txt`、前端/Remotion 的 `package-lock.json`、`scripts/dev/` 和本文档
都属于可复制、可版本控制的配置。

以下敏感值只能通过密码管理器、加密文件或面对面安全渠道从 Mac mini 迁移，然后手工填入新机 `.env`：

- PostgreSQL 用户名与密码；
- Redis 密码或 ACL 用户；
- YouTube、Google、Reddit、DeepSeek、Doubao API Key；
- Bilibili Cookie；
- ZSXQ Cookie；
- SMTP 密码及通知收件人。

`SECRET_KEY` 建议在 Mac Pro 上重新生成，不要与 Mac mini 共用：

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

## 哪些内容不要复制

- 不要直接复制 Mac mini 的 `.env`；它使用 Redis DB4-6。
- 不要复制 `venv/`、`node_modules/`、`runtime/`、`logs/`、`artifacts/` 或缓存目录。
- 不要把 `.env`、`.secrets/`、`client_secrets.json`、`youtube_token.pickle` 加入 Git。
- 不要在 Chrome 正运行时复制 Gemini/Doubao profile。默认应在 Mac Pro 创建独立 profile 并重新登录；
  若必须迁移 profile，应先退出两台机器上的 Chrome，再通过加密介质单独处理。
- 不要复制生成后的 `runtime/pgbouncer/userlist.txt`；它含数据库认证摘要，应在新机本地重新生成。

## 新机安装

先安装项目依赖的系统组件：Git、Python 3.11、PgBouncer、PostgreSQL client、FFmpeg、Node.js/npm、
Google Chrome 和 Tailscale。随后在项目根目录执行：

```bash
python3.11 -m venv venv
venv/bin/pip install -r requirements.txt
cd src/remotion_renderer && npm ci && cd ../..
cp docs/operations/dev-machine-bootstrap/mac-pro.env.example .env
chmod 600 .env
```

把模板中所有 `REQUIRED_...` 占位符替换成真实值。URL 内的用户名和密码必须进行 URL 编码；不要保留尖括号
占位符后直接启动。

## Tailscale 与端口预检

新机必须先加入与服务端相同的 Tailscale 网络：

```bash
tailscale ping 100.92.99.39
nc -vz 100.92.99.39 5432
nc -vz 100.92.99.39 6379
```

如果任一检查失败，先处理 Tailscale ACL、远端防火墙或 PostgreSQL HBA；不要把远端地址换成公网 IP 绕过。

## 生成本机 PgBouncer

模板同时保存：

- `DIRECT_DATABASE_URL`：远端 PostgreSQL；
- `DATABASE_URL`：本机 PgBouncer `127.0.0.1:6432`。

在新机运行：

```bash
venv/bin/python scripts/dev/setup-local-pgbouncer.py
scripts/dev/run-local-pgbouncer.sh
```

生成器会在 `runtime/pgbouncer/` 创建权限为 0600 的本机配置，并保持应用只连接本地 PgBouncer。不要手工把
`DATABASE_URL` 改回远端直连作为常态。

## 首次启动前验收

模板默认关闭自动 worker、scheduler 和 watchdog，避免刚接通共享 PostgreSQL 就触发周期任务。先执行：

```bash
venv/bin/python scripts/dev/check-runtime-redis.py
venv/bin/python scripts/dev/check-runtime-schema.py
lsof -nP -iTCP:6432 -sTCP:LISTEN
```

Redis 检查必须证明 DB7/8/9 三个 URL 均可连接。随后前台启动后端：

```bash
scripts/dev/run-backend.sh
```

检查：

```bash
curl -fsS http://127.0.0.1:5001/health
lsof -nP -iTCP:5432 -sTCP:ESTABLISHED
lsof -nP -iTCP:6379 -sTCP:ESTABLISHED
```

预期 PostgreSQL 远端为 `100.92.99.39:5432`，Redis 远端为 `100.92.99.39:6379`。

## 启用 worker 与 scheduler

确认 Mac Pro 的 Redis 分库和 API 健康后，按需要修改 `.env`：

```dotenv
BACKEND_PROCESS_BOOTSTRAP_CELERY_SERVICES=all
CELERY_AUTO_HEAL_SERVICES=all
BACKEND_PROCESS_BOOTSTRAP_WATCHDOG=1
```

周期 scheduler 会与 Mac mini 共同写入同一 PostgreSQL。除非已明确完成单实例/幂等验收，否则继续保留：

```dotenv
BACKEND_PROCESS_BOOTSTRAP_SCHEDULERS=none
```

需要把某个 scheduler 正式迁到 Mac Pro 时，应先在 Mac mini 停止对应 scheduler，确认旧 PID 与心跳消失，
再只在 Mac Pro 启用该项。禁止两台机器同时试跑同一个周期生产者。

## 浏览器自动化

Mac Pro 必须使用自己的绝对路径和独立 profile：

- `DOUBAO_BROWSER_TTS_USER_DATA_DIR`
- `DOUBAO_BROWSER_TTS_PROFILE_DIRECTORY`
- `GEMINI_BROWSER_USER_DATA_DIR`
- `GEMINI_BROWSER_PROFILE_DIRECTORY`

首次只启动一个浏览器 worker，人工确认账号、Cookie、下载权限和 Chrome 路径正确后，再启用自动恢复。

## 回滚

如果新机出现串队列、重复调度或配置错误：

```bash
scripts/dev/stop-all-background-jobs.sh
scripts/dev/stop-backend.sh
scripts/dev/stop-local-pgbouncer.sh
```

保留 `.env` 和日志用于排查，不要清空 Redis DB 或删除 PostgreSQL 业务数据。确认 Mac mini 仍健康后，再修正
Mac Pro 配置并从“首次启动前验收”重新开始。
