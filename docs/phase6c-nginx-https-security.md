# Phase 6C — host Nginx, HTTPS and production security

Date: 2026-11-01. Branch: `arena/bd5e1acd-chaoxing`, based on Phase 6B commit `99aa91b`. This is a source/configuration and portable-test delivery only. It does **not** certify a deployable production stack: Phase 6B's Docker/PostgreSQL acceptance is still outstanding, and no host Nginx, target Ubuntu machine, public DNS, firewall or certificate was accessed or changed.

## 1. Scope and reviewed architecture

This phase reviewed the Phase 6A/6B deployment notes; production/development/test Compose files and overlays; backend, edge and Worker Dockerfiles; production environment and ignore files; both Nginx layers; Flask origin/CSRF/session handling; the browser API client; private Worker routes and Compose networks; and the current admin/API routes.

The deployment topology remains the existing Phase 6A design. No new public container, database, Redis, gateway, Worker, or direct backend port is introduced:

```text
Internet (only after an authorized release)
       |
       v
Host Nginx :80 / :443
  - HTTP: ACME HTTP-01 path only during bootstrap; otherwise 503
  - HTTPS: redirect vhost + TLS termination + request limits + security headers
  - HSTS withheld by default; optional only after real HTTPS verification
       |
       | proxy to 127.0.0.1:8080; replace client-supplied forwarding headers
       v
Edge Nginx container (host-published port remains loopback-only)
  - Vue SPA/static cache; same-origin /api/ proxy
  - rejects /healthz and /api/internal/workers/* from public routing
       |
       +---- private Compose network ----> Flask/Gunicorn API
       |                                    |-- private DB network --> PostgreSQL
       |                                    `-- private per-platform networks --> opt-in Workers
       `---- no direct browser-to-backend, PostgreSQL, Redis or Worker listener

Worker --> private backend Worker API only; Worker profiles stay disabled.
Backend/Workers --X--> unrestricted Internet egress; no egress gateway is deployed.
```

The host template uses `default_server`, so it is for a dedicated listener/host. Inspect existing Nginx vhosts and default listeners before any authorized installation; do not overwrite another site's default configuration. The edge host mapping remains `127.0.0.1:${EDGE_PORT:-8080}`. PostgreSQL, backend and Workers have no published host ports. Redis is absent.

## 2. Nginx and HTTPS deliverables

Files under `unified_system/deploy/nginx/`:

- `unified-learning-http.conf.template`: temporary ACME HTTP-01 bootstrap; serves only `/.well-known/acme-challenge/`, returning 503 for all other HTTP paths. It is not an application-serving or redirect configuration.
- `unified-learning.conf.template`: parameterized dedicated-host configuration. HTTP serves ACME challenges and redirects other requests to HTTPS; HTTPS terminates TLS 1.2/1.3 and proxies to the loopback-only edge. It sets a 1 MiB body cap, timeouts, upstream error behavior, per-IP connection/request limits, access/error log paths and `X-Real-IP`/`X-Forwarded-*` values derived from the host connection. It rejects unexpected hosts and Worker-internal paths, and restricts readiness endpoints to loopback clients.
- `unified-learning-security-headers.conf`: HTTPS-host response headers and same-origin CSP; HSTS is intentionally separate.
- `render-nginx-config.py`: validates lowercase ASCII FQDN input and absolute certificate paths. HTTPS render validates that the chain matches the hostname, has at least 14 days remaining, matches the key, and that the private key is root-owned and unreadable by group/other. It requires root for those filesystem checks. OpenSSL diagnostics/private-key contents are not printed. HSTS is withheld unless the operator explicitly passes `--enable-hsts`.
- `check-certificate-expiry.sh`: operator-run validity/expiry check; defaults to a 30-day warning window.
- `certbot-deploy-hook.sh`: renewal deploy hook that runs `nginx -t` before `systemctl reload nginx`.
- `.env.nginx.example`: explicit `REPLACE_WITH_REGISTERED_LOWERCASE_FQDN` and certificate-path placeholders. It is neither an actual domain nor a certificate. A local `.env.nginx` is git-ignored; never put private-key contents in an environment file.

