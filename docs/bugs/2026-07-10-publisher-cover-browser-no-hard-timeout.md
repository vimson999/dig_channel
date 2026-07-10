# Publisher cover browser had no hard timeout

## 基本信息

- 状态：已修复待进程重载
- 严重级别：P1
- 影响模块：publisher / Playwright / render worker
- 首次发现：2026-07-10
- 修复提交：工作区待提交
- 根因分类：EXTERNAL-CALL-RESILIENCE
- 复发键：BROWSER-PROCESS-NO-HARD-TIMEOUT
- 是否复发：同类第 2 次
- 关联记录：2026-07-07-mp3-cover-render-cdn-timeout.md
- 防线层级：shared_guardrail / contract_test / observability

## Symptom

On 2026-07-10, publisher task `ded12885-0c68-427f-ad28-22f7bb039a54`
finished the landscape video and two workbench covers, then remained in
`v_synthesis` while starting the podcast cover browser. The Playwright driver
and Chrome process stayed alive and idle with no renderer response.

## Root cause

The in-process synchronous Playwright call had page navigation timeouts, but
there was no process-level deadline around browser/context creation, page
evaluation, or shutdown. A browser transport stall therefore blocked the only
render worker indefinitely.

## Fix and guard

- Run every workbench cover render in a dedicated child process.
- Enforce `REPORT_COVER_HARD_TIMEOUT_MS` (70 seconds by default).
- Terminate the entire child process group on timeout so Chrome descendants do
  not leak.
- Keep the direct renderer private for the isolated worker and explicit test
  use only.
- Unit-test both process isolation and timeout termination.

## Discipline

Every external browser or media process invoked by a Celery task must have an
OS-level deadline. Library-level navigation or request timeouts are not an
acceptable substitute because startup, IPC, and shutdown can stall outside
those scopes.
