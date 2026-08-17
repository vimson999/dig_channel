# Knowledge Planet Sidebar API-Owned State Design

**Date:** 2026-08-17

## Goal

让知识星球 MP3/PDF 侧边栏下载在不直接读取或写入本地 JSON 目录的前提下，可靠完成最近 3 天去重、本轮 manifest 持久化、Celery 入库以及既有清洗和发布编排。保留原知识星球 API 上游和现有 `manifest_paths` 导入方式。

## Root cause

侧边栏的浏览器下载权限与本地文件读取权限不是同一权限面。2026-08-17 08:45 和 10:41 两份 MP3 manifest 分别包含 13 和 30 个文件，后者重复包含前者全部 13 个文件；当天结果日志又只剩后一次的 30 条。这证明 MP3 虽能下载和覆盖写入日志，却没有可靠读取并合并历史。PDF skill 采用失败即停止策略，因此在目录枚举返回 `EPERM` 时暴露了问题。

macOS 对 Downloads 的权限可能是应用或文件粒度，侧边栏不能把目录枚举、已知文件读取和浏览器下载视为同一种授权。技能内的授权码只能表达用户意图，不能改变系统权限。

## Chosen architecture

本地系统成为去重状态和 manifest 的唯一文件系统所有者。侧边栏只执行三类动作：读取当前知识星球页面、通过 Chrome 下载符合条件的文件、调用 loopback API。

数据流：

1. 侧边栏调用 `POST /api/v1/zsxq/browser-import/history` 获取指定媒体类型最近 3 天已确认下载记录。
2. 侧边栏使用 API 返回的记录去重，扫描并通过浏览器下载新文件。
3. 侧边栏把本轮 manifest 对象直接提交到现有 `POST /api/v1/zsxq/browser-import`，不再传本地 JSON 路径。
4. 本地 API 原子写入 `Downloads/知识星球下载/inbox/sidebar-submit-<kind>-<timestamp>.json`，复用现有 preview、Celery 导入、文件归档、清洗、发布和邮件工作流。
5. 即使 `files: []`，本地 API 仍持久化 manifest 并创建 0 文件同步编排。

## API contract

### History lookup

`POST /api/v1/zsxq/browser-import/history`

Request:

```json
{
  "kind": "mp3",
  "days": 3,
  "group_id": "28888112822211",
  "tab_id": "88844545452542"
}
```

`kind` 只允许 `mp3` 或 `pdf`，`days` 允许 1–7，两个 skill 固定使用 3。`group_id` 和 `tab_id` 必须匹配服务端固定映射。

Response:

```json
{
  "success": true,
  "data": {
    "kind": "mp3",
    "days": 3,
    "complete": true,
    "files": [
      {
        "file_id": null,
        "filename": "example.mp3",
        "published_at": "2026-08-17T08:00:00+08:00",
        "source_url": "https://wx.zsxq.com/tags/...",
        "downloaded_at": "2026-08-17T10:00:00+08:00",
        "download_count_at_download": 12,
        "latest_download_count": 15,
        "success": true
      }
    ],
    "summary": {
      "source_files": 4,
      "records_seen": 43,
      "unique_downloads": 30,
      "duplicates_collapsed": 13
    }
  }
}
```

服务端按最近 3 个自然日的精确文件名模式读取 `sidebar-submit-<kind>-*.json`，并兼容迁移期的 `sidebar-result-<kind>-YYYYMMDD.json`。去重顺序为：来源 `file_id`；否则 `filename + published_at + source_url`；有 `sha256` 时作为补充。Chrome `(n)` 后缀在文件名比较前移除。

缺失某日日志属于空历史。最近窗口中的文件若存在但 JSON 损坏，返回 `complete: false` 和错误列表；skill 必须停止，不能在不完整历史上继续下载。

### Inline manifest submission

现有 `POST /api/v1/zsxq/browser-import` 同时支持旧的 `manifest_paths` 和新的 `manifest`，两者互斥。

Request:

