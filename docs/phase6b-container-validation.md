# Phase 6B — Docker containerization and service integration validation

Date: 2026-11-01. Branch: `arena/bd5e1acd-chaoxing`. Configuration review/repair and portable checks for this Phase 6B pass are complete; **end-to-end container acceptance is not complete because no Docker or PostgreSQL runtime is available**. No Docker image or container has been built or run in this Arena environment. This report distinguishes source/config checks from real container acceptance.

## 1. Environment inspection

Arena sandbox observed for this phase:

- Linux x86_64, 2 online CPUs, about 3.8 GiB RAM (about 3.3 GiB available), about 18 GiB free on the workspace filesystem.
- `docker`, Docker daemon, `docker compose`, Podman, Buildah, Nginx, `psql`, and `pg_ctl` are absent. No way to run a container build or daemon-backed test here.
- The sandbox's outbound network policy does not provide general Docker registry access. The production image registry pulls were not attempted.
- The target server remains a different environment. The stated Ubuntu/kernel/Python facts were not measured on the target; no remote access or production changes occurred.

This phase did not install Docker, alter firewall/DNS, start services, publish a port, connect to a platform, or use production data.

## 2. Files reviewed and changes made

Reviewed Phase 6A deployment report; production/development Compose files; backend, frontend and Worker Dockerfiles; root and unified-system `.dockerignore` files; production and development env examples; host and edge Nginx config; PostgreSQL role-init script; Worker settings/client/runtime; Flask config/WSGI/migrations; Vite/API client/package scripts.

### Container/build-context corrections

