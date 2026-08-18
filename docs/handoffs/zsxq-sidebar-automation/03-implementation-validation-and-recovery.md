# 实现、验证与恢复证据

## 1. 代码改动

后端：

- `src/backend/services/zsxq_browser_import_service.py`
- `src/backend/apis/v1/zsxq_browser_import.py`

技能：

- `plugins/zsxq-sidebar-sync/skills/zsxq-mp3-sidebar-download/SKILL.md`
- `plugins/zsxq-sidebar-sync/skills/zsxq-pdf-sidebar-download/SKILL.md`
- `/Users/v9/.codex/skills/zsxq-mp3-sidebar-download/SKILL.md`
- `/Users/v9/.codex/skills/zsxq-pdf-sidebar-download/SKILL.md`

测试：

- `tests/unit/test_zsxq_browser_import_service.py`
- `tests/unit/test_zsxq_browser_import_api.py`
- `tests/unit/test_zsxq_sidebar_skill_contract.py`
- `tests/unit/test_frontend_zsxq_bridge.py`

设计与计划：

- `docs/superpowers/specs/2026-08-18-zsxq-sidebar-dedup-and-manifest-integrity.md`
- `docs/superpowers/plans/2026-08-18-zsxq-sidebar-dedup-and-manifest-integrity.md`

## 2. 已完成的自动化验证

后端/API/技能契约：

```bash
venv/bin/python -m pytest \
  tests/unit/test_zsxq_browser_import_service.py \
  tests/unit/test_zsxq_browser_import_api.py \
  tests/unit/test_zsxq_sidebar_skill_contract.py -q
```

结果：`54 passed, 39 subtests passed`。

HTML 桥接：

```bash
venv/bin/python -m pytest tests/unit/test_frontend_zsxq_bridge.py -q
```

结果：`19 passed, 8 subtests passed`。

合计：73 个测试用例通过。第二组测试会临时监听本机回环端口，在受限沙箱中可能需要允许本机端口测试。

格式与差异：

- Black 检查 5 个 Python 文件通过；
- `git diff --check` 通过；
- 项目 MP3/PDF skill 与安装副本的 SHA-256 分别完全一致；
- 最终确认后端健康响应为 `{"status":"healthy","version":"1.0.0"}`。

## 3. 覆盖的关键回归场景

- 发布时间有秒/无秒仍折叠为一个历史文件；
- 同一标签页不同 URL 编码不再拆分身份；
- Chrome `(1)` 后缀和 Unicode 全角/大小写差异能规范化；
- 同名文件即使发布时间元数据变化，三日窗口内仍判为已下载；
- 新式 manifest 无数据库接收凭证时被忽略；
- 有凭证时正常进入历史；
- 接收凭证数据库不可用时历史 fail closed；
- `new_download_success` 与 `files` 数量不一致时拒绝；
- 同轮规范化基名重复时拒绝；
- 同轮内容 SHA 重复时拒绝；
- `filename` 与实际路径基名不匹配时拒绝；
- 明确校验失败不会预留 run_id；
- 两个 skill 均包含等待 2 秒重读、单下载在途、实际文件绑定和冻结 manifest 契约。

## 4. 失败批次与恢复

被判定为不可信的原始失败 manifest：

```text
/Users/v9/Downloads/知识星球下载/inbox/sidebar-submit-mp3-20260818-sidebar-mp3-2026-08-18T0832320800-63ket-90b2de41e6f3.json
```

该文件没有对应 `ZsxqBrowserSubmission` 接收凭证，包含：

- 多个已经在更早完成批次中入库的历史文件；
- 一个同轮 Chrome `(1)` 重复；
- 两个声明文件名与真实路径不一致的条目；
- 一个缺失路径条目；
- 六个经 SHA 和历史核对后确认真正新增的文件。

恢复只提交了这六个有效文件，未删除用户 Downloads 中的任何文件。

恢复证据：

- recovery run_id：`sidebar-mp3-20260818T094800+0800-recovery-six-new`
- submission_id：`e8f370b4-e225-49a3-bb36-ed8fc4025926`
- browser import task_id：`18b33dfe-471f-5a8e-9c8e-be9e8560f5bf`
- sync run：`db58a5d6-a7c7-437b-8875-b993eed866a4`
- workflow：`1f3292cf-aeb1-4f41-88d1-6d70fb77e3eb`
- clean task：`b244ffd4-54d5-4c04-8f3d-ac8244d22607`，`completed/done/100%`
- publish task：`49569ea3-8da7-4bee-865a-99ad6c8ccbf5`，`completed/done/100%`
- 完全相同请求重放返回同一 task_id，`replayed=true`、`queued=false`、`should_dispatch=false`

恢复后可信历史摘要：

- `complete=true`
- `source_files=17`
- `trusted_source_files=16`
- `ignored_untrusted_manifests=1`
- `records_seen=158`
- `unique_downloads=74`
- `duplicates_collapsed=84`

六个恢复文件已经进入可信历史，因此人工验收不应再次下载它们。

## 5. 版本与远端

- 分支：`agent/pdf-media-workflow-reliability`
- 远端：`https://gitee.com/vim999/dig_ytb_channel.git`
- `d779c2f`：manifest 在 run_id 预留前校验
- `6855d4d`：历史去重、可信 manifest、文件绑定及测试
- `fcac0e2`：实施计划完成状态

`HEAD` 与 `origin/agent/pdf-media-workflow-reliability` 已核对一致于 `fcac0e28081f7c55c64dc8cbf32ee5f34d72a459`。

## 6. 重新验证时的注意事项

- 测试前先确认后端健康、项目 skill 与安装 skill 一致、`mp3-2` 仍暂停。
- 不要把工作区其他未提交修改纳入本问题提交。
- 不要以 Downloads 中是否还有旧重复文件作为修复失败的唯一证据；本轮没有授权自动删除历史副本。应以“新一轮是否再次下载”和可信历史/manifest 为准。
- 如果人工运行返回 `replayed=true`，只要 task_id 相同且没有再次派发，这是幂等成功，不是失败。