```json
{
  "manifest": {
    "run_id": "sidebar-mp3-20260817T120000+0800",
    "kind": "mp3",
    "group_id": "28888112822211",
    "tab_id": "88844545452542",
    "tab_label": "调研纪要",
    "scan_summary": {
      "scanned_topics": 40,
      "found": 117,
      "new_downloads": 0
    },
    "files": []
  },
  "dispatch_clean": true,
  "auto_publish": {
    "enabled": true
  }
}
```

内联 manifest 必须提供唯一 `run_id`、固定类型和 tab 元数据，`files` 最多 40 项。服务端先原子持久化，再执行现有 preview；非空 manifest 中每个文件必须位于 Downloads、存在且非空、扩展名匹配。新内联提交的下载次数必须严格满足 MP3 `>10`、PDF `>30`。旧的路径模式保留现有兼容行为。

成功响应继续返回 HTTP 202、`task_id` 和 preview，并增加服务端生成的 `manifest_path`。侧边栏不得自行创建、读取或更新 `sidebar-result-*`/`sidebar-submit-*` 文件。

## Idempotency and retry

history 查询是只读操作，可以安全重复调用。内联 manifest 提交使用持久化幂等收据，不能只依赖附件 SHA-256：SHA-256 只能复用附件，无法阻止重复同步 run、重复工作流和重复邮件。

侧边栏在开始一轮扫描时生成一次 `run_id`，并使用同一个值构造请求头：

```text
Idempotency-Key: zsxq-sidebar:<kind>:<run_id>
```

API 分别计算 canonical manifest hash，以及由 canonical manifest、`dispatch_clean`、`cleaner`、`auto_publish` 共同构成的有效请求 `payload_sha256`。新增 `ZsxqBrowserSubmission` 持久化收据，至少保存 `idempotency_key`、`run_id`、`kind`、`payload_sha256`、`manifest_path`、`celery_task_id`、`status`、`result`、`error_message` 和时间字段；`idempotency_key` 唯一，`kind + run_id` 唯一。这样重试不能在保留幂等键的同时偷换清洗或发布参数。

第一次请求按以下顺序执行：

1. 在数据库事务中创建 `received` 收据并预分配确定性的 Celery task ID。
2. 以确定性文件名原子写入 manifest；文件名由 `kind + run 日期 + run_id + manifest hash` 派生，保证最近三天查询可以按日期发现服务端账本。
3. 校验文件和 manifest，提交收据事务。
4. 向 Celery 投递预分配 task ID，并把收据更新为 `queued`。

同一幂等键重试时：

- payload hash 相同且状态为 `queued`、`processing` 或 `completed`：不再次写文件、不再次投递，返回相同的 `manifest_path`、`task_id` 和当前状态，并标记 `replayed: true`。
- payload hash 相同且状态仍为 `received` 或 `dispatch_failed`：允许补投递相同逻辑任务，仍返回同一个收据；worker 端的原子 claim 防止 broker 已接收但 API 尚未来得及更新状态时产生双执行。
- 幂等键相同但 manifest 或清洗/发布参数不同：返回 HTTP 409 `idempotency_conflict`。修正任何有效请求内容后必须生成新的 `run_id`。

Celery worker 开始时对收据加行锁并原子 claim。只有第一个 worker 可以从 `received/queued` 进入 `processing` 并创建同步 run；重复消息看到 `processing/completed` 时直接返回已有状态，不创建第二个同步 run、工作流或邮件。成功后收据保存最终 import/workflow 结果并进入 `completed`；失败进入 `failed`，显式重试复用同一收据并增加 attempt，而不是创建新业务批次。

典型的“服务端已经排队但侧边栏没有收到响应”场景中，第二次调用会得到原 `task_id` 和 `replayed: true`，不会重复入库。侧边栏必须在重试时保留原 `run_id`、幂等键和完全相同的 manifest body；不得重新下载，也不得为网络重试生成新 `run_id`。

## State ownership and compatibility

