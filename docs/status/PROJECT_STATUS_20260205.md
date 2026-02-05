# 项目里程碑报告 (2026-02-05)

## 🎯 核心里程碑：自动化发布流水线闭环

本项目已成功从单一的数据分析工具进化为**端到端的内容生产与发布平台**。实现了从素材上传、视频合成、封面设计到 Bilibili 自动投递的全流程自动化。

---

## 🛠 新增核心模块

### 1. 一键发布台 (One-Click Publisher)
- **入口**: `http://localhost:8888/publisher.html`
- **功能**:
    - **视频合成**: 支持 Video+Audio+Image 混剪，片头视频可选。
    - **封面工坊**: 集成 Canvas 实时预览，支持“金融级”排版（宋体、居中、投影）。
    - **渠道发布**: 深度集成 Bilibili API，支持分区、标签、简介、原创/转载设置。
    - **任务管理**: 异步任务队列，支持实时进度监控和历史记录回溯。

### 2. 后端服务架构升级
- **异步任务**: 引入 **Celery + Redis** 架构，解决视频合成耗时问题。
- **数据持久化**: 新增 `publication_tasks` 表 (PostgreSQL)，记录所有任务状态。
- **路径健壮性**: 修复了多进程环境下的路径引用问题，支持绝对路径自动转换。

### 3. 封面生成服务 (Thumbnail Service)
- **渲染引擎**: 基于 Pillow 实现后端绘图，确保 100% 还原设计稿。
- **视觉标准**: 确立了“左对齐/居中 + 宋体 + 投影”的金融研报视觉规范。

---

## 🚀 启动指南 (Quick Start)

为了运行全套服务，需要启动三个独立的进程：

### 1. 启动 Redis (依赖)
```bash
redis-server
```

### 2. 启动 Celery Worker (核心工兵)
负责视频合成与上传。**必须在 `src/backend` 目录下运行，并指定环境变量以兼容 macOS。**

```bash
cd src/backend
source ../../venv-3.11/bin/activate
PYTHONPATH=. OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES celery -A celery_config.celery_app worker --loglevel=info --concurrency=1
```

### 3. 启动 Flask API (后端)
```bash
cd src/backend
source ../../venv-3.11/bin/activate
python app.py
```

### 4. 启动 Frontend (前端)
```bash
cd src/frontend
python3 -m http.server 8888
# 访问 http://localhost:8888/publisher.html
```

---

## ⚠️ 注意事项与已知问题

1.  **Bilibili Cookie**: 需要定期在 `.env` 中更新 `BILI_SESSDATA` 等字段。发布台已内置点击引导功能。
2.  **macOS 兼容性**: 必须使用 `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` 启动 Celery，否则会因多进程安全检查而崩溃。
3.  **字体依赖**: 封面生成依赖系统字体。已优先配置 macOS 的 `Songti.ttc`。

---

## 📅 下一步计划 (TODO)

- [ ] **YouTube 集成**: 复用发布流水线，增加 YouTube 上传渠道。
- [ ] **合集管理**: 实现 Bilibili 合集列表的自动获取。
- [ ] **多账号支持**: 支持切换不同的 B 站账号进行发布。
