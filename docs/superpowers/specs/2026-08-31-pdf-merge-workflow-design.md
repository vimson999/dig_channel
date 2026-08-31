# PDF 合并工作流设计

**状态：** 已确认，进入实现与验收

## 目标

在现有系统中提供一个可管理的 PDF 合并工作流。用户可以将本地上传的 PDF 与系统中已经清洗完成的 PDF 资产混合加入同一列表，严格按加入顺序合并；合并产物必须可下载、可在任务历史中追踪，并作为已有内容流水线的可复用 PDF 资产出现。

## 非目标

- 不修改单个 PDF 的页面内容，也不执行清洗、OCR、水印移除或重排序。
- 不提供拖拽排序、书签合并、表单重写、密码破解或外部 5005 服务依赖。
- 不新建独立资产表或新的数据库迁移；合并结果复用现有 `ContentPipelineAsset`。

## 用户体验

入口位于现有 `pdf-tools.html` 的“PDF 工具平台”，新增“合并多个 PDF”卡片，指向独立的 `pdf-merge-workflow.html` 工作台。

工作台包含：

1. “本地添加 PDF”文件选择器；文件仅在点击“开始合并”时上传。
2. “从已清洗 PDF 资产添加”面板；复用现有 `/api/report-audio-workflows/pdf-assets` 目录。目录中知识星球附件和流水线资产都含有 `pipeline_asset_id`，前端统一转换为 `pipeline:<id>` 引用。
3. 一个有序待合并列表，显示序号、来源、文件名和大小。列表顺序就是输出页顺序；用户可移除项目，但初版不支持拖拽或自动排序。
4. 可选的输出文件名。留空时服务端生成 `合并PDF-YYYYMMDD-HHMMSS.pdf`。
5. 当前任务状态、进度、日志、下载链接、重试按钮，以及属于当前用户的历史任务列表。

任何文件名、来源标签和错误文字均通过 DOM `textContent` 渲染，不把不可信文件名写入 `innerHTML`。

## 任务与数据边界

使用 `PublicationTask` 作为父任务，稳定 feature 名为 `pdf_merge_workflow`，队列为现有 `content_pipeline`。任务参数中的 `pdf_merge_workflow` 保存冻结的、按序排列的 `sources`：

```json
{
  "feature": "pdf_merge_workflow",
  "user_id": "<owner>",
  "pdf_merge_workflow": {
    "title": "可选输出名（无 .pdf 后缀也可）",
    "sources": [
      {
        "sequence": 1,
        "kind": "upload",
        "filename": "本地文件.pdf",
        "path": "/受控上传目录/<task-id>/input-0001.pdf",
        "sha256": "...",
        "bytes": 123
      },
      {
        "sequence": 2,
        "kind": "pipeline_asset",
        "asset_ref": "pipeline:<asset-id>",
        "asset_id": "<asset-id>",
        "filename": "已清洗研报.pdf",
        "cleaned_sha256": "...",
        "bytes": 456
      }
    ]
  }
}
```

本地上传写入任务专属、受控的上传根目录。系统资产在提交时必须已经是 `media_type="pdf"` 且 `status="cleaned"` 的 `ContentPipelineAsset`；服务端保存资产 ID、清洗输出哈希与大小。工作器再次解析资产并验证哈希和大小，缺失或变化时失败，而非静默替换为另一版本。

初始上限（均可由环境变量覆盖）：至少 2 个、最多 30 个；单文件最多 100 MiB；总输入最多 500 MiB。服务端会检查扩展名、空文件、受控路径、可打开性和加密状态。相同 PDF 可以在列表中出现多次：这表示用户明确要求在输出中重复其页面，系统绝不悄悄去重或改变顺序。

## HTTP API

所有路由均要求登录，并且读取、下载、重试、取消只能访问 `PublicationTask.user_id` 与当前用户匹配的任务。

