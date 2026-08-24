# Knowledge Planet Sidebar Import Design

**Date:** 2026-08-15

## Goal

接收 Chrome 侧边栏分别生成的 PDF/MP3 下载结果 JSON，将本地文件转换为现有知识星球数据契约，并通过 Celery 进入原有清洗、编排和发布链路，同时保持原有知识星球 API 上游不变。

## Context

当前侧边栏已经可以调用 `http://127.0.0.1:5001/health`，并能在 `/Users/v9/Downloads` 读写文件。当前批次有两个结果文件：PDF 94 条、MP3 46 条；文件路径均存在。日志是数组格式，部分下载次数未知，且没有真实的知识星球 topic/file ID。

## Chosen architecture

新增独立的 `browser sidebar import` 适配层，不修改既有 `run_zsxq_sync` API 上游。适配层负责读取并校验 manifest、计算 SHA-256、生成稳定的内部 ID、写入既有 `ZsxqSyncRun`/`ZsxqTopic`/`ZsxqAttachment`/`ZsxqSyncRunDownload` 契约，并为每个 manifest 创建一个标准 `ZsxqWorkflowRun`，再通过原有工作流状态机进入清洗和发布。

两类接口：

- `POST /api/v1/zsxq/browser-import/preview`：只读校验，返回文件、哈希、重复项、警告和待入库数量。
- `POST /api/v1/zsxq/browser-import`：创建入库 Celery 任务；只接受 Downloads 目录下的 manifest 路径，接口只允许本机访问。正式导入后将文件归档到现有 `data/zsxq/<group_id>/<tab_label>-<tab_id>/files/` 目录。

每个 manifest 是一个独立同步批次。当前旧格式没有 `run_id` 时，以 manifest 文件名加内容哈希生成稳定批次键；未来侧边栏写入的 `run_id` 优先使用。重复提交同一文件按内容 SHA-256 和稳定 `attachment_key` 复用已有记录，不创建重复附件。侧边栏每轮都提交一个 manifest；当本轮没有新增文件时，manifest 使用对象格式并携带固定 tab 元数据，`files` 为空，仍创建一条 0 文件同步 run 和标准工作流。

## Metadata mapping

固定业务映射：

- PDF：`group_id=28888112822211`、`tab_id=51184248544214`、`tab_label=外资研报`。
- MP3：`group_id=28888112822211`、`tab_id=88844545452542`、`tab_label=调研纪要`。

缺失的 `topic_id`/`file_id` 使用 `browser-topic-<digest>` 与 `browser-file-<sha256>` 生成；`raw_payload` 保存原始 manifest 记录、来源标记、SHA-256 和导入批次。PDF 没有 `topic_title` 时使用去掉 Chrome `(n)` 后缀的文件名作为标题。未知 `download_count` 保留为 `null`，不得转成 0。

## Import transaction

Celery 任务按 manifest 建立一个 `ZsxqSyncRun(mode="browser_sidebar")`。校验通过后，先对文件计算 SHA-256 并移动到现有项目数据目录；然后在同一数据库事务中 upsert topic、attachment 和 run-download ledger，数据库路径指向归档后的项目文件。空 manifest 不移动文件、不创建 topic/attachment，但仍建立 completed 的 0 文件 `ZsxqSyncRun`。文件移动失败或数据库提交失败会尽量回滚移动；提交后为该 manifest 创建标准工作流，由工作流调用既有 `submit_pdf_clean_for_run` 或 `submit_audio_clean_for_run`，无新增文件时将清洗/发布阶段标记为 `skipped(no_new_files)`，不创建子任务。清洗提交失败不回滚已确认的下载入库，而是在工作流和 run result 中记录错误，便于重试下游。浏览器侧边栏工作流复用原有节点邮件：`workflow_started`、`sync_completed`、`clean_submitted`/`clean_completed`（或 skipped/失败/部分成功）、`publish_submitted`/`publish_completed`（或 skipped/失败/部分成功），以及整体完成/失败通知。

## Safety and failure handling

- manifest 路径必须位于 `/Users/v9/Downloads`；manifest 中的文件首次导入必须位于 Downloads，重复导入也允许使用项目归档目录中的 `stored_path`。文件必须存在、非空且扩展名匹配。
- preview 不写数据库、不移动文件、不触发 Celery；正式导入才执行文件归档。
- 正式接口拒绝绝对路径越界、未知媒体类型和重复 manifest 内容。
- 当前日志缺少下载次数时只产生 warning；不伪造阈值判断结果。
- 旧 API 上游继续由 `run_zsxq_sync` 独立运行。

## Acceptance criteria

1. 当前两个 JSON 能通过 preview，报告 94 个 PDF、46 个 MP3，且所有路径通过校验。
2. 同一 manifest 重复 preview/import 不产生重复 attachment。
3. 同一内容的不同 Chrome 文件名（例如 `(1)`、`(2)`）按 SHA-256 识别为重复。
4. 正式导入使用现有 downstream clean task entrypoints 和原有工作流状态机，不复制清洗/发布逻辑。
5. 侧边栏工作流在各节点复用既有邮件通知机制，失败节点也能发出 `workflow_failed`。
6. 空 `files` manifest 仍创建同步编排，清洗/发布无子任务但正常跳过并发送同步编排的节点/完成通知。
7. 既有知识星球 API 同步测试继续通过。
