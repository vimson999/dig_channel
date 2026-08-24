#!/bin/bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  connect-secure-tunnel.sh --tunnel-id tunnel_<32 lowercase hex chars> [options]

Options:
  --runtime-key-stdin  Read a new Runtime API key from stdin.
  --replace-key       Replace the existing local Runtime API key (prompts securely).
  --help              Show this help.

The Runtime API key is stored outside the repository with mode 0600. The
tunnel-client profile receives only a file:/... secret reference.
EOF
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
plugin_dir="$(cd "${script_dir}/.." && pwd)"
mcp_launcher="${script_dir}/run-zsxq-sidebar-mcp.sh"

default_tunnel_client_bin="${HOME}/Library/Application Support/OpenAI/tunnel-client/v0.0.11/tunnel-client"
tunnel_client_bin="${ZSXQ_TUNNEL_CLIENT_BIN:-${default_tunnel_client_bin}}"
secret_file="${ZSXQ_TUNNEL_SECRET_FILE:-${HOME}/Library/Application Support/tunnel-client/secrets/zsxq-sidebar-runtime-api-key}"
runtime_alias="${ZSXQ_TUNNEL_RUNTIME_ALIAS:-zsxq-sidebar}"
runtime_profile="${ZSXQ_TUNNEL_PROFILE:-zsxq-sidebar}"

tunnel_id=""
runtime_key_stdin=false
replace_key=false

while (($#)); do
  case "$1" in
    --tunnel-id)
      if (($# < 2)); then
        echo "--tunnel-id requires a value" >&2
        exit 2
      fi
      tunnel_id="$2"
      shift 2
      ;;
    --runtime-key-stdin)
      runtime_key_stdin=true
      shift
      ;;
    --replace-key)
      replace_key=true
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ! "${tunnel_id}" =~ ^tunnel_[0-9a-f]{32}$ ]]; then
  echo "Invalid tunnel ID. Expected tunnel_<32 lowercase hexadecimal characters>." >&2
  exit 2
fi

if [[ ! -x "${tunnel_client_bin}" ]]; then
  echo "tunnel-client is not executable: ${tunnel_client_bin}" >&2
  exit 1
fi

if [[ ! -x "${mcp_launcher}" ]]; then
  echo "MCP launcher is not executable: ${mcp_launcher}" >&2
  exit 1
fi

write_runtime_key=false
if [[ ! -s "${secret_file}" || "${replace_key}" == true || "${runtime_key_stdin}" == true ]]; then
  write_runtime_key=true
fi

if [[ "${write_runtime_key}" == true ]]; then
  runtime_key=""
  if [[ "${runtime_key_stdin}" == true ]]; then
    IFS= read -r runtime_key
  elif [[ -t 0 ]]; then
    IFS= read -r -s -p "Paste the restricted Runtime API key (input hidden): " runtime_key
    printf '\n'
  else
    echo "No Runtime API key is stored. Re-run interactively or pass --runtime-key-stdin." >&2
    exit 2
  fi

  if [[ ! "${runtime_key}" =~ ^sk- ]]; then
    echo "Runtime API key must start with sk-." >&2
    exit 2
  fi

  secret_dir="$(dirname "${secret_file}")"
  umask 077
  mkdir -p "${secret_dir}"
  chmod 700 "${secret_dir}"
  temporary_secret_file="${secret_file}.tmp.$$"
  printf '%s' "${runtime_key}" > "${temporary_secret_file}"
  chmod 600 "${temporary_secret_file}"
  mv -f "${temporary_secret_file}" "${secret_file}"
  chmod 600 "${secret_file}"
  runtime_key=""
fi

echo "Connecting managed runtime '${runtime_alias}' to ${tunnel_id}..."
"${tunnel_client_bin}" runtimes connect \
  --alias "${runtime_alias}" \
  --profile "${runtime_profile}" \
  --tunnel-id "${tunnel_id}" \
  --runtime-api-key "file:${secret_file}" \
  --mcp-command "${mcp_launcher}" \
  --tunnel-client-bin "${tunnel_client_bin}" \
  --description "Knowledge Planet sidebar history and idempotent import" \
  --json

echo "Managed runtime status:"
"${tunnel_client_bin}" runtimes status "${runtime_alias}" --json

echo "Plugin directory: ${plugin_dir}"
