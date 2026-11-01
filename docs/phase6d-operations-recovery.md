# Phase 6D — Backup/recovery, key custody, observability and operations

**Scope:** Phase 6D only. This document does not certify a production deployment. The branch contains backup/recovery tooling, versioned credential-key rotation, structured/redacted logs, measured host/application signals, persisted/deduplicated alerts, and a superadmin-only monitoring dashboard.

**Evidence boundary:** SQLite tests, static checks and frontend build are not real PostgreSQL, Docker/Compose, age, Nginx or Ubuntu acceptance. No production database was connected, backed up, decrypted or restored. No host timer was installed, no email/webhook/page integration was configured, and no host, DNS, firewall, certificate or public service was changed. Actual PostgreSQL recovery remains untested.

## 1. Production gates that remain open

- Complete Phase 6B Docker/Compose/PostgreSQL acceptance on a permitted host and a separately disposable PostgreSQL test environment. Phase 6A/6B/6C deployment instructions remain prerequisites, not evidence of acceptance.
- Complete a real, isolated PostgreSQL restore rehearsal from a newly generated encrypted archive using a private age identity held separately from the database archives.
- Preserve and test the credential-encryption keyring independently of PostgreSQL. A database-only backup is not a recoverable credential backup.
- Phase 6C's missing admin MFA/server-side step-up remains a production gate for high-impact restore and key-management actions. This Phase 6D dashboard is view-only; key rotation stays a CLI operation, and restore remains a tightly guarded isolated-rehearsal script.
- External alert delivery is not configured. Current notification output is structured backend/Docker or host systemd/journal logging. An approved log-forwarding/on-call integration must be designed and tested before treating this as paging/24×7 alerting.
- Keep platform Workers disabled until their adapters, platform authorization and destination-specific egress have passed their separate gates. Never infer liveness from configured services alone.

## 2. PostgreSQL backup

`deploy/backup-postgres.sh` performs a full **logical application-database** backup via `pg_dump --format=custom`. It does not copy a live `PGDATA` directory. The plaintext custom dump is written only in a temporary directory under the restricted backup directory (`umask 077`, directory mode `0700`); the encrypted output and JSON sidecar are mode `0600`. A sudden power loss can leave a hidden `.backup.*` temporary directory. Inspect and safely remove such a directory only after establishing that no backup job is running and following the host's incident/retention procedure.

Before publication the script checks that the dump has a readable custom-format TOC, encrypts using the public `BACKUP_AGE_RECIPIENT`, creates a SHA-256/size/PostgreSQL-version/pg_dump-version/recipient-fingerprint manifest, and verifies it. It publishes the archive and manifest only after those checks. Retention runs only after the new archive and manifest verify; it keeps configured daily, weekly and monthly points. Legacy, unmanaged, malformed and damaged archives are left for operator inspection. The backup directory is not under a web root and is excluded from Git.

The age **private identity** is a separate recovery dependency. Store it offline or in an approved secret manager with tightly limited access; never put it in PostgreSQL, Git, a container image, logs, the manifest, or plaintext beside the backup archives. A public recipient fingerprint in a manifest does not enable decryption.

### Manual and scheduled run

After configuring a protected `deploy/.env.production` (mode `0600`), installing `age`, and completing the authorized Compose deployment:

```bash
cd /home/agentuser/chaoxing/unified_system
sudo systemctl start unified-learning-backup.service
sudo journalctl -u unified-learning-backup.service --since today
```

For a manual run without systemd, execute the same script as the dedicated deployment user with the protected environment and backup directory selected; do not source/print the environment file or copy secrets into command history. Check the exit status and confirm both `.dump.age` and `.dump.age.json` exist before considering the run successful.

The provided timer runs daily at **03:15 UTC** with a randomized delay. Install the reviewed service/timer files only on the target host, then enable the timer. Confirm `systemctl list-timers unified-learning-backup.timer`, service result/exit status, archive permissions, free disk and a successful manifest verification after the first authorized run. No live backup was performed in Arena.

