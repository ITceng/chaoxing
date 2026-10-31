from __future__ import annotations

import json

from sqlalchemy import select

from unified_system.db import get_db
from unified_system.models import (
    AccountAccessGrant, AccountExecutionLease, Platform, PlatformAccount, PlatformAccountSecret,
    SharedPlatformAccount, Task, TaskEvent,
)
from unified_system.tests.test_isolation import create_account


def sign_in(client, email="root@example.test", password="root-password-123"):
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return response.get_json()["csrf"]


def post(client, path, payload, csrf):
    return client.post(path, json=payload, headers={"X-CSRF-Token": csrf})


def create_shared(client, csrf, is_open=True):
    response = post(client, "/api/v1/admin/shared-accounts", {
        "platform_key": "chaoxing", "label": "Team license", "account_hint": "te***@example.test",
        "auth_type": "username_password", "credentials": {"username": "shared-user", "password": "DO_NOT_RETURN_THIS"},
        "is_open": is_open, "max_concurrency": 1,
    }, csrf)
    assert response.status_code == 201, response.get_json()
    assert "DO_NOT_RETURN_THIS" not in response.get_data(as_text=True)
    return response.get_json()["shared_account"]["id"]


def grant(client, csrf, account_id, user_id, scope=None):
    return post(client, f"/api/v1/admin/shared-accounts/{account_id}/grants", {
        "user_id": user_id, "scope": scope or {},
    }, csrf)


def activate_for_worker(app, account_id):
    with app.app_context():
        db = get_db()
        base = db.get(PlatformAccount, account_id)
        platform = db.get(Platform, base.platform_id)
        base.status = "active"
        platform.integration_status = "active"
        db.commit()


def test_personal_credentials_are_encrypted_and_not_visible_to_other_users(client, app):
    csrf = sign_in(client)
    raw_password = "Personal-Secret-Only"
    response = post(client, "/api/v1/personal-accounts", {
        "platform_key": "chaoxing", "label": "My course account", "account_hint": "me***@example.test",
        "auth_type": "username_password", "credentials": {"username": "me", "password": raw_password},
    }, csrf)
    assert response.status_code == 201
    account_id = response.get_json()["account"]["id"]
    assert raw_password not in response.get_data(as_text=True)
    with app.app_context():
        db = get_db()
        secret = db.scalar(select(PlatformAccountSecret).where(PlatformAccountSecret.account_id == account_id))
        assert secret.ciphertext != raw_password
        assert raw_password not in secret.ciphertext

    other_id = create_account(app, "second-user@example.test")
    other = app.test_client()
    sign_in(other, "second-user@example.test", "valid-password-123")
    listing = other.get("/api/v1/platform-accounts").get_json()
    assert listing["personal_accounts"] == []
    other_csrf = other.get("/api/v1/auth/me").get_json()["csrf"]
    assert other.delete(f"/api/v1/personal-accounts/{account_id}", headers={"X-CSRF-Token": other_csrf}).status_code == 404
    assert other_id