| 方法 | 路由 | 作用 |
| --- | --- | --- |
| `POST` | `/api/pdf-merge-workflows/jobs` | 用 `multipart/form-data` 创建任务；`items` JSON 决定上传文件索引与资产引用的完整顺序。 |
| `GET` | `/api/pdf-merge-workflows/jobs` | 分页返回当前用户任务历史。 |
| `GET` | `/api/pdf-merge-workflows/jobs/<id>` | 返回当前用户的一项任务。 |
| `GET` | `/api/pdf-merge-workflows/jobs/<id>/download` | 安全下载任务的已登记输出资产。 |
| `POST` | `/api/pdf-merge-workflows/jobs/<id>/retry` | 仅将失败或已取消任务按原冻结 sources 重投。 |
| `POST` | `/api/pdf-merge-workflows/jobs/<id>/cancel` | 仅取消尚未开始的 pending 任务；运行中返回冲突，避免产生竞争条件。 |

创建响应和状态响应不泄露本地绝对路径。公开载荷只包含任务 ID、标题、状态、步骤、进度、输入摘要、错误、输出文件名/大小/页数、`asset_ref`、是否复用资产和下载 URL。

## 合并和资产登记

纯服务层使用项目已安装的 PyMuPDF (`fitz`)：

1. 逐项解析冻结 source，并再次核验每个文件的 SHA-256、大小、PDF 可读性和非加密状态。
2. 以 source 顺序把页面插入一个新文档，进度随已处理文件数更新。
3. 写入任务输出目录内的临时文件，重新打开并验证页数等于各输入页数之和，再原子替换为最终文件。
4. 使用 `reserve_files(..., media_type="pdf", source_mode="pdf_merge")` 对最终文件哈希取得或创建 `ContentPipelineAsset`。新资产使用输出文件作为 `source_path` 和 `cleaned_path`，并通过 `register_cleaned_outputs` 登记为 `status="cleaned"`。
5. 如果相同合并产物已经有 ready canonical asset，任务记录那个既有 `pipeline:<asset-id>`，删除本次临时输出，并把 `reused_asset=true` 写入结果。

输出在现有 `CONTENT_PIPELINE_OUTPUT_ROOT/pdf_merge/<task-id>/` 中保存，因而 `resolve_cleaned_asset` 的现有路径校验可以直接复用。合并完成后，既有 PDF 资产目录以及使用 `pipeline:<asset-id>` 的下游工作流无需结构性修改即可使用产物；仅增加 `pdf_merge` 的来源标签。

## 生命周期、通知与错误

任务的正常步骤为 `queued → validating → merging → registering → completed`，进度为 0–100。提交、完成和失败使用现有内容流水线通知适配器，但通知失败绝不使合并任务失败。

处理异常、校验失败、资产版本不匹配和队列投递失败都记录为 `failed`，携带可读错误消息和结构化日志。失败任务可重试；重试只重用冻结的 sources，既不重新上传也不重新搜索资产。取消仅支持 `pending`，使 API 不需要在文档写入期间中断 PyMuPDF。

队列前后均使用现有 `runtime_guard` 和 `maybe_guarded_apply_async`。为 `pdf_merge_workflow` 添加保守的运行/积压限制，以免大型本地 PDF 合并占满 `content_pipeline` worker。

## 验收标准

1. 可以在同一任务中交错加入本地上传 PDF 和已有清洗资产；输出页数和每页文字顺序等于列表顺序。
2. 合并结果可下载，并被登记为 `ContentPipelineAsset(media_type="pdf", source_mode="pdf_merge", status="cleaned")`，可通过 `pipeline:<asset-id>` 被既有 PDF 消费者解析。
3. 缺少、损坏、加密、超限、非 PDF 或已变化的资产均明确失败；不出现半成品下载链接或未经验证的替代文件。
4. 历史、详情、重试、取消和下载均做用户所有权校验，且不暴露绝对路径。
5. 纯服务、任务、API、资产注册、前端静态契约和实际两份测试 PDF 的端到端合并均自动化验证通过。
