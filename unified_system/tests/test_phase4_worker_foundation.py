from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from unified_system.db import get_db
from unified_system.models import Platform, PlatformAccount, Task
from unified_system.platforms.chaoxing.adapter import ChaoxingAdapter
from unified_system.platforms.chaoxing.manual_verify import check_cookie
from unified_system.platforms.common import (
    AdapterCancelled, AdapterCapabilities, AdapterError, AdapterRegistry, AuthContext,
    CancellationToken, CourseSummary, HealthStatus, PlatformAdapter, PlatformInfo, UnsupportedCapability,
)
from unified_system.tests.test_accounts_workers import create_shared, grant, post, sign_in
from unified_system.tests.test_isolation import create_account
from unified_system.workers.runtime import WorkerClient, WorkerEngine, WorkerSettings


class FakeAdapter(PlatformAdapter):
    def __init__(self, *, kinds=("course_list",), verified=True):
        self.calls = []
        self.kinds = kinds
        self.verified = verified

    def platform_info(self):
        return PlatformInfo("chaoxing", "Learning Platform", "test.1")

    def capabilities(self):
        return AdapterCapabilities(
            course_list="course_list" in self.kinds,
            supported_task_kinds=tuple(self.kinds),
            verified_at=datetime.now(timezone.utc) if self.verified else None,
        )

    def health_check(self):
        return HealthStatus(True, datetime.now(timezone.utc))

    def list_courses(self, auth):
        self.calls.append(("course_list", auth.account_id))
        return [CourseSummary("chaoxing", "c-1", "Course One", source_verified=True)]


class FakeClient:
    def __init__(self, settings, task_envelope, *, cancel=False):
        self.settings = settings
        self.task_envelope = task_envelope
        self.cancel = cancel
        self.calls = []

    def heartbeat(self, capabilities):
        self.calls.append(("heartbeat", capabilities))
        return {"ok": True}

    def claim(self):
        self.calls.append(("claim",))
        return self.task_envelope

    def start_task(self, task_id, lease_token):
        self.calls.append(("start", task_id))
        return True

    def task_heartbeat(self, task_id, lease_token):
        self.calls.append(("task_heartbeat", task_id))
        return self.cancel

    def complete_task(self, task_id, lease_token, outcome, *, error_code=None, result_summary=None, course_catalog=None):
        self.calls.append(("complete", outcome, error_code, result_summary, course_catalog))
        return {"ok": True}


class FakeCookieJar:
    def __init__(self):
        self.values = []

    def set(self, name, value, **kwargs):
        self.values.append((name, value, kwargs))


class FakeHTTPResponse:
    def __init__(self, text, status=200):
        self.status_code = status
        self.text = text
        self.content = text.encode("utf-8")
        self.closed = False

    def close(self):
        self.closed = True


class FakeHTTPSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.cookies = FakeCookieJar()
        self.headers = {}
        self.calls = []
        self.closed = False

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)

    def close(self):
        self.closed = True


def worker_settings(**overrides):
    values = dict(
        backend_url="http://backend:8088", platform_key="chaoxing", worker_id="cx-worker-test",
        token="test-worker-token-which-is-at-least-32-bytes", version="test.1",
        poll_interval_seconds=0.1, heartbeat_interval_seconds=0.01,
        request_timeout_seconds=2, cancellation_grace_seconds=0.5,
    )
    values.update(overrides)
    return WorkerSettings(**values)


def task_envelope(kind="course_list"):
    return {
        "task": {
            "id": "task-id-1", "platform_key": "chaoxing", "platform_account_id": "account-id-1",
            "kind": kind, "request_data": {}, "timeout_seconds": 30, "lease_token": "long-enough-lease-token-123456789",
        },
        "credential": {"auth_type": "cookies", "values": {"cookies": "secret-session-value"}},
    }


def test_adapter_capabilities_only_allow_explicit_read_only_kinds():
    with pytest.raises(ValueError, match="non-read-only"):
        AdapterCapabilities(supported_task_kinds=("quiz_submit",))
    with pytest.raises(ValueError, match="matching platform capability"):
        AdapterCapabilities(supported_task_kinds=("course_list",))
    assert AdapterCapabilities().worker_capabilities() == []


def test_base_adapter_explicitly_reports_unsupported_features():
    adapter = FakeAdapter(kinds=())
    with pytest.raises(UnsupportedCapability):
        adapter.get_course_details(AuthContext("a", "cookies", {"cookies": "secret"}), "course-1")
    assert "secret" not in repr(AuthContext("a", "cookies", {"cookies": "secret"}))


