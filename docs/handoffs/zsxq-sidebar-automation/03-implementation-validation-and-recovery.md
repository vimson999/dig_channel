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

当前结果：`62 passed, 105 subtests passed`。

HTML 桥接：

```bash
venv/bin/python -m pytest tests/unit/test_frontend_zsxq_bridge.py -q
```

结果：`20 passed, 8 subtests passed`。

合计：82 个测试用例、113 个 subtest 通过。第二组测试会临时监听本机回环端口，在受限沙箱中需要允许本机端口测试；本轮沙箱内运行时后端/API/技能相关项先通过，桥接项仅因 `PermissionError: Operation not permitted` 无法绑定回环端口；在宿主机上下文重跑后 `82 passed, 113 subtests passed`。Gate R 提交又暴露“后端慢于桥接 3 秒超时”的回执误报，新增测试先稳定得到 502，再实现独立 30 秒提交等待预算，最终 `20 passed, 8 subtests passed`。

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
- `candidate_failure_count` 与 `candidate_failures` 数量不一致时拒绝；
- 已知失败阶段计数与 `candidate_failures.failure_stage` 实际条目数不一致时拒绝；
- 两个 skill 都要求历史四项逐字段复制 `data.summary`，禁止用 `data.files.length` 重算记录数或折叠重复数；
- 同轮规范化基名重复时拒绝；
- 同轮内容 SHA 重复时拒绝；
- `filename` 与实际路径基名不匹配时拒绝；
- 明确校验失败不会预留 run_id；
- 两个 skill 均包含等待 2 秒重读、单下载在途、实际文件绑定和冻结 manifest 契约；
- 下载点击前必须先写 `download_click_ledger` 并确认完成监听器 active；
- `ABORTED` 是不可恢复终态，连接重置、未决点击和无 armed 候选的意外事件会停止后续点击；
- 点击数、唯一终态事件数和未决数必须满足账本等式；
- 点击前干净中止与落盘污染分离，只有后者令 `manifest_blocked_by_download_integrity=true`；
- 8888 历史查询和有副作用的提交使用不同等待预算，后端在普通 3 秒预算之后但在提交预算内返回 202 时，页面仍呈现真实成功回执；
- 18 个全新上下文的决策压力场景覆盖未确认项继续、批量点击、连接重置、监听顺序、迟到事件、文件名错配和明确无落盘失败。旧版“排除未确认项后继续”的错误合理化已稳定复现，最终版相关复测均通过。

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

## 5. Gate A 第一轮失败与游离文件清单

### 5.1 侧边栏报告与实际落盘不一致

- MP3 报告：历史完整、74 条记录；扫描 40 个主题、发现 108 个 MP3；确认成功 1 个，并承认另有一次未绑定误触发。
- MP3 实际：10:27～10:29 新增 2 个文件。
- PDF 报告：历史完整、77 个唯一文件、210 条记录；扫描 40 个主题、发现 207 个 PDF；确认成功 1 个，随后连接重置。
- PDF 实际：10:55～10:58 新增 8 个文件。
- 两轮均没有 manifest 路径或 task_id；2026-08-18 12:17 的只读数据库复核显示，自 02:00 UTC 起 `ZsxqBrowserSubmission=[]`、browser `ZsxqSyncRun=[]`。

### 5.2 保留中的 10 个文件

以下文件均仍在 `/Users/v9/Downloads/`。内容 SHA-256 互不重复，且名称/SHA 均未命中当时的可信三日历史：

| 类型 | 落盘时间 | 字节数 | SHA-256 | 文件名 |
| --- | --- | ---: | --- | --- |
| MP3 | 10:27:23 | 22,343,305 | `6158788518c4abe6927dd987a7cf922f77eac8f3cfafffa33ea127fe85041b96` | `长江大金融周一有约213期政策空间与配置方向260817.mp3` |
| MP3 | 10:29:00 | 11,952,421 | `5fa4d14152ac7d21d6f7a4b6f644b332cfa793b50717dd15d0f8bc0f26b6225d` | `食品饮料深度报告巡礼食健谈AKK国产益生菌破局之矛260817.mp3` |
| PDF | 10:55:44 | 2,887,592 | `760aea921012071d033525f0b576ed47386af87c039ed4820a41dcdd952f5666` | `The Biggest Bounce Most Missed -ZeroHedge.pdf` |
| PDF | 10:56:41 | 916,747 | `76dac9510533bc05d9bd3b89f367062ee428e1e83ab05c9b966306677609ae12` | `高盛-平安银行(000001.SZ)：盈利回顾：基本符合预期；零售资产质量持续改善，聚焦财富管理和股东回报上行-260817.pdf` |
| PDF | 10:56:44 | 446,590 | `4b033adb6f08de2ce46502e724d830d84098e56fa968c8e750ae9c5035ea6612` | `高盛-日本：2026年第二季度实际GDP初值环比年化增长1.1_，低于市场预期-260817.pdf` |
| PDF | 10:57:20 | 805,609 | `9bfe2aceae744bf2fd5ac855501bf2d47e94bf365ba6ffd0f9d964d1b61a1a52` | `高盛-HawkEye 360公司(HAWK)：季度业绩稳健，国际势头强劲，星座建设进展顺利；买入-260816.pdf` |
| PDF | 10:57:23 | 629,825 | `23811a8bbc5f2403a750b988483762e5d70cbb92a96a7d9af344b418ffb59fe5` | `高盛-技嘉(2376.TW)：AI服务器扩张；强劲资本支出承诺以捕捉需求；7月和2026年第二季度业绩超预期；中性-260817.pdf` |
| PDF | 10:57:58 | 526,805 | `4f050e961ce88bfbab388b676ee2606357df7545a25e9dcf341f2478e8253d3b` | `伯恩斯坦-全球储能：电池周刊8月17日-260817.pdf` |
| PDF | 10:58:02 | 751,767 | `3caaebb75290e659b0b58ed29ab5b7ae3a669320b341d5b13df952003080e408` | `伯恩斯坦-印度电动汽车电动革命：稀土机会与印度新兴供应链-260817.pdf` |
| PDF | 10:58:37 | 1,256,156 | `5b93a27f73b1853b5ce57fb51e870f0b38df2a3c05259200ca28d45c491bf42c` | `摩根大通-医药行业周报：8月10日至14日：行业重新评级的机会-260815.pdf` |

### 5.3 只读页面核对结果与恢复草案

- 不删除、改名或移动上述文件。
- 不按落盘顺序猜候选，不把侧边栏报告的“1 + 1”当成完整清单。
- 已在原知识星球标签页逐个打开同名附件详情，只读核对文件名、主题时间和当前下载次数；全程没有点击“下载文件”。
- 合规 MP3：`食品饮料深度报告巡礼食健谈AKK国产益生菌破局之矛260817.mp3`，下载次数 12，主题时间 `2026-08-17 23:56`。
- 合规 PDF 共 7 个：下载次数依次为 36、32、31、32、31、31、32；主题时间分别为 `20:55`、六项中的五项 `19:56`、最后一项 `09:49`，均为 2026-08-17。
- 排除 MP3：`长江大金融周一有约213期政策空间与配置方向260817.mp3` 当前下载次数 10，不满足“严格大于 10”。
- 排除 PDF：`高盛-平安银行(000001.SZ)…260817.pdf` 详情页不显示下载次数，且受内容保护、没有“下载文件”按钮；不能伪造成满足“严格大于 30”。
- 历史观察不能单独证明一次新下载，后端也会拒绝低于阈值的 inline manifest；因此两个排除项没有被塞进 `files` 或 `history_observations`。

