#!/usr/bin/env bash
set -euo pipefail
umask 077
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
require_deploy_env

recipient="$(read_deploy_value BACKUP_AGE_RECIPIENT)"
retention="$(read_deploy_value BACKUP_RETENTION_DAYS)"
retention="${retention:-30}"
if [[ "$recipient" != age1* ]]; then
  printf 'BACKUP_AGE_RECIPIENT must be set to a valid age public recipient.\n' >&2
  exit 2
fi
if [[ ! "$retention" =~ ^[0-9]+$ ]] || (( retention < 1 || retention > 3650 )); then
  printf 'BACKUP_RETENTION_DAYS must be an integer from 1 to 3650.\n' >&2
  exit 2
fi
if ! command -v age >/dev/null 2>&1; then
  printf 'age is required to encrypt database backups before they are retained.\n' >&2
  exit 2
fi

backup_dir="${BACKUP_DIR:-$DEPLOY_DIR/backups}"
mkdir -p -- "$backup_dir"
chmod 700 "$backup_dir"
work_dir="$(mktemp -d "$backup_dir/.backup.XXXXXX")"
chmod 700 "$work_dir"
cleanup() { rm -rf -- "$work_dir"; }
trap cleanup EXIT HUP INT TERM

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
raw="$work_dir/unified_learning.dump"
archive="$backup_dir/unified_learning_${stamp}_$$.dump.age"
encrypted="$work_dir/backup.dump.age"

# pg_dump runs inside the private database container over its local socket. The plaintext
# dump exists only in a mode-0700 temporary directory and is removed after encryption.
compose exec -T postgres sh -ec \
  'pg_dump --no-password --username="$POSTGRES_USER" --dbname="$APP_DB_NAME" --format=custom' > "$raw"
test -s "$raw"
compose exec -T postgres pg_restore --list - < "$raw" >/dev/null
age --encrypt --recipient "$recipient" --output "$encrypted" "$raw"
test -s "$encrypted"
chmod 600 "$encrypted"
mv -- "$encrypted" "$archive"
chmod 600 "$archive"

find "$backup_dir" -maxdepth 1 -type f -name 'unified_learning_*.dump.age' -mtime "+$retention" -delete
printf 'Encrypted PostgreSQL backup created: %s\n' "$archive"
sha256sum "$archive"
printf 'Retention: %s days; old matching archives pruned.\n' "$retention"
