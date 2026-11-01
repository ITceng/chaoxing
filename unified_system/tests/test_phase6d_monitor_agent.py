from __future__ import annotations

import importlib.util
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


MONITOR_PATH = Path(__file__).resolve().parents[1] / "deploy" / "system-monitor.py"


def load_monitor():
    spec = importlib.util.spec_from_file_location("phase6d_system_monitor", MONITOR_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_host_agent_backup_metadata_is_bounded_and_checks_archive_size(tmp_path):
    monitor = load_monitor()
    name = "unified_learning_20261101T000000Z_123.dump.age"
    archive = tmp_path / name
    archive.write_bytes(b"encrypted-placeholder")
    created = datetime(2026, 11, 1, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    (tmp_path / f"{name}.json").write_text(json.dumps({
        "manifest_version": 1,
        "archive": name,
        "created_at_utc": created,
        "archive_bytes": archive.stat().st_size,
        "archive_sha256": "0" * 64,
    }))

    snapshot = monitor.latest_backup(tmp_path)
    assert snapshot["state"] == "available"
    assert snapshot["archive_bytes"] == len(b"encrypted-placeholder")
    assert snapshot["retained_count"] == 1
    # This is metadata/size monitoring, not a claim of re-hashing or age decryption.
    archive.write_bytes(b"different-size")
    assert monitor.latest_backup(tmp_path)["state"] == "missing"


def test_container_stat_parsers_retain_only_aggregated_numeric_values():
    monitor = load_monitor()
    assert monitor.parse_bytes("1.5GiB") == int(1.5 * 1024**3)
    assert monitor.parse_bytes("bad") is None
    assert monitor.parse_percent("12.5%") == 12.5
    assert monitor.parse_percent("bad") is None


def test_host_agent_writes_public_read_only_metrics_atomically(tmp_path):
    monitor = load_monitor()
    target = tmp_path / "host-metrics.json"
    snapshot = {"schema_version": 1, "captured_at": "2026-11-01T00:00:00Z", "host": {"cpu_percent": 25.0}}
    monitor.write_snapshot(target, snapshot)
    assert json.loads(target.read_text()) == snapshot
    assert target.stat().st_mode & 0o777 == 0o644


def test_compose_probe_uses_inspected_container_health(monkeypatch, tmp_path):
    monitor = load_monitor()
    calls = []

    def fake_run(args, timeout=12):
        calls.append(args)
        if args[-3:-1] == ["ps", "-q"]:
            return subprocess.CompletedProcess(args, 0, "container-id\n", "")
        if args[1] == "inspect":
            return subprocess.CompletedProcess(args, 0, json.dumps({"Status": "running", "Health": {"Status": "healthy"}}), "")
        return subprocess.CompletedProcess(args, 0, json.dumps({
            "CPUPerc": "12.5%", "MemUsage": "1.5GiB / 4GiB", "PIDs": "17",
        }), "")

    monkeypatch.setattr(monitor, "run", fake_run)
    result = monitor.compose_service_state(["docker", "compose"], "backend")
    assert result["state"] == "running"
    assert result["instances"] == 1
    assert result["resources"] == {
        "cpu_percent": 12.5,
        "memory_used_bytes": int(1.5 * 1024**3),
        "memory_limit_bytes": 4 * 1024**3,
        "memory_percent": 37.5,
        "pids_total": 17,
    }
    assert calls[0][-3:-1] == ["ps", "-q"]
    assert calls[1][1:3] == ["inspect", "--format"]
    assert calls[2][1:3] == ["stats", "--no-stream"]


def test_health_probe_reports_connection_failure_as_not_ready():
    monitor = load_monitor()
    assert monitor.readiness("1")["state"] == "not_ready"
