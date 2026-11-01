#!/usr/bin/env python3
"""Create/verify non-secret backup manifests and safely prune verified archives."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ARCHIVE_RE = re.compile(r"^unified_learning_(\d{8}T\d{6}Z)_([0-9]+)\.dump\.age$")
MANIFEST_VERSION = 1


class BackupError(ValueError):
    pass


def sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(block)
            digest.update(block)
    return digest.hexdigest(), size


def parse_archive_time(name: str) -> datetime:
    match = ARCHIVE_RE.fullmatch(name)
    if not match:
        raise BackupError("unexpected archive name")
    return datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def manifest_path(archive: Path) -> Path:
    return Path(f"{archive}.json")


def create_manifest(
    archive: Path,
    archive_name: str,
    *,
    created_at: str,
    server_version_num: int,
    pg_dump_version: str,
    recipient_fingerprint: str,
) -> dict:
    if not ARCHIVE_RE.fullmatch(archive_name):
        raise BackupError("archive name is not in the expected format")
    created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    if created.tzinfo is None:
        raise BackupError("created_at must include a timezone")
    digest, size = sha256_file(archive)
    if size < 1:
        raise BackupError("encrypted archive is empty")
    if not 10000 <= server_version_num <= 999999:
        raise BackupError("invalid PostgreSQL server_version_num")
    if not re.fullmatch(r"[0-9a-f]{16}", recipient_fingerprint):
        raise BackupError("recipient fingerprint must be a 16-character lowercase hex digest")
    return {
        "manifest_version": MANIFEST_VERSION,
        "archive": archive_name,
        "created_at_utc": created.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "format": "pg_dump_custom_encrypted_age",
        "archive_bytes": size,
        "archive_sha256": digest,
        "postgres_server_version_num": server_version_num,
        "pg_dump_version": pg_dump_version[:128],
        "age_recipient_fingerprint": recipient_fingerprint,
    }


def write_manifest(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + f".tmp.{os.getpid()}")
    data = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def read_and_verify_manifest(archive: Path) -> dict:
    if archive.is_symlink() or not archive.is_file():
        raise BackupError("archive must be a regular file")
    sidecar = manifest_path(archive)
    if sidecar.is_symlink() or not sidecar.is_file():
        raise BackupError("verified manifest sidecar is missing")
    try:
        value = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError("manifest is unreadable or invalid JSON") from exc
    if not isinstance(value, dict) or value.get("manifest_version") != MANIFEST_VERSION:
        raise BackupError("unsupported backup manifest version")
    if value.get("archive") != archive.name or value.get("format") != "pg_dump_custom_encrypted_age":
        raise BackupError("manifest does not describe this archive")
    archive_time = parse_archive_time(archive.name)
    try:
        created_at = datetime.fromisoformat(value["created_at_utc"].replace("Z", "+00:00"))
    except (KeyError, AttributeError, TypeError, ValueError) as exc:
        raise BackupError("manifest has an invalid creation timestamp") from exc
    if created_at.tzinfo is None or abs((created_at.astimezone(timezone.utc) - archive_time).total_seconds()) > 2:
        raise BackupError("manifest timestamp does not match the archive filename")
    if not isinstance(value.get("archive_bytes"), int) or not re.fullmatch(r"[0-9a-f]{64}", str(value.get("archive_sha256", ""))):
        raise BackupError("manifest size or checksum is invalid")
    if not isinstance(value.get("postgres_server_version_num"), int) or not 10000 <= value["postgres_server_version_num"] <= 999999:
        raise BackupError("manifest PostgreSQL version is invalid")
    if not re.fullmatch(r"[0-9a-f]{16}", str(value.get("age_recipient_fingerprint", ""))):
        raise BackupError("manifest age-recipient identifier is invalid")
    digest, size = sha256_file(archive)
    if value.get("archive_bytes") != size or value.get("archive_sha256") != digest:
        raise BackupError("archive integrity check failed")
    parse_archive_time(archive.name)
    return value


def plan_prune(
    archive_paths: list[Path],
    *,
    newest_name: str,
    now: datetime,
    daily_days: int,
    weekly_weeks: int,
    monthly_months: int,
) -> list[Path]:
    if not 1 <= daily_days <= 3650 or not 1 <= weekly_weeks <= 520 or not 1 <= monthly_months <= 120:
        raise BackupError("retention values are outside supported ranges")
    verified: list[tuple[Path, datetime]] = []
    for archive in archive_paths:
        try:
            read_and_verify_manifest(archive)
            timestamp = parse_archive_time(archive.name)
        except (BackupError, OSError):
            # Unknown, legacy or damaged files are retained for an operator to inspect.
            continue
        verified.append((archive, timestamp))
    if not any(path.name == newest_name for path, _ in verified):
        raise BackupError("newest successful backup is not present and verified; refusing retention cleanup")
    if now.tzinfo is None:
        raise BackupError("retention clock must be timezone-aware")
    now = now.astimezone(timezone.utc)
    keep = {newest_name}
    daily_cutoff = now - timedelta(days=daily_days)
    dated = sorted(verified, key=lambda item: item[1], reverse=True)
    for archive, timestamp in dated:
        if timestamp >= daily_cutoff:
            keep.add(archive.name)

    weekly_cutoff = now - timedelta(weeks=weekly_weeks)
    latest_by_week: dict[tuple[int, int], tuple[Path, datetime]] = {}
    for archive, timestamp in dated:
        if timestamp < weekly_cutoff:
            continue
        iso = timestamp.isocalendar()
        bucket = (iso.year, iso.week)
        latest_by_week.setdefault(bucket, (archive, timestamp))
    keep.update(path.name for path, _ in latest_by_week.values())

    month_index_now = now.year * 12 + now.month - 1
    first_retained_month = month_index_now - monthly_months + 1
    latest_by_month: dict[tuple[int, int], tuple[Path, datetime]] = {}
    for archive, timestamp in dated:
        index = timestamp.year * 12 + timestamp.month - 1
        if index < first_retained_month:
            continue
        latest_by_month.setdefault((timestamp.year, timestamp.month), (archive, timestamp))
    keep.update(path.name for path, _ in latest_by_month.values())
    return [path for path, _ in verified if path.name not in keep]


def prune_directory(
    directory: Path,
    *,
    newest_name: str,
    now: datetime,
    daily_days: int,
    weekly_weeks: int,
    monthly_months: int,
) -> list[str]:
    if directory.is_symlink() or not directory.is_dir():
        raise BackupError("backup directory must be a real directory")
    candidates = [
        path for path in directory.iterdir()
        if ARCHIVE_RE.fullmatch(path.name) and not path.is_symlink() and path.is_file()
    ]
    remove = plan_prune(
        candidates,
        newest_name=newest_name,
        now=now,
        daily_days=daily_days,
        weekly_weeks=weekly_weeks,
        monthly_months=monthly_months,
    )
    removed: list[str] = []
    for archive in remove:
        # Re-check immediately before deletion; a replaced/corrupted file is retained.
        try:
            read_and_verify_manifest(archive)
        except (BackupError, OSError):
            continue
        sidecar = manifest_path(archive)
        sidecar.unlink()
        archive.unlink()
        removed.append(archive.name)
    return removed


def _write_manifest(args: argparse.Namespace) -> int:
    value = create_manifest(
        Path(args.archive), args.archive_name,
        created_at=args.created_at,
        server_version_num=args.server_version_num,
        pg_dump_version=args.pg_dump_version,
        recipient_fingerprint=args.recipient_fingerprint,
    )
    write_manifest(Path(args.output), value)
    print(json.dumps(value, sort_keys=True))
    return 0


def _verify(args: argparse.Namespace) -> int:
    value = read_and_verify_manifest(Path(args.archive))
    print(json.dumps({"status": "verified", "archive": Path(args.archive).name,
                      "bytes": value["archive_bytes"], "sha256": value["archive_sha256"]}, sort_keys=True))
    return 0


def _prune(args: argparse.Namespace) -> int:
    removed = prune_directory(
        Path(args.directory), newest_name=args.newest,
        now=datetime.now(timezone.utc), daily_days=args.daily_days,
        weekly_weeks=args.weekly_weeks, monthly_months=args.monthly_months,
    )
    for name in removed:
        print(f"pruned verified expired backup: {name}")
    print(f"retention complete; removed={len(removed)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    manifest = subparsers.add_parser("manifest")
    manifest.add_argument("--archive", required=True)
    manifest.add_argument("--archive-name", required=True)
    manifest.add_argument("--output", required=True)
    manifest.add_argument("--created-at", required=True)
    manifest.add_argument("--server-version-num", required=True, type=int)
    manifest.add_argument("--pg-dump-version", required=True)
    manifest.add_argument("--recipient-fingerprint", required=True)
    manifest.set_defaults(func=_write_manifest)
    verify = subparsers.add_parser("verify")
    verify.add_argument("archive")
    verify.set_defaults(func=_verify)
    prune = subparsers.add_parser("prune")
    prune.add_argument("--directory", required=True)
    prune.add_argument("--newest", required=True)
    prune.add_argument("--daily-days", required=True, type=int)
    prune.add_argument("--weekly-weeks", required=True, type=int)
    prune.add_argument("--monthly-months", required=True, type=int)
    prune.set_defaults(func=_prune)
    args = parser.parse_args()
    try:
        return args.func(args)
    except (BackupError, OSError, ValueError) as exc:
        print(f"backup metadata operation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