The monitor shows the most recent archive manifest, timestamp, archive size, retention count and backup service result. For host-load reasons it does **not** re-hash a potentially multi-gigabyte archive every minute; it checks the manifest/file relationship and recorded size. Run the full `backup-tools.py verify` checksum check as part of a controlled validation. Dashboard status is not proof of a successful restore.

## 3. Isolated recovery rehearsal

`deploy/restore-postgres.sh` requires all of the following before it can proceed:

- A separate, readable restore env file with no group/other permissions (`0600`), not `deploy/.env.production`.
- A unique project name `unified-learning-restore-<suffix>` and a new target DB ending in `_restore`, `_test` or `_testdb`; the target must not equal the configured restore app DB.
- A protected age identity file (mode `0600`), a regular encrypted `.dump.age` archive with a valid verified manifest, matching PostgreSQL major version, and explicit `--confirm-isolated-restore` plus typed `RESTORE-ISOLATED:<target>` confirmation.
- A unique Compose project and its own disposable PostgreSQL volume. Existing production volumes and databases are never accepted as the restore target; the script has no `--clean`/overwrite path.

Decryption streams directly to `pg_restore`; it does not create a plaintext dump file. The script validates the decrypted custom-format TOC, restores in one transaction with `--exit-on-error`, verifies required schema/table presence, checks the expected current Alembic head (`0005_operational_alerts`), records row counts for all application tables, and rejects any unvalidated foreign keys. It writes a mode-`0600` report under the ignored `deploy/restore-reports/` directory. The project/volume remain available for inspection; remove only the specific rehearsal project and volume after reviewing the report, never an established project.

The restore deliberately does **not** decrypt stored account or AI-provider credentials. To test the configured recovery keyring without touching a real stored credential, set `RESTORE_VERIFY_KEYRING=1` for a rehearsal. The isolated backend runs `flask --app unified_system.app:create_app verify-credential-keyring`; that command round-trips random synthetic test data through each configured versioned key and the legacy path (when configured), without querying database rows. The report records `synthetic_credential_keyring_roundtrip: passed` and `stored_credential_decryption: not_performed`. This demonstrates the keyring code/configuration works, not that real historical credentials or an actual production restore work.

Example command shape only (do not substitute production paths/secrets or run until the target checks are approved):

```bash
RESTORE_ENV_FILE=/secure/path/.env.restore \
RESTORE_PROJECT_NAME=unified-learning-restore-drill01 \
RESTORE_TARGET_DB=unified_learning_restore \
RESTORE_VERIFY_KEYRING=1 \
BACKUP_AGE_IDENTITY_FILE=/secure/separate/path/backup-age-identity \
  unified_system/deploy/restore-postgres.sh \
  /secure/backup/path/unified_learning_<timestamp>_<pid>.dump.age \
  --confirm-isolated-restore
```

The archive path, identity path and real env path above are placeholders. The command requires an interactive typed confirmation. Do not pass identity contents or encryption keys as CLI arguments. No PostgreSQL/Docker/age restore rehearsal was run in this environment.

## 4. Credential-encryption key separation and rotation

The database credential keyring is configured separately from PostgreSQL through the protected runtime environment. New ciphertext is tagged `v1:<key-id>:`; unknown IDs fail closed. Legacy unversioned ciphertext remains readable with the configured legacy key while old rows still exist. Keep every historical key needed for retained ciphertext until a reviewed backup/recovery exercise and successful row migration prove it can be removed.

Keep key material in a dedicated secret manager or equally protected offline recovery store. It must not be included in the database, Git, application images, logs or backup directory. Maintain independent access control and an offline recovery copy. Keep the database backup and its key custody dependency separate but recoverable by authorized custodians.

The Flask command is dry-run by default:

```bash
docker compose --env-file deploy/.env.production -f docker-compose.prod.yml \
  run --rm backend flask --app unified_system.app:create_app rotate-credential-encryption-key
```

