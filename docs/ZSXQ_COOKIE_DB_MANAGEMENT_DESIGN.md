# 知识星球 Cookie DB 管理设计

## 背景

当前系统里，知识星球同步策略已经可以持久化到 `runtime/zsxq_scheduled_sync_test.json`，但 Cookie 仍然主要来自三处：

- 前端页面 `localStorage`：页面下拉框里的 Cookie，只存在某个浏览器 origin 下。
- `.env` 环境变量：`ZSXQ_SYNC_TEST_COOKIE` / `ZSXQ_AUTO_PUBLISH_COOKIE`，容易过期且不适合多账号。
- 自动发帖 runtime JSON：有账号池能力，但仍是文件存储，不适合作为统一凭据源。

这导致调度器重启时可能读到已过期的环境变量 Cookie，而不是用户页面里最后确认可用的 Cookie。解决方向是把 Cookie 作为后端托管账号资源，存进数据库，策略只引用账号 ID。

## 目标

- 系统同步和自动发帖共用同一套 ZSXQ Cookie 账号管理。
- 调度器重启后能按保存策略自动找到正确 Cookie，不依赖浏览器页面状态。
- 前端只展示账号名称、脱敏 token、状态和最近校验时间，不返回明文 Cookie。
- Cookie 在 DB 中加密存储，后端运行时按需解密。
- 支持多账号、多用途、多星球绑定，后续可做账号轮换和失败熔断。

## 非目标

- 不把浏览器原生 Cookie 存储当作数据源。
- 不在日志、API 响应、workflow 结果或任务参数快照里暴露明文 Cookie。
- 不改变现有同步/发布业务策略的含义，只改变 Cookie 的来源和引用方式。

## 表结构

### `zsxq_cookie_accounts`

存储账号级 Cookie 密文和可展示元数据。

```sql
CREATE TABLE IF NOT EXISTS public.zsxq_cookie_accounts (
    id varchar(36) PRIMARY KEY,
    name varchar(120) NOT NULL,
    status varchar(20) NOT NULL DEFAULT 'active',
    purpose varchar(20) NOT NULL DEFAULT 'both',
    encrypted_cookie text NOT NULL,
    cookie_fingerprint varchar(64) NOT NULL UNIQUE,
    token_suffix varchar(12),
    allowed_group_ids jsonb DEFAULT '[]'::jsonb,
    priority integer NOT NULL DEFAULT 100,
    notes text,
    last_validated_at timestamp without time zone,
    last_validation_status varchar(20),
    last_validation_error text,
    last_used_at timestamp without time zone,
    usage_count integer NOT NULL DEFAULT 0,
    created_at timestamp without time zone NOT NULL DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_zsxq_cookie_accounts_status
    ON public.zsxq_cookie_accounts (status);

CREATE INDEX IF NOT EXISTS ix_zsxq_cookie_accounts_purpose
    ON public.zsxq_cookie_accounts (purpose);

CREATE INDEX IF NOT EXISTS ix_zsxq_cookie_accounts_priority
    ON public.zsxq_cookie_accounts (priority);
```

字段说明：

- `encrypted_cookie`：加密后的完整 Cookie。
- `cookie_fingerprint`：对标准化 Cookie 或 `zsxq_access_token` 做 SHA-256，用于去重，不可逆。
- `token_suffix`：只存 token 后 6-8 位，用于页面识别。
- `purpose`：`sync` / `publish` / `both`。
- `allowed_group_ids`：为空表示不限制；非空表示只允许这些星球使用。
- `status`：`active` / `disabled` / `expired` / `error`。

### `zsxq_runtime_bindings`

保存某个运行场景默认使用哪个账号。

```sql
CREATE TABLE IF NOT EXISTS public.zsxq_runtime_bindings (
    id varchar(36) PRIMARY KEY,
    scope varchar(40) NOT NULL,
    group_id varchar(32),
    cookie_account_id varchar(36) NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    priority integer NOT NULL DEFAULT 100,
    created_at timestamp without time zone NOT NULL DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now(),
    CONSTRAINT fk_zsxq_runtime_bindings_cookie
        FOREIGN KEY (cookie_account_id)
        REFERENCES public.zsxq_cookie_accounts(id)
        ON DELETE RESTRICT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_zsxq_runtime_bindings_scope_group_priority
    ON public.zsxq_runtime_bindings (scope, COALESCE(group_id, ''), priority)
    WHERE enabled = true;
```

建议 scope：

- `system_sync_default`
- `auto_publish_default`
- `manual_sync_default`
- `manual_publish_default`

## 加密方案

使用 `cryptography.fernet.Fernet`。

新增环境变量：

- `ZSXQ_COOKIE_ENCRYPTION_KEY`

规则：

- 生产环境必须配置该变量。
- 开发环境如果缺失，可以从 `SECRET_KEY` 派生一个开发 key，但启动日志必须提示“不适合生产”。
- DB 只保存密文；API 默认不返回明文。
- 只有后端调度器、同步任务、发布任务在实际调用星球接口前解密。

