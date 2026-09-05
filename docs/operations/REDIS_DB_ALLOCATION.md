# Redis Logical Database Allocation

Status: active infrastructure contract

Effective date: 2026-09-01

Redis host: DIG production server, accessed through the Tailscale/private path

Canonical repository path: `docs/operations/REDIS_DB_ALLOCATION.md`

This document defines ownership of the shared Redis instance used by DIG and
BOT. It contains no password, ACL token, or complete connection URL.

## Allocation

The Redis server is configured with 16 logical databases, numbered `0` through
`15`. Database numbers `16` and above are invalid and must not be used.

| Redis DBs | Owner and environment | Cache/application state | Celery broker | Celery result backend | Current state |
| --- | --- | --- | --- | --- | --- |
| `DB0` | Infrastructure reserve | - | - | - | Reserved; do not allocate |
| `DB1-3` | DIG production | `DB1` | `DB2` | `DB3` | Active on CVM |
| `DB4-6` | DIG development, Mac mini | `DB4` | `DB5` | `DB6` | Active |
| `DB7-9` | DIG development, Mac Pro | `DB7` | `DB8` | `DB9` | Allocated; client cutover pending |
| `DB10-12` | BOT production | `DB10` | `DB11` | `DB12` | Reserved; production service not active |
| `DB13-15` | BOT development, Mac mini | `DB13` | `DB14` | `DB15` | Active |

## Three-database contract

Every environment owns exactly three adjacent logical databases:

1. First database: `REDIS_URL` and `CACHE_REDIS_URL` when present.
2. Second database: `CELERY_BROKER_URL`.
3. Third database: `CELERY_RESULT_BACKEND`.

Producers and consumers for one environment must use the same broker and result
backend. Different projects or development machines must never share a Celery
broker database, even when queue names currently differ.

## Change rules

1. Update this document before assigning or moving a Redis database.
2. Back up the Redis RDB and affected environment files before a cutover.
3. Stop schedulers and producers before moving queued broker data.
4. Change cache, broker, and result URLs as one operation, then restart both the
   API and every worker for that environment.
5. Verify a Redis `PING`, a real Celery round trip, API health, and the Redis
   client database numbers before deleting old keys.
6. Keep credentials only in protected environment/secret files. Never add them
   to this document or Git.
7. Do not increase Redis's `databases` setting merely to avoid defining
   ownership. Revisit the architecture if all five three-database groups become
   active and another isolated environment is required.

## Operational checks

These checks must not print credentials:

```bash
redis-cli --user <acl-user> --askpass INFO keyspace
redis-cli --user <acl-user> --askpass CLIENT LIST
```

For `CLIENT LIST`, group the `db=` values and confirm that each process connects
only to its allocated databases. Empty result-backend databases are normal when
no task result is currently retained.

## Rollback point

The migration rollback RDB created before this allocation was applied is stored
on the production server under:

```text
/srv/migration-backups/redis-layout/redis_pre_layout_20260901_103549.rdb
```

Restoring it is an incident action: stop all Redis clients first and preserve
the current RDB before rollback.

## Mirrors

- Operations workspace: `deploy/REDIS_DB_ALLOCATION.md`
- Production server: `/srv/ops/REDIS_DB_ALLOCATION.md`

The repository copy is authoritative. Mirrors are for operational discovery and
must be refreshed after the repository document changes.
