#!/usr/bin/env python3
import hashlib
import os
import re
from pathlib import Path
from urllib.parse import quote, unquote, urlparse, urlunparse


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"
RUNTIME_DIR = PROJECT_ROOT / "runtime" / "pgbouncer"
CONFIG_PATH = RUNTIME_DIR / "pgbouncer.ini"
USERLIST_PATH = RUNTIME_DIR / "userlist.txt"
LOG_PATH = PROJECT_ROOT / "logs" / "pgbouncer.log"
PID_PATH = RUNTIME_DIR / "pgbouncer.pid"

LISTEN_HOST = os.environ.get("LOCAL_PGBOUNCER_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("LOCAL_PGBOUNCER_PORT", "6432"))


def main():
    env = _read_env(ENV_PATH)
    direct_url = env.get("DIRECT_DATABASE_URL") or env.get("DATABASE_URL")
    if not direct_url:
        raise SystemExit(".env 缺少 DATABASE_URL")

    target = _parse_postgres_url(direct_url)
    pgbouncer_url = _pgbouncer_url(direct_url)

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(_config_text(target), encoding="utf-8")
    USERLIST_PATH.write_text(_userlist_text(target), encoding="utf-8")
    os.chmod(CONFIG_PATH, 0o600)
    os.chmod(USERLIST_PATH, 0o600)
    _update_env(ENV_PATH, direct_url=direct_url, pgbouncer_url=pgbouncer_url)

    print("PgBouncer local config generated")
    print(f"  listen: {LISTEN_HOST}:{LISTEN_PORT}")
    print(f"  target: {target['host']}:{target['port']}/{target['dbname']} user={target['user']}")
    print(f"  config: {CONFIG_PATH}")
    print(f"  env DATABASE_URL -> {LISTEN_HOST}:{LISTEN_PORT}/{target['dbname']}")


def _read_env(path):
    env = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = _strip_quotes(value.strip())
    return env


def _strip_quotes(value):
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _parse_postgres_url(url):
    parsed = urlparse(url)
    if not parsed.scheme.startswith("postgres"):
        raise SystemExit(f"DATABASE_URL 不是 PostgreSQL: {parsed.scheme}")
    user = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    dbname = parsed.path.lstrip("/")
    if not user or not password or not parsed.hostname or not dbname:
        raise SystemExit("DATABASE_URL 必须包含 host/user/password/dbname")
    return {
        "scheme": parsed.scheme,
        "host": parsed.hostname,
        "port": parsed.port or 5432,
        "user": user,
        "password": password,
        "dbname": dbname,
    }


def _pgbouncer_url(url):
    parsed = urlparse(url)
    user = quote(unquote(parsed.username or ""), safe="")
    password = parsed.password
    auth = user
    if password is not None:
        auth += ":" + quote(unquote(password), safe="")
    auth += "@"
    netloc = f"{auth}{LISTEN_HOST}:{LISTEN_PORT}"
    return urlunparse((parsed.scheme, netloc, parsed.path, "", parsed.query, ""))


def _config_text(target):
    db_line = " ".join([
        f"host={_conninfo(target['host'])}",
        f"port={target['port']}",
        f"dbname={_conninfo(target['dbname'])}",
        f"user={_conninfo(target['user'])}",
        f"password={_conninfo(target['password'])}",
    ])
    return f"""[databases]
{target['dbname']} = {db_line}

[pgbouncer]
listen_addr = {LISTEN_HOST}
listen_port = {LISTEN_PORT}
auth_type = md5
auth_file = {USERLIST_PATH}

pool_mode = transaction
max_client_conn = 300
default_pool_size = 15
min_pool_size = 2
reserve_pool_size = 5
reserve_pool_timeout = 5

server_reset_query = DISCARD ALL
server_connect_timeout = 10
server_idle_timeout = 120
server_lifetime = 3600
query_timeout = 30
query_wait_timeout = 30
client_idle_timeout = 300
tcp_keepalive = 1
tcp_keepcnt = 3
tcp_keepidle = 30
tcp_keepintvl = 10
ignore_startup_parameters = extra_float_digits, options

pidfile = {PID_PATH}
logfile = {LOG_PATH}
admin_users = {target['user']}
stats_users = {target['user']}
"""


def _conninfo(value):
    text = str(value)
    if re.fullmatch(r"[A-Za-z0-9_./:@-]+", text):
        return text
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _userlist_text(target):
    digest = hashlib.md5((target["password"] + target["user"]).encode("utf-8")).hexdigest()
    return f'"{target["user"]}" "md5{digest}"\n'


def _update_env(path, *, direct_url, pgbouncer_url):
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.exists() else []
    has_direct = False
    has_database = False
    updated = []
    for raw in lines:
        if raw.startswith("DIRECT_DATABASE_URL="):
            has_direct = True
            updated.append(raw)
            continue
        if raw.startswith("DATABASE_URL="):
            has_database = True
            if not has_direct:
                updated.append(f"DIRECT_DATABASE_URL={direct_url}")
                has_direct = True
            updated.append(f"DATABASE_URL={pgbouncer_url}")
            continue
        updated.append(raw)

    if not has_database:
        updated.append(f"DIRECT_DATABASE_URL={direct_url}")
        updated.append(f"DATABASE_URL={pgbouncer_url}")
    elif not has_direct:
        updated.append(f"DIRECT_DATABASE_URL={direct_url}")

    text = "\n".join(updated).rstrip() + "\n"
    path.write_text(text, encoding="utf-8")
    os.chmod(path, 0o600)


if __name__ == "__main__":
    main()