## 后端服务接口

新增服务：`services/zsxq_cookie_account_service.py`

核心方法：

- `list_accounts(purpose=None, group_id=None)`：返回脱敏账号列表。
- `create_or_update_account(name, cookie, purpose, allowed_group_ids, notes)`：标准化、加密、去重保存。
- `validate_account(account_id, group_id=None)`：解密后调用 `get_groups` 或 `get_menus` 校验。
- `resolve_cookie(scope, group_id=None, account_id=None, fallback_env=None)`：按优先级解析可用 Cookie。
- `mark_used(account_id)`：更新 `last_used_at` 和 `usage_count`。
- `mark_validation_result(account_id, status, error=None)`：记录校验结果。

解析优先级：

1. 请求里显式传入的 `cookie_account_id`。
2. 策略里保存的 `cookie_account_id`。
3. `zsxq_runtime_bindings` 里对应 `scope + group_id` 的账号。
4. `zsxq_runtime_bindings` 里对应 `scope` 的默认账号。
5. 兼容期回退 `.env`。

兼容期结束后，可以关闭第 5 步。

## API 设计

新增接口：

- `GET /api/v1/zsxq/cookie-accounts?purpose=sync&group_id=...`
  - 返回脱敏账号列表。
- `POST /api/v1/zsxq/cookie-accounts`
  - 保存或更新 Cookie。
  - 请求包含明文 Cookie，但响应只返回脱敏信息。
- `POST /api/v1/zsxq/cookie-accounts/<id>/validate`
  - 校验账号是否能访问指定星球。
- `DELETE /api/v1/zsxq/cookie-accounts/<id>`
  - 默认软删除：`status=disabled`。
- `POST /api/v1/zsxq/runtime-bindings`
  - 设置系统同步或自动发帖默认账号。

修改现有接口：

- `/system-sync-test/config`
  - 新增 `cookie_account_id`。
  - 配置保存时不再依赖明文 Cookie，但可以用明文 Cookie 创建/更新账号后绑定。
- `/system-sync-scheduler/start`
  - 支持 `cookie_account_id`。
  - 无页面 Cookie 时，从 DB 绑定账号解析。
- `/system-sync-scheduler/run-once`
  - 同上。
- `/auto-publish/config`
  - 自动发帖账号池迁移为 DB 账号引用。

## 前端改造

当前 `localStorage` 下拉框改为 DB 账号下拉框：

- 页面加载时请求 `GET /cookie-accounts`。
- 下拉项展示：`名称 + token 后缀 + 状态 + 最近校验时间`。
- 保存 Cookie 时调用 `POST /cookie-accounts`，然后刷新列表。
- 系统同步配置保存时保存 `cookie_account_id`。
- 启动计划任务时只传 `cookie_account_id`，不再传明文 Cookie。
- 保留“临时 Cookie”入口作为应急模式，但默认不使用。

页面提示文案：

- “当前调度器使用 DB 账号：xxx”
- “环境变量 Cookie 仅作为兼容回退，不建议长期使用”

## 调度器改造

系统同步调度器：

- 启动时读取保存策略。
- 从策略或 DB binding 解析 `cookie_account_id`。
- 解密 Cookie 后注入子进程环境变量，或者更进一步：让 workflow/task 在运行时按 account id 解密。

推荐最终形态：

- scheduler 不再传明文 Cookie。
- workflow config snapshot 保存 `cookie_account_id`。
- Celery task 开始执行时调用 `resolve_cookie(...)` 解密。

这样可以避免 Cookie 出现在进程环境变量里。

兼容过渡形态：

- scheduler 先按 DB 解密出 Cookie，仍通过现有 `COOKIE_OVERRIDE_ENV_NAME` 注入子进程。
- 这一步改动小，可以快速解决重启问题。
- 后续再把 Celery payload/env 里的明文 Cookie 去掉。

## 迁移路径

### 第 1 阶段：最小闭环

1. 建表：`zsxq_cookie_accounts`、`zsxq_runtime_bindings`。
2. 新增后端 service + API。
3. 系统同步配置增加 `cookie_account_id`。
4. 系统同步 scheduler start/run-once 支持从 DB 账号解析 Cookie。
5. 前端下拉框改为 DB 来源。

这一阶段就能解决“重启后找不到第三个 Cookie”的问题。

### 第 2 阶段：迁移自动发帖账号池

1. 读取 `runtime/zsxq_auto_publish_strategy.json` 里的 `cookie_accounts`。
2. 导入 DB，生成账号记录。
3. JSON 策略里只保留账号 ID 或 binding。
4. 自动发帖调度器改为 DB 解析。

### 第 3 阶段：去明文传递

1. workflow config snapshot 只保存 `cookie_account_id`。
2. Celery task 运行时从 DB 解密 Cookie。
3. 清理日志、任务参数、运行结果中可能残留的明文字段。