def test_chaoxing_candidate_is_unverified_and_not_dispatchable():
    adapter = ChaoxingAdapter()
    assert adapter.capabilities().worker_capabilities() == []
    assert adapter.health_check().healthy is False


def test_manual_chaoxing_check_emits_only_count_and_never_enables_capabilities():
    class RecordingAdapter:
        def list_courses(self, auth):
            assert auth.values["cookies"] == "manual-private-cookie"
            return [object(), object()]

    report = check_cookie(RecordingAdapter(), "manual-private-cookie")
    assert report["result"] == "candidate_read_only_request_succeeded"
    assert report["course_count"] == 2
    assert report["capabilities_enabled"] is False
    assert "manual-private-cookie" not in repr(report)


def test_chaoxing_candidate_uses_isolated_cookie_session_and_parses_only_course_summary():
    course_a = '<div class="course"><input class="courseId" value="c1"><input class="clazzId" value="cl1"><span class="course-name" title="Systems">Systems</span><p class="color3" title="Teacher">Teacher</p></div>'
    course_b = '<div class="course"><input class="courseId" value="c2"><input class="clazzId" value="cl2"><span class="course-name" title="Networks">Networks</span></div>'
    session = FakeHTTPSession([
        FakeHTTPResponse(course_a),
        FakeHTTPResponse('<ul class="file-list"><li fileid="folder-1"></li></ul>'),
        FakeHTTPResponse(course_a + course_b),
    ])
    adapter = ChaoxingAdapter(session_factory=lambda: session)
    auth = AuthContext("account-1", "cookies", {"cookies": "UID=user-cookie; _uid=123"})
    courses = adapter.list_courses(auth)
    assert [item.course_id for item in courses] == ["c1:cl1", "c2:cl2"]
    assert courses[0].course_name == "Systems" and courses[0].teacher_name == "Teacher"
    assert courses[0].source_verified is False
    assert session.closed
    assert all(call[2]["allow_redirects"] is False for call in session.calls)
    assert all(call[2]["timeout"] == (4.0, 12.0) for call in session.calls)
    assert all(call[1].startswith("https://mooc2-ans.chaoxing.com/") for call in session.calls)
    assert "user-cookie" not in repr(courses) and "user-cookie" not in repr(auth)


def test_chaoxing_candidate_rejects_redirects_and_never_exposes_cookie_values():
    session = FakeHTTPSession([FakeHTTPResponse("", status=302)])
    adapter = ChaoxingAdapter(session_factory=lambda: session)
    auth = AuthContext("account-1", "cookies", {"cookies": "UID=private-cookie"})
    with pytest.raises(AdapterError) as error:
        adapter.list_courses(auth)
    assert error.value.code == "authorization_required"
    assert "private-cookie" not in repr(error.value)
    assert session.closed


def test_chaoxing_candidate_checks_cancellation_between_fixed_platform_requests():
    class CancellingSession(FakeHTTPSession):
        def __init__(self, responses, token):
            super().__init__(responses)
            self.token = token

        def request(self, method, url, **kwargs):
            response = super().request(method, url, **kwargs)
            self.token.cancel()
            return response

    token = CancellationToken()
    session = CancellingSession([FakeHTTPResponse("<div></div>")], token)
    adapter = ChaoxingAdapter(session_factory=lambda: session)
    auth = AuthContext("account-1", "cookies", {"cookies": "UID=private-cookie"})
    with pytest.raises(AdapterCancelled):
        adapter.execute_read_only("course_list", {}, auth, token)
    assert len(session.calls) == 1 and session.closed


def test_registry_does_not_overwrite_existing_platform_adapters():
    registry = AdapterRegistry()
    adapter = FakeAdapter()
    registry.register(adapter)
    assert registry.get("chaoxing") is adapter
    with pytest.raises(ValueError, match="already registered"):
        registry.register(FakeAdapter())
    with pytest.raises(AdapterError, match="No verified adapter"):
        registry.get("yinghua")


def test_worker_settings_redact_token_and_reject_insecure_remote_url():
    env = {
        "WORKER_PLATFORM": "chaoxing", "WORKER_ID": "cx-1",
        "WORKER_TOKEN_CHAOXING": "s" * 40,
        "BACKEND_INTERNAL_URL": "http://backend:8088",
    }
    config = WorkerSettings.from_env(env)
    assert "s" * 32 not in repr(config)
    env["BACKEND_INTERNAL_URL"] = "http://backend.example.net:8088"
    with pytest.raises(ValueError, match="non-TLS"):
        WorkerSettings.from_env(env)


