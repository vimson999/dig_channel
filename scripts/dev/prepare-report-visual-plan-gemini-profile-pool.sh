#!/bin/bash

# Prepare isolated Gemini profiles for the report_visual_plan worker pool.
# The source must be an already verified, signed-in Chrome user-data directory.
# Do not copy a profile while its Chrome or Gemini worker is running.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

resolve_path() {
    local raw="$1"
    raw="${raw/#\~/$HOME}"
    if [[ "$raw" != /* ]]; then
        raw="$PROJECT_ROOT/$raw"
    fi
    printf "%s" "$raw"
}

COUNT="${REPORT_VISUAL_PLAN_PROFILE_COUNT:-${1:-2}}"
if ! [[ "$COUNT" =~ ^[0-9]+$ ]] || [ "$COUNT" -lt 1 ]; then
    echo "❌ REPORT_VISUAL_PLAN_PROFILE_COUNT 必须是正整数，当前: $COUNT"
    exit 1
fi

POOL_ROOT="$(resolve_path "${REPORT_VISUAL_PLAN_PROFILE_ROOT:-$PROJECT_ROOT/runtime/browser_profiles/report_visual_plan_gemini_pool}")"
SOURCE_DIR="${REPORT_VISUAL_PLAN_TEMPLATE_USER_DATA_DIR:-}"
if [ -z "$SOURCE_DIR" ]; then
    # This dated backup is the last known quiescent copy of the verified
    # worker_4 profile. Operators can override it with a newer template.
    SOURCE_DIR="$PROJECT_ROOT/runtime/browser_profiles/report_pdf_gemini_pool/worker_4_a_backup_20260804"
fi
SOURCE_DIR="$(resolve_path "$SOURCE_DIR")"

if [ ! -d "$SOURCE_DIR" ]; then
    echo "❌ Gemini 模板 profile 不存在: $SOURCE_DIR"
    exit 1
fi
if [ ! -f "$SOURCE_DIR/Local State" ] || [ ! -f "$SOURCE_DIR/Default/Preferences" ]; then
    echo "❌ 模板 profile 不完整，缺少 Local State 或 Default/Preferences: $SOURCE_DIR"
    exit 1
fi
for singleton in SingletonLock SingletonCookie SingletonSocket; do
    if [ -e "$SOURCE_DIR/$singleton" ]; then
        echo "❌ 模板 profile 正被 Chrome 占用（存在 $singleton）: $SOURCE_DIR"
        echo "   请关闭该 profile 后再复制，避免复制出损坏的登录状态。"
        exit 1
    fi
done

# Also refuse a source held by the application-level profile lock. This
# catches Playwright sessions that do not leave Chrome Singleton* markers.
LOCK_KEY="$(printf "%s|Default" "$SOURCE_DIR" | shasum | cut -c1-16)"
LOCK_PATH="$PROJECT_ROOT/runtime/locks/gemini_browser/${LOCK_KEY}.lock"
if [ -f "$LOCK_PATH" ]; then
    OWNER_PID="$(sed -n 's/^pid=\([0-9][0-9]*\).*/\1/p' "$LOCK_PATH" | head -n 1)"
    if [ -n "$OWNER_PID" ] && kill -0 "$OWNER_PID" 2>/dev/null; then
        echo "❌ 模板 profile 正被 worker $OWNER_PID 使用: $SOURCE_DIR"
        echo "   请等待任务完成，或指定一个已停止且已验证的模板 profile。"
        exit 1
    fi
fi

mkdir -p "$POOL_ROOT"
echo "🧬 正在准备分镜 Gemini profile 池..."
echo "   source: $SOURCE_DIR"
echo "   dest_root: $POOL_ROOT"
echo "   count: $COUNT"

for index in $(seq 1 "$COUNT"); do
    slot="worker_$index"
    destination="$POOL_ROOT/$slot"
    if [ -e "$destination" ] && [ "$(find "$destination" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then
        echo "↪️  跳过已存在的非空 profile: $destination"
        continue
    fi
    mkdir -p "$destination"
    rsync -a \
        --exclude='SingletonLock' \
        --exclude='SingletonCookie' \
        --exclude='SingletonSocket' \
        --exclude='Default/Cache/***' \
        --exclude='Default/Code Cache/***' \
        --exclude='Default/GPUCache/***' \
        --exclude='Default/DawnGraphiteCache/***' \
        --exclude='Default/DawnWebGPUCache/***' \
        --exclude='GraphiteDawnCache/***' \
        --exclude='ShaderCache/***' \
        "$SOURCE_DIR/" "$destination/"
    rm -f \
        "$destination/SingletonLock" \
        "$destination/SingletonCookie" \
        "$destination/SingletonSocket"
    echo "✅ 已准备: $slot -> $destination"
done

echo
echo "下一步启动分镜 worker 池:"
echo "  REPORT_VISUAL_PLAN_PROFILE_COUNT=$COUNT ./scripts/dev/run-celery-report-visual-plan-pool.sh"