Before applying, create and verify a current encrypted backup, stage the new key alongside all still-needed old keys in the approved secret store, set the intended active key ID, confirm rollback/recovery access, and use the interactive `--apply` path. The command requires typing `ROTATE:<active-key-id>`, re-encrypts platform-account and AI-provider ciphertext in one transaction, and never prints plaintext/ciphertext. It is not a dashboard action. A failed decrypt or wrong confirmation rolls the transaction back; focused tests cover this failure path. Never remove an old key until all rows, retained backups and recovery procedures that depend on it have been accounted for.

The `verify-credential-keyring` CLI is safe synthetic verification; it does not read the database and does not replace rotation, backup validation or restore testing.

## 5. Structured logs, redaction, rotation and retention

- **Flask backend:** JSON records contain UTC timestamp, level, service, logger/event, bounded message, validated request ID, route template, HTTP method/status/duration, and only an internal user ID where needed. Query strings, headers, request/response bodies and session cookies are not logged. Login records omit email/identifier; authz denials carry the permission and user ID. A redaction filter masks password/passwd, cookie, authorization, API-key, token and secret key/value patterns. Exception tracebacks are intentionally omitted from application JSON records.
- **Worker:** JSON startup/task/loop/protocol events include task/worker/platform identifiers and safe result/error codes. Adapter exception strings, platform payloads, credentials and session values are not logged.
- **Nginx host/edge:** access logs are JSON, carry request IDs and use `$uri` (not `$request_uri`), so query strings are excluded. Native Nginx error logs retain Nginx's timestamp/severity format; review them with the same no-secret policy.
- **PostgreSQL:** UTC timestamp and process/user/database prefix; slow statements over 1 second may be logged, while PostgreSQL bind-parameter output is configured off. SQLAlchemy hides parameters. Review any future raw SQL/literal construction before enabling more detailed statement logging.
- **Rotation/permissions:** Compose `json-file` logs are bounded at `10m × 5` per service (including backend, Worker and PostgreSQL). Host Nginx access/error logs use the existing daily, 14-rotation compressed logrotate rule, with new files `0640 www-data:adm`. The host monitor and backup one-shot services write to systemd journal. Configure journald retention according to the target host's overall retention/capacity policy; no global journald setting is imposed by this repository. Host monitor metrics are secret-free (`0755` directory, `0644` file); database backup directory is `0700`, archives/manifests and restore reports are `0600`.
- **Database records:** operational alerts are deduplicated and resolved history older than 180 days is pruned. Existing `AuditLog`, login/task history and AI usage data are not automatically purged by this phase; define a legal/operational retention policy before adding deletion jobs. Preserve audit evidence according to the organization's policy.
- No remote log shipper or external notification service is configured. Alert notifications are structured, secret-free log events; Docker log rotation and host journal retention apply. A notification exception increments `notification_failures` and stores only its exception class. Configure and test an authorized operator-forwarding channel before relying on delivery outside the host.

## 6. Real signals, alert rules and dashboard

The host systemd monitor samples host CPU, memory, load and root/backup-disk use; Compose service health and `docker stats` per-service CPU/memory/PID totals; host Nginx state; loopback edge readiness and latency; TLS certificate expiry; backup manifest metadata and most recent systemd backup result. It publishes one bounded, read-only, secret-free JSON file; only the backend receives it through the optional read-only Compose overlay. Docker socket access belongs to the host monitor user through the `docker` group, which is root-equivalent and must be restricted.

The application alert evaluator additionally queries real PostgreSQL data: queued/failed tasks, Worker heartbeat timestamps, enabled AI-provider count and persisted AI usage/token records. Existing platform state remains `not_integrated` unless actually enabled. If no AI usage rows exist, token totals are `null`/unavailable rather than fabricated zero. General API 5xx ratio/p95 is not persisted and is explicitly shown as not measured; the dashboard reports the actual readiness probe instead.

