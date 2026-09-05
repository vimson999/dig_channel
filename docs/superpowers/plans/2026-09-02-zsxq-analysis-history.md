# 知识星球文件分析与历史追溯实现计划

> 设计依据：`docs/superpowers/specs/2026-09-02-zsxq-analysis-history-design.md`

## 目标

实现一个受登录保护的 `zsxq-analysis.html` 入口，提供 PDF/audio 两个独立 Tab；用户上传 `zsxq-list-export/v1` JSON 后生成独立、可追溯的分析批次，并将结构化结果持久化到数据库。

## 实施顺序

1. **先写失败测试（TDD）**
   - 输入契约校验、字段规范化、统计重算、文件名关键词提取、PDF/audio 分析隔离。
   - API 登录、用户隔离、窄历史列表、详情和原始文件下载权限。
   - 模型/迁移表名、索引和无 JSON 结果列的契约检查。
2. **后端领域模型与迁移**
   - 新增 `ZsxqAnalysisRun/Item/Topic/Keyword` 模型并注册到 `models/__init__.py`。
   - 新增以 `20260829_workflow_notification_outbox` 为父 revision 的 Alembic 迁移。
   - 增加分析 artifact 根目录配置/服务常量，路径只允许落在项目 `data/zsxq_analysis` 下。
3. **分析服务**
   - 解析上传字节并限制大小/条数。
   - 根据 kind 分派到 PDF/audio 独立聚合器。
   - 批量落库，列表用显式列查询，明细用分页子表查询。
4. **API 蓝图**
   - 新增 `/api/v1/zsxq-analysis` 蓝图并注册。
   - 所有 handler 使用 `@require_login`，查询条件强制带当前用户 ID。
   - 统一 400/404/409/500 错误响应，避免暴露路径和其他用户 run。
5. **前端双 Tab**
   - 新增入口页面、独立 CSS/JS。
   - 上传区、summary cards、关键词词云、趋势/分布图、热门文件/主题表、当前 Tab 历史列表。
   - 历史列表只拉窄字段，点击后再拉单个批次详情；切换 Tab 不合并状态。
   - 在统一导航中加入入口和当前页标题映射。
6. **验证与上线准备**
   - 运行新增单测和受影响测试。
   - 使用 Alembic 检查迁移脚本、在测试 SQLite 建表并验证表/索引。
   - 对两份用户提供 JSON 做离线服务级 smoke test（不写正式数据库），核对 PDF/audio 分开生成结果。
   - 审计需求：独立批次、历史入库、用户隔离、列表不读大对象、原始文件可追溯、两 Tab 不合并。

## 验收标准

- 同一用户连续上传两个 PDF JSON，历史出现两个独立 run，前一个结果不被覆盖。
- 同一用户上传 audio JSON，结果只进入 audio Tab，不影响 PDF 历史或指标。
- 用户 A 不能通过 run ID 读取用户 B 的摘要、item 或原始 JSON。
- 历史列表返回的数据不包含原始 JSON 或 item 全量；item 接口支持分页。
- 数据库中没有用于本功能的整包输入/结果 JSON 列；原始输入通过受保护 artifact + hash 追溯。
- 前端可展示比词云更有用的汇总、趋势、热度排行、主题聚合和历史批次。