已生成并以冻结内容提交：

- [MP3 recovery 草案](gate-a-20260818-mp3-recovery-draft.json)：1 个 `files`，1 个 `recovery_exclusions`；canonical payload SHA-256 为 `4cadb5f57992745a87cbf8422cca154dee5d0e447a222842028578180e1e55fc`。
- [PDF recovery 草案](gate-a-20260818-pdf-recovery-draft.json)：7 个 `files`，1 个 `recovery_exclusions`；canonical payload SHA-256 为 `5a533d4aec358d15c7968a2f2402e22198e076bb7a875059bb7d0c3c6f09cfe4`。

两份草案均已通过真实 `canonical_manifest_payload`、`preview_manifest` 和 `_validate_inline_manifest_preview` 检查：文件数 1/7、重复内容 0、未知下载次数 0、声明/实际 SHA 不一致 0、排除项与 `files` 交集 0。

- 只有元数据与实际路径/SHA 完整绑定的 8 项进入恢复草案；缺字段或低阈值条目保持排除。
- 用户已明确授权两份恢复清单；恢复只包含上述 8 个合规文件，没有删除、移动或提交两个排除项。
- 两个恢复任务及可信历史复核均已完成；Gate A 前仍须再次只读检查两个排除项：若仍不合规，最终版 skill 应安全跳过；若后来变为合规，须先用现有落盘文件恢复或取得用户明确处置，不能直接再次下载。

### 5.4 Gate R 最终回执

MP3：

- submission：`d35b031c-9483-44b9-a236-7b8ac6fc69bb`，browser task：`b937eaa0-6918-5037-a626-91ffa14f8acc`，`completed`，attempt 1；
- manifest：`/Users/v9/Downloads/知识星球下载/inbox/sidebar-submit-mp3-20260818-sidebar-mp3-20260818T122825_0800-gatea-recovery-one-valid-4cadb5f57992.json`；
- sync run：`6d770570-c59c-4a16-ab0d-37345839f9e6`，workflow：`ed4351b1-503a-420e-b77c-7403ea358597`，`completed/done`；
- clean：`143ec238-0521-46df-bd91-c6c705ec6ff9`，publish：`5c333d83-1ed1-4258-9c99-f734fb250515`，均 `completed/done/100%`；
- 可信历史：`complete=true`、`source_files=18`、`trusted_source_files=17`、`ignored_untrusted_manifests=1`、`records_seen=159`、`unique_downloads=75`、`duplicates_collapsed=84`；目标文件命中 1/1。

PDF：

- submission：`87d83640-f06d-473d-ba15-5e336982dea4`，browser task：`8bcee273-5042-5af3-b9f1-f0e03de45088`，`completed`，attempt 1；
- manifest：`/Users/v9/Downloads/知识星球下载/inbox/sidebar-submit-pdf-20260818-sidebar-pdf-20260818T122825_0800-gatea-recovery-seven-valid-5a533d4aec35.json`；
- sync run：`84398175-6342-41a9-98d7-836bd6f3a60e`，workflow：`4fd649df-9a9f-4942-ba23-854ee0f65228`，`completed/done`；
- clean：`c323a760-367a-45bc-af07-df9d55906e80`，publish：`bef12828-8a67-4f83-b801-75d299e148e7`，均 `completed/done/100%`；
- 可信历史：`complete=true`、`source_files=12`、`trusted_source_files=10`、`ignored_untrusted_manifests=2`、`records_seen=217`、`unique_downloads=84`、`duplicates_collapsed=133`；目标文件命中 7/7。

MP3 首次提交页在后端已经插入接收凭证并最终返回 202 后，因旧桥接 3 秒超时先显示 `zsxq_backend_unavailable`。按固定 run_id 查询到真实 `completed` 回执后没有重试 MP3。桥接修复并重启后，PDF 页面明确返回 `success=true`、`queued=true`、`replayed=false`；两条 submission 的 attempt 均为 1。

### 5.5 Gate A 前排除项重查

首批 8 个文件进入可信历史后，又在原 `vimson5000` 知识星球标签页只读打开两个排除项详情，全程没有点击“下载文件”：

- `长江大金融周一有约213期政策空间与配置方向260817.mp3`：详情现显示星主已开启内容保护、无下载按钮，也没有可用下载次数，继续保持排除。
- `高盛-平安银行(000001.SZ)…260817.pdf`：详情现显示“下载文件”和“下载次数：31”，已从“不合规”变为满足 PDF 严格大于 30。其现有落盘文件不能让下一轮 skill 再下载。

已生成 [平安银行 PDF 单文件补充 recovery 草案](gate-a-20260818-pdf-pingan-recovery-draft.json)：

- run_id：`sidebar-pdf-20260818T125800+0800-gatea-recovery-pingan-newly-eligible`；
- canonical payload SHA-256：`d9aeaa74bcdd5e0b681d9caa2005af2f8d5d5930c6bc689b02e1cb4819533879`；
- `files=1`、下载次数 31、重复内容 0、未知下载次数 0、SHA 不一致 0、warnings 0；
- 用户随后明确授权提交这第三份清单；原两份授权未被自动扩展，第三份授权单独记录如下。

### 5.6 平安银行 PDF 单文件补充恢复回执

用户明确确认后，于 2026-08-18 13:15 左右通过 8888 本地提交桥接提交冻结清单；没有重新点击知识星球附件，也没有新增下载。

- submission：`c735e41b-f2b1-480a-8160-c16910f66e1c`，browser task：`86b09e56-b4b9-50f9-868b-832e6c79a6c3`，`completed`，attempt 1；
- manifest：`/Users/v9/Downloads/知识星球下载/inbox/sidebar-submit-pdf-20260818-sidebar-pdf-20260818T125800_0800-gatea-recovery-pingan-newly-eligible-d9aeaa74bcdd.json`；
- run_id：`sidebar-pdf-20260818T125800+0800-gatea-recovery-pingan-newly-eligible`；canonical payload SHA-256：`d9aeaa74bcdd5e0b681d9caa2005af2f8d5d5930c6bc689b02e1cb4819533879`；
- sync run：`29d25e00-6d45-4684-a7e7-1ef90d79b130`，workflow：`8a434113-896e-4132-b861-da5636f5807f`，均已 `completed/done`；
- clean：`f7c65c01-8e4d-4656-91cd-59f99a622f91`，publish：`03952b02-f016-40f4-9898-155ad6d3bbf1`，均 `completed/done/100%`，无错误；
- 文件：`高盛-平安银行(000001.SZ)：盈利回顾：基本符合预期；零售资产质量持续改善，聚焦财富管理和股东回报上行-260817.pdf`，下载次数 31，SHA-256 `76dac9510533bc05d9bd3b89f367062ee428e1e83ab05c9b966306677609ae12`；
- 提交预览校验：`files=1`、`valid=1`、重复内容 0、未知下载次数 0、SHA 不一致 0、warnings 0；
- 提交后可信 PDF 历史：`complete=true`、`records_seen=218`、`unique_files=85`，目标文件命中 1/1，`download_count_at_download=31`、`latest_download_count=31`，历史页回报 `success=true`。