def test_worker_client_uses_private_worker_headers_without_cookie_auth():
    captured = {}

    class Response:
        status_code = 200
        content = b'{"ok":true}'
        def json(self): return {"ok": True}

    def request(method, url, **kwargs):
        captured.update(method=method, url=url, **kwargs)
        return Response()

    client = WorkerClient(worker_settings(), request=request)
    client.heartbeat(["course_list"])
    assert captured["url"] == "http://backend:8088/api/internal/workers/heartbeat"
    assert captured["headers"]["X-Worker-Token"] == worker_settings().token
    assert "Cookie" not in captured["headers"] and "Authorization" not in captured["headers"]
    assert captured["timeout"] == 2


def test_worker_engine_completes_only_read_only_task_with_compact_summary():
    adapter = FakeAdapter()
    client = FakeClient(worker_settings(), task_envelope())
    engine = WorkerEngine(client, adapter)
    assert engine.run_once() is True
    assert adapter.calls == [("course_list", "account-id-1")]
    assert ("start", "task-id-1") in client.calls
    completion = next(call for call in client.calls if call[0] == "complete")
    assert completion[1] == "succeeded"
    assert completion[3] == {"operation": "course_list", "item_count": 1, "read_only": True}
    assert completion[4] == [{"course_id": "c-1", "course_name": "Course One", "teacher_name": "", "semester": "", "resource_status": "UNKNOWN"}]
    assert "secret-session-value" not in repr(client.calls)


def test_worker_engine_fails_closed_on_unhealthy_adapter_before_heartbeat_or_claim():
    class UnhealthyAdapter(FakeAdapter):
        def health_check(self):
            return HealthStatus(False, datetime.now(timezone.utc), "upstream_unavailable")

    client = FakeClient(worker_settings(), task_envelope())
    engine = WorkerEngine(client, UnhealthyAdapter())
    with pytest.raises(AdapterError, match="health check failed"):
        engine.run_once()
    assert client.calls == []


def test_worker_engine_never_dispatches_task_kind_not_advertised():
    adapter = FakeAdapter()
    client = FakeClient(worker_settings(), task_envelope("quiz_submit"))
    engine = WorkerEngine(client, adapter)
    engine.run_once()
    assert adapter.calls == []
    completion = next(call for call in client.calls if call[0] == "complete")
    assert completion[1:3] == ("failed", "unsupported_task_kind")


def test_worker_engine_asks_adapter_to_stop_when_backend_requests_cancel():
    class SlowAdapter(FakeAdapter):
        def execute_read_only(self, kind, request_data, auth, cancellation):
            self.calls.append(("course_list", auth.account_id))
            while not cancellation.cancelled:
                time.sleep(0.002)
            cancellation.checkpoint()

    adapter = SlowAdapter()
    client = FakeClient(worker_settings(), task_envelope(), cancel=True)
    engine = WorkerEngine(client, adapter)
    engine.run_once()
    completion = next(call for call in client.calls if call[0] == "complete")
    assert completion[1] == "cancelled"


