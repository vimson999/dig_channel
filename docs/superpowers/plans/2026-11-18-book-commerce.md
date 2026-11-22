# 图书带货情报 Implementation Plan

> 状态：按用户指示已实现并启用当前本地环境。迁移安全边界与实际执行记录见[迁移方案](../../operations/book-commerce/migration.md)。

**Goal:** 实现文件导入、历史浏览、增长榜、候选池和个人决策。
**Architecture:** Flask/SQLAlchemy 独立业务域，保留不可变采集事实并生成版本化分析；静态页面接现有导航与认证。
**Tech Stack:** Python、Flask、SQLAlchemy、Alembic、PostgreSQL、JavaScript。
**Spec:** `docs/superpowers/specs/2026-11-17-book-commerce-design.md` 和 `2026-11-17-book-commerce-standards.md`。

## Global Constraints

- 不实现手填、纠错、二创稿、发布或自动采集；导入冲突整批拒绝。
- 实际数据库迁移链未知，先只读查证，不 stamp、建表或升级现库。
- 未知值不能当零；只有同口径精确计数可比较；候选不声称因果。
- 管理员导入和计算，登录用户读共享数据；人工决策按用户隔离。

## Review Focus

- 重复/并发导入必须幂等；同一观测时点冲突不能覆盖。
- 空值、模糊计数、漏采、时间窗不齐及计数回落不能冒充有效增长。
- 商品平台销量与账号销量分开；挂载关系和账号必须匹配。
- 重算不得丢失个人决策，输入改变后不得发布旧计算。
- 现有用户和平台业务不回归，未知迁移版本不得运行生产升级。

## Tasks

- [x] 模型与约束：在 `models/book_commerce.py` 注册 13 张领域表；独立迁移在临时 PostgreSQL 演练，并应用于当前配置库。
- [x] 导入：在 `services/book_commerce/ingest.py` 实现来源身份、文件校验、预览、幂等提交、引用解析、冲突事务回滚及历史补录。
- [x] 计算：实现日度选择、范围/series 隔离、证据窗口、规则和版本化结果发布；分析经独立 `book_commerce` worker 执行。
- [x] API：实现登录/管理员权限、分页、榜单、证据、计算、个人决策和版本冲突。
- [x] 界面：实现 `book-commerce.html` 与 JS/CSS，接入导航；包含导入预览、账号/商品/视频/候选/决策工作区、空状态与日期筛选。
- [x] 完整验证：使用隔离 PostgreSQL/Redis 与浏览器工作流验证实现；当前运行环境另做页面导航、API、worker、样例文件预览和空库状态核对。性能范围和迁移差异见[迁移记录](../../operations/book-commerce/migration.md)。

## 当前运行验收记录（2026-11-18）

- 导航路径：**选题情报 → 发现机会 → 图书带货情报**；启用开关及模块结构就绪后显示。
- 后端健康检查与 `availability` 返回正常；图书带货专用 worker 正在消费 `book_commerce` 队列，既有其他队列保持运行。
- 真实配置库模块迁移版本为 `bc_20261118_01`，主迁移版本保持 `20261112_upower_topics`；旧表结构未变化。
- 真实配置库的账号、书籍、商品、视频与观测表仍为 0 条；在实际页面上传合成 JSON 并预览新增 6 条观测，预览未提交，输入 revision 保持 0。
- 浏览器首页显示 0 账号、0 商品、0 视频，未出现前端 JavaScript 错误。
- 定向回归：114 passed、4 skipped；隔离 PostgreSQL/Redis、worker 和浏览器端到端：8 passed。10 万观测分析 44.838 秒、榜单热读 P95 0.038 秒。
- 外部输入格式见[导入 JSON 约定](../../operations/book-commerce/import-contract.md)和[数据提供方消息草稿](../../operations/book-commerce/provider-request-draft.md)。当前尚无真实供应方样本，也未向第三方发送消息。
