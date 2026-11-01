# Phase 6A — production deployment preparation (no public release)

Date: 2026-11-01. Active branch: `arena/bd5e1acd-chaoxing`. This document describes a repeatable deployment candidate, not a completed production deployment. The target Ubuntu server was not accessed; no SSH credentials were provided or used. The Compose stack was not started and no public listener was created.

## 1. Existing project inventory and integration boundary

- Current branch initially contained the unified Flask API/models/Alembic migrations, Vue console, and generic Worker runtime under `unified_system/`. Existing `unified_system/docker-compose.dev.yml` is a **development-only** PostgreSQL + Flask + Vite stack that runs `flask init-db`; it is not a production migration workflow.
- Root `Dockerfile`, `Dockerfile.panel`, and `docker-compose.panel.yml` belong to the legacy Chaoxing application/panel. They were not reused for the unified system.
- Before this phase, the unified system had no production backend/frontend Dockerfiles, production Compose file, or Nginx template. It had a generic Worker Dockerfile, a dev-only env example and migrations through Alembic revision `0004_account_verification`.
- Phase 5 supplies only a safe, synchronous OpenAI-compatible provider contract. It has no provider admin/model/usage/conversation API, AI key rotation, or front-end assistant. The production Compose template intentionally does not accept or publish an AI Provider key.
- Database-backed task state is already designed around PostgreSQL row locks and `FOR UPDATE SKIP LOCKED`; SQLite tests do not validate that production behavior. No Worker platform adapter is currently verified/registered. The three Compose Worker profiles are opt-in and must remain disabled.
- The linked Yinghua commit is described in [`phase6a-yinghua-go-audit.md`](phase6a-yinghua-go-audit.md). It is not integrated or safe to run: no `go.mod`, no Go toolchain here, and source behavior includes study-progress writes and third-party CAPTCHA recognition.

## 2. Architecture prepared

```text
Internet
   |
   +--> Host Nginx (future TLS boundary; only after domain/certificate/HTTPS checks)
          |      `-- :80 ACME-only bootstrap, then HTTP redirect + HTTPS :443
          |          (host template has HSTS disabled by default)
          +--> 127.0.0.1:8080 only
                 |
                 +--> Edge Nginx container (static Vue build; rejects Worker paths)
                         |
                         +--> Flask/Gunicorn backend (private Compose networks)
                                 |-- private database network --> PostgreSQL 16 + persistent volume
                                 |-- per-platform private networks --> optional isolated Worker services
                                 `-- no general Internet egress by default

Optional Worker profile --> private backend network only (external platform egress is intentionally not configured)

Redis: deliberately omitted. The persistent PostgreSQL task queue remains authoritative.
```

### Network and service policy

- Only the edge container has a host port mapping, and it is explicitly bound to `127.0.0.1:${EDGE_PORT:-8080}`. Host Nginx is the future HTTPS/public boundary; it has **not** been installed or enabled here.
- PostgreSQL, backend, and Workers have no published host ports. PostgreSQL is attached only to an internal database network. No Redis service is defined.
- Separate internal networks are prepared for browser-edge/backend, backend/database, and one backend/Worker network per platform. Workers cannot directly reach another platform's Worker container and never share the database network. No general egress bridge is attached to the backend or Workers; services have no default Internet egress. A reviewed, destination-restricted egress policy must be added before any verified platform adapter or external AI Provider is activated.
- Both host Nginx and edge Nginx return 404 for `/api/internal/workers` and `/api/internal/workers/*`. Workers connect directly to the backend service name over the private Compose network, never through Nginx.
- Each platform Worker is a separate Compose service/profile and can be restarted independently once a verified adapter exists. All profiles are disabled by default; starting one now fails closed because no approved adapter factory/token is configured.
- CPU/memory limits, process limits, restart policies, non-root backend/edge/Worker users, read-only container roots, `no-new-privileges`, tmpfs scratch paths and bounded Docker JSON logs are configured. Worker images include an honest healthcheck based on a fresh successful private-backend heartbeat; persisted Worker state remains authoritative.

## 3. Deployment files

