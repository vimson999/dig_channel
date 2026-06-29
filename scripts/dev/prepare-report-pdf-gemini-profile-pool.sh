#!/bin/bash

# 从一个已登录、已验证可用的研报 PDF Gemini Chrome profile 复制出多个 worker profile。
set -euo pipefail

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

resolve_path() {
    local raw="$1"
    if [ -z "$raw" ]; then
        printf ""
        return
    fi
    raw="${raw/#\~/$HOME}"
    if [[ "$raw" != /* ]]; then
        raw="$PROJECT_ROOT/$raw"
    fi
    printf "%s" "$raw"
}

truthy() {
    case "$(printf "%s" "${1:-}" | tr '[:upper:]' '[:lower:]')" in
        1|true|yes|on) return 0 ;;
        *) return 1 ;;
    esac
}

COUNT="${REPORT_PDF_GEMINI_PROFILE_COUNT:-${1:-2}}"
if ! [[ "$COUNT" =~ ^[0-9]+$ ]] || [ "$COUNT" -lt 1 ]; then
    echo "❌ REPORT_PDF_GEMINI_PROFILE_COUNT 必须是正整数，当前: $COUNT"
    exit 1
fi

SOURCE_DIR="${REPORT_PDF_GEMINI_TEMPLATE_USER_DATA_DIR:-}"
if [ -z "$SOURCE_DIR" ]; then
    SOURCE_DIR="${REPORT_PDF_GEMINI_USER_DATA_DIR:-$(env_value REPORT_PDF_GEMINI_USER_DATA_DIR)}"
fi
if [ -z "$SOURCE_DIR" ]; then
    SOURCE_DIR="${GEMINI_BROWSER_USER_DATA_DIR:-$(env_value GEMINI_BROWSER_USER_DATA_DIR)}"
fi
if [ -z "$SOURCE_DIR" ]; then
    SOURCE_DIR="$PROJECT_ROOT/runtime/browser_profiles/report_pdf_gemini"
fi

SOURCE_DIR="$(resolve_path "$SOURCE_DIR")"
LEGACY_SOURCE="$PROJECT_ROOT/data/browser_profiles/gemini"
if [ ! -d "$SOURCE_DIR" ] && [ -d "$LEGACY_SOURCE" ]; then
    SOURCE_DIR="$LEGACY_SOURCE"
fi

DEST_ROOT="$(resolve_path "${REPORT_PDF_GEMINI_PROFILE_POOL_ROOT:-$PROJECT_ROOT/runtime/browser_profiles/report_pdf_gemini_pool}")"

if [ ! -d "$SOURCE_DIR" ]; then
    echo "❌ 模板 profile 不存在: $SOURCE_DIR"
    echo "   请先用 ./scripts/dev/open-report-pdf-gemini-profile.sh 登录 Gemini 并验证 Gem 可用。"
    exit 1
fi

if [ -e "$SOURCE_DIR/SingletonLock" ] || [ -e "$SOURCE_DIR/SingletonCookie" ] || [ -e "$SOURCE_DIR/SingletonSocket" ]; then
    echo "❌ 模板 profile 看起来正在被 Chrome 占用: $SOURCE_DIR"
    echo "   请先关闭该 profile 对应的 Chrome 窗口和正在运行的 report_pdf_gemini worker，再复制。"
    exit 1
fi

mkdir -p "$DEST_ROOT"

echo "🧬 正在准备研报 PDF Gemini profile 池..."
echo "   source: $SOURCE_DIR"
echo "   dest_root: $DEST_ROOT"
echo "   count: $COUNT"

for index in $(seq 1 "$COUNT"); do
    slot="worker_$index"
    dest="$DEST_ROOT/$slot"
    if [ -d "$dest" ] && [ "$(find "$dest" -mindepth 1 -maxdepth 1 | head -n 1)" != "" ]; then
        if truthy "${FORCE:-0}"; then
            rm -rf "$dest"
        else
            echo "↪️  跳过已存在 profile: $dest"
            continue
        fi
    fi

    mkdir -p "$dest"
    if command -v rsync >/dev/null 2>&1; then
        rsync -a --delete \
            --exclude='SingletonLock' \
            --exclude='SingletonCookie' \
            --exclude='SingletonSocket' \
            "$SOURCE_DIR/" "$dest/"
    else
        (cd "$SOURCE_DIR" && tar --exclude='SingletonLock' --exclude='SingletonCookie' --exclude='SingletonSocket' -cf - .) \
            | (cd "$dest" && tar -xf -)
    fi
    rm -f "$dest/SingletonLock" "$dest/SingletonCookie" "$dest/SingletonSocket"
    echo "✅ 已准备: $slot -> $dest"
done

echo
echo "下一步可启动 profile 池:"
echo "  REPORT_PDF_GEMINI_PROFILE_COUNT=$COUNT ./scripts/dev/run-celery-report-pdf-gemini-pool.sh"
echo
echo "单个 worker 手动启动示例:"
echo "  REPORT_PDF_GEMINI_WORKER_ID=worker_1 REPORT_PDF_GEMINI_USER_DATA_DIR=\"$DEST_ROOT/worker_1\" ./scripts/dev/run-celery-report-pdf-gemini.sh"
