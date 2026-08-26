#!/bin/sh
set -eu

plugin_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
project_root=$(CDPATH= cd -- "$plugin_dir/../.." && pwd)
python_bin=""

if [ -n "${ZSXQ_MCP_PYTHON:-}" ] && [ -x "$ZSXQ_MCP_PYTHON" ]; then
  python_bin="$ZSXQ_MCP_PYTHON"
elif [ -x "$project_root/venv/bin/python" ]; then
  python_bin="$project_root/venv/bin/python"
elif [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then
  python_bin="$VIRTUAL_ENV/bin/python"
elif command -v git >/dev/null 2>&1; then
  common_git_dir=$(git -C "$project_root" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)
  if [ -n "$common_git_dir" ]; then
    shared_project_root=$(CDPATH= cd -- "$(dirname "$common_git_dir")" && pwd)
    if [ -x "$shared_project_root/venv/bin/python" ]; then
      python_bin="$shared_project_root/venv/bin/python"
    fi
  fi
fi

if [ -z "$python_bin" ]; then
  echo "ZSXQ MCP startup failed: set ZSXQ_MCP_PYTHON or provide venv/bin/python" >&2
  exit 1
fi

if [ -n "${PYTHONPATH:-}" ]; then
  backend_pythonpath="$project_root/src/backend:$PYTHONPATH"
else
  backend_pythonpath="$project_root/src/backend"
fi

exec env PYTHONPATH="$backend_pythonpath" \
  "$python_bin" -m mcp_servers.zsxq_sidebar
