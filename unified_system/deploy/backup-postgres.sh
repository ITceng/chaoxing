#!/usr/bin/env bash
set -euo pipefail
umask 077
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
require_deploy_env

recipient="$(read_deploy_value BACKUP_AGE_RECIPIENT)"
daily_days="$(read_deploy_value BACKUP_DAILY_DAYS)"
weekly_weeks="$(read_deploy_value BACKUP_WEEKLY_WEEKS)"
monthly_months="$(read_deploy_value BACKUP_MONTHLY_MONTHS)"
daily_days="${daily_days:-7}"
weekly_weeks="${weekly_weeks:-8}"
monthly_months="${monthly_months:-12}"
if [[ "$recipient" != age1* ]]; then
  printf 'BACKUP_AGE_RECIPIENT must be set to an age public recipient.\n' >&2
  exit 2
fi
if [[ ! "$daily_days" =~ ^[0-9]{1,4}$ ]] || (( daily_days < 1 || daily_days > 3650 )); then
  printf 'BACKUP_DAILY_DAYS must be an integer from 1 to 3650.\n' >&2
  exit 2
fi
if [[ ! "$weekly_weeks" =~ ^[0-9]{1,3}$ ]] || (( weekly_weeks < 1 || weekly_weeks > 520 )); then
  printf 'BACKUP_WEEKLY_WEEKS must be an integer from 1 to 520.\n' >&2
  exit 2
fi
if [[ ! "$monthly_months" =~ ^[0-9]{1,3}$ ]] || (( monthly_months < 1 || monthly_months > 120 )); then
  printf 'BACKUP_MONTHLY_MONTHS must be an integer from 1 to 120.\n' >&2
  exit 2
fi
if ! command -v age >/dev/null 2>&1; then
  printf 'age is required to encrypt PostgreSQL backups before they are retained.\n' >&2
  exit 2
fi
if ! command -v python3 >/dev/null 2>&1; then
  printf 'python3 is required for backup manifest and retention validation.\n' >&2
  exit 2
fi

backup_dir="${BACKUP_DIR:-$DEPLOY_DIR/backups}"
mkdir -p -- "$backup_dir"
if [[ -L "$backup_dir" || ! -d "$backup_dir" ]]; then
  printf 'BACKUP_DIR must be a real directory, not a symlink.\n' >&2
  exit 2
fi
chmod 700 "$backup_dir"
work_dir="$(mktemp -d "$backup_dir/.backup.XXXXXX")"
chmod 700 "$work_dir"
cleanup() { rm -rf -- "$work_dir"; }
trap cleanup EXIT HUP INT TERM

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
created_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
archive_name="unified_learning_${stamp}_$$.dump.age"
raw="$work_dir/unified_learning.dump"
encrypted="$work_dir/$archive_name"
manifest="$work_dir/$archive_name.json"
archive="$backup_dir/$archive_name"

# This is a full logical application-database backup, not a raw PGDATA file copy.
# pg_dump's plaintext stream exists only in this protected temporary directory.
compose exec -T postgres sh -ec \
  'pg_dump --no-password --username="$POSTGRES_USER" --dbname="$APP_DB_NAME" --format=custom' > "$raw"
test -s "$raw"
compose exec -T postgres pg_restore --list - < "$raw" >/dev/null
server_version_num="$(compose exec -T postgres sh -ec \
  'psql --no-password --username="$POSTGRES_USER" --dbname="$APP_DB_NAME" -Atc "SHOW server_version_num"' | tr -d '\r')"
pg_dump_version="$(compose exec -T postgres pg_dump --version | tr -d '\r')"
if [[ ! "$server_version_num" =~ ^[0-9]{5,6}$ ]]; then
  printf 'Could not read a valid PostgreSQL server version; refusing to publish backup.\n' >&2
  exit 1
fi

recipient_fingerprint="$(printf '%s' "$recipient" | sha256sum | cut -c1-16)"
age --encrypt --recipient "$recipient" --output "$encrypted" "$raw"
test -s "$encrypted"
chmod 600 "$encrypted"
python3 "$DEPLOY_DIR/backup-tools.py" manifest \
  --archive "$encrypted" \
  --archive-name "$archive_name" \
  --output "$manifest" \
  --created-at "$created_at" \
  --server-version-num "$server_version_num" \
  --pg-dump-version "$pg_dump_version" \
  --recipient-fingerprint "$recipient_fingerprint" >/dev/null
python3 "$DEPLOY_DIR/backup-tools.py" verify "$encrypted" >/dev/null

# Publish archive first, manifest second. If interrupted between renames, the archive
# is deliberately treated as unverified and retention will preserve it for inspection.
mv -- "$encrypted" "$archive"
chmod 600 "$archive"
mv -- "$manifest" "$archive.json"
chmod 600 "$archive.json"
python3 "$DEPLOY_DIR/backup-tools.py" verify "$archive" >/dev/null

# Pruning happens only after the new encrypted archive and integrity manifest both verify.
python3 "$DEPLOY_DIR/backup-tools.py" prune \
  --directory "$backup_dir" --newest "$archive_name" \
  --daily-days "$daily_days" --weekly-weeks "$weekly_weeks" --monthly-months "$monthly_months"
printf 'Encrypted full PostgreSQL backup successful: %s\n' "$archive_name"