### 5.7 24/10/5 MP3 修复技术复验回执

用户在临时参数下完成 MP3 手动复验：扫描 10 个主题、84 个附件、28 个 MP3，均在 24 小时内；仅 1 个候选满足严格大于 10，成功下载并绑定为 `再谈MPO无源光互联的强通胀环节260818.mp3`，下载次数 12。其余 27 个均因下载次数不超过 10 跳过。

- browser task：`64e13268-0ec9-5f57-8569-fa041d8ab841`；submission：`224e1038-97a4-44bc-bed6-3caaa2a40af5`，`completed`，attempt 1；
- manifest：`/Users/v9/Downloads/知识星球下载/inbox/sidebar-submit-mp3-20260818-sidebar-mp3-2026-08-18T05-52-15-915Z-veyl6b-6d3c296d7fb2.json`；run_id：`sidebar-mp3-2026-08-18T05-52-15-915Z-veyl6b`；
- 下载事务：点击 1、完成事件 1、未决 0、意外事件 0、`abort_latch=false`、`manifest_blocked_by_download_integrity=false`；文件名绑定通过，无错配或重复；
- 历史：扫描前完整，75 条记录/75 个唯一文件；提交后只读复核为 `complete=true`、160 条记录、76 个唯一文件，目标文件下载次数 12 命中 1/1；
- sync run：`e4b6ed57-cbbf-4df0-81bb-ed455322e2a2`，workflow：`e528f48b-50f0-4b4a-9571-fbb94f9f869c`，均 `completed/done`；clean：`872d350e-4847-4d29-abcc-10ccf51ca6e9`，publish：`5e653d3d-8037-450a-bad5-364ade7fe02a`，均 `completed/done/100%`。

### 5.8 24/10/5 PDF 修复技术复验回执

用户在临时参数下完成 PDF 手动复验：扫描 10 个主题组、52 个 PDF，均在 24 小时内；52 个文件下载次数均不超过 30（最高 29），因此全部安全跳过，没有发起下载。无新文件不等于失败，后端仍完成可信的 0 文件幂等提交并明确跳过下游清洗/发布。

- browser task：`cf829376-0844-59de-aab0-03bb9253a33c`；submission：`1e728836-8dbf-4a93-9844-f4394ac95bd5`，`completed`，attempt 1；
- manifest：`/Users/v9/Downloads/知识星球下载/inbox/sidebar-submit-pdf-20260818-sidebar-pdf-2026-08-18T06-17-53-076Z-a7c-e3162efb291d.json`；run_id：`sidebar-pdf-2026-08-18T06-17-53-076Z-a7c`；
- 下载事务：点击 0、完成事件 0、未决 0、意外事件 0、连接重置在途 0、`abort_latch=false`、`manifest_blocked_by_download_integrity=false`；
- 历史：`complete=true`、218 条记录、85 个唯一文件、重复折叠 133、忽略不可信 manifest 2；`historical_already_downloaded=0`，本轮无新文件；
- sync run：`0b820789-67a6-4c08-ba48-07a738f179e8`，workflow：`9d75f0d7-874b-4f21-8c9a-0284a7cc2396`，均 `completed/done`；clean/publish 均为 `skipped`，原因 `no_new_files`，没有创建下游任务。

### 5.9 正式 Gate A PDF 回执：同 run 重放完成但发现重复副本

用户随后按正式 48/40/40 参数运行 PDF skill。下载事务部分先完成，首次提交因本地服务不可达而处于未知状态；服务恢复后只对原冻结对象做了一次同 run 幂等重放，没有重新扫描或下载：

- 扫描 29 个主题组、152 个 PDF，其中 147 个在 48 小时内、5 个超出窗口；97 个因下载次数不超过 30 跳过；观察到 40 个历史文件的最新下载次数；
- 新下载 10 个，下载点击/完成事件 `10/10`，失败、超时、冲突、未决均为 0；
- 本地同步提交页在服务不可达期间重试 2 次，均为 `ERR_CONNECTION_REFUSED`，没有拿到 `task_id` 或 `manifest_path`；后端只读查询也未发现对应的新 `zsxq_browser_submissions` 或 `zsxq_sync_runs` 记录；
- 之后只恢复了本地 5001/8888 服务，健康检查均为 `healthy`，启动时显式关闭了 worker、watchdog 和 scheduler；没有再次调用 PDF skill，也没有再次点击附件；
- 这 10 个文件已冻结，不能删除、改名、移动或直接再次下载。其中文件 `人工智能基础设施正超越GPU范畴 (1).pdf` 的 SHA-256 与 Downloads 中既有 `人工智能基础设施正超越GPU范畴.pdf` 相同，说明必须在恢复/提交前做同轮重复内容核对。

本轮新增落盘清单（以实际路径和 SHA-256 冻结）：

| 文件 | 字节数 | SHA-256 |
| --- | ---: | --- |
| `高盛-中国：中国的三件事-260816.pdf` | 485736 | `3f22c15c65523ee018170749be6a11c7e31dd063d7bb1f281f0bddccc469ef7e` |
| `摩根大通-美国医疗服务：每周盘点：事件日历、核心研究、数据趋势、业绩与估值-260815.pdf` | 2265102 | `c40b43b856c2cdb200c679e484f9eff3ccc9a53621ba34295c3ff6d3ca9dc578` |
| `伯恩斯坦-贵州茅台(600519)：第二季度因非标库存减少而低于预期，但新渠道策略仍在正轨-260816.pdf` | 783198 | `a41cdb9defdb563da6f9650b9df92cdecb4812ec8c845684464ff09098c76fff` |
| `高盛-Etsy Inc. (ETSY)：后 Depop 时代的 Etsy 崭露头角；更新运营预期并梳理核心争议；恢复“中性”评级（目标价 89 美元）-260816.pdf` | 1115351 | `533c3e8b9ddd53eebfd0bc1a1a92fe29a9695df731e7f47a1e8dce2bc3fee996` |
| `TMTB 早报 - TMT Breakout --- TMTB Morning Wrap - TMT Breakout.pdf` | 1322249 | `2e713b9b86e33b5c4280ac5cd82c07251bc265bb3ca48055eee4c2dfea914ed0` |
| `AI Infrastructure Is Moving Beyond the GPU.pdf` | 18291842 | `b406b85aefc9767487d6f2a5cbba8fc2b3edfb858249c3e25008303e041208d2` |
| `人工智能基础设施正超越GPU范畴 (1).pdf` | 18954354 | `381fae1d48d05ecb5e0512f9bfce42b40e9a625bab84763e60fb32d56ee0a1d8` |
| `高盛-韩国观点：8月货币政策委员会预览——鹰派按兵不动，但悬而未决-260817.pdf` | 800645 | `1154f084a2708ed7a06abe219d4d98847ac21bac6b60c39f4728a8615171e30c` |
| `高盛-美国周展望：8月17日至8月23日-260815.pdf` | 441013 | `44eff948b17c2809869f15fb26eb6b86c990fbe514f2aa009da2a874422172ff` |
| `高盛-全球观点：他们没有加息-260816.pdf` | 597401 | `b4f7e86d491531a84631dde0dd4b1b9fb1e05f0e2dab641316ebc6553e8b42ba` |