- Production backend and Worker build from `unified_system/`; both copy the hash-locked `requirements-prod.lock` and install with `pip --require-hashes`. Runtime packages are separate from tests; the unified `.dockerignore` now excludes tests, docs, deployment material, PostgreSQL initialization files and the frontend source from backend/Worker images.
- Production frontend build uses its own `frontend/` context, `npm ci`, and a multi-stage Node-to-unprivileged-Nginx image. Its public API path is supplied as the non-secret `VITE_API_BASE_URL` build argument; the default is same-origin `/api/v1`. No API key or other secret enters frontend build args.
- The development frontend Compose build previously used the whole unified-system directory as its context. Its context now points to `./frontend`, matching `frontend/Dockerfile.dev`'s copy paths and the frontend ignore file. The Vite development proxy remains a container-side request to `http://backend:8088`; browser code continues to use relative URLs.
- Backend runtime is Gunicorn via `unified_system.wsgi:app`, bound to `0.0.0.0:8088` inside the container. It runs as UID 10001 with a read-only root and bounded `/tmp`. The frontend Nginx image serves the built files and proxies `/api/` to the Compose service name `backend:8088`; the edge host mapping remains loopback-only.
- PostgreSQL uses a PostgreSQL 16 image tag candidate (digest pinning is still outstanding), with a persistent named volume, a readiness probe and separate cluster-admin, migration-owner and runtime DML roles. The database has no production host port mapping. Schema migration and catalog seeding are explicit one-shot `ops` jobs; `seed-catalog` waits for successful migration. `flask init-db` refuses `APP_ENV=production`.
- Each platform has a distinct Worker image tag built from the shared generic runtime Dockerfile, carrying a non-secret platform label; each service also has its own internal backend and proxy-handoff networks. The image code is still generic and contains no verified Zhihuishu/Yinghua adapter. Workers do not share a network with one another or the database. Profiles remain disabled by default. The image healthcheck uses a fresh successful backend heartbeat recorded only in container tmpfs. Docker reports stale Workers unhealthy; restart policy does not itself restart a process solely because it is unhealthy.
- Added `docker-compose.test.yml`, a test-only overlay that assigns `:phase6b-test` image tags (so test builds cannot overwrite the default `:local` tags) and binds disposable PostgreSQL to `127.0.0.1:${TEST_POSTGRES_PORT:-55432}` for the guarded Alembic/concurrency runner. Do not use this overlay with production data; the production Compose file still publishes no database port.
- Corrected a pre-existing Zhihuishu token-variable spelling mismatch. Backend configuration, `WorkerSettings`, development/production Compose, and the production env example now consistently use `WORKER_TOKEN_ZHIHUISHU` (the platform name's uppercase spelling); regression tests cover both backend and Worker lookup.

The Compose service named `edge` intentionally combines the static frontend and its internal Nginx/API proxy in one production container. The separate public-facing host Nginx remains an operator-installed template in Phase 6A, not a second Compose container. This is the existing two-layer design, not a missed browser-facing API route.

## 3. Egress design and current state

### Default (current)

All Compose runtime networks are `internal: true`. The API and Workers have no direct general Internet route. PostgreSQL, backend, internal Worker APIs and the `edge` service have no public host mapping except the edge's explicit loopback binding. The backend has an `ai_proxy` internal handoff network; each Worker has its own internal `*_proxy` handoff network. No egress gateway container is attached to those networks in this phase, so setting a proxy URL alone cannot create outbound access and requests fail closed. The proxy variables are blank in the example env file.

### Controlled activation design (must be implemented and tested before external calls)

- Add a dedicated forward-egress gateway only when an external service is authorized. The gateway would join exactly one internal proxy-handoff network and a separate outbound network; never attach the backend or Worker directly to a normal bridge network. Do not publish the proxy port to the host.
- The backend can consume `AI_HTTPS_PROXY` through `HTTPS_PROXY`; each Worker has an independent `WORKER_<PLATFORM>_HTTPS_PROXY`. `NO_PROXY` explicitly retains Compose service aliases and loopback. These hooks do not enable egress by themselves; the matching reviewed gateway and Compose profile must exist first.
- For AI, use separate gateway policy for exact destinations already allowlisted in `services/ai_providers.py`: `api.openai.com:443`, `dashscope.aliyuncs.com:443`, `ark.cn-beijing.volces.com:443`, `ark.cn-shanghai.volces.com:443`, `ark.cn-guangzhou.volces.com:443`, `api.sensenova.cn:443`, `token.sensenova.cn:443`, and `api.siliconflow.cn:443`. The application additionally checks provider-specific HTTPS paths. The gateway policy must deny other hosts/ports and reject loopback, RFC1918, link-local, IPv6 unique-local and cloud metadata destinations after DNS resolution. Keep TLS end-to-end (no interception); preserve certificate verification, bounded timeouts, response-size limits and redirect rejection.
- Platform egress must use a distinct gateway and network per platform. No real adapter is verified, so there is no approved platform endpoint allowlist to put into this Compose file yet. Do not guess domains or enable one of the Worker profiles to discover them.
- At the target host, restrict outbound firewall rules so only the gateway containers can reach the approved destination ports and the approved resolver. Review proxy-image digest/config, ACL tests, DNS-rebinding/private-address rejection and proxy access-log redaction before setting any proxy env value.
- Keep proxy logs free of authorization headers, request/response bodies, query strings and credentials. Review gateway access logs for domain/decision/status only. The existing Provider helper's HTTPS host/path validation is defense in depth, not a substitute for network egress controls.

No egress gateway image/config was added or activated: there are no authorized platform endpoints, AI management/key rotation is unfinished, no Docker runtime exists here, and introducing an untested public proxy would weaken the fail-closed boundary. The internal handoff networks and env hooks preserve a clear integration point without granting direct egress.

## 4. Service/build-context map

| Service | Build/context | Networks | Host ports | Health/readiness |
| --- | --- | --- | --- | --- |
| `postgres` | official PostgreSQL major image | internal `database` only | none in production; loopback-only test overlay | `pg_isready` |
| `backend` | `unified_system/Dockerfile`, Python lock with hashes | `web_backend`, `database`, three per-platform backend networks, `ai_proxy` | none | `/api/v1/health/ready` performs a DB `SELECT 1` |
| `edge` | `frontend/Dockerfile`, Node build then unprivileged Nginx | internal `web_backend` | `127.0.0.1:8080` only | `/healthz` |
| `worker-chaoxing` | `unified-learning-worker-chaoxing:local`; shared runtime with a platform label | Chaoxing/backend + Chaoxing/proxy internal networks | none | fresh successful backend-heartbeat marker |
| `worker-zhihuishu` | `unified-learning-worker-zhihuishu:local`; shared runtime with a platform label; no verified adapter | Zhihuishu/backend + Zhihuishu/proxy internal networks | none | fresh successful backend-heartbeat marker |
| `worker-yinghua` | `unified-learning-worker-yinghua:local`; shared runtime with a platform label; no approved adapter | Yinghua/backend + Yinghua/proxy internal networks | none | fresh successful backend-heartbeat marker; profile remains disabled |
| `migrate`, `seed-catalog` | backend image, explicit `ops` profile | internal `database` only | none | one-shot jobs; wait for PostgreSQL health |

Every service has restart policy/log rotation/resource and process limits appropriate to its role. Application/edge and Worker containers run non-root with read-only roots. Backend/edge are default services; Worker and one-shot `ops` profiles are opt-in. Redis remains intentionally absent.

## 5. Target-host isolated validation procedure

These commands are **instructions only**, not results. Run on a disposable Docker host, never against production. Start from `deploy/.env.production.example` to create a separate local `deploy/.env.phase6b`, then set fresh test-only passwords/keys, `APP_DB_NAME=unified_runtime_test` and `TEST_POSTGRES_PORT=55432`. Use independent 64-hex-character DB passwords as the template indicates; this keeps the Compose `DATABASE_URL` interpolation valid. Keep the file out of Git (the deploy `.gitignore` excludes it) and set mode `0600`. The overlay supplies isolated `:phase6b-test` application image tags. Do not reuse production env values or volumes. Keep Worker tokens, adapter factories and all proxy URLs blank for this local integration run.

```bash
docker version
docker info
docker compose version
uname -m
free -h
df -h /home/agentuser

cd /home/agentuser/chaoxing/unified_system
cp deploy/.env.production.example deploy/.env.phase6b
# Replace placeholders with fresh disposable-test values, set APP_DB_NAME=unified_runtime_test and TEST_POSTGRES_PORT=55432.
chmod 600 deploy/.env.phase6b

# This unique project name gives the test PostgreSQL volume an independent namespace.
docker compose -p unified-learning-6b \
  --env-file deploy/.env.phase6b \
  -f docker-compose.prod.yml -f docker-compose.test.yml config --quiet

docker compose -p unified-learning-6b \
  --env-file deploy/.env.phase6b \
  -f docker-compose.prod.yml -f docker-compose.test.yml build backend edge

# Build separately tagged Worker images for inspection; this does not start any Worker.
docker compose -p unified-learning-6b \
  --profile worker-chaoxing --profile worker-zhihuishu --profile worker-yinghua \
  --env-file deploy/.env.phase6b \
  -f docker-compose.prod.yml -f docker-compose.test.yml \
  build worker-chaoxing worker-zhihuishu worker-yinghua

docker image inspect --format '{{ index .Config.Labels "io.unified-learning.worker-platform" }}' unified-learning-worker-chaoxing:phase6b-test
docker image inspect --format '{{ index .Config.Labels "io.unified-learning.worker-platform" }}' unified-learning-worker-zhihuishu:phase6b-test
docker image inspect --format '{{ index .Config.Labels "io.unified-learning.worker-platform" }}' unified-learning-worker-yinghua:phase6b-test

docker compose -p unified-learning-6b \
  --env-file deploy/.env.phase6b \
  -f docker-compose.prod.yml -f docker-compose.test.yml up -d postgres
```

After PostgreSQL becomes healthy, the init script creates `unified_runtime_test` as configured. Create the separate migration-test DB inside that same disposable server (the name must end in `_test`):

```bash
docker compose -p unified-learning-6b --env-file deploy/.env.phase6b \
  -f docker-compose.prod.yml -f docker-compose.test.yml \
  exec -T postgres sh -ec \
  'createdb --no-password --username "$POSTGRES_USER" --owner "$APP_DB_OWNER" unified_migration_test'
```

Prepare a Python 3.12 virtual environment on the test host. Through a secret manager/protected shell, set `UNIFIED_MIGRATION_TEST_URL` to the migration-owner URL for `unified_migration_test`, `UNIFIED_TEST_POSTGRESQL_URL` to the same owner's URL for `unified_runtime_test`, and `UNIFIED_TEST_POSTGRESQL_CONFIRM=I_UNDERSTAND_TEST_DATABASES_WILL_BE_DROPPED`. Both URLs must use `127.0.0.1:55432`; URL-encode special characters in passwords. Do not put URL/password values in shell history or paste them into logs. The script separately sets its fixture confirmation after its initial guard. Then run the guarded runner:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-test.txt
bash deploy/test-postgresql.sh
```

This runs Alembic upgrade/check/downgrade in `unified_migration_test`, then the full backend suite in `unified_runtime_test`, including PostgreSQL-specific row-lock/`SKIP LOCKED` and concurrent task-creation/Worker-claim tests plus cancellation, heartbeat and stale-lease coverage. It intentionally drops/recreates test schema data; never point it at production.

Then exercise the application containers, still under the unique test project:

```bash
docker compose -p unified-learning-6b --env-file deploy/.env.phase6b \
  -f docker-compose.prod.yml -f docker-compose.test.yml --profile ops run --rm migrate
docker compose -p unified-learning-6b --env-file deploy/.env.phase6b \
  -f docker-compose.prod.yml -f docker-compose.test.yml --profile ops run --rm seed-catalog
docker compose -p unified-learning-6b --env-file deploy/.env.phase6b \
  -f docker-compose.prod.yml -f docker-compose.test.yml up -d backend edge

curl --fail http://127.0.0.1:8080/healthz
curl --fail http://127.0.0.1:8080/api/v1/health
curl --fail http://127.0.0.1:8080/api/v1/health/ready
docker compose -p unified-learning-6b --env-file deploy/.env.phase6b \
  -f docker-compose.prod.yml -f docker-compose.test.yml ps
```

The readiness request is the actual edge → backend → PostgreSQL integration probe. Exercise PostgreSQL volume persistence with a throwaway sentinel row, then restart the DB and confirm the value remains:

```bash
docker compose -p unified-learning-6b --env-file deploy/.env.phase6b \
  -f docker-compose.prod.yml -f docker-compose.test.yml \
  exec -T postgres sh -ec \
  'psql --no-password --username "$POSTGRES_USER" --dbname "$APP_DB_NAME" -v ON_ERROR_STOP=1 -c "CREATE TABLE phase6b_persist_probe (value text PRIMARY KEY); INSERT INTO phase6b_persist_probe VALUES (\$\$survives-restart\$\$);"'
docker compose -p unified-learning-6b --env-file deploy/.env.phase6b \
  -f docker-compose.prod.yml -f docker-compose.test.yml restart postgres
# Wait until `docker compose ... ps` reports postgres healthy before querying the row.
docker compose -p unified-learning-6b --env-file deploy/.env.phase6b \
  -f docker-compose.prod.yml -f docker-compose.test.yml \
  exec -T postgres sh -ec \
  'psql --no-password --username "$POSTGRES_USER" --dbname "$APP_DB_NAME" -Atc "SELECT value FROM phase6b_persist_probe"' | grep -Fx survives-restart
```

Then kill the backend in this isolated project and verify Compose restarts it and readiness returns:

```bash
docker compose -p unified-learning-6b --env-file deploy/.env.phase6b \
  -f docker-compose.prod.yml -f docker-compose.test.yml kill --signal SIGKILL backend
# Wait until `docker compose ... ps` reports backend healthy, then rerun both readiness curls.
```

Test-only port inspection must show PostgreSQL on loopback `55432`, not on `0.0.0.0`.

The Worker profiles remain disabled. Once the image is built, probe only the private network path—this overrides the real Worker entrypoint and does not send a platform request or task:

```bash
docker compose -p unified-learning-6b --profile worker-chaoxing \
  --env-file deploy/.env.phase6b \
  -f docker-compose.prod.yml -f docker-compose.test.yml \
  run --rm --no-deps --entrypoint python worker-chaoxing \
  -c "from urllib.request import urlopen; print(urlopen('http://backend:8088/api/v1/health/ready', timeout=3).status)"
```

Verify the Worker cannot resolve the other platform's service name (the command must print `platform network isolated`):

```bash
docker compose -p unified-learning-6b --profile worker-chaoxing \
  --env-file deploy/.env.phase6b \
  -f docker-compose.prod.yml -f docker-compose.test.yml \
  run --rm --no-deps --entrypoint python worker-chaoxing \
  -c 'import socket
try:
    socket.gethostbyname("worker-zhihuishu")
except OSError:
    print("platform network isolated")
else:
    raise SystemExit("unexpected cross-platform DNS reachability")'
```

Also verify the Worker network cannot reach the database (the command must print `database network isolated`):

```bash
docker compose -p unified-learning-6b --profile worker-chaoxing \
  --env-file deploy/.env.phase6b \
  -f docker-compose.prod.yml -f docker-compose.test.yml \
  run --rm --no-deps --entrypoint python worker-chaoxing \
  -c 'import socket
s = socket.socket()
s.settimeout(2)
try:
    s.connect(("postgres", 5432))
except OSError:
    print("database network isolated")
else:
    raise SystemExit("unexpected database reachability")'
```

These network probes were not run here. Do not use the real Worker entrypoint until a verified adapter, authorization and destination-specific egress policy exist.

After all checks, inspect the project name and remove **only** the disposable project/volume:

```bash
docker compose -p unified-learning-6b --env-file deploy/.env.phase6b \
  -f docker-compose.prod.yml -f docker-compose.test.yml down -v
```

Never use `down -v` for an established or production project.

## 6. Validation performed in Arena

- Python backend suite: **98 passed, 2 skipped**. The skips are the PostgreSQL integration tests because no PostgreSQL URL/server was configured; executed database tests use SQLite.
- Frontend Vitest: **5 passed**; `vue-tsc --noEmit` and Vite production build passed with `VITE_API_BASE_URL=/api/v1`.
- PyYAML parsed production, development and test Compose files. Structural assertions confirmed loopback-only edge, no production DB/backend/Worker host ports, all networks internal, one Worker backend/proxy network pair per platform, and no Worker database attachment.
- Production lock artifact hashes were checked with `pip download --require-hashes`; CPython 3.12 x86_64 manylinux wheel availability was also checked. This is package-artifact compatibility checking, not a Python 3.12 container build.
- Python compilation, `bash -n`, `sh -n`, and `git diff --check` passed. SQLite Alembic migration roundtrip and Gunicorn config checks from Phase 6A remain valid; no container test has occurred.

## 7. Not executed / outstanding acceptance

- Docker Engine/Compose `config`, backend/frontend/three tagged Worker image builds, image secret/vulnerability inspection, container startup or container network probes: **not executed** (no Docker CLI/daemon/build tools). Production base-image tags have not been pinned to reviewed registry digests.
- PostgreSQL container migrations, locks/concurrency, restart persistence, restore or recovery drill: **not executed** (no PostgreSQL server/client). The two PG-only tests were skipped, not passed.
- Nginx `-t`, host TLS, DNS, systemd or target Ubuntu checks: **not executed**. Recalculate and monitor the aggregate CPU/RAM limits on the actual host before enabling any Worker; all profiles remain disabled.
- Runtime external AI Provider or platform egress proxy: **not implemented or tested**. Proxy handoff/env hooks are present; approved gateway policy and image are still required.
- No public release, firewall/DNS change, production data migration, platform task or Worker profile activation occurred. Historical AI-key supplier-side revocation/rotation is still awaiting confirmation by the credential holder; no AI credential was added to an image, template or frontend.

Phase 6B stops at configuration repair, static review and portable tests. Actual Docker/Compose/PostgreSQL acceptance remains blocked on a permitted Docker host and disposable PostgreSQL test instance. Do not progress to Phase 6C, publish the service, enable Workers, or claim production integration until the target-host commands above have real passing outputs and the listed security gates are resolved.