def test_backend_only_accepts_read_only_tasks_and_claims_only_advertised_kind(client, app):
    admin_csrf = sign_in(client)
    user_id = create_account(app, "phase4-capability-user@example.test")
    account_id = create_shared(client, admin_csrf)
    created_grant = grant(client, admin_csrf, account_id, user_id)
    assert created_grant.status_code == 201
    grant_id = created_grant.get_json()["grant_id"]
    with app.app_context():
        db = get_db()
        platform = db.scalar(select(Platform).where(Platform.key == "chaoxing"))
        platform.capabilities = ["course_sync", "course_list"]
        platform.integration_status = "active"
        db.get(PlatformAccount, account_id).status = "active"
        db.commit()

    user = app.test_client()
    csrf = sign_in(user, "phase4-capability-user@example.test", "valid-password-123")
    denied = post(user, "/api/v1/tasks", {
        "account_mode": "shared", "account_id": account_id, "kind": "quiz_submit", "request_data": {},
    }, csrf)
    assert denied.status_code == 400
    queued = post(user, "/api/v1/tasks", {
        "account_mode": "shared", "account_id": account_id, "kind": "course_sync", "request_data": {},
    }, csrf)
    assert queued.status_code == 202
    task_id = queued.get_json()["task"]["id"]

    app.config["WORKER_TOKENS"] = {"chaoxing": "phase4-capability-token-long-enough-123456"}
    headers = {"X-Worker-Platform":"chaoxing", "X-Worker-ID":"phase4-capability-worker", "X-Worker-Token":"phase4-capability-token-long-enough-123456"}
    worker = app.test_client()
    assert worker.post("/api/internal/workers/heartbeat", json={"capabilities":["quiz_submit"]}, headers=headers).status_code == 400
    assert worker.post("/api/internal/workers/heartbeat", json={"capabilities":["resource_list"]}, headers=headers).status_code == 409
    assert worker.post("/api/internal/workers/heartbeat", json={"capabilities":["course_list"]}, headers=headers).status_code == 200
    assert worker.post("/api/internal/workers/claim", json={}, headers=headers).status_code == 204
    with app.app_context():
        assert get_db().get(Task, task_id).status == "queued"
    assert worker.post("/api/internal/workers/heartbeat", json={"capabilities":["course_sync"]}, headers=headers).status_code == 200
    claimed = worker.post("/api/internal/workers/claim", json={}, headers=headers)
    assert claimed.status_code == 200
    task = claimed.get_json()["task"]
    lease = task["lease_token"]
    assert worker.post(f"/api/internal/workers/{task['id']}/start", json={"lease_token":lease}, headers=headers).status_code == 200
    completed = worker.post(f"/api/internal/workers/{task['id']}/complete", json={
        "lease_token":lease, "outcome":"succeeded", "result_summary":{"items":1},
        "course_catalog":[{"course_id":"course-1", "course_name":"Verified Course"}],
    }, headers=headers)
    assert completed.status_code == 200 and completed.get_json()["synced_course_count"] == 1
    assert [course["course_id"] for course in user.get("/api/v1/courses").get_json()["courses"]] == ["course-1"]
    create_account(app, "unrelated-course-user@example.test")
    unrelated = app.test_client()
    sign_in(unrelated, "unrelated-course-user@example.test", "valid-password-123")
    assert unrelated.get("/api/v1/courses").get_json()["courses"] == []
    revoked = client.post(
        f"/api/v1/admin/shared-accounts/{account_id}/grants/{grant_id}/revoke",
        json={}, headers={"X-CSRF-Token": admin_csrf},
    )
    assert revoked.status_code == 200
    assert user.get("/api/v1/courses").get_json()["courses"] == []


