# ZSXQ schema 合并查询把 PostgreSQL 数组当字符集合

## 基本信息

- 状态：代码与契约测试已修复，真实应用连接已验证，待补跑失败轮次
- 严重级别：高
- 影响模块：ZSXQ workflow / scheduler / PostgreSQL / SQLAlchemy 文本查询
- 首次发现：2026-07-21 15:34 新进程真实 scheduler 验收
- 修复提交：待提交
- 根因分类：数据库驱动返回类型假设、真实连接验收缺失
- 复发键：`REGRESSION-ACCEPTANCE-INTEGRITY`
- 是否复发：是；属于回归假绿与真实验收复发簇
- 防线层级：typed_query_contract / driver_probe / runtime_acceptance

## 现象

- 新 scheduler 第 133 轮报错，误称 `publication_tasks` 缺少 5 个清洗宽字段，工作流未入队。
- 同一时刻启动 schema 门禁确认 5 个字段均已部署，说明不是迁移遗漏。
- 用应用同一连接执行查询，`ARRAY(...)` 返回 `str`，长度 1837；代码执行 `set(value)` 后得到字符集合，任何完整列名都无法命中。

## 根因

为减少每 5 分钟一次的 schema 反射开销，把多次元数据读取合并成一条文本 SQL，但默认假设 PostgreSQL `text[]` 一定由当前驱动解码为 Python `list`。确定性测试只检查 SQL 数量，没有验证真实驱动返回类型，因此全量回归假绿。

## 实际方案

- 列名聚合由 `ARRAY(...)` 改为 `jsonb_agg(...)`，当前 psycopg2/SQLAlchemy 链路稳定解码为 Python `list`。
- 保留单条元数据查询、5 分钟缓存和 fail-closed 行为。
- 契约测试禁止恢复 `COALESCE(ARRAY(...))`，并明确要求有序 `jsonb_agg`。
- 真实应用连接验证返回 `list`、83 个列名和全部 5 个清洗宽字段。

## 验证

- 失败形态复现：`ARRAY(...)` 返回 `str`，5 个目标列命中数为 0。
- 修复形态探针：`jsonb_agg(...)` 返回 `list`，83 列中 5 个目标列全部命中。
- 待完成：重载 scheduler 后补跑第 133 轮，并确认生成 workflow ID 且无 schema 错误。

## 复发预防

1. 文本 SQL 的数组、JSON、时间和布尔返回值不得凭记忆假设类型；必须用项目真实驱动做探针或显式声明类型。
2. 元数据优化必须同时验证查询次数、返回类型和业务判定结果，不能只做源码断言。
3. scheduler/bootstrap 的首次真实运行属于发布门禁；出现一次业务错误就必须重新打开验收，不能以测试通过收尾。