- `unified_system/Dockerfile` — multi-stage Python 3.12 backend image, exact Python package version lock, non-root Gunicorn runtime and DB-aware readiness probe.
- `unified_system/frontend/Dockerfile` — multi-stage Vue build and unprivileged Nginx static/API edge image. Public API base path is a non-secret `VITE_API_BASE_URL` build arg, defaults to same-origin `/api/v1`, and is supplied by the production env template; no API credential enters the frontend build.
- `unified_system/workers/Dockerfile` — multi-stage Python 3.12 generic Worker runtime, non-root and fail-closed adapter factory.
- `unified_system/docker-compose.prod.yml` — production service/network/volume/resource/restart/logging profile definitions.
- `unified_system/frontend/nginx/nginx.conf` — edge static serving, SPA fallback, API proxy, request-size limits and internal Worker-route denial.
- `unified_system/deploy/nginx/unified-learning.conf.template` and `unified-learning-http.conf.template` — parameterized HTTPS and ACME-only HTTP bootstrap host templates. Render with the validator; HSTS is withheld unless explicitly enabled after real HTTPS validation. See [`phase6c-nginx-https-security.md`](phase6c-nginx-https-security.md).
- `unified_system/deploy/nginx/render-nginx-config.py` — FQDN/path validation and HTTPS certificate/key/expiry/mode checks; requires root for HTTPS rendering.
- `unified_system/deploy/nginx/unified-learning-security-headers.conf` — host HTTPS security-header snippet, CSP included, HSTS deliberately excluded.
- `unified_system/deploy/nginx/check-certificate-expiry.sh` and `certbot-deploy-hook.sh` — operator-run validity check and validate-before-reload renewal hook.
- `unified_system/deploy/nginx/.env.nginx.example` — explicit non-domain placeholders for host renderer configuration; not a certificate or a live hostname.
- `unified_system/deploy/nginx/unified-learning-logrotate` — host Nginx log rotation template.
- `unified_system/deploy/.env.production.example` — secret-free production environment template. Copy locally to `.env.production`; never commit it.
- `unified_system/postgres/init/10-create-app-db.sh` — first-volume initialization script that creates a cluster administrator, a separate migration owner, and a restricted runtime DML role. Role names are SQL-identifier-validated; passwords must be hex so they are URL-safe in the SQLAlchemy DSN. The script reads psql variables from process environment via `\getenv`; it does not echo password values.
- `unified_system/deploy/server-preflight.sh` — non-mutating target-host inventory report.
- `unified_system/deploy/backup-postgres.sh`, `restore-postgres.sh` — encrypted `age` backup and explicitly confirmed destructive restore.
- `unified_system/deploy/systemd/unified-learning-backup.{service,timer}` — daily backup schedule template (installation path must be adjusted if the checkout is not `%h/chaoxing`).
- `unified_system/deploy/health-report.sh` — Compose, edge/backend readiness, disk and resource snapshot.
- `unified_system/deploy/test-postgresql.sh` — guarded migration and full runtime test runner for two disposable PostgreSQL databases whose names end in `_test` or `_testdb`.
- `unified_system/requirements-prod.in` records exact approved Python package versions; `requirements-prod.lock` is the hash-checked, `pip-compile` output used by production images. `requirements-test.txt` adds test tooling. Frontend uses `npm ci` with the checked-in package lock. The runtime lock was generated from the Arena Python 3.11 environment and still requires install verification on the target Python 3.12 image.
- `unified_system/.dockerignore`, `unified_system/frontend/.dockerignore`, and `unified_system/deploy/.gitignore` exclude local data, build output, environment files, secrets and backups.

The image tags in the examples (`postgres:16-alpine`, `python:3.12-slim-bookworm`, `node:20-alpine`, `nginx:alpine`) are buildable defaults, **not immutable image pins**. Before a production release, resolve and review registry digests for the target architecture, replace the tags with `name@sha256:...`, and run a vulnerability review. The Arena environment could not access a Docker daemon or verify registry metadata.

## 4. Target server checks and environment distinction

### Arena sandbox observed during this phase

- Debian GNU/Linux 12 (Bookworm), x86_64; kernel `6.1.158+`; Python `3.11.2`.
- 2 online CPUs, about 3.8 GiB total memory, root filesystem about 21 GiB with about 19 GiB free at check time.
- Docker CLI/daemon, Compose plugin, Nginx and local PostgreSQL tools/server were unavailable. No PostgreSQL listener was found.
- Current sandbox TCP listeners included ports `8088` and `5173` (likely existing development/preview listeners; process ownership was not verified), plus system ports. No deployment process was started by Phase 6A.
- Current Linux test result is not an Ubuntu test.

