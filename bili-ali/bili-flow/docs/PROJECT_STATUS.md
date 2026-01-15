# Bili-Flow Project Status & Documentation

**Last Updated:** 2026-01-14
**Status:** Alpha / Core Infrastructure Ready

## 1. Project Overview
Bili-Flow is a high-performance automation toolkit for Bilibili, designed to streamline video publishing and management workflows.

## 2. Directory Structure & Rules

### Data Pipeline
We use a strictly defined directory structure for file processing:

*   **`data/input/`**: **DROP ZONE**. Place your video files here (`.mp4`, `.mkv`, `.mov`). The system monitors this folder.
    *   *Rule*: The filename (excluding extension) will be used as the video title.
*   **`data/processed/`**: Successfully uploaded videos are automatically moved here.
*   **`data/failed/`**: Videos that failed to upload are moved here for inspection.

### Source Code
*   `src/biliflow/config/`: Configuration management (Env vars).
*   `src/biliflow/core/`: Core logic (e.g., `uploader.py` wrapper for Bilibili API).
*   `src/biliflow/utils/`: Utilities like logging.
*   `scripts/`: Executable scripts for users.

## 3. Usage Guide

### Setup
1.  Copy `.env.example` to `.env`.
2.  Fill in your Bilibili credentials (`SESSDATA`, `BILI_JCT`, `BUVID3`, `DEDEUSERID`).

### Uploading Videos
1.  **Place** video files into `data/input/`.
2.  **Run** the batch processor:
    ```bash
    ./venv/bin/python scripts/batch_upload.py
    ```
3.  **Check** results:
    *   Success: File moved to `data/processed/`.
    *   Failure: File moved to `data/failed/`.
    *   Logs: Check terminal output or `logs/biliflow.log`.

### Web UI (New!)
We now provide a modern Web Control Center:
1. **Run**: `./start.sh`
2. **Access**: Open `http://localhost:5173` in your browser.
3. **Flow**:
   - **Step 1**: Configure Credentials (saved to `.env`).
   - **Step 2**: Verify Login Status.
   - **Step 3**: Monitor `data/input` and trigger Batch Upload.

## 4. Current Feature Set
- [x] **Project Skeleton**: Poetry-style structure, Type hints, Logging.
- [x] **Authentication**: Cookie-based auth via Web UI or `.env`.
- [x] **Upload Core**: Async video upload with resume support.
- [x] **Batch Pipeline**: Automated scanning and processing.
- [x] **Web Control Center**: Modern React + FastAPI interface with real-time logs.

## 5. Roadmap / Todo
- [ ] **Metadata Sidecar**: Support `.json` or `.yaml` files alongside videos to specify description, tags, and cover image.
- [ ] **Knowledge Cards**: Generate summary cards for videos (Planned).
- [ ] **Bilingual Subtitles**: Auto-generate/upload subtitles (Planned).
- [ ] **Daemon Mode**: Run the script as a background service using `APScheduler`.
