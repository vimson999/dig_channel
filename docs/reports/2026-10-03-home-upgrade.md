# 首页优化与升级验收

日期：2026-10-03。范围：首页及其数据接口；未改变采集计划、定价配置、发布任务或数据库结构。

## 已实现

- 收入榜和新增订阅榜按用户后续反馈恢复为 Top 10，空状态收紧，“查看全部”始终保留；手机单列、平板双列、桌面四列。趋势图仍默认比较 5 个频道。
- 趋势图默认比较最新有效值最高的 5 个频道，支持指标选择、自选频道、清空、重置、滚动图例；收入标明 USD。饼图明确表示各频道最新有效值，而非周期累计。
- 缺失日期和缺失月份的后端值返回 null，保留真实的 0。返回范围内最新数据日期与今日待同步频道数。
- 充电数据仅在存在连续两天快照时计算日净增；首次快照或跨日缺口显示未知。收入列明确为估算，展开说明显示每个 UP 主实际配置的单价和分成公式。
- 充电人数列表按人数排序；逐账号展示快照日和同步时间，颜色按 UID 稳定生成。同步提示数量不再写死，部分失败信息不会被后续刷新覆盖。
- 频道筛选框固定头尾、只滚动内容区；支持 Esc、Tab 焦点约束与关闭后焦点归还。图表与表格回调隔离；关闭／切换弹窗后，迟到响应不会覆盖新弹窗。
- 首页默认“一行一个频道”的最新日期摘要，支持搜索与排序；保留完整日期明细、横向滚动、日期分页。没有快照的频道显示待同步，不假装观测值为 0。
- 移除旧 Dashboard 的重复初始化。保留五分钟自动刷新，使用项目现有的 visibility-polling：页面隐藏时暂停、不重复启动、不重叠轮询。

摘要表使用所选范围的共同最新日期，不把不同日期的频道数据混成同一日快照。

## 生效状态

前端文件已更新且带新版本参数，刷新首页即可使用新布局与交互。

本地 5001 后端没有自动重载。本次没有重启后端，也没有停止任何后台任务。以下改动需要后端重启后才会进入正在运行的接口：null 缺失值、has_data 标记、最新数据日期、待同步数量和充电日增缺失基准处理。新前端在旧后端下明确提示需要重启，不将“没有日期元数据”误称为没有快照。

已向用户询问是否现在仅重启本地 5001 服务；截至本次交付尚未获得回复。因此这些后端规则已通过隔离测试，但尚未声称运行中接口已更新。无需数据库迁移，也无需为本次修改重启采集或发布 worker。

## 验证结果

### 自动测试

- `node --test tests/frontend/*.test.cjs`：恢复 Top 10 后重跑，41 项通过。
- `PYTHONPATH=src/backend venv/bin/python -m pytest tests/unit/test_home_dashboard_data.py tests/unit/test_bilibili_charging_dashboard.py tests/unit/test_data_table_pagination.py tests/unit/test_bilibili_charging_frontend_contract.py -q --tb=short`：13 项通过。
- 修改的 JavaScript 语法检查、`git diff --check`：通过。
- 新增回归覆盖：缺失与真实零、月度缺失、首次及非连续日快照、Top 10 榜单与详情链接、Top 5 趋势图、摘要搜索、日期分页、稳定颜色、部分同步失败、弹窗上下文切换、迟到响应隔离、可见性感知自动刷新。

### 全量后端检查的限制

初次 `venv/bin/python -m pytest tests/unit/ -q` 在收集阶段遇到：

- `test_audio_cleaner_output_limit.py` 无法导入 `services`。添加 `PYTHONPATH=src/backend` 后消除。
- `test_zsxq_sidebar_mcp.py` 缺少 `mcp` 包。本次未安装无关依赖，后续全量检查排除了该文件。

添加路径并排除缺失依赖文件后，全量结果为 **1752 通过、22 失败、4 跳过**：

- `test_ensure_zsxq_sidebar_bridge.py` 的 1 项及 `test_frontend_zsxq_bridge.py` 的 20 项受沙箱回环端口绑定限制影响；申请本地测试权限后重跑，**21 项全部通过**。
- 剩余 `test_ranking_pagination.py::RankingPaginationTest::test_yearly_rankings_aggregate_the_requested_calendar_year` 单独重跑仍失败：测试把年榜结束日期写死为 `2026-09-15`，当前接口按当天返回 `2026-10-03`。该测试文件与 ranking_service.py 均未被本次修改，不擅自修改无关断言。

因此不能声称全量后端测试全部通过。

### 浏览器验证

使用独立临时浏览器会话，未点击外部同步／发布按钮；结束时关闭会话。

- 1280×720：筛选框底部位于 y=684，操作按钮没有裁切。
- 390×844：页面宽度与视口均为 390，无整页横向溢出；弹窗底部位于 y≈802。
- Esc 关闭与焦点返回通过；默认 Top 5 显示已选 5，实际勾选 5。
- 图表选择单频道后显示 1 条系列，表格仍保留 51 个频道。
- 表格清空后显示“暂无匹配频道”，不改变图表选择。
- 摘要搜索 Mark 返回 1 行；完整明细切换正常，摘要专属搜索／排序在明细模式禁用。
- 初始请求中排行榜与趋势图不再由两个控制器各请求一次。

## 截图

后续 Top 10 验证：浏览器请求 `/rankings/all?limit=10`，两个月榜均显示 10 条；当天两项日榜无数据，保留空状态，不补假数据。以下截图为恢复 Top 10 之前的布局记录。

桌面：

![桌面首页](/Users/v9/.codex/visualizations/2026/09/30/01a0efc3-1a82-7422-ba3b-f4da758a4219/home-upgrade-desktop.png)

筛选框：

![筛选框](/Users/v9/.codex/visualizations/2026/09/30/01a0efc3-1a82-7422-ba3b-f4da758a4219/home-upgrade-filter.png)

手机：

![手机首页](/Users/v9/.codex/visualizations/2026/09/30/01a0efc3-1a82-7422-ba3b-f4da758a4219/home-upgrade-mobile.png)

摘要表：

![摘要表](/Users/v9/.codex/visualizations/2026/09/30/01a0efc3-1a82-7422-ba3b-f4da758a4219/home-upgrade-summary.png)