def test_shared_grant_scopes_visibility_and_task_creation(client, app):
    csrf = sign_in(client)
    user_id = create_account(app, "shared-user@example.test")
    account_id = create_shared(client, csrf)
    grant_response = grant(client, csrf, account_id, user_id, {"task_kinds": ["course_sync"]})
    assert grant_response.status_code == 201, grant_response.get_json()

    user_client = app.test_client()
    user_csrf = sign_in(user_client, "shared-user@example.test", "valid-password-123")
    listing = user_client.get("/api/v1/platform-accounts").get_json()
    assert listing["personal_accounts"] == []
    assert len(listing["shared_accounts"]) == 1
    assert "credential" not in json.dumps(listing)

    blocked = post(user_client, "/api/v1/tasks", {
        "account_mode": "shared", "account_id": account_id, "kind": "course_sync", "request_data": {},
    }, user_csrf)
    assert blocked.status_code == 409  # Account is unverified and platform is still pending.
    activate_for_worker(app, account_id)
    forbidden_kind = post(user_client, "/api/v1/tasks", {
        "account_mode": "shared", "account_id": account_id, "kind": "quiz_submit", "request_data": {},
    }, user_csrf)
    assert forbidden_kind.status_code == 403
    sensitive = post(user_client, "/api/v1/tasks", {
        "account_mode": "shared", "account_id": account_id, "kind": "course_sync",
        "request_data": {"nested": {"cookie": "never-store"}},
    }, user_csrf)
    assert sensitive.status_code == 400
    created = post(user_client, "/api/v1/tasks", {
        "account_mode": "shared", "account_id": account_id, "kind": "course_sync",
        "request_data": {"course_id": "course-7"}, "idempotency_key": "request-001",
    }, user_csrf)
    assert created.status_code == 202, created.get_json()
    assert created.get_json()["task"]["status"] == "queued"
    repeated = post(user_client, "/api/v1/tasks", {
        "account_mode": "shared", "account_id": account_id, "kind": "course_sync",
        "request_data": {"course_id": "course-7"}, "idempotency_key": "request-001",
    }, user_csrf)
    assert repeated.status_code == 200 and repeated.get_json()["task"]["id"] == created.get_json()["task"]["id"]
    assert client.get(f"/api/v1/admin/shared-accounts/{account_id}/usage").status_code == 200


def test_worker_lease_serializes_account_tasks_and_returns_credentials_only_to_worker(client, app):
    csrf = sign_in(client)
    user_id = create_account(app, "worker-user@example.test")
    account_id = create_shared(client, csrf)
    assert grant(client, csrf, account_id, user_id).status_code == 201
    activate_for_worker(app, account_id)
    user_client = app.test_client()
    user_csrf = sign_in(user_client, "worker-user@example.test", "valid-password-123")
    for key in ("task-one", "task-two"):
        response = post(user_client, "/api/v1/tasks", {
            "account_mode": "shared", "account_id": account_id, "kind": "course_sync",
            "request_data": {"course_id": key}, "idempotency_key": key,
        }, user_csrf)
        assert response.status_code == 202

    app.config["WORKER_TOKENS"] = {"chaoxing": "worker-secret-for-tests-not-production"}
    headers = {"X-Worker-Platform": "chaoxing", "X-Worker-ID": "cx-test-1", "X-Worker-Token": "worker-secret-for-tests-not-production"}
    worker = app.test_client()
    unauthorized = {**headers, "X-Worker-Token": "wrong-token"}
    assert worker.post("/api/internal/workers/heartbeat", json={}, headers=unauthorized).status_code == 401
    assert worker.post("/api/internal/workers/heartbeat", json={"version": "test", "capabilities": []}, headers=headers).status_code == 200
    claimed = worker.post("/api/internal/workers/claim", json={}, headers=headers)
    assert claimed.status_code == 200
    payload = claimed.get_json()
    assert payload["credential"]["values"]["password"] == "DO_NOT_RETURN_THIS"
    assert payload["task"]["platform_account_id"] == account_id
    token = payload["task"]["lease_token"]
    second_claim = worker.post("/api/internal/workers/claim", json={}, headers=headers)
    assert second_claim.status_code == 204

    task_id = payload["task"]["id"]
    assert worker.post(f"/api/internal/workers/{task_id}/start", json={"lease_token": token}, headers=headers).status_code == 200
    heartbeat = worker.post(f"/api/internal/workers/{task_id}/heartbeat", json={"lease_token": token}, headers=headers)
    assert heartbeat.status_code == 200 and heartbeat.get_json()["cancel_requested"] is False
    unsafe_summary = worker.post(f"/api/internal/workers/{task_id}/complete", json={
        "lease_token": token, "outcome": "succeeded", "result_summary": {"metadata": {"access_token": "never-log"}},
    }, headers=headers)
    assert unsafe_summary.status_code == 400
    complete = worker.post(f"/api/internal/workers/{task_id}/complete", json={
        "lease_token": token, "outcome": "succeeded", "result_summary": {"items": 1},
    }, headers=headers)
    assert complete.status_code == 200
    assert worker.post("/api/internal/workers/claim", json={}, headers=headers).status_code == 200


