# 生财项目库

独立页面：`src/frontend/shengcai-projects.html`，导航入口位于「选题情报 → 生财项目库」。

这是项目列表目录，与知识星球帖子数据分开保存。不请求项目详情、案例正文或资源正文。支持按发布时间范围、项目名称和平台/变现方式/人群标签组合筛选，按发布、收益上限、收益下限、点赞、成本、时间排序及每页 12/24/48/100 项分页（页面默认 48 项）。收益及成本保留来源数值，不补充来源未注明的币种、收益周期或利润口径。

持久化表：

- `shengcai_projects`：来源 ID 主键、名称、简介、全部原始列表字段、内容 SHA-256、首次入库/最后看到/内容更新时间。
- `shengcai_project_imports`：每次导入的来源文件名、快照摘要、项目数、来源 total、新增/更新/重复数量及入库时间。

接口：

- 登录用户 `GET /api/v1/shengcai-projects`；可传 q/name/published_within/platform/monetize/crowd/sort/secondary_sort/page/per_page。name 只匹配名称（忽略大小写），q 保留名称、简介和亮点搜索。published_within 支持 all/7/30/90/365；最近一个月为请求时刻前 30 天，包含起止边界，有时间限制时排除无发布时间和未来发布的项目。sort 和 secondary_sort 支持 newest/income_max/income_min/likes/cost/time；secondary_sort 可为空。先筛选、再排序、最后分页，缺失排序值排后。per_page 范围 1–100。

页面搜索区按句式排列：`[最近一个月] 发布的，按 [收益上限从高到低] 排序，名称包含 [输入框]，每页 [48条]`。各控件可调整，名称默认留空；排序值相同按最近发布排列。重置筛选恢复上述默认值；筛选或排序变化后回到第一页。
- 管理员 `POST /api/v1/shengcai-projects/import`；multipart file，接受 UTF-8 JSON（`success: true, data: {items: [...], total: N}`），最大 5 MiB / 5000 项。

导入事务以来源 ID 更新，重复输入不增加项目数；未出现在本次快照中的旧项目保留。校验失败或写入失败会回滚。仅收集和展示列表中的字段，名称与标签以文本节点渲染，封面只加载来源图片域名的 HTTPS 链接。

数据库迁移：`20261021_shengcai_catalog`。对已有实例也可仅创建这两个缺失表后导入，不迁移或标记其他业务表：

```sh
venv/bin/python scripts/maintenance/import_shengcai_projects.py /absolute/path/list.json --ensure-tables
```

已部署过表结构时省略 `--ensure-tables`。原始文件位于忽略目录 `data/shengcai_catalog/`，不纳入代码仓库。

验证：`venv/bin/python -m unittest discover -s tests/unit -p test_shengcai_project_catalog.py`。
