#!/usr/bin/env bash
set -euo pipefail
umask 077

DEPLOY_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SYSTEM_DIR="$(cd -- "$DEPLOY_DIR/.." && pwd)"
source "$DEPLOY_DIR/lib.sh"

# Restores are only allowed into an isolated, separately named Compose project using
# its own environment file/volume. The production Compose project is never accepted.
RESTORE_ENV_FILE="${RESTORE_ENV_FILE:-}"
RESTORE_PROJECT_NAME="${RESTORE_PROJECT_NAME:-}"
RESTORE_TARGET_DB="${RESTORE_TARGET_DB:-}"
if [[ -z "$RESTORE_ENV_FILE" || -z "$RESTORE_PROJECT_NAME" || -z "$RESTORE_TARGET_DB" ]]; then
  printf 'Set RESTORE_ENV_FILE, RESTORE_PROJECT_NAME, and RESTORE_TARGET_DB for an isolated rehearsal.\n' >&2
  exit 2
fi
if [[ ! -r "$RESTORE_ENV_FILE" ]]; then
  printf 'The isolated restore environment file must exist and be readable.\n' >&2
  exit 2
fi
ENV_FILE="$(realpath -e -- "$RESTORE_ENV_FILE")"
production_env="$(realpath -m -- "$DEPLOY_DIR/.env.production")"
if [[ "$ENV_FILE" == "$production_env" ]]; then
  printf 'Refusing to use the production environment file for restore rehearsal.\n' >&2
  exit 2
