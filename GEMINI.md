# Project Mandates & Discipline

This document defines the architectural standards and development discipline for the `dig_ytb_channel` project. All AI agents and developers must strictly adhere to these rules.

## 1. Project Structure & Organization

- **Code Isolation**: The `src/` directory is reserved exclusively for source code (`.py`, `.js`, `.html`, `.css`).
- **Data & Instance Separation**: 
  - Never create `data/`, `instance/`, `logs/`, or `uploads/` folders inside `src/`.
  - All persistence layers, uploads, and runtime artifacts must reside in the project root directories: `/data`, `/instance`, `/logs`.
- **Tests Location**: All test scripts (`test_*.py`) must be placed in the `/tests` directory at the project root. No test files should be mixed with source code in `src/`.

## 2. Path Resolution Standards

- **Absolute Paths Only**: Never use `os.getcwd()` or `Path.cwd()` for locating project resources, templates, or config files.
- **Root Traversal**: Always derive the project root path using `os.path.abspath(__file__)` and relative navigation.
- **Execution Environment**: Code must be robust enough to run correctly whether started from the project root or from within `src/backend/`.

## 3. Version Control Discipline

- **Media & Binaries**: Do not track large binary files (`.mp4`, `.wav`, `.pdf`, `.png`) in Git. Use `/data/uploads` and ensure they are covered by `.gitignore`.
- **Clean Source**: The `src/` tree must remain free of temporary artifacts, test-generated images, or orphaned data folders.
- **Gitignore Maintenance**: Keep `.gitignore` updated to proactively block media formats in source directories.

## 4. Execution Logic

- **Launcher Scripts**: Root-level `.sh` files act as convenient entry points for users.
- **Logic Entry Points**: Implementation entry points (e.g., `run_scheduler.py`) reside in `src/backend/` but must follow the Path Resolution Standards defined in section 2.

## 5. Log File Conventions

All runtime logs are stored in `/logs/` at the project root. Key files:

- **`app.log`** — Main application log (Flask backend, rotated daily with `.YYYY-MM-DD` suffix)
- **`celery_worker.log`** — Celery task worker output (video synthesis, publishing tasks)
- **`error.log`** — Aggregated errors only
- **`tts.log`** — TTS service specific logs
- **`scheduler.log`** — Periodic scheduler task logs
- **`youtube.log`** / **`bilibili.log`** — Platform-specific upload/publish logs
- **`security.log`** — Auth/security events
- **`performance.log`** / **`slow_query.log`** — Performance monitoring

When debugging a task (e.g., video synthesis), always check `celery_worker.log` first, then `app.log`. Do **not** grep the source tree for log output.

---
*Note: This document takes precedence over general instructions. If an automated task or agent identifies a violation of these rules, it must be corrected immediately before proceeding with feature development.*