def test_personal_account_verification_requires_catalog_capability_and_records_only_result(client, app):
    csrf = sign_in(client)
    created = post(client, "/api/v1/personal-accounts", {
        "platform_key": "chaoxing", "label": "Verification test", "account_hint": "te***@example.test",
        "auth_type": "cookies", "credentials": {"cookies": "mock-cookie-not-a-real-session"},
    }, csrf)
    assert created.status_code == 201
    account_id = created.get_json()["account"]["id"]
    pending = post(client, f"/api/v1/personal-accounts/{account_id}/verify", {}, csrf)
    assert pending.status_code == 409
    create_account(app, "account-verification-outsider@example.test")
    outsider = app.test_client()
    outsider_csrf = sign_in(outsider, "account-verification-outsider@example.test", "valid-password-123")
    assert post(outsider, f"/api/v1/personal-accounts/{account_id}/verify", {}, outsider_csrf).status_code == 404

    with app.app_context():
        db = get_db()
        platform = db.scalar(select(Platform).where(Platform.key == "chaoxing"))
        platform.integration_status = "active"
        platform.capabilities = ["account_validate"]
        db.commit()
    queued = post(client, f"/api/v1/personal-accounts/{account_id}/verify", {}, csrf)
    assert queued.status_code == 202
    assert queued.get_json()["task"]["kind"] == "account_validate"

    app.config["WORKER_TOKENS"] = {"chaoxing": "phase4-account-verify-token-at-least-32"}
    headers = {"X-Worker-Platform":"chaoxing", "X-Worker-ID":"phase4-account-validator", "X-Worker-Token":"phase4-account-verify-token-at-least-32"}
    worker = app.test_client()
    assert worker.post("/api/internal/workers/heartbeat", json={"capabilities":["account_validate"]}, headers=headers).status_code == 200
    claimed = worker.post("/api/internal/workers/claim", json={}, headers=headers)
    assert claimed.status_code == 200
    task = claimed.get_json()["task"]
    lease = task["lease_token"]
    assert worker.post(f"/api/internal/workers/{task['id']}/start", json={"lease_token":lease}, headers=headers).status_code == 200
    success = worker.post(f"/api/internal/workers/{task['id']}/complete", json={
        "lease_token":lease, "outcome":"succeeded",
        "result_summary":{"operation":"account_validate", "authorized":True, "read_only":True},
    }, headers=headers)
    assert success.status_code == 200
    listed = client.get("/api/v1/platform-accounts").get_json()["personal_accounts"][0]
    assert listed["status"] == "active" and listed["verified_at"]
    assert "mock-cookie-not-a-real-session" not in success.get_data(as_text=True)

    reauthorized = post(client, f"/api/v1/personal-accounts/{account_id}/reauthorize", {
        "auth_type":"cookies", "credentials":{"cookies":"second-mock-cookie"},
    }, csrf)
    assert reauthorized.status_code == 200 and reauthorized.get_json()["verified"] is False
    queued_again = post(client, f"/api/v1/personal-accounts/{account_id}/verify", {}, csrf)
    assert queued_again.status_code == 202
    assert worker.post("/api/internal/workers/heartbeat", json={"capabilities":["account_validate"]}, headers=headers).status_code == 200
    claimed_again = worker.post("/api/internal/workers/claim", json={}, headers=headers).get_json()
    task_id, lease = claimed_again["task"]["id"], claimed_again["task"]["lease_token"]
    assert worker.post(f"/api/internal/workers/{task_id}/start", json={"lease_token":lease}, headers=headers).status_code == 200
    rejected = worker.post(f"/api/internal/workers/{task_id}/complete", json={
        "lease_token":lease, "outcome":"failed", "error_code":"authorization_failed",
    }, headers=headers)
    assert rejected.status_code == 200
    account = client.get("/api/v1/platform-accounts").get_json()["personal_accounts"][0]
    assert account["status"] == "error" and account["verified_at"] is None


def test_shared_account_owner_can_validate_while_private_account_is_closed(client, app):
    csrf = sign_in(client)
    account_id = create_shared(client, csrf, is_open=False)
    with app.app_context():
        db = get_db()
        platform = db.scalar(select(Platform).where(Platform.key == "chaoxing"))
        platform.integration_status = "active"
        platform.capabilities = ["account_validate"]
        db.commit()
    queued = post(client, f"/api/v1/admin/shared-accounts/{account_id}/verify", {}, csrf)
    assert queued.status_code == 202
    with app.app_context():
        from unified_system.models import AccountAccessGrant, SharedPlatformAccount, User
        db = get_db()
        owner = db.scalar(select(User).where(User.email == "root@example.test"))
        grant = db.scalar(select(AccountAccessGrant).where(
            AccountAccessGrant.shared_account_id == account_id,
            AccountAccessGrant.user_id == owner.id,
            AccountAccessGrant.revoked_at.is_(None),
        ))
        assert get_db().get(SharedPlatformAccount, account_id).is_open is False
        assert grant is not None and grant.granted_by == owner.id

    app.config["WORKER_TOKENS"] = {"chaoxing": "phase4-shared-validation-token-long-123456"}
    headers = {"X-Worker-Platform":"chaoxing", "X-Worker-ID":"phase4-shared-validator", "X-Worker-Token":"phase4-shared-validation-token-long-123456"}
    worker = app.test_client()
    assert worker.post("/api/internal/workers/heartbeat", json={"capabilities":["account_validate"]}, headers=headers).status_code == 200
    claimed = worker.post("/api/internal/workers/claim", json={}, headers=headers)
    assert claimed.status_code == 200
    task = claimed.get_json()["task"]
    lease = task["lease_token"]
    assert worker.post(f"/api/internal/workers/{task['id']}/start", json={"lease_token":lease}, headers=headers).status_code == 200
    completed = worker.post(f"/api/internal/workers/{task['id']}/complete", json={
        "lease_token":lease, "outcome":"succeeded",
        "result_summary":{"operation":"account_validate", "authorized":True, "read_only":True},
    }, headers=headers)
    assert completed.status_code == 200
    with app.app_context():
        account = get_db().get(PlatformAccount, account_id)
        assert account.status == "active" and account.verified_at is not None
        assert get_db().get(SharedPlatformAccount, account_id).is_open is False