The previous `unified-learning.conf` pathname is obsolete. The templates depend on installing the host security snippet at `/etc/nginx/snippets/unified-learning-security-headers.conf`. Do not install any file from this phase until the target hostname is confirmed, the TLS certificate is trusted/valid, host Nginx conflicts are reviewed, the target is tested, and an authorized change window exists.

### Certificate lifecycle (operator procedure only; not run in this phase)

1. A domain owner selects and confirms a registered FQDN; verify DNS points only to the authorized host and port 80/443 exposure is approved. Replace all placeholder strings in a protected local copy of `.env.nginx.example`; set it to mode `0600`. Do not use a fabricated example domain as a live hostname.
2. During an approved bootstrap window, render/install the HTTP bootstrap vhost, run `nginx -t`, then reload only after review. It will serve only the ACME challenge webroot and return 503 elsewhere. The host must have the approved DNS and HTTP-01 reachability before a certificate request.
3. A credential/domain owner—not this Arena task—may then use the approved ACME account and Certbot webroot flow to obtain a certificate. No Certbot request or certificate issuance occurred here. Confirm the resulting chain and private-key ownership/mode; keep the private key root-only (normally `0600`).
4. Install `unified-learning-security-headers.conf` as root-owned mode `0644` at `/etc/nginx/snippets/unified-learning-security-headers.conf`. Render the HTTPS template with the real domain and certificate paths. The first render intentionally omits HSTS. Replace the bootstrap vhost during the approved change window (avoid two `default_server` listeners), run `nginx -t`, then reload only after a successful syntax test. Test redirects, certificate hostname/trust/expiry, and public HTTPS endpoints from an authorized external vantage point.
5. Only after those real HTTPS checks succeed, rerender with `--enable-hsts` (header `max-age=15552000`; no `includeSubDomains` or `preload`), run `nginx -t`, and reload. The Flask process and edge Nginx do not emit HSTS. Do not enable HSTS while the hostname/certificate is unknown or HTTPS is not confirmed.
6. On the target, verify the Certbot timer/renewal configuration, run the authorized `certbot renew --dry-run`, install `certbot-deploy-hook.sh` root-owned under `/etc/letsencrypt/renewal-hooks/deploy/`, and check that the hook's `nginx -t` succeeds before every reload. Periodically run `check-certificate-expiry.sh 30` against the actual fullchain and alert before renewal failure. Keep rollback copies of the last known-good vhost and certificate metadata; never log key material.

These are documented target-host operations, not evidence that Certbot, DNS, renewal, Nginx, or HTTPS was tested here.

## 3. Edge proxy and browser/API security

- Edge Nginx keeps the Vue SPA fallback, serves content-hashed `/assets/` files as immutable for one year, marks `/index.html` `no-store`, and applies the shared security-header snippet to static/API responses. Its request cap is 1 MiB. Host Nginx applies the same cap at public ingress and Flask `MAX_CONTENT_LENGTH` is also 1 MiB.
- Both Nginx layers deny `/api/internal/workers` and descendants; host Nginx also hides `/healthz`. The detailed readiness route is restricted to loopback on the host template. The API and PostgreSQL have no public host port. Admin APIs remain authenticated/permission-checked application routes, not an Nginx management listener.
- No application-path WebSocket usage was found, so no WebSocket upgrade proxy is configured. No document/file-upload endpoint or AI Provider endpoint is currently wired into the application. The 1 MiB cap is defense in depth, not an upload feature. If upload or AI functionality is added later, it needs backend authorization, content/type/size validation, scanning where appropriate, quotas and egress/SSRF review; Nginx rate limits do not replace business-level quota enforcement.
- Flask production session cookies are `Secure`, `HttpOnly`, `SameSite=Lax`, host-only (no `Domain`), with a 12-hour lifetime. CORS middleware is not enabled; the frontend uses a same-origin `/api/v1` path. Browser-facing session-cookie mutations use CSRF protection (Worker service calls use their separate private authentication). Login, registration and password-reset now reject a present cross-origin/malformed `Origin` before processing credentials/body. A missing `Origin` remains allowed for compatibility with non-browser clients; keep CSRF tokens and cookie settings enforced and test behavior through the real proxy before release.
- CSP is same-origin for scripts, styles, fonts and API connections, denies objects/framing, and permits inline style attributes needed by Vue/Element Plus; it does not permit inline scripts or remote fonts. This policy must be rechecked against the production frontend build before adding external assets.
- Host Nginx rate limits login (5/minute plus burst), registration and password reset (3/minute plus burst), general API (20 requests/second plus burst), and concurrent connections, keyed by the host-observed client address. Flask separately limits failed attempts by identifier/client fingerprint (5 failures per 15 minutes by default). These controls complement, but do not replace, provider quotas, task quotas or per-user business limits.
- Access log formats include method, path without query string, status, bytes and duration; they omit request bodies, cookies and authorization headers. The host overwrites incoming forwarding headers and uses `$remote_addr`; the edge only trusts forwarding headers received through the loopback-only host route. Error logs remain at `warn`; operators must still avoid placing secrets in URLs and must protect/rotate logs under the host's retention policy.

