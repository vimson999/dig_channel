#!/bin/bash

# 兼容旧命令：Gemini 浏览器 worker 已按业务场景命名为 report_pdf_gemini。
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "ℹ️ gemini_browser 已更名为 report_pdf_gemini，正在转到新脚本..."
exec "$SCRIPT_DIR/stop-celery-report-pdf-gemini.sh"