同 run 重放与只读终态核验结果：

- 原侧边栏 run_id：`sidebar-pdf-2026-08-18T08-30-50-606Z-yjzgn0`；冻结 manifest：`/Users/v9/Downloads/知识星球下载/inbox/sidebar-submit-pdf-20260818-sidebar-pdf-2026-08-18T08-30-50-606Z-yjzgn0-90cba7040023.json`；
- submission：`2dc7c079-d69c-46e7-87a8-7d5b6853a224`，browser task：`9adb7e08-cb07-5bf1-9198-f6cf9d92a276`，`completed`、`attempt=1`、无错误；
- sync run：`9d629cc8-dd1f-4b91-855d-e8ef2f407b53`，`completed`，下载账本完整，附件 `10/10`；
- workflow：`8c5daae3-1d9f-4ecb-bede-acb3a495abc0`，`completed/done`；
- PDF 清洗任务：`5d364d2e-991c-4c53-8bf4-1ea54fcd496c`，`completed/done`，成功 `10/10`、失败 0；
- 自动发布任务：`efb89adf-adb2-4ecb-b90a-a2e17aa5a7e4`，`completed/done`，分两帖发布并回查确认 `10/10`，失败 0、待确认 0；
- 最近三日可信历史 `complete=true`，10 个规范化文件名命中 `10/10`；统计为 15 个来源文件、13 个可信来源、忽略 2 个无 receipt manifest、268 条记录、95 个唯一下载、折叠 173 条重复记录。

`人工智能基础设施正超越GPU范畴 (1).pdf` 与 Downloads 中旧的无后缀文件 SHA-256 相同，但旧文件没有受信 submission receipt，按既定架构不得作为侧边栏成功历史。当前 manifest 内只有这一份内容，`duplicate_actual_file_in_same_run=0`、后端预览 `duplicate_content=0`，运行前可信历史也未命中它。因此单个 Chrome 后缀只是旧游离文件造成的本地命名冲突，不是可信历史重复或同轮重复；不能仅凭 Downloads 旧副本否决本轮。

当前处置：此前对 PDF 子门禁的批准结论 **撤回并重新标记为待复验**。`(1)` 文件与旧 Downloads 游离文件同 SHA，证明本轮仍有重复下载问题；不得为了复核盲目重跑 PDF skill，必须先修复下载完成回执/重复下载根因。计划任务继续暂停。

### 5.10 正式 Gate A MP3 回执：终态缺路径但实际落盘 17 个文件

用户按正式 48/40/40 参数运行 MP3 skill。侧边栏报告和冻结 manifest 显示：

- 扫描 40 个主题组、327 个附件、109 个 MP3，全部在 48 小时内；历史完整，160 条记录、76 个唯一文件、折叠 84 条，忽略 1 个不可信 manifest；
- 16 个历史文件观察、75 个低阈值文件跳过；
- 发出 17 次下载点击并收到 17 个终态，但 17 个终态连续两次都缺少 `absolute_path` 和 `filename`，全部记为 `download_result_incomplete_after_retry`，没有进入 `files`；
- `new_download_success=0`、`files=[]`、`unresolved_download_count=0`、`unexpected_download_without_armed_candidate=0`、`duplicate_candidate_in_same_run=0`、`duplicate_actual_file_in_same_run=0`、`abort_latch=false`、`manifest_blocked_by_download_integrity=false`；
- submission：`85f3d3f0-178e-4a14-bd1e-bd22d4a7fa18`，browser task：`e73c76cd-0517-58a1-bf0f-ba22d12364e3`，`completed`、attempt 1；
- sync run：`0c6e43b2-2f1e-4869-a3ed-b3ebaccd2697`，`completed`，`attachments_downloaded=0`；workflow：`bf4f3ad5-4fc6-4104-82a4-63a7e8e20791`，`completed/done`，没有创建清洗或发布任务。

但只读文件系统核对发现，在本轮 17:37～17:44 的下载时间窗口内，`/Users/v9/Downloads/` 实际新增 17 个 MP3，文件名与 17 个失败候选一一对应；其中：

- `长江大金融周一有约213期政策空间与配置方向260817 (1).mp3` 于 17:40 落盘，大小 22,343,305 字节；
- `/Users/v9/Downloads/长江大金融周一有约213期政策空间与配置方向260817.mp3` 于 10:27 已存在，两个文件大小相同，SHA-256 都是 `6158788518c4abe6927dd987a7cf922f77eac8f3cfafffa33ea127fe85041b96`；
- 其余 16 个新文件同样没有进入 manifest、可信历史、sync、clean 或 publish。

这证明“终态缺路径/文件名”不是单个候选偶发失败，而是下载完成回执能力在连续候选上整体失真；当前候选失败后继续点击的策略把一个全局回执故障放大成 17 个游离文件，并再次产生 Chrome `(1)` 重复副本。正式 MP3 Gate A **未通过**，不得批准或启用计划任务。

### 5.11 第二层 fail-closed 修复与第二批 Gate R 冻结清单

根因修复已按 TDD 完成：先把“路径/文件名缺失后仍继续、污染后仍提交 `files: []`”写入双技能契约测试，旧实现得到 14 个关键失败；再修改 MP3/PDF skill。新规则为：

- 第一次下载点击前，必须证明当前浏览器下载完成机制能在同一终态返回非空 `absolute_path` 和 `filename`；不得用真实下载试探能力；
- 只有点击前失败，或浏览器明确返回 `download_failed` 且确认没有文件落盘，才可安全继续；
- `download_terminal_timeout_after_retry`、`download_result_incomplete_after_retry`、事件冲突、真实文件错配、同轮实际重复、无 armed 候选事件、迟到事件和在途连接重置，首次出现即设置 `abort_latch=true` 与 `manifest_blocked_by_download_integrity=true`；
- 完整性污染后立即停止后续点击，不提交普通 manifest，包括伪装成正常的 `files: []`；冻结账本并进入 Gate R，恢复前不得自动重试候选。

验证结果：技能契约 `10 passed, 76 subtests passed`；侧边栏 MCP、8888 桥接、browser import、workflow failure/recovery/finalizer 等相关链路合计 `118 passed, 99 subtests passed`。受限沙箱首次运行时 33 项仅因禁止绑定本机回环端口失败；允许临时回环测试端口后全绿。项目与本机安装副本逐字节一致：

