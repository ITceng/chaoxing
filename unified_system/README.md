# Unified Learning Management System

This is the isolated unified backend and web console. It does not import legacy platform runtime configuration or modify the platform projects. The architecture, ER model and security/worker policies are in [`docs/phase2-architecture.md`](../docs/phase2-architecture.md). Stage-three API compatibility work and the exact interface gaps found in the checked-in implementation are recorded in [`docs/phase3-api-contract.md`](../docs/phase3-api-contract.md).

## Local development

Start the backend in one terminal:

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
flask --app unified_system.app:create_app run --host 0.0.0.0 --port 8088
```

Start the Vue console in a second terminal:

```bash
cd /home/user/chaoxing/unified_system/frontend
npm ci
npm run dev
```

Vite listens on `0.0.0.0:5173`. Browser requests use the relative `/api/v1` path; Vite proxies those requests to `VITE_API_PROXY_TARGET` (default `http://127.0.0.1:8088`). No browser JavaScript calls localhost, and session cookies remain same-origin from the browser's perspective. To use a different backend, set `VITE_API_PROXY_TARGET` in an untracked `.env.local` file. Do not put secrets in Vite `VITE_*` variables.

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

A fresh SQLite database has been tested through `upgrade head`, downgrade of `0002_user_sessions` to `0001_initial`, and re-upgrade. A populated `0001_initial` SQLite database was also upgraded and its existing user row verified intact; no session rows are fabricated during migration. PostgreSQL DDL is exported to `unified_system/postgresql-schema.sql`. SQLite tests enable foreign keys. PostgreSQL transaction/locking behavior and production deployment still require a real PostgreSQL test run.

## Current capability boundary

- **Implemented:** invitation-based registration, login, role and scoped-admin APIs, session revocation, personal/shared account management, encrypted credential submission, authorized shared-account visibility, users/invitations/audit management, live dashboard aggregates, platform/Worker status, and responsive browser pages.
- **Not integrated:** real Chaoxing/Zhihuishu/Yinghua authentication or execution Worker, per-account platform verification, system announcements, email delivery, AI provider, Redis queue, Docker Compose production, Nginx/HTTPS, PostgreSQL migration/backup drills.
- All platform catalog states remain `pending`; accounts remain `unverified` until a real verification adapter exists. The interface must not be treated as a learning-task execution service.
- The legacy repository contains an old committed AI credential. This application neither reads nor reuses that configuration; supplier-side revocation/rotation remains an operator action.