### User-provided target facts (not independently verified)

The requested target is Linux/Ubuntu, kernel `6.8.0`, user directory `/home/agentuser`, host Python `3.12.3`. The exact Ubuntu release, architecture, resources, Docker/Compose versions, network/DNS, listening ports, Docker-group permissions, Nginx/domain/certificate state remain unknown. No SSH or remote execution authority was available.

Run the non-mutating report **on the target**, not in Arena. Replace the placeholder with a real, authorized FQDN before running the second command; do not submit the placeholder literally:

```bash
bash /home/agentuser/chaoxing/unified_system/deploy/server-preflight.sh
PUBLIC_DOMAIN=REPLACE_WITH_REGISTERED_FQDN bash /home/agentuser/chaoxing/unified_system/deploy/server-preflight.sh
```

The second command makes only DNS/HTTPS readiness probes to the supplied domain. The script also reports Docker/Compose, Nginx, age, systemd, curl and OpenSSL availability/version. It does not install software, open ports, alter firewall rules, or deploy services. Save its output without including credentials. Confirm Ubuntu version, CPU/memory/disk, Docker and Compose versions, Docker socket permissions, ports 80/443/8080/5432/6379, DNS and certificate readiness before selecting resource limits or pinning image digests.

The application containers use the Python 3.12 image line and do not depend on the host Python for runtime. Host Python is used only for operations/tests such as the PostgreSQL test runner. The stated host Python `3.12.3` still needs on-target confirmation.

## 5. Secrets and Phase 5 release blockers

- Copy `unified_system/deploy/.env.production.example` to `.env.production`, `chmod 600` it, and replace every placeholder. Keep it out of Git and Docker build contexts. Generate three independent database passwords as hex strings, a separate `APP_SECRET_KEY`, and a valid Fernet `CREDENTIAL_ENCRYPTION_KEY`. For example, in a protected local terminal (the outputs are secrets; do not paste them into chat/issues or shell command text):

  ```bash
  umask 077
  openssl rand -hex 32  # repeat separately for the three database role passwords and APP_SECRET_KEY
  python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
  ```

  Copy each output directly into the protected local env file without echoing it. Do not put secrets into command history, command-line arguments, or issue output.
- Compose environment variables are not a secret manager: host root/Docker-daemon administrators can inspect container environments. Use an approved secret manager for a real production deployment and follow the host's access-control policy.
- Platform-account and AI-provider ciphertext in PostgreSQL depends on the separate versioned Fernet keyring. Backing up the database without its independently controlled keyring is not a recoverable backup. Phase 6D adds versioned key IDs, dry-run-first rotation and synthetic keyring verification; see [`phase6d-operations-recovery.md`](phase6d-operations-recovery.md). None has been validated with a production key or a real PostgreSQL restore.
- New AI-provider secrets use the same credential keyring and are included in the rotation scan; no AI provider is activated by this. Phase 5's historical key-shaped value remains in shared Git history, and supplier-side revocation/exposure review is still unconfirmed. Do not publish AI service calls until the credential owner has completed that review and the independent egress/runtime gates pass.
- Phase 5 found key-shaped values in a legacy tracked config. The current version was scrubbed in a previous commit, but the old blob remains in shared Git history. Supplier-side revocation/rotation and external exposure review are **unconfirmed**. No history rewrite was done.
- Worker tokens in the example are blank. Use a different high-entropy token per platform/Worker identity after adapter review. Never place tokens, Cookies or Authorization headers in application or proxy logs.

## 6. PostgreSQL initialization, migrations and connection pool

The first initialization of a **new empty** `postgres_data` volume runs `10-create-app-db.sh`; it creates the app database and two separate roles. Changing the init variables later does not re-run the script on an existing volume. Protect and back up the Compose `.env.production` values needed to reproduce those roles; do not delete a production volume to fix a role/password mismatch.