def test_user_disable_revokes_grants_and_cancels_queued_tasks(client, app):
    admin_csrf = sign_in(client)
    user_id = create_account(app, "disabled-user@example.test")
    account_id = create_shared(client, admin_csrf)
    assert grant(client, admin_csrf, account_id, user_id).status_code == 201
    activate_for_worker(app, account_id)
    user_client = app.test_client()
    user_csrf = sign_in(user_client, "disabled-user@example.test", "valid-password-123")
    response = post(user_client, "/api/v1/tasks", {
        "account_mode": "shared", "account_id": account_id, "kind": "course_sync", "request_data": {},
    }, user_csrf)
    assert response.status_code == 202
    task_id = response.get_json()["task"]["id"]
    disabled = client.patch(f"/api/v1/admin/users/{user_id}/status", json={"status": "disabled"}, headers={"X-CSRF-Token": admin_csrf})
    assert disabled.status_code == 200
    with app.app_context():
        db = get_db()
        task = db.get(Task, task_id)
        assert task.status == "cancelled"
        grant_row = db.scalar(select(AccountAccessGrant).where(AccountAccessGrant.shared_account_id == account_id, AccountAccessGrant.user_id == user_id))
        assert grant_row.revoked_at is not None
        assert db.scalar(select(TaskEvent).where(TaskEvent.task_id == task_id, TaskEvent.event_type == "cancelled")) is not None
    assert user_client.get("/api/v1/auth/me").status_code == 401


def test_revoking_grant_requests_running_task_cancel_and_keeps_lease_until_ack(client, app):
    admin_csrf = sign_in(client)
    user_id = create_account(app, "revocation-user@example.test")
    account_id = create_shared(client, admin_csrf)
    grant_id = grant(client, admin_csrf, account_id, user_id).get_json()["grant_id"]
    activate_for_worker(app, account_id)
    user_client = app.test_client()
    user_csrf = sign_in(user_client, "revocation-user@example.test", "valid-password-123")
    created = post(user_client, "/api/v1/tasks", {
        "account_mode": "shared", "account_id": account_id, "kind": "course_sync", "request_data": {},
    }, user_csrf)
    assert created.status_code == 202
    task_id = created.get_json()["task"]["id"]

    app.config["WORKER_TOKENS"] = {"chaoxing": "revocation-worker-token-for-tests"}
    headers = {"X-Worker-Platform": "chaoxing", "X-Worker-ID": "cx-revoke-1", "X-Worker-Token": "revocation-worker-token-for-tests"}
    worker = app.test_client()
    worker.post("/api/internal/workers/heartbeat", json={}, headers=headers)
    claimed_response = worker.post("/api/internal/workers/claim", json={}, headers=headers)
    assert claimed_response.status_code == 200
    claimed = claimed_response.get_json()
    token = claimed["task"]["lease_token"]
    assert worker.post(f"/api/internal/workers/{task_id}/start", json={"lease_token": token}, headers=headers).status_code == 200
    revoked = client.post(f"/api/v1/admin/shared-accounts/{account_id}/grants/{grant_id}/revoke", json={}, headers={"X-CSRF-Token": admin_csrf})
    assert revoked.status_code == 200
    heartbeat = worker.post(f"/api/internal/workers/{task_id}/heartbeat", json={"lease_token": token}, headers=headers)
    assert heartbeat.status_code == 200
    assert heartbeat.get_json()["cancel_requested"] is True
    finished = worker.post(f"/api/internal/workers/{task_id}/complete", json={
        "lease_token": token, "outcome": "succeeded", "result_summary": {},
    }, headers=headers)
    assert finished.status_code == 200 and finished.get_json()["status"] == "cancelled"
    assert user_client.get("/api/v1/shared-accounts").get_json()["shared_accounts"] == []


