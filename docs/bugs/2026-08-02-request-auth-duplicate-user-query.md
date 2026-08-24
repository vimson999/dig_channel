# 请求鉴权在全局守卫和路由装饰器中重复查询用户

## 基本信息

- 状态：代码与查询预算测试已通过并完成进程重载，待真实请求定量验收
- 严重级别：P1
- 影响模块：全站受保护页面 / API / 用户鉴权 / PostgreSQL
- 首次发现：2026-08-02
- 修复提交：当前工作区
- 根因分类：请求级 N+1、共享鉴权结果未复用
- 复发键：`HOT-PATH-PAYLOAD-POLLING`
- 是否复发：是；此前已要求页面媒体并发请求共享用户权限快照，但全局守卫与路由装饰器仍各自解析并查询同一用户
- 关联记录：`2026-07-21-stick-figure-asset-owner-query-amplification.md`、`2026-07-24-db-session-crossed-broker-and-remote-io.md`
- 防线层级：shared_guardrail / contract_test / observability

## 现象

- 24 小时慢 SQL 与 request id 关联中，请求 `690425e6` 在同一受保护 API 内两次执行 `utils/auth_utils._get_light_user_by_id`，分别耗时 1.402 秒和 1.205 秒。
- 代码链路显示项目级 `before_request` 已把用户写入 `flask.g.current_user`，随后路由 `require_login` 又调用 `get_current_user()`，重新解析 token/session 并查询同一用户。

## 影响

- 每个经过项目级守卫的已登录路由请求都会多一次相同用户查询；高频轮询接口会线性放大数据库往返、连接占用和 PgBouncer 抖动影响。
- PostgreSQL 对同类主键/唯一键查询的 executor 很快，因此重复查询的主要成本是应用到远端连接池/数据库的往返，不应通过新增索引处理。

## 根因

1. 项目级守卫与路由装饰器都调用 `get_current_user()`，但该函数没有先复用请求上下文中的已解析结果。
2. 既有鉴权测试只覆盖未登录边界，没有约束同一请求内鉴权解析和用户查询次数。

## 解决思路

- 在 Flask 请求上下文内优先复用项目级守卫已经确认的非空 `g.current_user`。
- 不缓存空结果，保留公开请求中后续登录或 session 变更后重新解析的语义。
- 不改变用户对象字段、权限判断或未登录响应。

## 实际方案

- `utils/auth_utils.get_current_user()` 在 token/session 查询前检查 `has_request_context()` 和 `g.current_user`。
- 非空时直接返回同一对象；未设置或为空时沿用原 JWT、session 和开发回退流程。
- 增加运行时边界测试，确保命中请求级用户后不再调用 token/session 解析；增加结构纪律测试锁定复用发生在数据库查询前。

## 验证

- 定向鉴权、数据库投影纪律和日志分析器测试：`19 passed`。
- 相关三文件较宽回归：`97 passed, 1 failed`；唯一失败为既有火柴人前端 cache-bust 断言漂移，与本修复无关。
- 完整 unit tests：`1003 passed, 3 skipped, 53 subtests passed, 1 failed`；唯一失败为既有火柴人前端 cache-bust 断言漂移，与本修复无关。
- compileall、`git diff --check`、Bug 记录严格检查均通过。
- 2026-08-03 重启安全门禁通过，后端、Celery 与 scheduler 已重载，健康检查返回 `healthy`；代码已加载到运行进程，仍需新的真实登录流量验证每个 request id 最多一次用户读取。

## 复发预防

- 一个请求内，项目级守卫确认的用户身份是路由装饰器和页面媒体子请求的共享事实来源。
- 鉴权测试同时约束授权结果与 token/session 后端调用次数，禁止只验证 HTTP 状态码。
- 热点端点的 SQL 预算应包含鉴权查询；相同 request id 出现重复用户查询时直接重新打开本记录。

## 后续事项

- 用新的真实受保护高频接口 request id 验证每个请求最多一次用户读取，并观察端点累计耗时和连接持有分布。
