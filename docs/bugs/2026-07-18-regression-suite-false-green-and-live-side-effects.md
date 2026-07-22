# 回归套件范围不足并混入真实副作用测试

## 基本信息

- 状态：确定性回归门禁已建立并通过；生产迁移与新进程验收仍未完成
- 严重级别：P0
- 影响模块：pytest / 编译检查 / TypeScript / 数据库迁移 / 发布验收
- 首次发现：2026-07-18 全面回归
- 修复提交：未提交
- 根因分类：回归范围不完整、测试假绿、真实副作用未显式隔离
- 复发键：`REGRESSION-ACCEPTANCE-INTEGRITY`
- 是否复发：是
- 关联记录：`2026-07-11-backend-restart-accepted-stale-listener.md`、`2026-07-18-bilibili-upload-json-polling-and-online-ddl-lock-convoy.md`
- 防线层级：deterministic_suite / side_effect_gate / compile_gate / migration_gate / runtime_gate

## 现象

此前定向回归显示 `201 passed`，但扩大到完整 `tests/unit` 后立即发现 4 个失败：一个所谓单元测试实际访问本机 HTTP、一个启动测试仍断言旧调度器集合、两个通知测试仍断言旧业务文案。

继续审计发现 `tests/unit/test_youtube_api.py` 会访问真实 YouTube 并可能写当前配置数据库，却用返回布尔值代替断言，失败时仍可能被 pytest 计为通过。完整 `compileall` 还发现未加载的 `tts_backup.py` 存在语法错误，定向测试无法发现。

这证明“改动附近的测试通过”不能代表历史问题已全面回归，也不能代表代码可部署。

## 实际方案

- 真实 HTTP、YouTube、PostgreSQL 和渲染测试默认跳过，分别使用显式环境变量开启，避免单元套件产生网络或数据库副作用。
- 修正过期断言，并将通知断言更新为当前“发布后回查确认”的真实业务语义。
- 修复 `tts_backup.py` 的语法与分支缩进错误。
- 新增 `scripts/dev/run-known-issue-regression.sh`，固定执行：Bug 复发登记检查、完整 unit + safe integration、Python compileall、Remotion TypeScript、SQL 纪律测试、单 Alembic head、`git diff --check`。
- 数据库结构门禁和重启安全检查保持独立：测试通过不等于迁移已应用，也不等于新进程已真实验收。

## 当前验证

- 完整确定性套件：`558 passed, 5 skipped, 35 subtests passed`。
- Python 全源编译通过。
- Remotion TypeScript 检查通过。
- SQL 纪律、分析器和重启保护专项：`57 passed`。
- Alembic 只有一个 head：`20260718_bili_upload_exec`。
- `git diff --check` 通过。
- 生产只读结构检查仍正确返回 `BLOCKED`：缺少 16 个待迁移投影字段，因此没有把代码测试通过误报成上线完成。

## 验收门槛

1. 每次数据库/编排修复必须完整运行 `scripts/dev/run-known-issue-regression.sh`。
2. 所有真实副作用测试必须显式 opt-in，且不得用返回值代替断言。
3. 数据库改造必须依次给出迁移应用、结构门禁、新 PID 和真实负载日志证据。
4. 任一阶段失败都要重新打开对应复发记录，修复后从第一道门重新执行。
5. 只有自动化回归、迁移、进程替换和真实业务回放全部通过，才能使用“已修复”。

## 复发纪律

- 定向测试只能作为开发反馈，不能作为最终验收。
- 被默认收集的测试不得访问公网、本机服务或生产数据库；需要时必须通过明确环境变量开启。
- 未加载模块也必须编译；前端构建、迁移图和补丁格式属于同一回归门禁。
- 验收证据必须记录命令、数量、跳过原因、迁移状态、进程启动时间和观察窗口。