- MP3：`68f7a29f1c09685f6f602e3f0ec7bda60fd0f87cdd3a5da514c2a8952a721070`
- PDF：`4481538488ff759c50c22b4cf8c0449136e51da6657e623e8609c4c21280fd47`

正式 MP3 轮次的 17 个失败候选与 17:37～17:44 实际落盘文件已按文件名、终态时间、大小和 SHA-256 只读对应。冻结证据如下；这不是恢复 manifest，尚未授权提交或发布：

| 候选/实际文件 | 下载次数 | 大小 | SHA-256 |
| --- | ---: | ---: | --- |
| `继续看好反制链品种-钨稀土锆260818.mp3` | 13 | 13,482,988 | `c671a2874b6280659928bd5ec69430580b957ebf0a0f19c3d199e57f14ddb8e1` |
| `最深度消费出海跨境产业链深度报告260818.mp3` | 11 | 29,008,500 | `75a980332b392cc86c2212dcff52aa2cd62bda9d88cc88c1737fb50196a5bb75` |
| `洁净室专题存储及先进制程扩产提速量增利升双轮驱动260818.mp3` | 13 | 16,865,532 | `5193f11e550bc26f406a1fe9d5aa4b527a5f357e456ef2ca3da81002fa798278` |
| `cpo进入量产阶段设备端我们应该关注什么260818.mp3` | 12 | 10,099,608 | `a2876a7318f52397589b84208dd15894c2f03b72000699e7a457fafd57d44ac9` |
| `长江大金融周一有约213期政策空间与配置方向260817 (1).mp3` | 14 | 22,343,305 | `6158788518c4abe6927dd987a7cf922f77eac8f3cfafffa33ea127fe85041b96` |
| `如何看空调需求和竞争格局260817.mp3` | 14 | 48,685,182 | `97f9be44954e0148a9faf801afc6e9120e7b64f24c90d4de4558286a1ebddaf7` |
| `三祥新材杨占魁260817.mp3` | 15 | 13,966,149 | `65c1db2af0760a4d982cd75df3dbd365f9cbadf914285fcc5145babe82d6184e` |
| `石头科技观点解读260817.mp3` | 20 | 45,704,715 | `2532a4165f4afb88f1baefaf4eabbf0ec4f770c601ee365a7b110df5420f7293` |
| `纳指逼近前高关键变量还有哪些港股海外周论260817.mp3` | 12 | 7,401,465 | `eccaa32db91c9e0c5faefd906e86a758d8640a869d96b3c2e4bd0ff7babe8302` |
| `AI_Coding为何是企业AI最大战场260817.mp3` | 14 | 23,874,707 | `f01e7f59a63ca4b1dc9489c6462ccdbfb30ac48b4724a059b8483e5ef89d6f6d` |
| `从药物研发出发AI4S行业全解析260817.mp3` | 11 | 76,357,842 | `d878485206b45d662cb858df6805a84e40225937d3f356dbf8ca8c29a7c26454` |
| `AI+硬科技产业投资机会再梳理260817.mp3` | 14 | 38,576,839 | `9a1a2ff14eaabe64f1460eaa85a45b2681031301cc2d39ea306443cf89013bf6` |
| `AI趋势下的‘大轮换’时刻经济重构、投资布局与就业转型260817.mp3` | 11 | 69,391,716 | `01e5767c2c50134e3932182b4dd92a8b317f15ed93a1b5e320bb7708fc1cddef` |
| `周观点精粹260817.mp3` | 11 | 34,425,669 | `2528c5c7d5fbbe7c679b6d8c3d83484f4b6cb75b120f192f20fce268e1a3a10d` |
| `周期拾级三7月数据看猪的节奏260817.mp3` | 11 | 22,102,142 | `facc0645da3525b0c2fcbb2f118dc24b2f171cfead841cb9b9a4068b41d4d4f2` |
| `速览全球模型及AI应用260817.mp3` | 12 | 14,852,222 | `bb7ed2eafd196d313757bcea8748ae5dc1a06d8950496d07c5e356224ea2c71f` |
| `基本面观察二挂牌量260817.mp3` | 11 | 19,363,674 | `032e746e8617d9bc55cf6af3c79dfb3864f99d7a68d805f2e1a7f337a8aa1c14` |

`长江大金融… (1).mp3` 与 10:27 已存在的无后缀文件大小和 SHA 完全相同。恢复时只能选择其中一个实际路径进入 `files`，另一个必须列入重复内容排除项；不删除、不改名、不双重发布。

每个候选的 `published_at`、`topic_title` 和 `source_url` 已在当前 MP3 标签页只读绑定，17 个名称均为唯一页面匹配，全程未打开详情弹层或点击下载。本地可信历史随后核对为 `complete=true`、176 条记录、76 个唯一下载、折叠 100 条、忽略 1 个不可信 manifest；17 个候选命中 0 个。

恢复草案：`docs/handoffs/zsxq-sidebar-automation/gate-a-20260818-mp3-postclick-recovery-draft.json`；run_id：`sidebar-mp3-20260818T181250+0800-recovery-postclick-17`。真实 `canonical_manifest_payload`、`preview_manifest` 和 `_validate_inline_manifest_preview` 结果：17/17 有效、下载次数全部严格大于 10、重复内容 0、未知下载次数 0、声明/实际 SHA 不一致 0、warning 0；payload SHA-256 为 `a32fe61fc76d62c82453c4c711d6ce65c878e3ea932f23071e66d6e8cc823d9a`。旧无后缀 `长江大金融…mp3` 仅在 `recovery_exclusions`，不进入 `files`。

以上是提交前的冻结状态；后续完成回执见 5.12。

### 5.12 第二批 Gate R 完成与 PDF 0-click 能力预检修复

用户明确确认后，第二批 17 个 MP3 已使用原冻结恢复清单提交，没有重新扫描或下载：

- manifest：`/Users/v9/Downloads/知识星球下载/inbox/sidebar-submit-mp3-20260818-sidebar-mp3-20260818T181250_0800-recovery-postclick-17-a32fe61fc76d.json`；
- submission：`57e865fe-6918-484d-bf6d-deb9cd9af5f2`；browser task：`10783685-83bb-5b96-b124-984cbca01ab0`；
- workflow 完成；清洗 17/17、自动发布 17/17、失败 0；
- 最近三日可信历史 `complete=true`，恢复清单中的 17 个规范化文件名命中 17/17。

2026-08-19 08:41 重新执行 PDF 正式 Gate A 时，历史和扫描均正常，但在首次下载前被能力门禁阻断：扫描 35 个主题组，34 个在 48 小时内；共发现 164 个 PDF，规范化 162 个，窗口内 161 个；历史 95 个唯一成功文件、折叠重复 173 条、忽略不可信 manifest 2 条，历史命中 47 个。下载点击、事件和未决事务均为 0，未提交 manifest、未创建 task，因此没有新游离文件，也不需要 Gate R。

