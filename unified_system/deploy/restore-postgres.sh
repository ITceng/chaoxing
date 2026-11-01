#!/usr/bin/env bash
set -euo pipefail
umask 077
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
require_deploy_env

if [[ $# -ne 2 || "$2" != "--confirm-overwrite" ]]; then
  printf 'Usage: %s <encrypted-backup.dump.age> --confirm-overwrite\n' "$0" >&2
  exit 2
fi
archive="$(realpath -e -- "$1")"
if [[ ! -f "$archive" || "$archive" != *.dump.age ]]; then
  printf 'A regular *.dump.age backup file is required.\n' >&2
  exit 2
fi
identity="${BACKUP_AGE_IDENTITY_FILE:-}"
if [[ -z "$identity" || ! -r "$identity" ]]; then
  printf 'Set BACKUP_AGE_IDENTITY_FILE to a readable, protected age identity file.\n' >&2
  exit 2
fi
mode="$(stat -c '%a' "$identity")"
if (( (8#$mode & 077) != 0 )); then
  printf 'The age identity file must not be accessible by group or other users (chmod 600).\n' >&2
  exit 2
fi
if ! command -v age >/dev/null 2>&1; then
  printf 'age is required to decrypt this backup.\n' >&2
  exit 2
fi

db_name="$(read_deploy_value APP_DB_NAME)"
if [[ -z "$db_name" ]]; then
  printf 'APP_DB_NAME is missing from the deployment environment.\n' >&2
  exit 2
fi
printf 'WARNING: this destructively replaces objects/data represented in the archive in database "%s"; objects outside the archive may remain.\n' "$db_name"
printf 'Encrypted archive: %s\n' "$archive"
read -r -p "Type RESTORE:${db_name} to continue: " confirmation
if [[ "$confirmation" != "RESTORE:${db_name}" ]]; then
  printf 'Restore cancelled.\n' >&2
  exit 2
fi

compose up -d postgres
# Validate decryption and the PostgreSQL custom archive TOC before stopping application services.
age --decrypt --identity "$identity" "$archive" | compose exec -T postgres pg_restore --list - >/dev/null

stop_if_profile_service_running worker-chaoxing worker-chaoxing
stop_if_profile_service_running worker-zhihuishu worker-zhihuishu
stop_if_profile_service_running worker-yinghua worker-yinghua
compose stop edge backend

# Restore as the non-superuser migration owner. The app and edge remain stopped afterwards
# so an operator can inspect the result before allowing new requests or Worker claims.
age --decrypt --identity "$identity" "$archive" | compose exec -T postgres sh -ec \
  'pg_restore --no-password --username="$POSTGRES_USER" --role="$APP_DB_OWNER" --dbname="$APP_DB_NAME" --clean --if-exists --no-owner --single-transaction'
printf 'Restore completed. Backend and edge remain stopped; verify before running docker compose up.\n'
