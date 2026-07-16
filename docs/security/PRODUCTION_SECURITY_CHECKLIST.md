# Production security checklist

Detailed Chinese release gate and executable verification steps: [`上线前必须完成.md`](./上线前必须完成.md).

## Before first public deployment

- Copy `.env.production.example` to an untracked server-side `.env.production`.
- Copy `SECRET_KEY`, `JWT_SECRET_KEY`, and `LOGIN_PORTAL_TOKEN` from the protected authentication bundle; never put them in Git or deployment logs.
- Set `CORS_ORIGINS` to the single real HTTPS origin and replace the example Nginx hostname/certificate paths.
- As the PostgreSQL table owner, apply the repository migrations and verify `users.auth_version` exists. Do not grant the web application DDL/owner privileges.
- Rotate every credential that was ever stored in `.env.bak` or `scripts/deployment/production.env` (database, YouTube/Google, Bilibili, Reddit, session/application secrets). Deleting the files does not invalidate exposed values.
- Remove those files from Git history in a coordinated maintenance window, then require every collaborator and deployment checkout to re-clone. Do not rewrite shared history ad hoc.
- Keep backend and Redis on private container/loopback networks. Expose only Nginx ports 80/443.
- Test the private login URL, direct `/login.html` returning 404, unauthenticated page/API access, logout, password rotation, and rate limiting.
- Ensure any CDN, WAF, load balancer, and external access-log pipeline also suppresses or redacts `/access/*`; the bundled Nginx and Flask logs already do so.
- Back up the database and verify restore before switching DNS.

## Operating cadence

- Daily: alert on repeated `auth.login` failures, `request.rate_limited`, unexpected 401/403 bursts, 5xx errors, disk pressure, and service restarts.
- Weekly: review `logs/security.log`, Nginx access/error logs, dependency advisories, privileged account activity, and exposed ports.
- Monthly: patch the OS/runtime/dependencies, restore-test backups, review active accounts and secrets, and run a repository security scan.
- Quarterly: rotate administrator, application, database, and provider credentials; rehearse recovery and review firewall/TLS settings.

The opaque login path reduces automated scanning noise; it is defense in depth, not a substitute for a strong password, TLS, rate limits, private backend networking, patching, and monitoring. MFA or an identity-aware proxy is the recommended next security layer for a public service.