def test_scoped_admin_can_manage_only_own_shared_accounts_and_scoped_users(client, app):
    root_csrf = sign_in(client)
    admin_id = create_account(app, "shared-manager@example.test", "admin")
    allowed_id = create_account(app, "allowed-grantee@example.test")
    denied_id = create_account(app, "unscoped-grantee@example.test")
    assert post(client, "/api/v1/admin/scopes", {"admin_id": admin_id, "user_id": allowed_id}, root_csrf).status_code == 201
    own_account = create_shared(client, root_csrf)

    admin = app.test_client()
    admin_csrf = sign_in(admin, "shared-manager@example.test", "valid-password-123")
    managed_account = create_shared(admin, admin_csrf)
    listed = admin.get("/api/v1/admin/shared-accounts").get_json()["shared_accounts"]
    assert [row["id"] for row in listed] == [managed_account]
    outside = grant(admin, admin_csrf, managed_account, denied_id)
    assert outside.status_code == 404
    inside = grant(admin, admin_csrf, managed_account, allowed_id)
    assert inside.status_code == 201
    assert admin.get(f"/api/v1/admin/shared-accounts/{own_account}/usage").status_code == 404


def test_worker_reaps_expired_lease_without_starving_next_task(client, app):
    admin_csrf = sign_in(client)
    user_id = create_account(app, "lease-expiry-user@example.test")
    account_id = create_shared(client, admin_csrf)
    assert grant(client, admin_csrf, account_id, user_id).status_code == 201
    activate_for_worker(app, account_id)
    user_client = app.test_client()
    user_csrf = sign_in(user_client, "lease-expiry-user@example.test", "valid-password-123")
    task_ids = []
    for key in ("lease-old", "lease-next"):
        created = post(user_client, "/api/v1/tasks", {
            "account_mode": "shared", "account_id": account_id, "kind": "course_sync",
            "request_data": {"course_id": key}, "idempotency_key": key,
        }, user_csrf)
        assert created.status_code == 202
        task_ids.append(created.get_json()["task"]["id"])
    app.config["WORKER_TOKENS"] = {"chaoxing": "lease-expiry-worker-token"}
    headers = {"X-Worker-Platform": "chaoxing", "X-Worker-ID": "cx-expiry-1", "X-Worker-Token": "lease-expiry-worker-token"}
    worker = app.test_client()
    assert worker.post("/api/internal/workers/heartbeat", json={}, headers=headers).status_code == 200
    first = worker.post("/api/internal/workers/claim", json={}, headers=headers).get_json()
    assert first["task"]["id"] == task_ids[0]
    with app.app_context():
        db = get_db()
        lease = db.scalar(select(AccountExecutionLease).where(AccountExecutionLease.task_id == task_ids[0]))
        from unified_system.models import utcnow
        from datetime import timedelta
        lease.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    next_claim = worker.post("/api/internal/workers/claim", json={}, headers=headers)
    assert next_claim.status_code == 200
    assert next_claim.get_json()["task"]["id"] == task_ids[1]
    with app.app_context():
        db = get_db()
        assert db.get(Task, task_ids[0]).status == "timed_out"


def test_disabling_user_disables_personal_account_and_revokes_its_secret(client, app):
    admin_csrf = sign_in(client)
    user_id = create_account(app, "personal-disabled@example.test")
    user_client = app.test_client()
    user_csrf = sign_in(user_client, "personal-disabled@example.test", "valid-password-123")
    created = post(user_client, "/api/v1/personal-accounts", {
        "platform_key": "chaoxing", "label": "Personal", "account_hint": "private@example.test",
        "auth_type": "username_password", "credentials": {"username": "private", "password": "keep-encrypted"},
    }, user_csrf)
    assert created.status_code == 201
    account_id = created.get_json()["account"]["id"]
    disabled = client.patch(f"/api/v1/admin/users/{user_id}/status", json={"status": "disabled"}, headers={"X-CSRF-Token": admin_csrf})
    assert disabled.status_code == 200
    with app.app_context():
        db = get_db()
        base = db.get(PlatformAccount, account_id)
        secret = db.scalar(select(PlatformAccountSecret).where(PlatformAccountSecret.account_id == account_id))
        assert base.status == "disabled"
        assert secret.status == "revoked" and secret.revoked_at is not None
    assert created.get_json()["account"]["account_hint"] == "p***@example.test"