## 4. Admin RBAC, audit and confirmation review

The existing application uses permission-checked admin endpoints and explicit user/admin/superadmin role rules. Superadmin assignment/recovery is restricted to offline recovery paths; ordinary admin scopes are explicit. Password changes/reset, role changes, account disablement and session revocation invalidate user sessions/auth versions where implemented. Self-service users can revoke individual/other sessions.

Admin audit events are persisted for user status, role and session changes; admin scopes; reset-link issuance; invitation create/disable; shared-account create/update/reauthorize/verify; and grants/revocations. Audit metadata records counts, IDs, action types or role values, not raw invitation codes, platform credentials or reset tokens. Login attempts are recorded separately. Audit/log read access is permission-restricted.

Destructive or high-impact UI paths now show a second confirmation for user status/role/session/reset actions, admin-scope grants/revocations, invitation generation/disable, shared-account credential submission, grant/revoke and shutdown/disable actions. This confirmation is browser UI friction only: it is not server-enforced step-up authentication and does not protect against a compromised admin session or a direct API caller. The system has no administrator MFA/reauthentication step-up. Some self-service profile/session changes are not represented by a comprehensive immutable audit trail, and no centralized audit retention/alerting policy is configured. Before production, decide on admin MFA/step-up, audit retention/alerting, and whether the highest-risk mutations require server-verifiable reauthentication.

## 5. Network, egress and secrets

- Ingress intent: only the authorized host's public 80/443 listeners; the edge mapping remains loopback-only; no host listeners for PostgreSQL, Redis, backend or Workers. Host firewall state was not inspected or changed. The actual server's listening ports and existing vhosts remain unknown.
- Intra-stack: existing internal Compose networks preserve edge/backend, backend/database, and per-platform Worker boundaries. Worker profiles remain disabled. No public or browser route is added for Worker internal endpoints.
- Egress: no Gateway is deployed. `AI_HTTPS_PROXY` and `WORKER_*_HTTPS_PROXY` are empty configuration hooks, not gateways; the Compose handoff networks are private/internal, not an Internet egress path. Do not put an external provider or platform request behind those empty values. A future gateway needs an allowlist of exact host/port, controlled DNS resolution, private/link-local/loopback and rebinding defenses, redirect policy, TLS certificate verification, connection/read timeouts, rate/usage caps, and redacted request/response logs. The existing Phase 5 SSRF helper is not an admin-configurable provider service and does not implement this network gateway.
- Secrets remain outside frontend bundles and tracked env files. The only host TLS variable in `.env.nginx` is a file path; the private key bytes must remain in root-protected filesystem storage. Keep production env files `0600`, do not pass secret values on command lines, and do not write credentials, cookies, reset tokens or AI keys to logs/issues. Keep separate least-privilege database roles; the runtime DB role is not the migration owner or cluster admin.
- A historical AI API key existed in commit `08062a3`. This work did not inspect, print, reuse or copy it. Deleting current source does not remove the historical blob. The credential holder must confirm supplier-side revocation/rotation and review possible exposure; no replacement key belongs in this deployment. A release must remain blocked until that owner confirms revocation/rotation through the approved channel. Keep a documented rotation, suspected-leak containment and incident-response procedure.

## 6. Validation and evidence boundary

### Static/source and portable tests

The following were performed in the Arena checkout, without starting services or connecting to a target:

