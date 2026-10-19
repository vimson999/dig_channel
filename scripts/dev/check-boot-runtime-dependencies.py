#!/usr/bin/env python3
"""Fail closed until the private runtime route is safe after a host boot."""

from __future__ import annotations

import argparse
import ipaddress
import json
import math
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import psycopg2
import redis
from dotenv import dotenv_values


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROXY_TUN_ADDRESS = "198.18.0.1"
ROUTE_INTERFACE = re.compile(r"^\s*interface:\s*(?P<value>\S+)", re.MULTILINE)


def validate_tailscale_status(payload):
    errors = []
    state = str(payload.get("BackendState") or "unknown")
    if state != "Running":
        errors.append(f"Tailscale backend is {state}, expected Running")
    if not payload.get("TailscaleIPs"):
        errors.append("Tailscale has no assigned IP")
    if not (payload.get("Self") or {}).get("Online"):
        errors.append("Tailscale self node is offline")
    return errors


def validate_private_route(output):
    if PROXY_TUN_ADDRESS in str(output or ""):
        return [
            f"private service route is using proxy TUN address {PROXY_TUN_ADDRESS}"
        ]
    match = ROUTE_INTERFACE.search(str(output or ""))
    if not match or not match.group("value").startswith("utun"):
        return ["private service route is not using a utun interface"]
    return []


def _runtime_endpoints(environ):
    values = dotenv_values(PROJECT_ROOT / ".env")
    merged = {**values, **environ}
    candidates = (
        ("PostgreSQL", merged.get("DIRECT_DATABASE_URL")),
        ("Redis cache", merged.get("REDIS_URL")),
        ("Celery broker", merged.get("CELERY_BROKER_URL")),
        ("Celery result backend", merged.get("CELERY_RESULT_BACKEND")),
    )
    endpoints = []
    seen = set()
    for label, raw_url in candidates:
        if not raw_url:
            continue
        parsed = urlparse(str(raw_url))
        if not parsed.hostname:
            continue
        port = parsed.port or (5432 if label == "PostgreSQL" else 6379)
        key = (parsed.hostname, port)
        if key in seen:
            continue
        seen.add(key)
        endpoints.append((label, str(raw_url), parsed.hostname, port))
    return endpoints


def _is_private_ip(host):
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address in ipaddress.ip_network("100.64.0.0/10")


def _run(command, timeout):
    return subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    ).stdout


def probe_runtime_url(label, raw_url, timeout):
    """Complete an authenticated, protocol-level readiness probe."""
    if label == "PostgreSQL":
        connection = psycopg2.connect(
            raw_url,
            connect_timeout=max(1, math.ceil(timeout)),
            application_name="dig_ytb_boot_guard",
        )
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
        finally:
            connection.close()
        return

    client = redis.Redis.from_url(
        raw_url,
        socket_connect_timeout=timeout,
        socket_timeout=timeout,
    )
    try:
        client.ping()
    finally:
        client.close()


def check_dependencies(*, environ=None, timeout=3.0):
    environ = os.environ if environ is None else environ
    errors = []
    try:
        status = json.loads(_run(["tailscale", "status", "--json"], timeout))
        errors.extend(validate_tailscale_status(status))
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        errors.append(f"Tailscale status unavailable: {type(exc).__name__}")

    endpoints = _runtime_endpoints(environ)
    if not endpoints:
        errors.append("no private PostgreSQL or Redis runtime endpoints configured")
        return errors

    routed_hosts = set()
    for label, raw_url, host, port in endpoints:
        if _is_private_ip(host) and host not in routed_hosts:
            routed_hosts.add(host)
            try:
                errors.extend(validate_private_route(_run(["route", "-n", "get", host], timeout)))
            except (OSError, subprocess.SubprocessError) as exc:
                errors.append(f"{label} private route unavailable: {type(exc).__name__}")
        try:
            probe_runtime_url(label, raw_url, timeout)
        except Exception as exc:
            errors.append(f"{label} endpoint unavailable: {type(exc).__name__}")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=3.0)
    args = parser.parse_args()
    errors = check_dependencies(timeout=max(0.1, args.timeout))
    if errors:
        print("BLOCKED: boot runtime dependencies are not safe:")
        for error in errors:
            print(f"  - {error}")
        return 3
    print("OK: Tailscale identity, private routes, PostgreSQL, and Redis are ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