根因不是浏览器完全没有真实路径能力，而是 skill 要求下载事件对象直接含有 `absolute_path` 与 `filename`。Chrome 下载对象实际可在同一 `PlaywrightDownload` 上调用 `download.path()`；修复后以该路径作为 `absolute_path`，以 `basename(absolute_path)` 作为真实落盘 `filename`。路径为空时等待 2 秒后只重读同一下载对象一次；仍为空则硬中止，禁止扫描 Downloads 猜测。

验证证据：四份项目/安装技能包均通过 `quick_validate.py`；技能契约为 11 项测试、80 个子检查通过；browser import service/API、技能契约和 8888 桥接合计 `79 passed, 103 subtests passed`。项目与安装副本逐字节一致：

- MP3：`e0bed72c01617451a58b8f02382e42df68683640255f698ced368cb587630c56`
- PDF：`7204112e7cd5fe57d86a85dcc1af87bd1df4efc876321f8d7bc349d3aef82d71`

截至该检查点，后端 `5001/health` 与本机网络上下文中的 8888 `/bridge/zsxq/health` 均为 `healthy`，`mp3-2` 为 `PAUSED`；随后执行的 PDF 正式 Gate A 结果见 5.13。

### 5.13 PDF 正式 Gate A 通过回执

用户于 2026-08-19 09:07 在新侧边栏对话重新执行 PDF skill；本轮没有重放旧 run，也没有修改冻结 manifest：

- 扫描 37 个主题组，36 个在 48 小时内、1 个超时；发现 166 个 PDF，规范化后 164 个；历史命中 50 个，下载次数不大于 30 的 8 个安全跳过；
- 达到正式上限并新下载 40 个；下载账本点击/确认 `40/40`，未决 0，候选失败 0，终态超时/回执不完整/事件冲突/文件错配/同轮实际重复均为 0，`abort_latch=false`、`manifest_blocked_by_download_integrity=false`；
- manifest：`/Users/v9/Downloads/知识星球下载/inbox/sidebar-submit-pdf-20260819-sidebar-pdf-2026-08-19T01-09-03-116Z-b7c1-a11b73131684.json`；run_id：`sidebar-pdf-2026-08-19T01-09-03-116Z-b7c1`；
- submission：`df7b42d3-bdd8-48f2-89e3-3558adda4b72`，browser task：`cb93346c-1c71-5d11-8330-fdc342889c1b`，`completed`、attempt 1、无错误；
- sync run：`7c29037f-4442-41a0-b430-bf22e583f865`，`completed`，附件 40/40、失败 0，下载账本完整且有 40 条 ledger；
- workflow：`28ca82f4-bde5-4adc-a2a2-3f03fd5ee8db`，`completed/done`；清洗任务 `09d758b4-c991-49ce-8e5e-b45ee4b6d2c4` 为 `completed/done`，40/40、失败 0；
- 发布任务 `28c36642-088a-4c77-af0b-f70bd7ddd9cd` 为 `completed/done`；40 个文件分 5 帖，每帖 8 个，上传/提交/回查均 40/40，失败 0、待确认 0；过程中知识星球临时 `1059` 由既有重试自行恢复，没有人工重提；
- 入库存储路径存在 40/40；filename 与 stored path basename 一致 40/40；无 Chrome `(n)` 文件名、同名重复或同内容重复，SHA-256 为 40 个唯一值；
- 最近三日可信历史 `complete=true`、错误 0，12 个来源文件中 10 个可信、忽略 2 个无 receipt manifest；309 条记录、135 个唯一下载、折叠 174 条；本轮 40 个规范化文件名命中 40/40。

结论：PDF 正式 Gate A 子门禁通过。整体 Gate A 仍需 MP3 正式复验通过；`mp3-2` 保持 `PAUSED`，不得再次运行本轮 PDF。

### 5.14 MP3 正式 Gate A 连接重置、22 文件 Gate R 冻结与副作用屏障修复

用户于 2026-08-19 09:28 在侧边栏执行 MP3 正式 Gate A。侧边栏报告扫描 40 个主题、111 个 MP3 附件、109 个唯一候选，历史桥接完整且有 84 条可信成功记录；报告只确认 8 个下载，然后因最后一批处理期间浏览器控制连接重置而设置 `abort_latch=true`、`manifest_blocked_by_download_integrity=true`，未提交 manifest、未创建队列任务。

只读文件系统核对证明报告低估了真实副作用：`/Users/v9/Downloads/` 在 09:35:08～09:46:14 实际新增 22 个 MP3。前 8 个截至 09:42:37 落盘，随后 14 个仍于 09:43:38～09:46:14 落盘。22 个文件 SHA-256 互不重复；完整可信三日历史为 `source_files=15`、`trusted_source_files=14`、忽略不可信 manifest 1 个、`records_seen=175`、`unique_downloads=84`、`duplicates_collapsed=91`，22 个规范化文件名命中 0/22、内容 SHA 命中 0/22。因此它们不是可信历史文件的重复下载，而是连接失控后继续执行的长浏览器批次被报告漏记。

Gate R 只读核对没有再次点击下载：22 个页面文件名均唯一匹配，全部在 48 小时内，详情文件名匹配 22/22，成员下载次数为 12～23，均严格大于 10；详情弹层最终全部关闭。冻结恢复草案：`docs/handoffs/zsxq-sidebar-automation/gate-a-20260819-mp3-connection-reset-recovery-draft.json`；run_id：`sidebar-mp3-20260819T100543+0800-recovery-connection-reset-22`。后端纯内存预检结果为：22/22 有效、重复内容 0、未知下载次数 0、声明/实际 SHA 不一致 0、warning 0；canonical payload SHA-256 为 `d0c64b5f3364b40e52a5deb3aa1322a387f593f365f473f53a4f15243d45ecd1`。

提交前冻结状态为：恢复草案尚未写入 Downloads inbox、没有 submission/task；在用户明确确认前没有执行任何提交。用户确认后的完成回执见下文。

根因是旧契约虽规定“严格串行、一个下载在途”，却仍允许在一次长浏览器工具/JavaScript 调用里循环处理多个候选。控制连接重置后，会话失去回执，但浏览器侧长调用仍可能继续点击。MP3/PDF skill 已增加硬性副作用屏障：一次浏览器工具调用最多处理一个候选的下载事务，禁止用循环、批处理或递归在同一调用中处理多个候选；每个候选完成后必须返回会话，核对账本回到 `IDLE`、无未决事务且 `abort_latch=false`，才能发起下一个候选的独立调用。

该修复采用 TDD：新增契约测试先在 MP3/PDF 旧 skill 上同时失败，修复后完整技能契约 12 项通过；四份项目/安装技能包结构校验通过且逐字节一致。当前 SHA-256：

- MP3：`834d5a0ae6786ab23031e173450437718ce7c64f7d0449e3e926ffd184b727d0`
- PDF：`824bb92d1624e2d441a5ac6aab3fe7d279e6998d9c460f658ac5cc03a116b857`

用户明确确认后，只使用上述冻结清单和同一 run_id 提交一次，没有重新扫描或下载：

