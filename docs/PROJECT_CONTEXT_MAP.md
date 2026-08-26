# 项目上下文地图

本文档记录一次新接手视角下的项目地图，补充散落在代码、运行手册和 bug 记录里的隐性上下文。它不替代既有纪律文件；真正修改代码前仍要先读对应模块的标准、运行手册和最近 bug 记录。

## 文档可信顺序

优先读这些当前仍在指导开发和排障的文件：

1. `GEMINI.md`：源码、运行态目录、路径解析、日志和测试位置的硬纪律。
2. `docs/WORKFLOW_RUNTIME_OPERATIONS_PLAYBOOK.md`：数据库连接、worker、scheduler、发布判定、排队任务处理的运行纪律。
3. `docs/WORKFLOW_ORCHESTRATION_STANDARD.md`：新增或修改编排任务时必须遵守的状态、续跑、子任务和通知标准。
4. `docs/production-hardening.md`：PgBouncer、连接池、任务准入和观测项。
5. `docs/PRODUCTION_EVOLUTION_ROADMAP.md`：按 P0/P1/P2 记录生产级演进方向和验收标准。
6. `docs/bugs/README.md` 及近期 bug 记录：真实事故沉淀，通常比旧规划更贴近当前系统。
7. `scripts/dev/README.md`：本地启动、重启、暂停和托管进程默认值。
8. `docs/api/ZSXQ_BROWSER_IMPORT.md`：知识星球统一投递历史查询的当前公网契约、AppID/Secret 分配和签名规则。

`docs/planning/`、`docs/tasks/` 和较早 `docs/status/` 更偏历史记录。它们能解释系统来源，但不能直接当作当前架构事实。

## 当前真实定位

项目最早是 YouTube 多用户数据分析平台，但当前重心已经扩展为本地内容生产流水线：

- 同步内容：YouTube、知识星球、RSS/Reddit 等采集入口。
- 清洗内容：PDF 去水印、音频裁切清洗、历史文件保留清理。
- AI 处理：Gemini 浏览器自动化、DeepSeek/OpenAI-compatible 结构化、ASR、TTS。
- 视频生产：一键发布台、研报导图视频工厂、研报转语音/视频、MP3 视频编排。
- 分发发布：Bilibili、YouTube、知识星球清洗产物发布。
- 运行控制：Celery Monitor、watchdog、scheduler、自举恢复、邮件通知。

所以排查问题时不要只按“YouTube 数据平台”理解。很多核心路径围绕 `PublicationTask`、Celery 队列、知识星球同步和内容流水线展开。

## 代码地图

后端入口：

- `src/backend/app.py`：Flask app factory，注册数据库、CORS、中间件、API、日志维护和后端进程自举。
- `src/backend/celery_config.py`：Celery 应用、队列、路由和 SQLAlchemy session 清理信号。
- `src/backend/models/`：核心模型。`PublicationTask` 是多数业务任务的统一承载表。
- `src/backend/services/`：业务服务，包含同步、清洗、编排、发布、凭据、运行保护。
- `src/backend/tasks/`：Celery 任务入口。

API 有两个目录并存：

- `src/backend/apis/` 是当前主注册入口。
- `src/backend/api/` 保留部分旧模块和独立模块，仍会被 `apis/__init__.py` 或 `apis/v1/__init__.py` 注册。

新增或排查路由时必须确认实际注册路径，而不是只按目录名判断。

前端不是 SPA：

- `src/frontend/*.html` 是多页面静态前端。
- `src/frontend/js/shared-navigation.js` 统一导航和页面标题。
- `src/frontend/js/env.js`、`config.js`、`api.js` 提供部分共享 API 配置。
- 仍有少数页面直接写 `http://localhost:5001`，部署或端口调整时要逐页检查。

## 关键业务线

知识星球系统同步：

- API：`src/backend/apis/v1/zsxq.py`
- 同步任务：`src/backend/tasks/zsxq_sync.py`
- 同步服务：`src/backend/services/zsxq_sync_service.py`
- 工作流服务：`src/backend/services/zsxq_workflow_service.py`
- 调度器：`src/backend/services/zsxq_system_sync_scheduler.py`

知识星球统一投递历史查询与公网调用方凭据：

- 历史接口：`src/backend/apis/v1/zsxq_browser_import.py` 的 `POST /api/v1/zsxq/browser-import/history`；底层以 `content_pipeline_assets` 共享账本为主，覆盖计划任务/API、侧边栏和手动投递，旧侧边栏 manifest 仅作迁移兼容。
- 多租户调用方表：`src/backend/models/zsxq_history_api_client.py` 的 `zsxq_history_api_clients`。
- 分配、轮换、吊销接口：`src/backend/apis/v1/zsxq_history_api_clients.py`。
- 签名校验：`src/backend/utils/zsxq_history_auth.py`；公网调用方使用 `X-App-Id + X-Timestamp + X-Signature`，不要求交互式登录。
- 详细契约：`docs/api/ZSXQ_BROWSER_IMPORT.md`。

内容清洗流水线：

- API：`src/backend/apis/v1/content_pipeline.py`
- Celery：`src/backend/tasks/content_pipeline.py`
- PDF 清洗：`src/backend/services/content_pipeline/pdf_cleaner_adapter.py`
- 音频清洗：`src/backend/services/content_pipeline/audio_cleaner_adapter.py`
- 清洗后发布 handoff：`src/backend/services/content_pipeline/auto_publish_handoff.py`
- 规范产物账本与解析：`src/backend/services/content_pipeline/canonical_asset_service.py`；`ContentPipelineAsset.cleaned_*` 是清洗成功后唯一允许被工作台、预览和新编排使用的文件引用。

