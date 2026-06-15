# 知识星球研报资产扫描支线

第一版目标：只把同步得到的研报 PDF 转成可复用的研报视频基础资产，不生成 AI 文案，不生成视频。

## 触发方式

- 同步任务完成后，如果本轮同步结果里存在已下载 PDF，会异步投递 `tasks.report_video.scan_zsxq_report_assets_task` 到 `report_video` 队列。
- 每日补漏调度器会按配置扫描指定日期已下载 PDF，并把新资产投递到 `report_video` 队列。
- 手动预览：
  - `POST /api/v1/report-video/asset-scan/preview`
- 手动执行：
  - `POST /api/v1/report-video/asset-scan/run`
- 补漏调度控制：
  - `GET|POST /api/v1/report-video/asset-scan/scheduler/config`
  - `GET /api/v1/report-video/asset-scan/scheduler/status`
  - `POST /api/v1/report-video/asset-scan/scheduler/start`
  - `POST /api/v1/report-video/asset-scan/scheduler/stop`
  - `POST /api/v1/report-video/asset-scan/scheduler/run-once`
  - `GET /api/v1/report-video/asset-scan/scheduler/logs`

## 支持来源

`sync_run`：

```json
{
  "source": "sync_run",
  "run_id": "ZSXQ_SYNC_RUN_ID",
  "parse_pdfs": true,
  "config": {
    "max_files": 50,
    "tab_label_keyword": "外资研报"
  }
}
```

`daily_backfill`：

```json
{
  "source": "daily_backfill",
  "target_date": "2026-06-13",
  "parse_pdfs": true,
  "config": {
    "max_files": 50,
    "tab_label_keyword": "外资研报"
  }
}
```

## 产物

- 创建 `report_video_tasks`，类型为 `daily_digest`。
- 每个新 PDF 创建一条 `report_video_reports`。
- PDF 复制到 `data/tasks/<task_id>/reports/<report_code>/original.pdf`。
- `raw_meta.auto_asset` 保存来源 hash、知识星球 topic/attachment 信息和轻量主题 hint。
- `parse_pdfs=true` 时，只投递 PDF 解析任务，生成可复用正文缓存；不进入 AI、聚类、渲染。

## 去重策略

按 PDF SHA256 去重。扫描会同时读取 `raw_meta.auto_asset`、`raw_meta.auto_scan` 以及 task 级别的来源 hash，避免同一个 PDF 被重复入库。

## 配置

同步完成后的自动支线可以通过环境变量关闭：

```bash
ZSXQ_REPORT_ASSET_SCAN_ENABLED=false
```

可调整默认每轮最大入库数量：

```bash
ZSXQ_REPORT_ASSET_SCAN_MAX_FILES=50
```

可调整默认 tab 过滤关键字：

```bash
ZSXQ_REPORT_ASSET_SCAN_TAB_KEYWORD=外资研报
```

补漏调度器配置保存在：

```text
runtime/report_video_asset_scan_scheduler_config.json
```

默认配置：

- 每天 `02:30` 执行一次。
- 扫描前一天的数据，`target_date_offset_days=1`。
- 单次最多 50 个 PDF。
- 只扫描 `外资研报` tab。
- 只做 PDF 解析入库，不生成 AI 或视频。

启动方式：

```bash
scripts/dev/run-report-video-asset-scheduler.sh
```

等价于：

```bash
venv/bin/python src/backend/run_report_video_asset_scheduler.py start
```

停止方式：

```bash
scripts/dev/stop-report-video-asset-scheduler.sh
```

立即执行一次：

```bash
venv/bin/python src/backend/run_report_video_asset_scheduler.py once
```

日志：

```text
logs/report_video_asset_scan_scheduler.log
```
