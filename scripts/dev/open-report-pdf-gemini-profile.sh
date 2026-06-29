#!/bin/bash

# 打开研报 PDF Gem 解析专用 Chrome profile，用于首次登录和登录态维护。
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENV_FILE="$PROJECT_ROOT/.env"
WORKER_ID="${REPORT_PDF_GEMINI_WORKER_ID:-${1:-}}"
PROFILE_POOL_ROOT="${REPORT_PDF_GEMINI_PROFILE_POOL_ROOT:-$PROJECT_ROOT/runtime/browser_profiles/report_pdf_gemini_pool}"

env_value() {
    local key="$1"
    local line=""
    if [ -f "$ENV_FILE" ]; then
        line="$(grep -E "^${key}=" "$ENV_FILE" | tail -n 1 || true)"
    fi
    line="${line#*=}"
    line="${line%\"}"
    line="${line#\"}"
    line="${line%\'}"
    line="${line#\'}"
    printf "%s" "$line"
}

USER_DATA_DIR="${REPORT_PDF_GEMINI_USER_DATA_DIR:-$(env_value REPORT_PDF_GEMINI_USER_DATA_DIR)}"
if [ -z "$USER_DATA_DIR" ]; then
    USER_DATA_DIR="${GEMINI_BROWSER_USER_DATA_DIR:-$(env_value GEMINI_BROWSER_USER_DATA_DIR)}"
fi
if [ -n "$WORKER_ID" ] && [ -z "${REPORT_PDF_GEMINI_USER_DATA_DIR:-}" ]; then
    USER_DATA_DIR="$PROFILE_POOL_ROOT/$WORKER_ID"
fi

PROFILE_DIRECTORY="${REPORT_PDF_GEMINI_PROFILE_DIRECTORY:-$(env_value REPORT_PDF_GEMINI_PROFILE_DIRECTORY)}"
if [ -z "$PROFILE_DIRECTORY" ]; then
    PROFILE_DIRECTORY="${GEMINI_BROWSER_PROFILE_DIRECTORY:-$(env_value GEMINI_BROWSER_PROFILE_DIRECTORY)}"
fi

CHROME_PATH="${REPORT_PDF_GEMINI_CHROME_PATH:-$(env_value REPORT_PDF_GEMINI_CHROME_PATH)}"
if [ -z "$CHROME_PATH" ]; then
    CHROME_PATH="${GEMINI_BROWSER_CHROME_PATH:-$(env_value GEMINI_BROWSER_CHROME_PATH)}"
fi

GEMINI_URL="${REPORT_PDF_GEMINI_URL:-$(env_value REPORT_PDF_GEMINI_URL)}"
if [ -z "$GEMINI_URL" ]; then
    GEMINI_URL="${GEMINI_BROWSER_URL:-$(env_value GEMINI_BROWSER_URL)}"
fi
LEGACY_USER_DATA_DIR="$PROJECT_ROOT/data/browser_profiles/gemini"

if [ -z "$USER_DATA_DIR" ]; then
    USER_DATA_DIR="$PROJECT_ROOT/runtime/browser_profiles/report_pdf_gemini"
fi

if [ ! -d "$USER_DATA_DIR" ] && [ -d "$LEGACY_USER_DATA_DIR" ]; then
    echo "检测到旧研报 PDF Gem profile，当前继续使用旧路径；关闭 Chrome 后可迁移到 runtime/browser_profiles/report_pdf_gemini。"
    USER_DATA_DIR="$LEGACY_USER_DATA_DIR"
fi

if [ -z "$CHROME_PATH" ]; then
    CHROME_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
fi

if [ -z "$GEMINI_URL" ]; then
    GEMINI_URL="https://gemini.google.com/gem/099a92ae7888"
fi

if [ ! -x "$CHROME_PATH" ]; then
    echo "Chrome 可执行文件不存在或不可执行: $CHROME_PATH"
    exit 1
fi

mkdir -p "$USER_DATA_DIR"

ARGS=(
    "--user-data-dir=$USER_DATA_DIR"
    "--password-store=basic"
    "--use-mock-keychain"
    "--no-first-run"
    "--no-default-browser-check"
)

if [ -n "$PROFILE_DIRECTORY" ]; then
    ARGS+=("--profile-directory=$PROFILE_DIRECTORY")
fi

echo "打开研报 PDF Gem 专用 Chrome profile:"
echo "  user-data-dir: $USER_DATA_DIR"
if [ -n "$WORKER_ID" ]; then
    echo "  worker-id: $WORKER_ID"
fi
if [ -n "$PROFILE_DIRECTORY" ]; then
    echo "  profile-directory: $PROFILE_DIRECTORY"
fi
echo "  url: $GEMINI_URL"

if [[ "$OSTYPE" == darwin* && "$CHROME_PATH" == *"/Contents/MacOS/"* ]]; then
    APP_PATH="${CHROME_PATH%/Contents/MacOS/*}"
    open -na "$APP_PATH" --args "${ARGS[@]}" "$GEMINI_URL"
else
    "$CHROME_PATH" "${ARGS[@]}" "$GEMINI_URL" >/dev/null 2>&1 &
fi
