# 一键发布台 (One-Click Publisher) 部署与启动指南

本文档详细说明了如何启动“自动化视频发布流水线”的全套服务。该系统包含前端界面、后端 API 和异步任务队列。

---

## 📋 架构概览

系统由三个核心进程组成，需要分别启动：

1.  **Backend Server (Flask)**: 接收 API 请求，管理数据库。
2.  **Task Worker (Celery)**: 在后台执行耗时的视频合成、封面生成和上传任务。
3.  **Frontend Server**: 提供 Web 操作界面。

此外，依赖以下基础服务：
*   **Redis**: 用于任务消息队列 (默认端口 6379)。
*   **PostgreSQL**: 用于存储任务记录。

---

## 🚀 启动步骤

请打开三个独立的终端窗口（或使用 tmux/screen），分别执行以下命令。

### 窗口 1：启动 Celery Worker (异步任务处理)

这是核心“工兵”，负责实际的视频合成与上传。

```bash
# 1. 进入后端目录
cd src/backend

# 2. 激活虚拟环境 (确保路径正确)
source ../../venv-3.11/bin/activate

# 3. 启动 Worker
# --concurrency=1 确保单线程执行，避免视频合成时 CPU 过载
celery -A celery_config.celery_app worker --loglevel=info --concurrency=1
```

> **成功标志**: 看到 Celery 的 ASCII Logo，并且日志显示 `[tasks]` 列表包含 `tasks.publisher.run_publication_pipeline`。

---

### 窗口 2：启动 Backend API (后端服务)

这是系统的“大脑”，负责接收请求和读写数据库。

```bash
# 1. 进入后端目录
cd src/backend

# 2. 激活虚拟环境
source ../../venv-3.11/bin/activate

# 3. 启动 Flask 开发服务器
# 默认运行在 http://localhost:5001
python app.py
```

---

### 窗口 3：启动 Frontend (前端界面)

这是系统的“脸面”，提供操作 UI。

```bash
# 1. 进入前端目录
cd src/frontend

# 2. 启动简单的 HTTP 服务器
# 默认运行在 http://localhost:8000
python3 -m http.server 8000
```

---

## ✅ 验证与使用

1.  打开浏览器访问前端页面：
    *   👉 **http://localhost:8000/publisher.html**
2.  如果看到“一键发布台”界面，且左侧导航栏加载正常，说明前端正常。
3.  填写参数并点击“提交发布任务”。
4.  观察 **窗口 1 (Celery)** 的日志，应该能看到：
    *   `Task tasks.publisher.run_publication_pipeline[...] received`
    *   `Starting video synthesis...`
5.  观察网页上的进度条是否开始走动。

---

## 🛠 常见问题 (Troubleshooting)

**Q: 提交任务时报错 "Connection refused"**
*   A: 检查 Redis 服务是否启动 (`redis-cli ping` 应返回 PONG)。

**Q: 任务一直处于 "PENDING" 状态**
*   A: 检查 **窗口 1** 的 Celery Worker 是否启动。如果没有 Worker，任务会一直卡在 Redis 队列里。

**Q: 数据库报错 "permission denied"**
*   A: 确保数据库中已创建 `publication_tasks` 表。如果权限不足，请参考 `scripts/create_publisher_table.py` 中的建表语句手动创建。

**Q: 视频合成太慢**
*   A: 这是正常现象。视频合成是 CPU 密集型操作。请确保不要同时开启其他大型程序。