## 风险控制

- API 响应禁止返回 `encrypted_cookie` 和明文 Cookie。
- 日志统一脱敏：只允许输出账号 ID、名称、token suffix。
- 账号校验失败：
  - `401`：标记 `expired`，不再自动使用。
  - `1059`：标记临时错误，不禁用账号。
  - 网络错误：标记临时错误，不禁用账号。
- 调度器如果没有可用账号：
  - 不应静默回退到过期 env。
  - 应明确报错：“没有可用 DB Cookie 账号，请重新保存/校验账号。”

## 推荐落地顺序

先做系统同步，因为它现在的问题最直接：

1. DB 表 + service。
2. 系统同步 scheduler DB 解析。
3. 页面 Cookie 下拉框改为 DB 账号。
4. 再迁移自动发帖账号池。

## 运维控制面接口

Cookie 进 DB 后，调度器重启就不再依赖页面临时 Cookie。因此可以把“恢复现场”收敛成一组后端接口。

### 现有能力

系统同步调度器已有接口：

- `GET /api/v1/zsxq/system-sync-scheduler/status`
- `POST /api/v1/zsxq/system-sync-scheduler/start`
- `POST /api/v1/zsxq/system-sync-scheduler/stop`
- `POST /api/v1/zsxq/system-sync-scheduler/restart`
- `POST /api/v1/zsxq/system-sync-scheduler/run-once`
- `GET /api/v1/zsxq/system-sync-scheduler/logs`

其他调度器已补齐重启接口：

- `POST /api/v1/zsxq/auto-publish/scheduler/restart`
- `POST /api/v1/content-pipeline/retention/scheduler/restart`
- `POST /api/v1/report-video/auto-scan/scheduler/restart`
- `POST /api/v1/report-video/asset-scan/scheduler/restart`
- `POST /api/v1/scheduler/restart`

Celery worker 已有接口：

- `GET /api/v1/celery/status`
- `POST /api/v1/celery/start/<service_id>`
- `POST /api/v1/celery/stop/<service_id>`
- `POST /api/v1/celery/restart/<service_id>`
- `GET /api/v1/celery/logs/<service_id>`
- `POST /api/v1/celery/watchdog/start`
- `POST /api/v1/celery/watchdog/stop`
- `POST /api/v1/celery/watchdog/restart`
- `POST /api/v1/celery/watchdog/run-once`

其中 ZSXQ 相关 Celery service id：

- `zsxq_workflow`
- `zsxq_sync`
- `content_pipeline`
- `zsxq_publish`

### 缺口

现有接口是“单服务控制”。真正远程恢复时，更需要一个业务级的一键恢复接口：

```text
POST /api/v1/ops/zsxq/recover
```

建议默认执行：

1. 检查 DB Cookie 账号是否可用。
2. 检查 Redis broker 是否可连。
3. 重启 ZSXQ 四个 Celery worker：
   - `zsxq_workflow`
   - `zsxq_sync`
   - `content_pipeline`
   - `zsxq_publish`
4. 启动或重启 Celery watchdog。
5. 启动系统同步 scheduler。
6. 回读 scheduler status、Celery status、最近日志摘要。

请求示例：

```json
{
  "restart_celery": true,
  "restart_watchdog": true,
  "restart_scheduler": true,
  "run_once": false,
  "dry_run": false
}
```

响应示例：

```json
{
  "success": true,
  "data": {
    "cookie": {
      "account_id": "xxx",
      "status": "active",
      "validated": true
    },
    "celery": {
      "zsxq_workflow": {"running": true},
      "zsxq_sync": {"running": true},
      "content_pipeline": {"running": true},
      "zsxq_publish": {"running": true}
    },
    "scheduler": {
      "running": true,
      "next_run": "2026-06-14T07:19:16+08:00"
    }
  }
}
```

### 安全边界

这些接口能启动/停止本机进程，不能裸露给公网。

必须加：

- 操作鉴权：至少要求 `OPS_API_TOKEN` 或登录态管理员权限。
- 审计日志：记录操作者、来源 IP、动作、目标服务、结果。
- 白名单服务：只允许重启明确列出的 worker，不能传任意脚本路径。
- 幂等：已经运行时返回 `already_running`，避免重复启动。
- 冷却时间：防止连续重启抖动。
- 默认不清队列：`purge` 必须单独确认，不放进一键恢复默认流程。

### 可靠性边界

接口能恢复的前提是 Flask 后端服务还活着。如果 Flask 后端本身也挂了，HTTP 接口就不可用。那一层需要 OS 级守护：

- macOS 本地：`launchd`
- Linux 服务器：`systemd`
- 容器：Docker restart policy / compose healthcheck

因此完整恢复链路应该是：

1. OS supervisor 保证 Flask 后端活着。
2. Flask ops API 负责重启调度器和 Celery worker。
3. DB Cookie 账号保证调度器重启后能自动拿到有效凭据。
