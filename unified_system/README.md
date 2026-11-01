# Unified Learning Management System

This is the isolated unified backend and web console. It does not import legacy platform runtime configuration or modify the platform projects. The architecture, ER model and security/worker policies are in [`docs/phase2-architecture.md`](../docs/phase2-architecture.md). Stage-three API compatibility work and the exact interface gaps found in the checked-in implementation are recorded in [`docs/phase3-api-contract.md`](../docs/phase3-api-contract.md).

## Local development

For the supported single-user Windows-native workflow (PostgreSQL service, one-time venv/npm preparation, safe local config, Alembic initialization, and root-level double-click BAT files), see [`docs/local-windows-runbook.md`](../docs/local-windows-runbook.md). It binds the combined frontend/backend to `127.0.0.1:8088`; Worker profiles remain disabled and it is not a production deployment.

Start the backend in one terminal (Linux/macOS development reference): this example uses SQLite only for disposable local development and is not the supported Windows single-user deployment. The Windows launcher explicitly requires PostgreSQL.

```bash
cd /home/user/chaoxing
. .venv/bin/activate
pip install -r unified_system/requirements.txt
export APP_ENV=development
export DATABASE_URL=sqlite:///./unified_system/data/unified-dev.db
export DATA_DIR=./unified_system/data
export APP_SECRET_KEY='replace-with-a-long-random-development-secret'
flask --app unified_system.app:create_app init-db
BOOTSTRAP_ADMIN_EMAIL=admin@example.test BOOTSTRAP_ADMIN_PASSWORD='a-long-local-password' \
  flask --app unified_system.app:create_app bootstrap-admin
flask --app unified_system.app:create_app run --host 127.0.0.1 --port 8088
```

Start the Vue console in a second terminal:

```bash
cd /home/user/chaoxing/unified_system/frontend
npm ci
npm run dev -- --host 127.0.0.1
```

Vite listens on `127.0.0.1:5173` for this local development command. Browser requests use the relative `/api/v1` path; Vite proxies those requests to `VITE_API_PROXY_TARGET` (default `http://127.0.0.1:8088`). No browser JavaScript calls localhost, and session cookies remain same-origin from the browser's perspective. To use a different backend, set `VITE_API_PROXY_TARGET` in an untracked `.env.local` file. Do not put secrets in Vite `VITE_*` variables.

### Optional Docker development stack

`docker-compose.dev.yml` starts PostgreSQL, the Flask API, and Vite on localhost-only ports (`8088`, `5173`). It uses `flask init-db` for a disposable development schema and is not a production deployment/migration workflow. Docker was not installed in the implementation environment, so this development Compose file has not been built or run here.

### Production preparation (not yet deployed)

`docker-compose.prod.yml`, production Dockerfiles, Postgres role initialization, host Nginx templates, backup/restore scripts, and a server preflight are documented in [`docs/phase6a-deployment.md`](../docs/phase6a-deployment.md). Phase 6B build-context corrections, internal per-platform networks, egress design and explicit validation commands are in [`docs/phase6b-container-validation.md`](../docs/phase6b-container-validation.md). Phase 6C's staged host-Nginx/HTTPS templates, certificate lifecycle, response headers, and security/test boundaries are in [`docs/phase6c-nginx-https-security.md`](../docs/phase6c-nginx-https-security.md). The Compose edge port is bound to loopback only; Worker profiles remain disabled, and no egress gateway is configured. This environment has no Docker/Compose, PostgreSQL or Nginx runtime, so no production image/container, PostgreSQL integration or Nginx validation has been completed. No target server, DNS, firewall or certificate was changed; do not expose the stack publicly until those checks, key rotation and explicit authorization are complete.

### Windows native local deployment

Use the root-level `启动项目.bat`, `停止项目.bat`, and `查看运行状态.bat` after following [`docs/local-windows-runbook.md`](../docs/local-windows-runbook.md). This workflow uses an installed local PostgreSQL Windows service and does not use Docker. The optional `docker-compose.dev.yml` below remains only for developers who explicitly choose Docker; it is not the documented Windows local deployment.

For local single-origin serving, run `npm run build` first. Flask serves `frontend/dist` and its history-mode routes when the build exists; it falls back to the legacy static shell only when no build exists.

## Auth and secrets

Registration is invitation-only. For a local preview only, bootstrap credentials can be set as environment variables; never use example credentials on a public server. Production must inject independent `APP_SECRET_KEY`, `CREDENTIAL_ENCRYPTION_KEY`, and per-platform `WORKER_TOKEN_*` values from a secret manager. Use PostgreSQL over a private network. Apply Alembic migrations explicitly before deploying; production startup does not create or mutate the schema automatically.

Cookie sessions are HttpOnly/SameSite and CSRF-protected. The SPA keeps CSRF metadata and user state in memory only; it does not store session credentials in LocalStorage. Server-side session metadata permits listing and revoking sessions. Phase-three migration `0002_user_sessions` is additive; existing signed phase-two cookies remain valid until expiry and are registered when the session page is opened.

## Tests and schema checks

```bash
.venv/bin/python -m compileall -q unified_system
.venv/bin/pytest -q unified_system/tests
cd unified_system/frontend
npm ci
npm run test
npm run typecheck
npm run build
```

A fresh SQLite database has been tested through `upgrade head` at `0004_account_verification`. Additive `0003_platform_courses` and `0004_account_verification` add a minimal per-account course cache and account-specific verification timestamp; `/api/v1/courses` rechecks live personal ownership/shared grants, and Worker course sync accepts only bounded, allowlisted course fields under a valid task lease. The personal-account verification endpoint only queues `account_validate` when the platform catalog explicitly enables that verified read-only capability. A populated `0001_initial` SQLite database was previously upgraded with its existing user row intact; no session rows were fabricated by `0002_user_sessions`. PostgreSQL DDL is exported to `unified_system/postgresql-schema.sql`. SQLite tests enable foreign keys. PostgreSQL transaction/locking behavior and production deployment still require a real PostgreSQL test run.

## Current capability boundary

- **Implemented:** invitation-based registration, login, role and scoped-admin APIs, session revocation, personal/shared account management, encrypted credential submission, authorized shared-account visibility, users/invitations/audit management, live dashboard aggregates, platform/Worker status, a lease-validated minimal per-account course catalog cache/read API, and a course-data page.
- **Not verified/integrated:** real Chaoxing/Zhihuishu/Yinghua authentication or execution Worker, live credential-validation tests for either account mode, verified capability registration/admin activation, system announcements, email delivery, AI Provider management/API, Redis queue. A production Docker Compose/Nginx/backup plan is prepared but not built, started, publicly exposed, or recovery-tested. PostgreSQL migration/runtime/concurrency validation remains outstanding.
- All platform catalog states remain `pending`; accounts remain `unverified` until a real verification adapter exists. The Phase 4 Worker runtime and course cache are foundation code only; actual platform tasks remain unavailable until a concrete adapter is verified.
- The legacy repository contains an old committed AI credential. This application neither reads nor reuses that configuration; supplier-side revocation/rotation remains an operator action.