The runtime role has DML access to tables/sequences; the migration owner is used only for Alembic. The `flask init-db` command explicitly refuses to run under `APP_ENV=production`. After the first PostgreSQL health check, apply the current Alembic chain (`0001`–`0005`, including Phase 6D's operational-alerts table) explicitly, then seed roles/permissions/platform catalog with the idempotent `seed-catalog` CLI command. Application startup never runs `create_all` or migrations. See [`phase6d-operations-recovery.md`](phase6d-operations-recovery.md) for the updated backup, monitoring and recovery sequence.

Changing a role password in `.env.production` does **not** alter an existing PostgreSQL role. Plan an authorized rotation on an existing volume: issue `ALTER ROLE` through a protected `psql` session (never place the password in SQL command arguments/logs), update the protected env/secret-manager value in the same maintenance window, and restart only dependent containers after verifying the new connection. The init script is only for a fresh volume; no online role-rotation automation has been added.

The backend engine uses `pool_pre_ping`, parameter hiding, bounded `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_TIMEOUT_SECONDS`, and `DB_POOL_RECYCLE_SECONDS`; SQLAlchemy operational/connection/pool errors return a generic safe error and are logged by error class only. Defaults are 5 + 5 connections per Gunicorn worker process; the current Gunicorn configuration has two workers, so plan for up to roughly 20 pooled API connections plus migration/maintenance connections. Recalculate after target sizing.

PostgreSQL locking, shared-account slot leases, heartbeat, task cancellation, stale-lease recovery and simultaneous task creation must be exercised on PostgreSQL before enabling production tasks. The guarded runner requires **two different disposable test databases**:

```bash
# Set these in a protected shell/secret manager, not in command history or chat.
export UNIFIED_MIGRATION_TEST_URL='postgresql+psycopg://.../unified_migration_test'
export UNIFIED_TEST_POSTGRESQL_URL='postgresql+psycopg://.../unified_runtime_test'
export UNIFIED_TEST_POSTGRESQL_CONFIRM=I_UNDERSTAND_TEST_DATABASES_WILL_BE_DROPPED
python3 -m venv unified_system/.venv
. unified_system/.venv/bin/activate
python -m pip install -r unified_system/requirements-test.txt
bash unified_system/deploy/test-postgresql.sh
```

This script performs Alembic `upgrade head`, `alembic check`, and `downgrade base` in the migration test DB. It then runs the full backend tests against the runtime test DB; the fixture drops/recreates the application metadata and the Postgres-only tests exercise row-lock wait/`SKIP LOCKED`, concurrent task creation, and simultaneous Worker claims on a shared account slot. Never point these values at production. **This suite was not run in Arena** because neither a Postgres server nor Docker was available. SQLite results below are not a substitute.

## 7. Local deployment procedure (commands prepared, not executed here)

Run only after the target checks, secret generation, DNS/TLS and key-rotation gates are resolved. Keep the host firewall closed until explicit authorization to publish.

```bash
cd /home/agentuser/chaoxing/unified_system
cp deploy/.env.production.example deploy/.env.production
chmod 600 deploy/.env.production
# Edit the local file with generated values. Do not commit or print it.

docker compose --env-file deploy/.env.production -f docker-compose.prod.yml config --quiet
docker compose --env-file deploy/.env.production -f docker-compose.prod.yml build backend edge
docker compose --env-file deploy/.env.production -f docker-compose.prod.yml up -d postgres
# On a brand-new volume, wait for postgres to become healthy before continuing.
docker compose --env-file deploy/.env.production -f docker-compose.prod.yml --profile ops run --rm migrate
docker compose --env-file deploy/.env.production -f docker-compose.prod.yml --profile ops run --rm seed-catalog
```

Bootstrap the first administrator only after migrations and catalog seeding, reading the password without echo and passing only environment-variable names (not values) on the CLI:

```bash
read -r -p 'Admin email: ' BOOTSTRAP_ADMIN_EMAIL
read -r -s -p 'Admin password: ' BOOTSTRAP_ADMIN_PASSWORD; printf '\n'
export BOOTSTRAP_ADMIN_EMAIL BOOTSTRAP_ADMIN_PASSWORD
docker compose --env-file deploy/.env.production -f docker-compose.prod.yml run --rm \
  -e BOOTSTRAP_ADMIN_EMAIL -e BOOTSTRAP_ADMIN_PASSWORD backend \
  flask --app unified_system.app:create_app bootstrap-admin
unset BOOTSTRAP_ADMIN_EMAIL BOOTSTRAP_ADMIN_PASSWORD
# Start application services only after migrations, catalog seeding and bootstrap succeed.
docker compose --env-file deploy/.env.production -f docker-compose.prod.yml up -d backend edge
```

The one-off container is removed after the command. Docker administrators can inspect live container environments; use this only on a host whose Docker access is trusted.

The Compose file's `edge` port is loopback-only; host Nginx must be installed/configured separately. The previous single-file `unified-learning.conf` path is obsolete; do not copy it or hand-edit literal domain/certificate paths. Phase 6C now provides separately rendered ACME-bootstrap and HTTPS templates, a TLS validation renderer, staged HSTS activation, certificate-expiry checks, and a renewal hook. Follow [`phase6c-nginx-https-security.md`](phase6c-nginx-https-security.md) only after an authorized, real domain is confirmed. No Nginx host config has been installed or reloaded by this work.

The templates refuse Worker routes and overwrite client-supplied forwarding headers before the edge/backend proxy chain. Flask `ProxyFix` trusts one hop only in the production profile.

Useful operations after an authorized test deployment:

```bash
docker compose --env-file deploy/.env.production -f docker-compose.prod.yml ps
docker compose --env-file deploy/.env.production -f docker-compose.prod.yml logs --tail=200 backend edge postgres
bash deploy/health-report.sh
docker compose --env-file deploy/.env.production -f docker-compose.prod.yml stop edge backend postgres
# `down` retains the named database volume; never use `down -v` for an established database.
```

To enable an individual Worker later, first merge a reviewed adapter, issue its dedicated token, verify platform authorization and task capabilities, then activate only its profile (example: `--profile worker-chaoxing`). No profile should be started with a blank factory/token. Current adapters remain unverified, so none is enabled by this plan.

## 8. Backups, retention and recovery

Backups are PostgreSQL custom-format dumps, created through the private database container, checked with `pg_restore --list`, encrypted to `BACKUP_AGE_RECIPIENT`, stored with directory mode `0700` and archive mode `0600`, checksum-reported, and pruned by `BACKUP_RETENTION_DAYS` (default 30). The backup script requires `age`; the public recipient is not secret. Keep the corresponding private age identity offline/in a secret manager and separately protect the Fernet credential-encryption key.

The systemd timer template schedules a daily 03:15 host-local-time run (with a randomized delay). Adjust repository paths, verify user/group/Docker socket access, then validate and install on the target:

```bash
sudo systemd-analyze verify deploy/systemd/unified-learning-backup.service deploy/systemd/unified-learning-backup.timer
sudo install -o root -g root -m 0644 deploy/systemd/unified-learning-backup.service /etc/systemd/system/
sudo install -o root -g root -m 0644 deploy/systemd/unified-learning-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now unified-learning-backup.timer
systemctl list-timers unified-learning-backup.timer
```

Off-site replication of encrypted archives is not configured; choose and test an approved remote destination before production.

Manual backup:

```bash
bash deploy/backup-postgres.sh
```

Restore requires a readable age identity file with no group/other permissions, validates decryption/archive listing first, asks for `RESTORE:<database-name>`, stops enabled Worker profiles plus edge/backend, then performs a destructive single-transaction restore as the migration owner. It intentionally leaves edge/backend stopped for inspection:

```bash
BACKUP_AGE_IDENTITY_FILE=/secure/path/age-identity.txt \
  bash deploy/restore-postgres.sh /secure/path/unified_learning_....dump.age --confirm-overwrite
# Inspect logs/data before explicitly starting backend/edge again.
```

A backup command is not a recovery test. No real dump, encryption, restore, permission, retention, or systemd-timer test ran here. A scheduled backup is not production-ready until a restore drill on an isolated PostgreSQL instance has verified database content and key recovery.

## 9. Logging, monitoring and safety checklist

- Backend liveness: `GET /api/v1/health`; readiness performs `SELECT 1` at `GET /api/v1/health/ready`. The edge container has `/healthz`; PostgreSQL uses `pg_isready`.
- Worker liveness is the persisted `WorkerInstance.last_seen_at` heartbeat. The Worker Docker healthcheck only passes after a fresh successful backend heartbeat (temporary marker in tmpfs); Docker marks stale Workers unhealthy but restart policies do not automatically restart an otherwise-running unhealthy process.
- Host Nginx rate-limits login and general API traffic; the app also maintains login-failure limits. CORS remains disabled; browser/API are same-origin and state-changing routes require CSRF. Body size is capped at 1 MiB at Flask and both Nginx layers.
- SQLAlchemy hides SQL parameters; DB error logs contain only the exception class. Gunicorn and Nginx access logs omit query strings and request bodies. No app/proxy log format includes passwords, Cookies, AI keys or Authorization headers.
- Container stdout/stderr uses Docker `json-file` rotation (`10m` × 5 files). Host Nginx logs use the provided daily/14-file logrotate policy. Monitor `docker compose ps`, Worker heartbeat status, disk usage (`health-report.sh`) and host log/disk alerts; external alerting/metrics collection is not included.
- Database and Worker interfaces have no published ports; Worker routes are rejected at both Nginx layers.
- SSRF: the offline-tested Phase 5 Provider helper accepts only exact reviewed HTTPS host/path pairs, rejects non-443 ports/userinfo/query/fragment, verifies TLS and disables redirects. It is not wired to an admin-configurable Provider API; do not add arbitrary endpoint configuration. Compose runtime egress is disabled by default.
- Uploads: no document/file-upload feature is enabled; Flask and both Nginx layers cap request bodies at 1 MiB. File parsing and virus scanning remain future work.
- The Phase 5 endpoint allowlist is not a replacement for the still-missing admin key-management/rotation subsystem or real Provider connectivity tests.

## 10. Validation report and release blockers

### Completed locally in the Arena development environment

- Backend tests: `.venv/bin/pytest unified_system/tests -q` — **92 passed, 2 skipped**. The skipped tests require the disposable PostgreSQL URL; all executed DB behavior used SQLite.
- SQLite Alembic `upgrade head`, `alembic check`, and `downgrade base` passed on a throwaway `/tmp` database; `pip check` reported no broken requirements.
- Frontend: Vitest **5 passed**, TypeScript check passed, Vite production build passed.
- Python `py_compile`, shell `bash -n`/`sh -n`, `git diff --check`, and Gunicorn `--check-config` passed.
- `pip download --require-hashes --no-deps -r requirements-prod.lock` verified the pinned production artifact hashes for the current Arena Python/platform.
- PyYAML parsed `docker-compose.prod.yml`; structural checks confirmed edge loopback-only port mapping, no published database/backend/Worker ports, internal-only networks and opt-in Worker profiles.

### Not executed / not claimed

- Docker/Compose config validation by the Docker Compose binary, image builds, image scan, container startup, restart/persistence tests, container log rotation, Nginx syntax test, and host Nginx/TLS tests: not run (Docker and Nginx unavailable).
- PostgreSQL server/migration/concurrency tests, production connection pool behavior, Postgres backup/restore and recovery drill: not run (no Postgres service/client available).
- Target Ubuntu release/kernel/Python/Docker/Compose/resources/network/ports/permissions/DNS/HTTPS: not checked; no remote access.
- Public DNS changes, firewall changes, systemd installation, port exposure, and real platform or AI API calls: not performed.

### Must remain incomplete

1. Supplier-side revocation/rotation and scope review of the historical AI key: unconfirmed; do not release AI.
2. Phase 5 AI encryption key management, admin Provider config, models, quotas, conversations/history, assistant UI, file parsing and real provider validation.
3. Real Learning Platform/Chaoxing and Zhihuishu authorization/Worker verification.
4. Yinghua Go source is not buildable from the linked commit and is disallowed as written; do not enable its Compose profile.
5. PostgreSQL live migration/concurrency testing and backup/restore drill.
6. Target Ubuntu preflight, immutable image digest selection, Docker/Nginx build/start validation, monitoring/alerting and public HTTPS readiness.

Phase 6A's historical deployment preparation is superseded by the follow-up checks in [`phase6b-container-validation.md`](phase6b-container-validation.md) and [`phase6c-nginx-https-security.md`](phase6c-nginx-https-security.md). Docker/PostgreSQL acceptance, target-host validation, authorized DNS/firewall/certificate work and public HTTPS remain outstanding. Do not open the stack to the Internet, activate a Worker, or claim production readiness until those blockers are explicitly resolved and authorized.
