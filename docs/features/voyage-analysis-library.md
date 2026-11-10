# 航海项目分析库

这是一个独立于“生财项目库”的分析内容与浏览页面。来源 Markdown 保存在 `docs/航海项目分析/`；网页 API 从独立数据库表 `voyage_analyses` 读取。它不复用现有生财项目列表表。

## 页面与接口

- 页面：`src/frontend/voyage-analysis.html`，导航“选题情报 → 航海项目分析”。
- API：登录用户 `GET /api/v1/voyage-analysis` 列表，`GET /api/v1/voyage-analysis/<slug>` 正文。
- 数据表：`voyage_analyses`，一条记录对应一个航海项目，保存活动 ID、标题、日期、判断摘要、完整 Markdown、顺序及内容摘要哈希。
- 同步方式：以 Markdown 和 README 索引为来源，通过导入脚本幂等同步到数据库。

首次建表时，如果暂不执行其他尚未应用的项目迁移，可仅创建本表并导入内容：

```sh
venv/bin/python scripts/maintenance/import_voyage_analyses.py --ensure-table
```

后续修改 Markdown 和 README 索引后，运行同一脚本（省略 `--ensure-table`）即可同步变更。Alembic migration 为 `20261110_voyage_analyses`，位于 `20261105_hotspot_images` 之后。

## 综合评级

评级存入独立表 `voyage_analysis_ratings`，包含项目标识、分数、等级和理由。API 每次从数据库读取，并按分数降序排列；未评级项目排在最后。A 为 80 分及以上，B 为 65–79 分，C 为 65 分以下。这是基于已有分析、面向个人副业筛选的主观综合判断，不是收益预测。

初始评级来源为 `src/backend/apis/v1/voyage_ratings.json`，使用 `venv/bin/python scripts/maintenance/import_voyage_ratings.py` 幂等写入数据库。
