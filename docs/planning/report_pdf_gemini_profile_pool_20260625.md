# 研报 PDF Gemini Profile 池

## 背景

单篇研报文案和 Top10 基础素材都依赖 `report_pdf_gemini` 浏览器自动化。原先只有一个 Chrome profile 和一个 `concurrency=1` worker，批量任务会串行等待。

本试验不把单个 Chrome profile 内部并发打开，而是复制出多个独立 `user-data-dir`，让多个 `report_pdf_gemini` worker 共同消费同一个队列。

## 目标

1. 验证 2 个独立 profile 同时跑 Gemini 是否稳定。
2. 观察是否出现登录失效、验证码、页面串任务、结果串任务。
3. 在稳定前不扩大到大量 profile。

## 正式接入

`report_pdf_gemini` 的 Celery monitor 启动入口已经切到 profile 池：

```text
scripts/dev/run-celery-report-pdf-gemini-pool.sh
```

因此以下正式入口都会启动多 profile worker：

- Celery monitor 的 `report_pdf_gemini` 启动/重启
- Celery watchdog 自愈拉起
- 后端 bootstrap 的 `BACKEND_PROCESS_BOOTSTRAP_CELERY_SERVICES=all`

默认 profile 数仍由 `REPORT_PDF_GEMINI_PROFILE_COUNT` 控制；当前默认脚本值为 2。扩到 3 个浏览器前，需要先准备并验证 `worker_3` profile。

## 操作

先关闭正在使用模板 profile 的 Chrome 窗口和 `report_pdf_gemini` worker。

准备 2 个 profile：

```bash
REPORT_PDF_GEMINI_PROFILE_COUNT=2 ./scripts/dev/prepare-report-pdf-gemini-profile-pool.sh
```

启动 2 个 worker：

```bash
REPORT_PDF_GEMINI_PROFILE_COUNT=2 ./scripts/dev/run-celery-report-pdf-gemini-pool.sh
```

启动 2 个 worker 消费隔离测试队列：

```bash
REPORT_PDF_GEMINI_QUEUE=report_pdf_gemini_profile_test REPORT_PDF_GEMINI_PROFILE_COUNT=2 ./scripts/dev/run-celery-report-pdf-gemini-pool.sh
```

停止全部 `report_pdf_gemini` worker：

```bash
./scripts/dev/stop-celery-report-pdf-gemini.sh
```

打开某个 worker profile 做登录态维护：

```bash
REPORT_PDF_GEMINI_WORKER_ID=worker_1 ./scripts/dev/open-report-pdf-gemini-profile.sh
```

## 关键约束

- 每个 worker 仍然是 `concurrency=1 -P solo`。
- 并发只来自多个独立 `user-data-dir`，不是同一个 profile 内多线程。
- 同一账号复制出的多个 profile 仍可能触发账号级限流或安全验证。
- 首轮建议只用 2 个 profile，小批量投递验证。

## 观察指标

- `report_pdf_gemini` 子任务平均耗时是否下降。
- 是否出现 `Gemini 浏览器 profile 正在被其他 Chrome 占用`。
- 是否出现登录、验证码、异常流量、安全验证。
- 是否出现 A PDF 拿到 B PDF 回复的串任务迹象。

## 回退

停止 profile 池后，直接启动原单 worker：

```bash
./scripts/dev/run-celery-report-pdf-gemini.sh
```
