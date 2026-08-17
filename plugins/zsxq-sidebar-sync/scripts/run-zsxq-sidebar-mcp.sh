#!/bin/sh
set -eu

plugin_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
project_root=$(CDPATH= cd -- "$plugin_dir/../.." && pwd)
python_bin="$project_root/venv/bin/python"

if [ ! -x "$python_bin" ]; then
  echo "ZSXQ MCP startup failed: missing project Python at $python_bin" >&2
  exit 1
fi

if [ -n "${PYTHONPATH:-}" ]; then
  backend_pythonpath="$project_root/src/backend:$PYTHONPATH"
else
  backend_pythonpath="$project_root/src/backend"
fi

exec env PYTHONPATH="$backend_pythonpath" \
  "$python_bin" -m mcp_servers.zsxq_sidebar