fi
mode="$(stat -c '%a' "$ENV_FILE")"
if (( (8#$mode & 077) != 0 )); then
  printf 'The isolated restore env file must not be accessible by group/other (chmod 600).\n' >&2
  exit 2
fi
if [[ ! "$RESTORE_PROJECT_NAME" =~ ^unified-learning-restore-[a-z0-9-]{1,32}$ ]]; then
  printf 'RESTORE_PROJECT_NAME must use the unified-learning-restore-<unique-suffix> form.\n' >&2
  exit 2
fi
if [[ ! "$RESTORE_TARGET_DB" =~ ^[a-z][a-z0-9_]{0,48}_(restore|test|testdb)$ ]]; then
  printf 'RESTORE_TARGET_DB must be a new isolated database ending in _restore, _test, or _testdb.\n' >&2
  exit 2
fi
if [[ $# -ne 2 || "$2" != "--confirm-isolated-restore" ]]; then
  printf 'Usage: RESTORE_ENV_FILE=... RESTORE_PROJECT_NAME=... RESTORE_TARGET_DB=... %s <encrypted-backup.dump.age> --confirm-isolated-restore\n' "$0" >&2
  exit 2
fi
archive="$(realpath -e -- "$1")"
if [[ ! -f "$archive" || "$archive" != *.dump.age || -L "$archive" ]]; then
  printf 'A regular *.dump.age archive is required.\n' >&2
  exit 2
fi
identity="${BACKUP_AGE_IDENTITY_FILE:-}"
if [[ -z "$identity" || ! -r "$identity" || -L "$identity" ]]; then
  printf 'Set BACKUP_AGE_IDENTITY_FILE to a readable, protected age identity file.\n' >&2
  exit 2
fi
identity="$(realpath -e -- "$identity")"
mode="$(stat -c '%a' "$identity")"
if (( (8#$mode & 077) != 0 )); then
  printf 'The age identity file must not be accessible by group/other users (chmod 600).\n' >&2
  exit 2
fi
if ! command -v age >/dev/null 2>&1; then
  printf 'age is required to decrypt this backup.\n' >&2
  exit 2
fi
if ! command -v python3 >/dev/null 2>&1 || ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  printf 'Python 3 and Docker Compose v2 are required for an isolated restore rehearsal.\n' >&2
  exit 2
fi

# Use production service definitions but a unique project name and its own env file/volume.
compose_restore() {
  docker compose --project-name "$RESTORE_PROJECT_NAME" --project-directory "$SYSTEM_DIR" \
    --env-file "$ENV_FILE" -f "$SYSTEM_DIR/docker-compose.prod.yml" -f "$SYSTEM_DIR/docker-compose.test.yml" "$@"
}

manifest_json="$(python3 "$DEPLOY_DIR/backup-tools.py" verify "$archive")"
source_version_num="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["postgres_server_version_num"])' <<<"$manifest_json")"
source_checksum="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["sha256"])' <<<"$manifest_json")"
app_db_name="$(read_deploy_value APP_DB_NAME)"
if [[ "$RESTORE_TARGET_DB" == "$app_db_name" ]]; then
  printf 'Restore target must not equal the isolated Compose app database name.\n' >&2
  exit 2
fi
owner="$(read_deploy_value APP_DB_OWNER)"
if [[ ! "$owner" =~ ^[a-z][a-z0-9_]{0,62}$ ]]; then
  printf 'APP_DB_OWNER in the restore environment is missing or invalid.\n' >&2
  exit 2
fi

printf 'This will create a new database named "%s" in isolated Compose project "%s".\n' "$RESTORE_TARGET_DB" "$RESTORE_PROJECT_NAME"
printf 'No existing database is overwritten. Confirm this environment contains no production volume.\n'
read -r -p "Type RESTORE-ISOLATED:${RESTORE_TARGET_DB} to continue: " confirmation
if [[ "$confirmation" != "RESTORE-ISOLATED:${RESTORE_TARGET_DB}" ]]; then
  printf 'Restore rehearsal cancelled.\n' >&2
  exit 2
fi

compose_restore up -d postgres
ready=0
for _attempt in $(seq 1 60); do
  if compose_restore exec -T postgres sh -ec 'pg_isready --username="$POSTGRES_USER" --dbname=postgres' >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
if (( ready != 1 )); then
  printf 'Isolated PostgreSQL did not become ready; no restore was attempted.\n' >&2
  exit 1
fi

target_version_num="$(compose_restore exec -T postgres sh -ec \
  'psql --no-password --username="$POSTGRES_USER" --dbname=postgres -Atc "SHOW server_version_num"' | tr -d '\r')"
if [[ ! "$target_version_num" =~ ^[0-9]{5,6}$ ]] || (( target_version_num / 10000 != source_version_num / 10000 )); then
  printf 'PostgreSQL major-version mismatch; source=%s target=%s; refusing restore.\n' "$source_version_num" "$target_version_num" >&2
  exit 2
fi

# Decryption is streamed directly into pg_restore; no plaintext dump is written to disk.
# Validate the authenticated age payload and custom-format TOC before creating the target DB.
age --decrypt --identity "$identity" "$archive" | compose_restore exec -T postgres pg_restore --list - >/dev/null
# `createdb` fails if the isolated target already exists; no --clean or overwrite path exists.
if ! compose_restore exec -T postgres sh -ec \
  'createdb --no-password --username="$POSTGRES_USER" --owner="$1" "$2"' sh "$owner" "$RESTORE_TARGET_DB"; then
  printf 'Could not create a new restore target. It may already exist; no data was overwritten.\n' >&2
  exit 2
fi
age --decrypt --identity "$identity" "$archive" | compose_restore exec -T postgres sh -ec \
  'pg_restore --no-password --username="$POSTGRES_USER" --role="$1" --dbname="$2" --no-owner --single-transaction --exit-on-error' \
  sh "$owner" "$RESTORE_TARGET_DB"

report_dir="${RESTORE_REPORT_DIR:-$DEPLOY_DIR/restore-reports}"
mkdir -p -- "$report_dir"
chmod 700 "$report_dir"
report_tmp="$(mktemp "$report_dir/.restore-report.XXXXXX")"
trap 'rm -f -- "$report_tmp"' EXIT HUP INT TERM
query="SELECT 'users', count(*) FROM users UNION ALL SELECT 'roles', count(*) FROM roles UNION ALL SELECT 'invitations', count(*) FROM invitations UNION ALL SELECT 'platform_accounts', count(*) FROM platform_accounts UNION ALL SELECT 'platform_account_secrets', count(*) FROM platform_account_secrets UNION ALL SELECT 'account_access_grants', count(*) FROM account_access_grants UNION ALL SELECT 'tasks', count(*) FROM tasks UNION ALL SELECT 'task_events', count(*) FROM task_events UNION ALL SELECT 'audit_logs', count(*) FROM audit_logs UNION ALL SELECT 'ai_provider_configs', count(*) FROM ai_provider_configs UNION ALL SELECT 'ai_usage_logs', count(*) FROM ai_usage_logs ORDER BY 1"
counts="$(compose_restore exec -T postgres psql -X -A -t -F $'\t' --no-password --username "$owner" --dbname "$RESTORE_TARGET_DB" -v ON_ERROR_STOP=1 -c "$query" | tr -d '\r')"
alembic_revision="$(compose_restore exec -T postgres psql -X -A -t --no-password --username "$owner" --dbname "$RESTORE_TARGET_DB" -v ON_ERROR_STOP=1 -c 'SELECT version_num FROM alembic_version' | tr -d '\r')"
unvalidated_fks="$(compose_restore exec -T postgres psql -X -A -t --no-password --username "$owner" --dbname "$RESTORE_TARGET_DB" -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM pg_constraint WHERE contype='f' AND NOT convalidated" | tr -d '\r')"
python3 - "$report_tmp" "$archive" "$RESTORE_TARGET_DB" "$source_version_num" "$target_version_num" "$source_checksum" "$alembic_revision" "$unvalidated_fks" "$counts" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

out, archive, database, source_version, target_version, checksum, revision, invalid_fks, raw_counts = sys.argv[1:]
counts = {}
for line in raw_counts.splitlines():
    if not line:
        continue
    name, value = line.split("\t", 1)
    counts[name] = int(value)
expected = {
    "users", "roles", "invitations", "platform_accounts", "platform_account_secrets",
    "account_access_grants", "tasks", "task_events", "audit_logs", "ai_provider_configs", "ai_usage_logs",
}
if set(counts) != expected:
    raise SystemExit("restore validation did not return the complete required table set")
if int(invalid_fks) != 0:
    raise SystemExit("restore validation found unvalidated foreign-key constraints")
report = {
    "status": "restored_and_validated",
    "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    "archive": Path(archive).name,
    "archive_sha256": checksum,
    "target_database": database,
    "source_postgres_version_num": int(source_version),
    "restore_postgres_version_num": int(target_version),
    "alembic_revision": revision,
    "unvalidated_foreign_keys": int(invalid_fks),
    "table_row_counts": counts,
    "credential_decryption_test": "not_run; use an isolated synthetic fixture and matching keyring",
}
with open(out, "w", encoding="utf-8") as handle:
    json.dump(report, handle, sort_keys=True, separators=(",", ":"))
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
PY
report="$report_dir/restore_$(date -u +%Y%m%dT%H%M%SZ)_${RESTORE_TARGET_DB}.json"
mv -- "$report_tmp" "$report"
chmod 600 "$report"
printf 'Isolated PostgreSQL restore and validation succeeded. Report: %s\n' "$report"
printf 'The restore project contains only the separate rehearsal volume; review it before cleanup.\n'
