# X 内容运营工作台

入口：`/x-content.html`，统一导航「选题情报 → X 内容运营」。四个栏目通过 URL hash 可直达：`#patterns`、`#cases`、`#posts`、`#tactics`。

## 设计与存储

沿用现有 Flask + SQLAlchemy + 原生 HTML/CSS/JavaScript，不引入独立前端应用。四张核心表分别为 `x_viral_patterns`、`x_viral_cases`、`x_own_posts`、`x_reply_tactics`；字段详见 `src/backend/models/x_content.py`。套路通过真实外键关联案例和自有帖子。帖子引用案例采用 JSON 数组并在写入时校验。

共同字段：稳定字符串 ID、`is_mock`、`source_batch`、UTC 创建/更新时间。指标为可空非负整数；未知与 0 分开。回复率 = 回复 / 浏览 × 100，点赞率 = 点赞 / 浏览 × 100，粉丝撬动倍数 = 浏览 / 当时粉丝数；分母缺失或为 0 返回 null，不相信导入包中已有的计算结果。套路的 `avg_reply_rate` 为来源提供的预期基准，不冒充本系统实测平均。

补充字段包含：标签、来源经验总结、译文、媒体类型、可复用金句、采集时间、原帖链接、蓝 V 回复数、互动老铁、复盘、接梗场景与优先级。独立 `unresolved_case_ids` 保存尚未收到的案例编号，不伪造案例。

## 导入与替换

管理员可通过页面上传最大 5 MiB 的 UTF-8 JSON，或运行：

```sh
venv/bin/python scripts/maintenance/import_x_content.py /absolute/path/data.json --ensure-tables --replace-mock
```

`--ensure-tables` 仅创建四张不存在的新表，不迁移或标记其他表的版本。正式迁移文件为 `20261027_x_content.py`，接在现有 `20261021_shengcai_catalog` 后；在既有迁移链上按项目惯例应用。

支持两种格式：

- 标准格式：`patterns`、`cases`、`posts`、`tactics` 数组，使用数据库字段名。
- 用户交接格式：可选 `meta`，加四个 `x_*` 表名数组；`pattern_code/case_code/post_code/tactic_code` 映射稳定 ID，`ref_case_codes` 映射案例引用；`key_takeaway/reply_formula/matched_handles` 等映射对应字段。

交接格式中无时区日期按 Asia/Shanghai（UTC+8）解释；标准格式要求带时区的 ISO 日期。UI 输入和显示使用北京时间。`cold_start` 归一为 `cold_start_engagement`，`ready_draft` 归一为 `draft_variant`，0/1 的负向标记转换为布尔值。未知字段拒绝，避免静默丢失。

导入按套路 → 案例 → 帖子 → 话术顺序，同 ID 更新，其余真实记录保留。校验、写入、示例清理处于一个事务，失败全部回滚。只删除 `is_mock=true` 的记录；真实记录引用示例时拒绝清理。编辑示例仍保留示例身份；通过真实导入转换为正式记录。示例种子仅显式调用 CLI `--mock` 或管理员 POST `/mock` 时生成，不在应用启动时自动灌入。

## 当前导入结果（2026-10-27）

用户提供完整 JSON：10 套套路、14 个案例、5 篇自有帖子、8 套话术，共 37 条。已导入当前配置数据库，无 mock。原文、作者、拆解与指标来自用户提供的文件，未额外抓取或核验 X。

`POST-20261024-001` 引用的 `CASE-20261024-001`、`CASE-20261024-002` 未包含在数据包中，保存在 `unresolved_case_ids` 并在页面提示。后续收到案例后，可在帖子编辑中选入参考案例并清除待补全编号。

截图字段只有来源仓库相对路径，本次未收到图片文件；页面展示证据路径，不展示虚构缩略图。

## 页面功能与权限

- 套路：时段/验证状态筛选，公式卡片，完整母版复制，带套路关联创建新草稿。
- 案例：正负样本筛选，按浏览、回复率、撬动倍数排序，原文/译文/拆解详情。
- 自有帖子：草稿/已发布筛选，正文、关联套路/案例跳转、冷启动、蓝 V 回复与老铁名单。
- 话术：场景/熟客全文检索，应对方式筛选，优先级排序，逐条复制回复。
- 全部栏目：搜索、分页、详情、新建与编辑，加载/空数据/错误重试。

登录用户可读，管理员可写与导入，沿用系统现有权限。无自动发布、自动评论或自动拉黑行为。

API 前缀 `/api/v1/x-content`：`GET /summary`、`GET /<kind>`、`GET /<kind>/<id>`、`POST /<kind>`、`POST /import`、`POST /mock`、`DELETE /mock`。

## 验证

```sh
venv/bin/python -m unittest discover -s tests/unit -p test_x_content.py
node --check src/frontend/js/x-content.js
```

覆盖权限、原始格式映射、日期时区、派生指标、非法数据、原子回滚、mock 幂等与真实引用保护、迁移结构一致性和回滚。前端另外使用实际浏览器检查四个栏目、筛选、排序、详情与编辑流程。