- Full backend suite, including this phase's auth/header/renderer tests: **125 passed, 2 skipped**. The skipped cases require PostgreSQL and remain untested.
- Frontend Vitest: **5 passed**; `npm run typecheck` and `VITE_API_BASE_URL=/api/v1 npm run build` passed.
- Python syntax compilation; `bash -n` for the expiry/deploy-hook scripts; and `git diff --check` passed.
- HTTP bootstrap template rendering with a reserved `.test` name, domain/path rejection tests, and HSTS-withheld/explicit-render unit tests passed. The HTTPS unit test mocks certificate validation; it does **not** validate a real certificate or perform an HTTPS handshake.
- `check-certificate-expiry.sh` passed a 30-day threshold and correctly rejected a 365-day threshold/zero-day input using a temporary 90-day self-signed certificate under `/tmp`. This was a local utility smoke test only; the certificate was not trusted, installed, committed or used as a production credential.

### Required layered acceptance (not completed here)

| Layer | Result | What remains |
|---|---|---|
| Static/source + portable tests | Local checks only | Does not prove Nginx grammar, Docker runtime or target behavior. |
| Host Nginx syntax/runtime | **Not tested**; Nginx binary unavailable | On an authorized Ubuntu clone/target, inspect vhosts and run renderer, `nginx -t`, staged reload/rollback and local health restrictions. |
| Container edge Nginx | **Not tested**; Docker/Compose unavailable | Build/start the edge image, inspect headers/cache/routes and ensure only the intended loopback host mapping. |
| Target Ubuntu | **Not tested**; no remote access | Confirm OS, packages, resource/port/firewall/DNS, file ownership/modes, systemd/Certbot and existing Nginx configuration. |
| Real public HTTPS | **Not tested**; no domain/certificate/DNS changes | Only after explicit authorization, verify certificate chain/name/expiry, redirects, headers, cookies, rate limits and renewal from an external vantage point. |
| PostgreSQL/Compose | **Not tested** in this phase; Phase 6B remains incomplete | Complete the disposable PostgreSQL locks/concurrency/migration/restart tests and Compose build/start/network checks in the Phase 6B report. |

At the Phase 6C cutoff, no public deployment, firewall/DNS/SSH change, production certificate request/renewal, server-side file installation, Docker build/start, Worker activation, AI/platform request or Phase 6D work had occurred. Phase 6D was subsequently implemented in code/docs; see [`phase6d-operations-recovery.md`](phase6d-operations-recovery.md). The later work did not perform any of the real-host actions listed here.

## 7. Security checklist and handoff

- [x] Preserve Phase 6A/6B topology; do not publish the backend/database/Worker directly.
- [x] Parameterized host HTTP/HTTPS templates; ACME-only bootstrap; HTTP-to-HTTPS redirect; TLS 1.2/1.3; certificate/key/domain/mode/expiry checks; validate-before-reload hook.
- [x] HSTS withheld from Flask and default host render; explicit opt-in only after public HTTPS checks.
- [x] Same-origin CSP/security headers; cookies, CSRF, no-CORS default and cross-origin auth-request rejection reviewed.
- [x] 1 MiB ingress/app limits; IP rate limits plus existing login-failure limiting; no upload/AI endpoint or WebSocket path found.
- [x] Worker internal routes and readiness constrained; secrets omitted from access logs; source historical key not inspected or reused.
- [x] Admin RBAC/audit and UI confirmation paths reviewed; documented missing server-side step-up/MFA and audit-retention work.
- [ ] Confirm supplier-side revocation/rotation of the historical AI credential with its owner.
- [ ] Finish Phase 6B Docker/PostgreSQL acceptance; perform host/edge Nginx and target Ubuntu validation.
- [ ] Obtain explicit approval for a real hostname, DNS/firewall exposure, certificate issuance and staged public HTTPS verification.
- [ ] Implement a real constrained egress gateway before any external AI/Worker calls; do not mistake empty proxy variables for one.

Phase 6C itself ends at code/config/docs and portable tests. Phase 6D's subsequent implementation is documented separately and does not close the outstanding gates above. Do not publish or alter the target server, request a production certificate, or enable a Worker on the basis of either phase's portable tests. Continue deployment only after the target-host, PostgreSQL and security gates have authorized, recorded evidence.
