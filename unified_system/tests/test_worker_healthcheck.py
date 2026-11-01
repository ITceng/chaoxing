from __future__ import annotations

from unified_system.workers.runtime.healthcheck import clear_health_marker, is_healthy, record_backend_heartbeat


def test_worker_healthcheck_requires_a_recent_successful_backend_heartbeat(tmp_path, monkeypatch):
    marker = tmp_path / "worker-health"
    monkeypatch.setenv("WORKER_HEALTH_FILE", str(marker))
    monkeypatch.setenv("WORKER_HEARTBEAT_TTL_SECONDS", "10")

    assert not is_healthy()
    assert record_backend_heartbeat() is True
    modified = marker.stat().st_mtime
    assert is_healthy(now=modified + 5)
    assert not is_healthy(now=modified + 11)

    clear_health_marker()
    assert not marker.exists()
    assert not is_healthy()


def test_worker_healthcheck_fails_closed_on_bad_ttl_or_future_marker(tmp_path, monkeypatch):
    marker = tmp_path / "worker-health"
    monkeypatch.setenv("WORKER_HEALTH_FILE", str(marker))
    marker.touch()
    monkeypatch.setenv("WORKER_HEARTBEAT_TTL_SECONDS", "not-a-number")
    assert not is_healthy()

    monkeypatch.setenv("WORKER_HEARTBEAT_TTL_SECONDS", "90")
    assert not is_healthy(now=marker.stat().st_mtime - 10)
