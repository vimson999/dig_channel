# Security Hardening Review: Public Internet Access

## Evidence Basis

I inspected the current authentication, route registration, deployment, credential
bootstrap, and logging boundaries at revision
`d7de7a023f7572e499d1996abe1b0323db598c5a`. The worktree has drift, so the exact
reviewed files are bound by the evidence digest in [context.md](context.md).

The evidence supports three structural opportunities. Most importantly, tracked
secrets and a still-active known default administrator password make public launch
unsafe today. The browser redirect added earlier improves user experience but is
not an authorization boundary.

## Constraints

- The application is intended to remain reachable from the public Internet.
- All business pages and APIs must require authentication.
- Automated local `file://` rendering must continue to work without becoming a
  remotely reachable bypass.
- We assume a balanced delivery profile: immediate containment first, followed by
  incremental application and deployment changes.
- Passwords, tokens, cookies, and provider secrets must never be written to source,
  normal logs, shell arguments, or this design artifact.

## Opportunity Portfolio

| Opportunity | Evidence | Options | Recommendation | Proposal |
| --- | --- | --- | --- | --- |
| Contain exposed credentials and bootstrap authority | Tracked secrets and known default administrator (`E001`, `E002`) | Tactical rotation; managed secret lifecycle | Managed lifecycle, beginning with immediate rotation | [Proposal](proposals/contain-exposed-secrets.md) |
| Centralize public authentication enforcement | Client-only guard, 225 undecorated routes, DEBUG bypass, exposed origin (`E003`–`E008`, `E010`) | Application/Nginx gate; identity-aware edge plus application gate | Identity-aware edge plus application default-deny | [Proposal](proposals/centralize-authentication-enforcement.md) |
| Establish actionable security telemetry | Missing auth failure events and untrusted client IP (`E009`, `E010`) | Structured local audit; centralized append-only monitoring | Centralized monitoring after local events are corrected | [Proposal](proposals/establish-security-telemetry.md) |

## Recommendation Summary

I recommend the combined managed option for all three opportunities. We should
first rotate every tracked credential and the administrator password, then clean
Git history and prevent recurrence. We should place an identity-aware proxy with
MFA in front of the origin while also making Flask authorization default-deny and
using an `HttpOnly` server-side session. Finally, we should emit structured security
events locally and forward them to an append-only store with alerts.

The hidden login path can remain as defense in depth: an unrecognized visitor sees
a generic 404 and the login API requires a short-lived pre-auth cookie issued only
by the secret portal. It must not be counted as the brute-force control. Independent
IP and account limits, progressive temporary lockout, MFA, origin isolation, and
alerts carry that security property.

## Next Decisions

1. Approve or reject the recommended combination:
   `managed-secrets + edge-and-app-gate + centralized-audit`.
2. Select the identity-aware edge provider (for example Cloudflare Access,
   Authentik, or Authelia) and MFA method.
3. Select credential delivery: recommended is a one-time bootstrap value written
   to a local mode-`0600` file or directly into a password manager, never chat.
4. Identify which of the two non-admin database accounts should remain active.

No production credential, database user, or external provider was mutated during
this review.