- 侧边栏不再拥有本地 JSON 状态，只保存当前对话中的临时扫描结果。
- 本地 API 原子写入每轮 submit manifest；这些文件构成下载去重审计账本。
- Celery 导入继续把文件和原始 manifest 内容写入现有 `ZsxqSyncRun`、`ZsxqTopic`、`ZsxqAttachment` 和 `ZsxqSyncRunDownload`。
- 现有 `POST /browser-import/preview` 与 `POST /browser-import` 的 `manifest_paths` 模式保持可用。
- 原知识星球 API 同步上游、清洗、发布、节点邮件和 Celery Monitor 不变。

## Error handling

- history API 不可达、返回非 2xx 或 `complete: false`：skill 在下载前停止并报告 `history_api_unavailable` 或 `history_incomplete`。
- 内联 manifest 校验失败：不排队 Celery；已下载文件保留在 Downloads，可用同一对话中的 manifest 重试提交，不得再次下载。
- Celery 排队成功后由既有任务状态和工作流通知负责后续可见性。
- 页面上下文错误仍在扫描前停止；页面有效但无新文件时提交空 manifest。
- 所有新接口继续只接受 loopback 客户端。

## Skill contract

MP3/PDF skill 在页面检查后首先调用 history API，不再访问 `/Users/v9/Downloads/知识星球下载/inbox/`。API 返回的 `files` 是唯一去重输入。扫描结束后，skill 将 manifest 对象直接 POST 给本地导入接口，并汇报后端返回的 `manifest_path` 与 `task_id`。

Codex 计划任务仍只负责 profile、窗口、精确 URL、页面刷新和侧边栏唤起。手动入口由用户先打开精确 URL。两个入口共享同一套 API 状态流程。

## Testing

1. 服务测试覆盖最近 3 日 submit/result 合并、13 条重叠折叠、缺失日期、损坏 JSON、Chrome 后缀和媒体类型隔离。
2. API 测试覆盖 loopback 限制、history 参数校验、内联 manifest 持久化、空 manifest、阈值、最多 40 项以及旧路径模式回归。
3. 幂等测试并发或连续提交相同键与相同 payload，断言只生成一个 manifest、一个 Celery 逻辑任务、一个同步 run 和一套工作流通知；相同键不同 payload 返回 409。
4. Celery/工作流测试确认服务端持久化后的 manifest 继续进入原有入库、清洗、发布和通知节点，并验证重复 broker 消息由 worker claim 拦截。
5. skill 契约测试确认不再出现本地 JSON 读写指令，并要求 history 失败时停止、0 文件时仍提交、API 网络重试复用原 `run_id` 和幂等键。
6. 使用当前 Downloads 账本做只读验证：08:45 与 10:41 MP3 manifest 合并后应为 30 个唯一下载，折叠 13 个重复。

## Acceptance criteria

1. 侧边栏不需要 Downloads 目录读取权限即可完成 MP3/PDF 去重和提交。
2. 当前两份 MP3 manifest 的 13 个重叠记录被 history API 折叠，下一轮不再重复下载。
3. PDF skill 不再因本地目录 `EPERM` 停止；只依赖 history API。
4. 有新文件和 0 文件两种提交都生成服务端 manifest、Celery task ID 和标准同步工作流。
5. MP3 严格 `>10`、PDF 严格 `>30`、48 小时窗口、最多 40 组/40 个和 3 天去重保持不变。
6. 原 API 上游、旧 `manifest_paths` 调用和下游清洗/发布测试继续通过。
7. 同一内联 manifest 连续提交两次或第一次响应丢失后重试，只返回同一个 task ID，只创建一个同步 run、一个工作流和一套节点邮件。
8. 计划任务在人工测试和一次 Codex 调用测试通过前保持暂停。

## Non-goals

- 不修改知识星球网页抓取规则或 Chrome profile 定位方式。
- 不恢复或删除已经重复下载的历史文件；现有后端 SHA-256 去重继续防止重复附件入库。
- 不扩大 macOS Full Disk Access 权限。