Current thresholds are: disk warning at 85% / critical at 95%; memory warning at 88% / critical at 95%; CPU warning at 90% / critical at 98%; backup overdue warning after 36 hours / critical after 72 hours; TLS warning within 30 days / critical within 7 days or expired; backend readiness failure critical; unhealthy required services error; a missing fresh heartbeat on an active platform warning/error; five task failures in 15 minutes error; queue backlog ≥50 warning or oldest wait >1 hour error; and at least three recorded AI usage failures in ten minutes warning when an AI provider is enabled. Thresholds should be tuned only with production baselines, not by inventing test signals.

Alerts use severity `INFO`, `WARNING`, `ERROR`, `CRITICAL`, a stable SHA-256 fingerprint from alert code/scope, a default 30-minute notification cooldown, occurrence counts, recovery events and 180-day resolved-history retention. Notification failures are caught and recorded without logging exception text or alert payload secrets. No email/webhook receiver is configured.

The new `ops.view` permission is not granted to the regular `admin` role; superadmin access is implicit, and the backend endpoint enforces the permission. The dashboard is read-only and does not reveal ciphertext, decrypted credentials, encryption keys, backup contents, identity files or restore controls. Backend authorization—not frontend route hiding—is the security boundary. Continue to treat absent admin MFA/server-side step-up as a production gate.

### Deployment integration (target host only; not executed here)

After reviewing Phase 6A/6B deployment prerequisites, taking a current backup, applying the current Alembic migration and seeding the system catalog/permission:

```bash
# Use the protected production env file. Run only in the authorized deployment window.
docker compose --env-file deploy/.env.production -f docker-compose.prod.yml \
  --profile ops run --rm migrate
docker compose --env-file deploy/.env.production -f docker-compose.prod.yml \
  --profile ops run --rm seed-catalog

# Install the reviewed systemd files at the target; user/path must match the service unit.
sudo install -d -o agentuser -g agentuser -m 0755 /var/lib/unified-learning
sudo install -o root -g root -m 0644 deploy/systemd/unified-learning-monitor.service /etc/systemd/system/
sudo install -o root -g root -m 0644 deploy/systemd/unified-learning-monitor.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start unified-learning-monitor.service  # creates the non-secret mount file; verify service result

docker compose --env-file deploy/.env.production \
  -f docker-compose.prod.yml -f docker-compose.ops.yml config --quiet
docker compose --env-file deploy/.env.production \
  -f docker-compose.prod.yml -f docker-compose.ops.yml up -d backend
sudo systemctl enable --now unified-learning-monitor.timer
```

Install/enable the existing backup service/timer separately after review. The monitor uses a Docker socket, local edge probe and host paths; inspect the unit's `SYSTEM_DIR`, env and backup paths if the checkout is not `%h/chaoxing/unified_system`. If the optional overlay is not installed, host metrics/alerts correctly remain unavailable rather than pretending to be healthy. Verify the `ops-check` result and `ops.view` permission after migration. Do not run these commands against production from the Arena sandbox.

## 7. Tests and verification performed

- Final backend suite: **152 passed, 2 skipped** (the skipped tests require PostgreSQL).
- Focused backup integrity/retention, keyring/rotation rollback, synthetic keyring round-trip, alert deduplication/cooldown/recovery, notifier-failure recording, host-metrics freshness, worker-activation logic, superadmin-only endpoint and restore-preflight tests passed using SQLite/fakes/synthetic fixtures.
- SQLite Alembic `upgrade head` and `alembic check` reported `0005_operational_alerts` / **No new upgrade operations detected**. This is not PostgreSQL migration acceptance.
- Frontend Vitest: **5 passed**; Vue typecheck and Vite production build passed.
- Python compilation, shell syntax (`bash -n`), Compose YAML parsing and `git diff --check` passed.
- `systemd-analyze verify` could not fully validate the monitor unit because the sandbox has no `docker.service`. Docker, Compose, PostgreSQL, `psql`, age and Nginx are unavailable here. No real archive encryption/decryption, backup, restore, host metrics cycle, timer run, PostgreSQL restore, or target Ubuntu test was done.

This completes the requested Phase 6D code/documentation scope only. Stop here; do not begin Phase 6E or represent these local/mock/SQLite results as production acceptance.