### 规范清洗产物：运行与保留规则

知识星球计划任务、侧边栏和手动投递只是三个上游入口；进入内容流水线后，它们共享同一个 `ContentPipelineAsset` 去重账本和后续通知/发布链路。清洗成功后：

- MP3/PDF 工作台列表、预览和新建编排只使用已验证的 `cleaned_path`，不会回退到 `source_path` 或 Downloads；
- 单个文件失败只会标记该资产失败，其他已登记的清洗产物继续进入自动发布；
- 知识星球自动视频和 PDF 基础文案物料也在清洗产物登记后逐文件触发；同步阶段仅记录等待清洗，绝不从 Downloads 原件直接建编排任务；
- 原始下载文件必须保留，直到所属清洗任务完成且对应规范清洗产物已验证为 `ready`；验证完成后，Downloads 原件可以删除；
- 若清洗产物丢失，资产会变为不可用，需重新登记仍在的输出或重新投递原件，不能使用原始下载文件绕过清洗。

部署这一改造时，先升级数据库并回填历史可验证产物：

```bash
cd src/backend
BACKEND_PROCESS_BOOTSTRAP_ENABLED=0 ../../venv/bin/flask --app app:create_app db upgrade
cd ../..
PYTHONPATH=src/backend venv/bin/python scripts/maintenance/backfill_content_pipeline_cleaned_assets.py --dry-run --limit 200
PYTHONPATH=src/backend venv/bin/python scripts/maintenance/backfill_content_pipeline_cleaned_assets.py --limit 200
```

回填先执行 dry-run，检查已登记与跳过数量后再执行实际命令；执行后重启后端和内容流水线 worker。

研报转语音/视频编排：

- API：`src/backend/api/report_audio_workflow.py`
- Celery：`src/backend/tasks/report_audio_workflow.py`
- 服务：`src/backend/services/report_audio_workflow.py`
- 关键子任务：Gemini 浏览器解析、豆包浏览器朗读、publisher 视频合成。

MP3 视频编排：

- API：`src/backend/api/mp3_video_workflow.py`
- Celery：`src/backend/tasks/mp3_video_workflow.py`
- 服务：`src/backend/services/mp3_video_workflow.py`
- 关键阶段：Gemini 上传和信息图、DS 规划、四张视觉资产、横屏/竖屏 publisher 子任务。

一键发布台：

- API：`src/backend/apis/v1/publisher.py`
- Celery：`src/backend/tasks/publisher.py`
- 产物：视频、竖屏视频、封面、Bilibili/YouTube 上传结果。

运行控制：

- API：`src/backend/apis/v1/celery_monitor.py`
- 自举：`src/backend/services/backend_process_bootstrap.py`
- 准入保护：`src/backend/services/runtime_guard.py`
- 本地脚本：`scripts/dev/`

## 容易踩坑

- `PublicationTask.result` 可能很大。列表页、轮询页和监控页不要为了一个展示字段读取整块 JSON，应优先投影列或详情接口。
- 长耗时父编排不要跨等待阶段持有 ORM 实例。等待子任务时只保存 ID，读取状态时重新短事务查询。
- Celery result backend 被弱化，业务状态以 PostgreSQL 中的任务表和工作流表为准。
- worker、scheduler、watchdog 是多进程系统，SQLAlchemy 连接池按进程独立计算，不是全局共享。
- 本地默认会通过后端自举补齐托管 worker、watchdog，并启动核心 `zsxq_auto` scheduler；排障时如不想自动产生新批次，要显式设置 `BACKEND_PROCESS_BOOTSTRAP_SCHEDULERS=none`。
- 人工 pause worker 是有语义的，watchdog 和后端自举应尊重暂停状态。
- `.env`、`data/`、`runtime/`、`logs/`、`instance/` 是敏感或运行态内容，不要复制到文档或源码变更里。
- 平台凭据正在从 `.env` 迁往加密凭据表，优先通过 `platform_credential_service` 解析，而不是新增环境变量直读。
- 知识星球统一投递历史的公网访问采用租户级 AppID + Secret；不得重新引入全局 `ZSXQ_HISTORY_API_KEY` / `ZSXQ_HISTORY_API_SECRET`。只有 history 读接口可走签名认证，manifest 预览和导入写接口仍只允许 loopback。
- 发布类任务不能把“已提交”当“已成功”。必须区分 submitted、reviewing、visible、failed、unknown。

## 已观察到的小不一致

这些不是必然的业务 bug，但后续相关改动时值得顺手核实：

- `src/frontend/server.py` 设置端口是 `8888`，启动提示里仍写了 `http://localhost:8080`。
- `docs/README.md` 的快速开始仍描述较早的 YouTube Analytics 形态，当前真实系统已经比它复杂得多。
- 部分前端页面走 `env.js`/`CONFIG.API_BASE_URL`，部分页面仍硬编码 `localhost:5001`。
- `api/` 与 `apis/` 双目录并存，新接口最好优先放在当前主注册体系下，并明确是否需要兼容旧路径。

## 新增文档建议

如果后续继续完善项目自描述，可以考虑补这些文档：

- `docs/API_ROUTE_MAP.md`：从实际 Flask 注册结果生成路由索引。
- `docs/CELERY_QUEUE_MAP.md`：队列、worker 脚本、业务 feature、页面入口、任务模型之间的映射。
- `docs/FRONTEND_PAGE_MAP.md`：HTML 页面、JS 文件、API 基址策略和导航入口映射。
- `docs/DATA_MODEL_HOT_PATHS.md`：哪些模型/列属于热路径，哪些 JSON 字段禁止列表页直接读取。