- manifest：`/Users/v9/Downloads/知识星球下载/inbox/sidebar-submit-mp3-20260819-sidebar-mp3-20260819T100543_0800-recovery-connection-reset-22-d0c64b5f3364.json`；
- submission：`bea02e59-93bf-40c2-878c-dca676b3a2f7`，browser task：`a199fd03-70ed-52ab-8626-1cf28f1dd507`；`completed`、attempt 1、`replayed=false`；
- sync run：`2a591d5d-6494-419b-95ed-92d33abdaf81`；新增主题/附件/账本均 22，失败 0；
- workflow：`ff0268af-3b87-49b1-83d5-c01194e26a02`，`completed/done`；
- 清洗任务：`f9f1ccee-36d9-4e88-a6fe-0f7e89d07a71`，22/22、失败 0；
- 发布任务：`c496ae26-6d99-47d2-b484-82599a65e39e`，3 帖、上传/提交/回查 22/22，失败 0、待确认 0；
- 最近三日可信历史 `success=true`、`complete=true`，16 个来源文件中 15 个可信、忽略 1 个不可信 manifest；197 条记录、106 个唯一下载、折叠 91 条；本批规范化文件名命中 22/22。

结论：本轮 MP3 正式 Gate A **未通过**，但其第三批 Gate R 已完整结束。现在必须新开侧边栏对话执行一次全新的 MP3 正式 Gate A；只有该轮明确通过后才可恢复计划任务，`mp3-2` 继续 `PAUSED`。

### 5.15 10:46 MP3 新轮次：下载链路 2/2 成功，但审计证据矛盾

用户于 2026-08-19 10:46 在新侧边栏对话再次执行 MP3 正式 Gate A。该轮的浏览器下载副作用已经受控：扫描 40 个主题组、326 个附件、109 个 MP3 条目和 108 个唯一文件；2 个候选真实下载成功，点击/终态 `2/2`、未决 0、浏览器重置 0、`abort_latch=false`、`manifest_blocked_by_download_integrity=false`。两个新文件为：

- `兆易创新603986SH3986HK2026年中报投资者交流会260818.mp3`，下载次数 17；
- `半导体设备重点公司深度中科飞测微导纳米260818.mp3`，下载次数 11。

下游只读回执完整：

- run_id：`sidebar-mp3-2026-08-19T02-50-41-473Z-79km28`；manifest：`/Users/v9/Downloads/知识星球下载/inbox/sidebar-submit-mp3-20260819-sidebar-mp3-2026-08-19T02-50-41-473Z-79km28-26f2e0af7715.json`；
- submission：`b1c1f81f-3261-4b58-b16d-982af28debf1`，browser task：`bd2a88f3-5395-55c3-b82f-7744dc71f6bb`，均 `completed`、attempt 1；
- sync run：`946b9c59-22b9-4f5f-ae7b-9740ec840524`，新增主题/附件/下载账本均 2，失败 0、账本完整；
- workflow：`71679d25-afc0-4269-968d-4227c5d54885`，`completed/done`；clean：`5f682593-4bcc-4132-8a98-85083ef67c16`，2/2、失败 0；publish：`21360b4a-2de3-42e0-a0f4-579b7ab31644`，1 帖上传/提交/回查 2/2、失败 0、待确认 0；
- 当前可信历史 `complete=true`，17 个来源文件中 16 个可信、忽略 1 个无 receipt manifest；209 条记录、108 个唯一下载、折叠 101 条，本轮两个文件命中 2/2。

但冻结 manifest 的审计字段自相矛盾：`candidate_failures` 有 3 条，其中 1 条 `duplicate_candidate_in_same_run`、2 条 `detail_file_mismatch_after_2s`；`candidate_failure_count=3` 和重复候选计数为 1，却把 `detail_file_mismatch_after_2s` 写为 0。历史又写成 106 条记录、106 个唯一文件、折叠 0 条，与此前完成第三批 Gate R 后的 197/106/91 及本轮提交后的 209/108/101 都不一致。该形态证明执行时误用 `data.files.length` 重算了审计字段。

因此这轮只能证明“下载、入库、清洗、发布没有重复或游离副作用”，不能批准为正式 Gate A；它还不满足 A4 的 `candidate_failure_count == 0`，也不满足证据自洽要求。

修复采用 TDD：新增技能契约和后端 service 测试先在旧实现稳定失败，随后要求失败计数只从最终数组派生、历史四项逐字段冻结，并在 `canonical_manifest_payload()` 持久化前校验失败总数和已知失败阶段计数。相关后端/API/技能/桥接回归最终为 `82 passed, 113 subtests passed`；四份技能包结构校验通过且项目/安装副本哈希一致：

- MP3：`5bff9843617a5b0738e93f73c49430ee0ff660a3b658b0121502a4a56df4e378`
- PDF：`58973bcc2b8653d9577ea5452e4cfdfd43c7ced579e18cbf227511412384ce62`

后端于 11:16 仅重启 5001 进程并显式关闭 bootstrap、worker、watchdog 和 scheduler 自动启动；重启前活动任务安全检查通过。`5001/health` 与 8888 `/bridge/zsxq/health` 均为 healthy。真实回环 API 自测使用一个保证无文件、失败计数矛盾的 manifest，后端在持久化和排队前返回 HTTP 400，错误为 `scan_summary.detail_file_mismatch_after_2s 与 candidate_failures 不一致`。

结论：修复后现在可以再执行一次全新的 MP3 正式 Gate A；PDF 不重跑，`mp3-2` 继续 `PAUSED`。

### 5.16 13:47 MP3 外层超时、2 文件 Gate R 与受控完成期限修复

用户于 2026-08-19 13:47 再次执行 MP3 正式 Gate A。扫描完成 40 个主题组、323 个附件、108 个 MP3 条目和 107 个唯一文件；第 30 个候选成功下载 `AI_新技术系列OCS_迎来新变化260819.mp3`。第 41 个候选 `重视银行科技叙事260818.mp3` 的浏览器调用从 14:01:17 运行到 14:02:47，明确以 `js execution timed out; kernel reset` 结束；调用的外层 `timeout_ms` 为 90000。

只读落盘核对证明第二个文件在 14:03:12 完成：第一文件 28,913,205 bytes、SHA-256 `d03cd25f462c7f9c267a7ffd581975a9ca3c9945d2a32e355d277560f6036117`；第二文件 51,469,208 bytes、SHA-256 `bafb26f0798991c1ea9f391e78963bef0f8970f2f22ece226c44273d9ef38d3a`。两者页面下载次数均为 14，可信历史名称/SHA 命中均为 0，无临时文件、无额外 MP3、无原轮 submission/sync run。因此不是重复下载；第二文件约 115 秒完成，外层 90 秒先重置运行时，导致终态漏记。候选间副作用屏障有效，未继续点击后续候选。

用户确认后，冻结的 2 文件恢复清单已提交一次，没有重新扫描或下载：

