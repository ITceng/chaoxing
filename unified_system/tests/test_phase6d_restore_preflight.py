from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESTORE_SCRIPT = ROOT / "deploy" / "restore-postgres.sh"


def _restore_env(tmp_path: Path) -> tuple[Path, Path, Path]:
    env_file = tmp_path / ".env.restore-test"
    env_file.write_text(
        "POSTGRES_ADMIN_USER=restore_admin\n"
        "POSTGRES_ADMIN_PASSWORD=placeholder-not-a-real-secret\n"
        "APP_DB_NAME=unified_learning_restore_seed\n"
        "APP_DB_OWNER=unified_restore_migrator\n"
        "APP_DB_OWNER_PASSWORD=placeholder-not-a-real-secret\n"
        "APP_DB_USER=unified_restore_runtime\n"
        "APP_DB_PASSWORD=placeholder-not-a-real-secret\n"
    )
    env_file.chmod(0o600)
    identity = tmp_path / "age-identity-test.txt"
    identity.write_text("AGE-SECRET-KEY-1synthetic-test-only\n")
    identity.chmod(0o600)
    archive = tmp_path / "unified_learning_20261101T000000Z_1.dump.age"
    archive.write_bytes(b"not-an-encrypted-backup; preflight must stop before restore")
    return env_file, identity, archive


def test_restore_rejects_non_isolated_target_before_tool_or_database_actions(tmp_path):
    env_file, identity, archive = _restore_env(tmp_path)
    env = os.environ.copy()
    env.update({
        "RESTORE_ENV_FILE": str(env_file),
        "RESTORE_PROJECT_NAME": "unified-learning-restore-preflight1",
        "RESTORE_TARGET_DB": "unified_learning",
        "BACKUP_AGE_IDENTITY_FILE": str(identity),
    })
    result = subprocess.run(
        ["bash", str(RESTORE_SCRIPT), str(archive), "--confirm-isolated-restore"],
        cwd=ROOT, env=env, text=True, capture_output=True, timeout=15,
    )
    assert result.returncode == 2
    assert "new isolated database" in result.stderr
    assert "RESTORE-ISOLATED" not in result.stdout
    assert "production" not in result.stdout.lower()


def test_restore_preflight_uses_protected_separate_env_and_stops_before_restore(tmp_path):
    env_file, identity, archive = _restore_env(tmp_path)
    env = os.environ.copy()
    env.update({
        "RESTORE_ENV_FILE": str(env_file),
        "RESTORE_PROJECT_NAME": "unified-learning-restore-preflight2",
        "RESTORE_TARGET_DB": "unified_learning_testdb",
        "BACKUP_AGE_IDENTITY_FILE": str(identity),
    })
    result = subprocess.run(
        ["bash", str(RESTORE_SCRIPT), str(archive), "--confirm-isolated-restore"],
        cwd=ROOT, env=env, text=True, capture_output=True, timeout=15,
    )
    # With the deliberately invalid fixture archive, preflight/tool availability stops the
    # script before confirmation, Compose startup, database creation, or pg_restore.
    assert result.returncode != 0
    assert "RESTORE-ISOLATED:" not in result.stdout
    assert "createdb" not in result.stdout.lower()
    assert "pg_restore" not in result.stdout.lower()
    assert "AGE-SECRET-KEY-1synthetic-test-only" not in result.stdout + result.stderr
    assert env_file.stat().st_mode & 0o077 == 0
    assert identity.stat().st_mode & 0o077 == 0
