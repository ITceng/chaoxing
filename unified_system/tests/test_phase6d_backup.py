from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOLS_PATH = ROOT / "deploy" / "backup-tools.py"
SPEC = importlib.util.spec_from_file_location("phase6d_backup_tools", TOOLS_PATH)
assert SPEC and SPEC.loader
backup_tools = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup_tools)


def create_archive(directory: Path, when: datetime, pid: int, payload: bytes = b"test-only ciphertext") -> Path:
    name = f"unified_learning_{when.strftime('%Y%m%dT%H%M%SZ')}_{pid}.dump.age"
    archive = directory / name
    archive.write_bytes(payload)
    manifest = backup_tools.create_manifest(
        archive,
        name,
        created_at=when.isoformat().replace("+00:00", "Z"),
        server_version_num=160004,
        pg_dump_version="pg_dump (PostgreSQL) 16.4",
        recipient_fingerprint="0123456789abcdef",
    )
    backup_tools.write_manifest(backup_tools.manifest_path(archive), manifest)
    return archive


def test_backup_manifest_verifies_encrypted_archive_bytes_and_detects_tampering(tmp_path):
    timestamp = datetime(2026, 11, 1, 3, 15, tzinfo=timezone.utc)
    archive = create_archive(tmp_path, timestamp, 1234)
    assert backup_tools.read_and_verify_manifest(archive)["format"] == "pg_dump_custom_encrypted_age"
    archive.write_bytes(b"changed")
    with pytest.raises(backup_tools.BackupError, match="integrity"):
        backup_tools.read_and_verify_manifest(archive)


def test_retention_keeps_newest_daily_weekly_and_monthly_points(tmp_path):
    now = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    timestamps = [
        datetime(2026, 11, 1, 3, 0, tzinfo=timezone.utc),
        datetime(2026, 10, 31, 3, 0, tzinfo=timezone.utc),
        datetime(2026, 10, 15, 3, 0, tzinfo=timezone.utc),
        datetime(2026, 9, 20, 3, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 1, 3, 0, tzinfo=timezone.utc),
    ]
    archives = [create_archive(tmp_path, stamp, index + 1) for index, stamp in enumerate(timestamps)]
    remove = backup_tools.plan_prune(
        archives,
        newest_name=archives[0].name,
        now=now,
        daily_days=7,
        weekly_weeks=2,
        monthly_months=3,
    )
    keep_names = {path.name for path in archives} - {path.name for path in remove}
    assert archives[0].name in keep_names
    assert archives[1].name in keep_names  # daily / newest October monthly point
    assert archives[2].name not in keep_names  # superseded in the same month
    assert archives[3].name in keep_names  # one monthly point for September
    assert archives[4].name not in keep_names  # outside the three-month window


def test_retention_refuses_to_prune_until_new_backup_is_verified(tmp_path):
    now = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    archive = create_archive(tmp_path, datetime(2026, 10, 1, tzinfo=timezone.utc), 1)
    with pytest.raises(backup_tools.BackupError, match="newest successful backup"):
        backup_tools.plan_prune(
            [archive], newest_name="missing.dump.age", now=now,
            daily_days=7, weekly_weeks=4, monthly_months=12,
        )


def test_retention_leaves_unmanaged_or_damaged_legacy_archives_untouched(tmp_path):
    now = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    latest = create_archive(tmp_path, now, 2)
    legacy = tmp_path / "unified_learning_20200101T000000Z_1.dump.age"
    legacy.write_bytes(b"legacy no sidecar")
    damaged = create_archive(tmp_path, datetime(2020, 1, 2, tzinfo=timezone.utc), 3)
    damaged.write_bytes(b"corrupted")
    removed = backup_tools.prune_directory(
        tmp_path, newest_name=latest.name, now=now,
        daily_days=7, weekly_weeks=4, monthly_months=3,
    )
    assert legacy.name not in removed and legacy.exists()
    assert damaged.name not in removed and damaged.exists()
    assert latest.exists()


def test_backup_metadata_rejects_name_timestamp_mismatch(tmp_path):
    stamp = datetime(2026, 11, 1, 3, 15, tzinfo=timezone.utc)
    archive = create_archive(tmp_path, stamp, 4)
    sidecar = backup_tools.manifest_path(archive)
    metadata = sidecar.read_text()
    sidecar.write_text(metadata.replace("2026-11-01T03:15:00Z", "2026-10-31T03:15:00Z"))
    with pytest.raises(backup_tools.BackupError, match="timestamp"):
        backup_tools.read_and_verify_manifest(archive)
