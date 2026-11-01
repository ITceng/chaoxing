from __future__ import annotations

import json
from datetime import timedelta, timezone

import pytest
from sqlalchemy import select

from unified_system.db import get_db
from unified_system.models import (
    OperationalAlert, Platform, Role, User, UserRole, WorkerInstance, utcnow,
)
from unified_system.operations import (
    AlertManager, AlertSignal, build_ops_overview, evaluate_and_persist_alerts, read_host_metrics,
)
from unified_system.security import hash_password


@pytest.fixture()
def ops_db(app):
    with app.app_context():
        yield get_db()


class RecordingNotifier:
    def __init__(self, fail: bool = False):
        self.events = []
        self.fail = fail

    def notify(self, event, alert):
        if self.fail:
            raise RuntimeError("private notifier detail must not be stored")
        self.events.append((event, alert.fingerprint))


def test_host_metrics_requires_regular_versioned_fresh_snapshot(tmp_path):
    now = utcnow().replace(tzinfo=timezone.utc)
    target = tmp_path / "host.json"
    assert read_host_metrics(target, now=now)["state"] == "missing"

    target.write_text(json.dumps({"schema_version": 1, "captured_at": now.isoformat(), "host": {"cpu_percent": 12.3}}))
    result = read_host_metrics(target, now=now)
    assert result["state"] == "ok"
    assert result["snapshot"]["host"]["cpu_percent"] == 12.3

    stale_time = now - timedelta(minutes=10)
    target.write_text(json.dumps({"schema_version": 1, "captured_at": stale_time.isoformat()}))
    assert read_host_metrics(target, now=now)["state"] == "stale"

    target.write_text('{"schema_version":1,"captured_at":"bad"}')
    assert read_host_metrics(target, now=now)["state"] == "invalid"
    target.write_text('{"schema_version":1,"captured_at":"2026-01-01T00:00:00Z","cpu":NaN}')
    assert read_host_metrics(target, now=now)["state"] == "invalid"


def test_alerts_deduplicate_cool_down_recover_and_redact_details(app, ops_db):
    db = ops_db
    notifier = RecordingNotifier()
    start = utcnow()
    manager = AlertManager(db, notifier, cooldown_seconds=60)
    signal = AlertSignal(
        "service.backend.unhealthy", "ERROR", "Backend service is unhealthy",
        {"service_state": "unhealthy", "authorization": "Bearer no-log-this", "note": "token=private"},
        scope="backend",
    )

    first = manager.reconcile([signal], now=start)
    db.commit()
    alert = db.scalar(select(OperationalAlert).where(OperationalAlert.fingerprint == signal.fingerprint))
    assert first == {"active": 1, "opened": 1, "notified": 1, "recovered": 0}
    assert len(notifier.events) == 1
    assert alert.details == {"service_state": "unhealthy", "note": "token=[REDACTED]"}

    second = manager.reconcile([signal], now=start + timedelta(seconds=30))
    db.commit()
    assert second["notified"] == 0
    assert alert.occurrence_count == 2
    assert len(notifier.events) == 1

    manager.reconcile([signal], now=start + timedelta(seconds=61))
    db.commit()
    assert notifier.events[-1][0] == "ops.alert.reminder"

    recovered = manager.reconcile([], now=start + timedelta(seconds=62))
    db.commit()
    assert recovered["recovered"] == 1
    assert notifier.events[-1][0] == "ops.alert.recovered"
    assert alert.state == "resolved"
    assert alert.resolved_at is not None


def test_notifier_failure_is_recorded_without_exception_text(app, ops_db):
    db = ops_db
    signal = AlertSignal("host.metrics_unavailable", "WARNING", "Host metrics missing", {"state": "missing"})
    manager = AlertManager(db, RecordingNotifier(fail=True), cooldown_seconds=60)
    manager.reconcile([signal], now=utcnow())
    db.commit()
    alert = db.scalar(select(OperationalAlert).where(OperationalAlert.fingerprint == signal.fingerprint))
    assert alert.notification_failures == 1
    assert alert.last_notification_error_type == "RuntimeError"
    assert "private notifier detail" not in alert.last_notification_error_type


def test_overview_reports_unavailable_host_worker_and_ai_signals_as_unknown(ops_db):
    db = ops_db
    overview = build_ops_overview(db, "", now=utcnow())
    assert overview["host"]["state"] == "missing"
    assert overview["readiness"]["state"] == "unknown"
    assert overview["workers"]["state"] == "not_integrated"
    assert overview["workers"]["online"] == 0
    assert overview["ai_usage"]["state"] == "not_integrated"
    assert overview["ai_usage"]["input_tokens_last_24h"] is None
    assert overview["ai_usage"]["output_tokens_last_24h"] is None


def test_worker_alerts_only_when_platform_is_active(ops_db, tmp_path):
    db = ops_db
    platform = db.scalar(select(Platform).where(Platform.key == "chaoxing"))
    platform.integration_status = "pending"
    db.commit()
    notifier = RecordingNotifier()
    evaluate_and_persist_alerts(db, tmp_path / "missing.json", notifier, now=utcnow())
    assert db.scalar(select(OperationalAlert).where(OperationalAlert.code == "worker.no_fresh_heartbeat")) is None

    platform.integration_status = "active"
    db.commit()
    evaluate_and_persist_alerts(db, tmp_path / "missing.json", notifier, now=utcnow())
    worker_alert = db.scalar(select(OperationalAlert).where(OperationalAlert.code == "worker.no_fresh_heartbeat"))
    assert worker_alert is not None
    assert worker_alert.severity == "WARNING"


def test_ops_endpoint_is_superadmin_only_and_exposes_no_decryption_controls(app):
    with app.app_context():
        db = get_db()
        admin_role = db.scalar(select(Role).where(Role.name == "admin"))
        admin = User(email="ops-view-test@example.test", display_name="Scoped", password_hash=hash_password("valid-password-123"))
        db.add(admin)
        db.flush()
        db.add(UserRole(user_id=admin.id, role_id=admin_role.id))
        db.commit()
        admin_email = admin.email

    admin_client = app.test_client()
    login = admin_client.post("/api/v1/auth/login", json={"email": admin_email, "password": "valid-password-123"})
    assert login.status_code == 200
    assert admin_client.get("/api/v1/admin/ops/overview").status_code == 403

    super_client = app.test_client()
    assert super_client.post("/api/v1/auth/login", json={"email": "root@example.test", "password": "root-password-123"}).status_code == 200
    response = super_client.get("/api/v1/admin/ops/overview")
    assert response.status_code == 200
    data = response.get_json()
    assert data["host"]["state"] == "missing"
    assert data["ai_usage"]["state"] == "not_integrated"
    serialized = json.dumps(data).lower()
    assert "ciphertext" not in serialized
    assert "decrypt" not in serialized
    assert "secret_ciphertext" not in serialized
