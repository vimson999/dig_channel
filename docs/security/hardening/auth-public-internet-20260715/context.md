# Public Internet Authentication Hardening — Evidence Context

This is a derived, revisable security design context. It does not prove that any
finding has been remediated.

## Target

- Source root: `/Users/v9/Documents/3---work/06---dev---python/dig_ytb/dig_ytb_channel`
- Git revision: `d7de7a023f7572e499d1996abe1b0323db598c5a`
- Source drift: present (the worktree contained pre-existing user changes plus the
  client-side page guard changes made immediately before this review)
- Evidence collection digest: `3403f761f04590b61797223ef199bade75084740541dd8ed14f79e9545e6bde9`
- Review date: 2026-07-15
- Scope: authentication, authorization, public deployment boundary, bootstrap
  credentials, secret handling, and security logging

## Evidence Registry

| Evidence | Title | Kind | What it establishes |
| --- | --- | --- | --- |
| `E001` | Tracked production secrets | Source and Git history | `.env.bak` and `scripts/deployment/production.env` are tracked and contain non-placeholder secrets or credentials. They must be treated as disclosed and rotated; removing only the current files is insufficient. |
| `E002` | Known default administrator credential | Source and runtime inspection | `init_db.py` creates a fixed administrator credential and prints it. A read-only database check confirmed the active `admin` account still matched that known default password. The password value is intentionally omitted here. |
| `E003` | Client-only page guard | Source | Nginx serves HTML directly, while the current guard runs in browser JavaScript. A client can skip JavaScript or call APIs directly. |
| `E004` | Dispersed and missing API authorization | Static route inventory and source | Static inspection found 371 route handlers; 225 lacked a known authentication decorator, including platform-credential CRUD and worker/process controls. No Blueprint-wide authentication hook compensates for this. |
| `E005` | DEBUG administrator fallback | Source and environment inspection | The active environment selects development/DEBUG behavior, and `require_login` substitutes an administrator for anonymous requests when DEBUG is enabled. |
| `E006` | Login abuse controls absent | Source | Login has no active rate limiter, account/IP failure counters, temporary lockout, CAPTCHA escalation, or MFA. Public self-registration remains enabled and passwords can be six characters. |
| `E007` | Browser bearer-token exposure and weak revocation | Source | JWTs are placed in `localStorage`; password changes and logout do not revoke previously issued bearer tokens. Remember-me tokens last up to seven days. |
| `E008` | Origin and Redis bypass exposure | Deployment source | Docker publishes backend port 5000 and unauthenticated Redis port 6379 on the host, allowing traffic to bypass Nginx controls if host networking permits it. |
| `E009` | Security log stream is not effective | Source and local inspection | A `security` file logger is configured, but authentication uses a separate `auth` logger. Invalid credentials return without a failure audit event. The local security log was empty and world-readable by default. |
| `E010` | Untrusted proxy identity and permissive cross-origin policy | Source | Application code trusts arbitrary `X-Forwarded-For`; CORS is hard-coded to `*` with credentials, and secure session-cookie attributes are not explicitly enforced. |

## Principal Source Files

| File | SHA-256 |
| --- | --- |
| `src/backend/app.py` | `459ce27b4af568a97548d92a3f1dffb9676803829628ae2a9c6a72975ac72288` |
| `src/backend/config/base.py` | `786b73cd6cbf720cdb6cac3aeb72f4905ee3f1aea1f9c57eb104dee2b1cd9913` |
| `src/backend/config/development.py` | `16865d1921f4fe4474031715620186835cca78bd7b550e0a9446b84923276f80` |
| `src/backend/apis/v1/auth.py` | `d7e70014224e7922c7821c7fe143f5d9e3dd362111ab52f4f5be077ba0345f45` |
| `src/backend/utils/auth_utils.py` | `b987411357127fbd8deee1e9b61957cd44657c52f96bcb9ace38eb1605c170c1` |
| `src/backend/models/user.py` | `cdacbb8c1c3f2f5a5a2ad6e2cc90b01eb29d802c9997b1f3e2e48f2475f498fc` |
| `src/backend/init_db.py` | `91adb0b8d169cc3e3b19ef4462dd7f98bc01538555c42930aa8c157cdc5dd707` |
| `scripts/reset_admin_password.py` | `e1ada414dcc3ede23b16e76e304f53664a34f64ad443b525ea97e3c4d21fbe7c` |
| `src/backend/middleware/request_logger.py` | `4620006fd065c26821087579bef1afae8738eab75c17c834459e217b804f23ec` |
| `src/backend/utils/log_manager.py` | `a81c9be3e4dec893ce5ff062c9045dee066508b1031d3023c5b610072fc246fd` |
| `src/frontend/js/shared-navigation.js` | `163413ff4d3e424257eb428e317a26217956a72f691fdb25992484079f76cd35` |
| `src/frontend/login.html` | `0f5a33f52ab90687a98a243226289191536af0171647b57fecf09694cf6f0b0a` |
| `scripts/deployment/nginx/youtube-analytics.conf` | `e54744419cc9f61f3b88fb7a74afea77cc004343bb22c17c581a960df172323a` |
| `scripts/deployment/docker/nginx.conf` | `84656987352932f3bc31b8459fbc93bd029e9349e6c646fb665e6e0953bb482d` |
| `scripts/deployment/scripts/deploy.sh` | `a6f9f805e60037d55b3e937055292be1cbd32086d52ee9a90a5347c8a4d71d44` |
| `scripts/deployment/scripts/deploy-traditional.sh` | `334355c8f213afe2c44d593730dc93840ed7480e3aa3d804b6820c582afbdb24` |
| `requirements/production.txt` | `a6b640adee38a4bace977c860159f1c9f58a028aeb4c6330e3e2eb6fd17e31bd` |

## External Design References

- OWASP Authentication Cheat Sheet: login throttling, generic responses, MFA,
  and authentication monitoring.
- OWASP Session Management Cheat Sheet: do not store credentials in Web Storage;
  prefer `Secure`, `HttpOnly`, `SameSite` cookies.
- OWASP Logging Cheat Sheet: authentication successes and failures are mandatory
  security events; secrets and session identifiers must not be logged.
- Nginx `auth_request` documentation: a reverse proxy can authorize static-page
  requests through an application subrequest.
- Flask-Limiter documentation: production rate-limit state should use shared
  storage rather than per-process memory.

## Limitations

- This was a targeted hardening review, not an exhaustive repository security scan.
- No public production host was tested.
- Current external-provider secret validity was not tested; tracked secrets are
  treated as compromised regardless of whether they remain active.
- No performance measurements were supplied or run.