- 草案：`docs/handoffs/zsxq-sidebar-automation/gate-a-20260819-mp3-connection-reset-2-recovery-draft.json`；
- run_id：`sidebar-mp3-20260819T140312+0800-recovery-connection-reset-2`；canonical SHA-256 `855a0c5c5f44111ce37429724eaed247c3ed591df09649a152af14603aac3ded`；
- manifest：`/Users/v9/Downloads/知识星球下载/inbox/sidebar-submit-mp3-20260819-sidebar-mp3-20260819T140312_0800-recovery-connection-reset-2-855a0c5c5f44.json`；
- submission：`f7c3e5f5-a0ec-4e51-8e0d-ac7d9abd9ad8`；browser task：`d465c2af-c3ce-555f-ac7f-5b363d872067`；返回 `queued=true`、`replayed=false`；
- 预检 2/2 有效、重复内容 0、未知下载次数 0、warning 0。

修复采用 TDD。旧 MP3/PDF skill 在“内层下载完成期限必须早于外层浏览器工具超时”的新契约测试上同时失败；修复后单候选事务为 `download_transaction_deadline_ms = 180000`，外层为 `browser_tool_timeout_ms >= 210000`，事件与 `download.path()` 都用 `Promise.race` 受内层期限约束。内层失败必须在外层存活时正常冻结 Gate R，禁止由外层先触发 `kernel reset`。技能契约现为 `14 passed, 92 subtests passed`，项目/安装副本逐字节一致并通过 `quick_validate.py`：

- MP3：`a3b6563f468058e0c1a54db7320ba6f487849d149a6eee72b2b4041795c12e64`
- PDF：`29c694382cf30a7bd8e7a53c5a774119d8626c8b7d0e573329a0e6510880019e`

结论：本轮仍未通过 MP3 正式 Gate A；第四批 Gate R 已入队，超时根因修复已部署。下游完成后仍需执行一次全新的 MP3 正式 Gate A；PDF 不重跑，`mp3-2` 继续 `PAUSED`。

### 5.17 单候选异常不再阻断已确认文件提交

继续复盘确认，上一版契约把两件事过度绑定：一是某个点击后候选状态不明时停止后续点击，二是禁止提交此前已经唯一绑定、真实落盘且通过自检的文件。前者用于防止迟到事件与下一候选串扰，仍有必要；后者会将一个局部异常扩大成整批业务失败，不符合本流程“还有下一轮”的可恢复目标。

现在 MP3/PDF 共用的处理边界是：

- 点击前失败、明确未落盘，或已取得唯一终态但真实文件错配/同轮重复：记录 `candidate_failures`，当前候选不进 `files`，安全收尾后继续下一候选。
- 终态超时、缺真实路径/文件名，或能限定在当前候选监听代次内的事件冲突：记录 `candidate_failures`，隔离当前候选、恢复 `IDLE` 后继续下一候选；下一轮可以重试该候选，但这不影响本轮处理其他候选。
- 跨候选未绑定事件、无 armed 候选事件、迟到事件、在途连接重置，或无法证明候选隔离的事件冲突：置 `abort_latch=true` 并停止后续点击；但冻结、自检并提交此前已确认的 `files` 与完整 `candidate_failures`。
- `manifest_blocked_by_download_integrity=true` 仅用于准备提交的 `files` 本身无法通过路径/文件名、同轮重复或内容重复自检的重大逻辑问题；不再由单候选失败、`abort_latch=true` 或未决候选单独触发。

本次修改只调整 skill 决策契约；后端 manifest 本来就能接收“成功 `files` + `candidate_failures`”的混合结果，无需新建发布接口或另一套部分成功工作流。TDD 契约先在两份旧 skill 上失败，修改后为 `14 passed`；与后端 manifest 回归合并为 `51 passed`。项目与安装副本已同步并通过 `quick_validate.py`：

- MP3：`8a595afddcbf27020f22be0158027ff8b0fa57f51f87f7526946e5939de6c7eb`
- PDF：`ac4636842618b8dfccd58de5360b1b3ac3c2dafbdc35ba4d9e9b93dd3e883dc7`

### 5.18 正式参数调整与下载按钮最终门禁

用户将 MP3/PDF 的当前正式扫描参数从 48/40/40 调整为 36/30/40：回溯 36 小时、最多扫描 30 个唯一主题组、最多成功下载 40 个文件。下载次数阈值不变，MP3 仍严格大于 10，PDF 仍严格大于 30；最近 3 个自然日可信历史窗口也不变。manifest 的 `within_48h`、`outside_48h` 字段名因兼容性保留，但现在按 36 小时窗口统计。

同时补充统一的下载按钮最终门禁。只有时间窗口、媒体扩展名、历史未命中、同轮候选唯一、弹层文件名绑定、下载次数阈值、按钮存在、下载元数据能力、`download_state == IDLE`、`abort_latch == false` 和无在途下载全部通过，才可创建账本和启用监听器。监听器 active 后，点击命令发出前还要再次确认当前监听代次、`download_state == ARMED`、`abort_latch == false` 和无其他在途下载；任一条件变化即取消当前候选，不补点。

TDD 契约先在旧参数和缺少统一最终门禁的两份 skill 上失败；修改后技能/后端回归 `52 passed`，8888 桥接回归 `20 passed`，两份项目与安装副本均通过 `quick_validate.py` 并逐字节一致：

- MP3：`33ed5436183a6b83ed5289d3a58c402b90d0109aaa64902149deee83da880395`
- PDF：`c56dd566251551ef5c0377a67625b3cfa54a87c25aa96bd40464cc33bf50e9fc`

## 6. 本轮下载事务修复版本与远端

- 分支：`agent/pdf-media-workflow-reliability`
- 远端：`https://gitee.com/vim999/dig_ytb_channel.git`
- `d779c2f`：manifest 在 run_id 预留前校验
- `6855d4d`：历史去重、可信 manifest、文件绑定及测试
- `fcac0e2`：实施计划完成状态
- `be3d0f6`：知识星球侧边栏会话交接文档

`HEAD` 与 `origin/agent/pdf-media-workflow-reliability` 已核对一致于 `be3d0f6d5cfd0a986cfaafd2bc3fbbe6ebab97b0`。本轮状态机、测试和交接更新仍为本地未提交改动。

当前项目/安装 skill 哈希：

- MP3：`33ed5436183a6b83ed5289d3a58c402b90d0109aaa64902149deee83da880395`
- PDF：`c56dd566251551ef5c0377a67625b3cfa54a87c25aa96bd40464cc33bf50e9fc`

## 7. 重新验证时的注意事项

- 测试前先确认后端健康、项目 skill 与安装 skill 一致、`mp3-2` 仍暂停。
- 不要把工作区其他未提交修改纳入本问题提交。
- Gate R 首批 8 个、平安银行 PDF 1 个、第二批 17 个 MP3 和第三批 22 个 MP3 均已完成；13:47 的第四批 2 文件 Gate R 已提交并等待下游完成。PDF 正式 Gate A 已通过；受控下载完成期限修复后仍需再执行一次全新 MP3 正式 Gate A，计划任务继续暂停。
- 不要以 Downloads 中是否还有旧重复文件作为修复失败的唯一证据；本轮没有授权自动删除历史副本。应以“新一轮是否再次下载”和可信历史/manifest 为准。
- 如果人工运行返回 `replayed=true`，只要 task_id 相同且没有再次派发，这是幂等成功，不是失败。
