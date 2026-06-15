#!/bin/bash

# 打开豆包网页朗读捕获专用 Chrome profile，用于首次登录和登录态维护。
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENV_FILE="$PROJECT_ROOT/.env"

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

USER_DATA_DIR="${DOUBAO_BROWSER_TTS_USER_DATA_DIR:-$(env_value DOUBAO_BROWSER_TTS_USER_DATA_DIR)}"
PROFILE_DIRECTORY="${DOUBAO_BROWSER_TTS_PROFILE_DIRECTORY:-$(env_value DOUBAO_BROWSER_TTS_PROFILE_DIRECTORY)}"
CHROME_PATH="${DOUBAO_BROWSER_TTS_CHROME_PATH:-$(env_value DOUBAO_BROWSER_TTS_CHROME_PATH)}"
DOUBAO_URL="${DOUBAO_BROWSER_TTS_URL:-$(env_value DOUBAO_BROWSER_TTS_URL)}"
EXTENSION_PATH="${DOUBAO_BROWSER_TTS_EXTENSION_PATH:-$(env_value DOUBAO_BROWSER_TTS_EXTENSION_PATH)}"
LEGACY_USER_DATA_DIR="$PROJECT_ROOT/runtime/doubao-browser-profile"

if [ -z "$USER_DATA_DIR" ]; then
    USER_DATA_DIR="$PROJECT_ROOT/runtime/browser_profiles/doubao"
fi

if [ ! -d "$USER_DATA_DIR" ] && [ -d "$LEGACY_USER_DATA_DIR" ]; then
    echo "检测到旧豆包 profile，当前继续使用旧路径；关闭 Chrome 后可迁移到 runtime/browser_profiles/doubao。"
    USER_DATA_DIR="$LEGACY_USER_DATA_DIR"
fi

if [ -z "$CHROME_PATH" ]; then
    CHROME_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
fi

if [ -z "$DOUBAO_URL" ]; then
    DOUBAO_URL="https://www.doubao.com/chat/"
fi

if [ -z "$EXTENSION_PATH" ]; then
    EXTENSION_PATH="$PROJECT_ROOT/tools/doubao-audio-capture-extension"
fi

if [ ! -x "$CHROME_PATH" ]; then
    echo "Chrome 可执行文件不存在或不可执行: $CHROME_PATH"
    exit 1
fi

mkdir -p "$USER_DATA_DIR"

ARGS=(
    "--user-data-dir=$USER_DATA_DIR"
    "--autoplay-policy=no-user-gesture-required"
    "--password-store=basic"
    "--use-mock-keychain"
    "--no-first-run"
    "--no-default-browser-check"
)

if [ -n "$PROFILE_DIRECTORY" ]; then
    ARGS+=("--profile-directory=$PROFILE_DIRECTORY")
fi

if [ -d "$EXTENSION_PATH" ]; then
    ARGS+=("--disable-extensions-except=$EXTENSION_PATH")
    ARGS+=("--load-extension=$EXTENSION_PATH")
fi

echo "打开豆包专用 Chrome profile:"
echo "  user-data-dir: $USER_DATA_DIR"
if [ -n "$PROFILE_DIRECTORY" ]; then
    echo "  profile-directory: $PROFILE_DIRECTORY"
fi
echo "  url: $DOUBAO_URL"

if [[ "$OSTYPE" == darwin* && "$CHROME_PATH" == *"/Contents/MacOS/"* ]]; then
    APP_PATH="${CHROME_PATH%/Contents/MacOS/*}"
    open -na "$APP_PATH" --args "${ARGS[@]}" "$DOUBAO_URL"
else
    "$CHROME_PATH" "${ARGS[@]}" "$DOUBAO_URL" >/dev/null 2>&1 &
fi
