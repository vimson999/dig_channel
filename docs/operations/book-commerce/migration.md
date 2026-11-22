# 图书带货模块迁移方案

## 现状与选择

2026-11-18 只读检查：当前配置的 PostgreSQL 数据库 `show_ytb_data` 的主迁移版本为 `20261112_upower_topics`，仓库和可访问的远程 main 均没有该 revision；库中存在该仓库没有的 B站 upower 表。`users.id` 是整数主键，尚无 `bc_` 表和 `alembic_version_book_commerce`。该检查不代表其他部署环境，执行时必须重新核对。

为避免猜测或篡改未知的主迁移历史，图书带货使用独立 Alembic 迁移目录 `src/backend/book_commerce_migrations` 和版本表 `alembic_version_book_commerce`。该轨道仅创建/管理 `bc_` 表，通过外键引用现有 `users.id`；不修改主 `alembic_version`，不 stamp 主迁移。主迁移 autogenerate 显式排除 `bc_` 表及模块版本表；后续此模块的 schema 变更必须写入独立轨道。主迁移链不一致的问题仍需原项目单独处理，本方案不声称修复它。

初始迁移校验数据库类型、`users.id` 类型/主键和已有 `bc_` 表，发现冲突立即失败，不采用 `IF NOT EXISTS` 掩盖差异。PostgreSQL 使用事务内 advisory lock 防止同时执行。回退检测任意业务表数据、规则状态或历史分析，存在则拒绝删除；启用后的常规回退应关闭入口与 worker 并保留数据。

## 演练与执行顺序

1. 确认连接目标、账号权限、备份及恢复路径；重新只读检查当前 schema、`users.id`、`alembic_version`、`bc_` 名称冲突。不得将未知库当测试库。
2. 在一次性 PostgreSQL 副本上演练迁移、重复升级、用户和外键约束、导入及计算；检查主迁移版本与非 `bc_` 对象未变。不要在真实库用 `db.create_all()` 或 `stamp`。
3. 完成代码和配置验证后执行 `venv/bin/python scripts/maintenance/migrate_book_commerce.py upgrade`。命令读取项目的 `DATABASE_URL`/数据库配置；执行前确认目标库。初始迁移在一个事务内完成；不通过前置校验则不创建功能表。
4. 部署更新后的应用，按环境启用功能开关并启动 `scripts/dev/run-celery-book-commerce.sh`。先用无敏感信息的测试数据在隔离环境验证；不在业务库写入虚构样本。
5. 用健康检查、迁移版本及应用日志确认状态。原主迁移版本应保持不变；完成赛道关系增量迁移后，模块版本应为 `bc_20261118_02`；并发 worker 必须限制为 1。

`venv/bin/python scripts/maintenance/migrate_book_commerce.py downgrade` 仅适用于尚无业务数据且状态保持初始值的演练环境；真实数据导入后会拒绝执行。若需处理生产数据，须另行制定保留/迁移方案。

## 验证记录

已在临时 PostgreSQL 14 数据库使用未知主 revision `20261112_upower_topics` 演练：模块升级后主 revision 保持不变、模型与迁移结构对比无差异、空模块可回退；并执行并发冲突导入和计算期间输入变更测试。

2026-11-18 本次执行记录：对当前 `show_ytb_data.public` 执行了独立模块升级，模块版本为 `bc_20261118_01`，主版本仍为 `20261112_upower_topics`。升级前后比对旧表的列、索引和约束；除独立版本表自身新增的主键约束外，原有对象一致。模型与模块实际 schema 比较无差异，模块状态为初始值。结构快照位于 `runtime/book_commerce/`，只含 schema 元数据，**不是完整数据库备份**。

本机合成数据测试：Intel Core i5-1038NG7、16 GiB 内存、临时本地 PostgreSQL 14；100 个账号、2,000 个商品、10,000 个视频、100,000 条观测。最新完整重跑分析 44.838 秒、榜单热读 P95 0.038 秒（20 次测量，前 2 次预热）。这些结果不保证远端 PostgreSQL 16 的性能。可运行 `venv/bin/python scripts/dev/test-book-commerce-isolated.py --benchmark` 重现；脚本仅使用新建临时数据库与 Redis，退出时清理。

2026-11-18 赛道关系增量迁移记录：对同一 `show_ytb_data.public` 执行 `bc_20261118_02`，新增 `bc_categories` 与 `bc_account_categories`，把已有账号斜杠赛道拆成层级节点并关联至末级。主迁移版本保持 `20261112_upower_topics`，独立模块版本为 `bc_20261118_02`；旧账号 `category` 原文列保留作导入兼容。
