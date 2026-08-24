#!/usr/bin/env python3
"""Ensure the loopback-only Knowledge Planet sidebar bridge is healthy."""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HEALTH_URL = "http://127.0.0.1:8888/zsxq-sidebar-bridge.html"


def _healthy(url: str, timeout_seconds: float = 2.0) -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8", errors="replace")
            return response.status == 200 and "服务状态：healthy" in body
    except (OSError, urllib.error.URLError):
        return False


def _endpoint_accepts_connections(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--health-url", default=DEFAULT_HEALTH_URL)
    parser.add_argument("--python", default=str(PROJECT_ROOT / "venv" / "bin" / "python"))
    parser.add_argument(
        "--server-script",
        default=str(PROJECT_ROOT / "src" / "frontend" / "server.py"),
    )
    parser.add_argument(
        "--server-cwd",
        default=str(PROJECT_ROOT / "src" / "frontend"),
    )
    parser.add_argument(
        "--pid-file",
        default=str(PROJECT_ROOT / "runtime" / "zsxq-sidebar-bridge.pid"),
    )
    parser.add_argument(
        "--log-file",
        default=str(PROJECT_ROOT / "logs" / "zsxq-sidebar-bridge.log"),
    )
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    if _healthy(args.health_url):
        print("healthy: existing bridge")
        return 0

    if _endpoint_accepts_connections(args.health_url):
        print(
            "unhealthy: target port is occupied; refusing to replace an unknown listener",
            file=sys.stderr,
        )
        return 3

    python_path = Path(args.python).resolve()
    server_script = Path(args.server_script).resolve()
    server_cwd = Path(args.server_cwd).resolve()
    pid_file = Path(args.pid_file).resolve()
    log_file = Path(args.log_file).resolve()
    if not python_path.is_file() or not server_script.is_file() or not server_cwd.is_dir():
        print("unhealthy: bridge launch inputs are missing", file=sys.stderr)
        return 4

    pid_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("ab", buffering=0) as log_handle:
        process = subprocess.Popen(
            [str(python_path), str(server_script)],
            cwd=str(server_cwd),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )

    deadline = time.monotonic() + max(0.1, args.timeout_seconds)
    while time.monotonic() < deadline:
        if _healthy(args.health_url, timeout_seconds=0.5):
            pid_file.write_text(f"{process.pid}\n", encoding="utf-8")
            print(f"started: bridge pid={process.pid}")
            return 0
        if process.poll() is not None:
            print(
                f"unhealthy: bridge process exited with code {process.returncode}",
                file=sys.stderr,
            )
            return 5
        time.sleep(0.1)

    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)
    print("unhealthy: bridge did not become healthy before timeout", file=sys.stderr)
    return 6


if __name__ == "__main__":
    raise SystemExit(main())
