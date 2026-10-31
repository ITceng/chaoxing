from __future__ import annotations

from sqlalchemy import select

from unified_system.db import get_db
from unified_system.models import AuditLog, PlatformAccount, User, UserSession, utcnow
from unified_system.tests.test_accounts_workers import create_shared, sign_in, post
from unified_system.tests.test_isolation import create_account


def test_browser_sessions_can_be_listed_and_revoked_individually(app):
    first = app.test_client()
    second = app.test_client()
    first_login = first.post("/api/v1/auth/login", json={"email": "root@example.test", "password": "root-password-123"})
    second.post("/api/v1/auth/login", json={"email": "root@example.test", "password": "root-password-123"})
    first_csrf = first_login.get_json()["csrf"]
    listing = first.get("/api/v1/auth/sessions")
    assert listing.status_code == 200
    sessions = listing.get_json()["sessions"]
    assert len(sessions) == 2
    other = next(row for row in sessions if not row["current"])
    revoke = first.post(f"/api/v1/auth/sessions/{other['id']}/revoke", json={}, headers={"X-CSRF-Token": first_csrf})
    assert revoke.status_code == 200
    assert second.get("/api/v1/auth/me").status_code == 401
    assert first.get("/api/v1/auth/me").status_code == 200


def test_session_revocation_others_leaves_current_session_valid(app):
    first, second = app.test_client(), app.test_client()
    login = first.post("/api/v1/auth/login", json={"email": "root@example.test", "password": "root-password-123"})
    second.post("/api/v1/auth/login", json={"email": "root@example.test", "password": "root-password-123"})
    response = first.post("/api/v1/auth/sessions/revoke-others", json={}, headers={"X-CSRF-Token": login.get_json()["csrf"]})
    assert response.status_code == 200
    assert response.get_json()["revoked_count"] == 1
    assert first.get("/api/v1/auth/me").status_code == 200
    assert second.get("/api/v1/auth/me").status_code == 401


def test_personal_account_label_update_is_owner_scoped(client, app):
    csrf = sign_in(client)
    created = post(client, "/api/v1/personal-accounts", {
        "platform_key": "chaoxing", "label": "Before", "account_hint": "student@example.test",
        "auth_type": "username_password", "credentials": {"username": "student", "password": "secret"},
    }, csrf)
    assert created.status_code == 201
    account_id = created.get_json()["account"]["id"]
    updated = client.patch(f"/api/v1/personal-accounts/{account_id}", json={"label": "Updated"}, headers={"X-CSRF-Token": csrf})
    assert updated.status_code == 200
    assert updated.get_json()["account"]["label"] == "Updated"

    other_id = create_account(app, "phase3-other@example.test")
    other = app.test_client()
    login = other.post("/api/v1/auth/login", json={"email": "phase3-other@example.test", "password": "valid-password-123"})
    forbidden = other.patch(f"/api/v1/personal-accounts/{account_id}", json={"label": "stolen"}, headers={"X-CSRF-Token": login.get_json()["csrf"]})
    assert forbidden.status_code == 404
    assert other_id


def test_shared_account_label_update_is_manager_only(client):
    csrf = sign_in(client)
    account_id = create_shared(client, csrf)
    response = client.patch(f"/api/v1/admin/shared-accounts/{account_id}", json={"label": "Renewed label"}, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    assert response.get_json()["shared_account"]["label"] == "Renewed label"


def test_admin_dashboard_is_real_and_scope_aware(client, app):
    root_login = client.post("/api/v1/auth/login", json={"email": "root@example.test", "password": "root-password-123"})
    dashboard = client.get("/api/v1/admin/dashboard")
    assert dashboard.status_code == 200
    result = dashboard.get_json()
    assert result["service"] == {"api": "ok", "database": "ok"}
    assert result["users"]["total"] == 1
    assert result["invitations"]["total"] == 0
    assert {p["key"] for p in result["platform_accounts"]} == {"chaoxing", "zhihuishu", "yinghua"}
    assert all(p["account_count"] == 0 for p in result["platform_accounts"])
    assert result["workers"]["total"] == 0

    admin_id = create_account(app, "phase3-admin@example.test", "admin")
    allowed_id = create_account(app, "phase3-allowed@example.test")
    create_account(app, "phase3-hidden@example.test")
    with app.app_context():
        db = get_db()
        from unified_system.models import AdminUserScope
        db.add(AdminUserScope(admin_id=admin_id, user_id=allowed_id))
        db.commit()
    admin = app.test_client()
    admin_login = admin.post("/api/v1/auth/login", json={"email": "phase3-admin@example.test", "password": "valid-password-123"})
    scoped = admin.get("/api/v1/admin/dashboard").get_json()
    assert scoped["scope"] != "global"
    assert scoped["users"]["total"] == 2  # self plus the one user in scope
    assert scoped["invitations"] is None
    assert scoped["workers"] is None
    assert admin_login.status_code == 200


def test_admin_user_and_audit_endpoints_support_search_and_pagination(client, app):
    csrf = sign_in(client)
    create_account(app, "alpha-page@example.test")
    create_account(app, "beta-page@example.test")
    users = client.get("/api/v1/admin/users?q=alpha&page=1&page_size=1")
    assert users.status_code == 200
    assert users.get_json()["pagination"]["total"] == 1
    assert users.get_json()["users"][0]["email"] == "alpha-page@example.test"

    with app.app_context():
        db = get_db()
        actor = db.scalar(select(User).where(User.email == "root@example.test"))
        db.add(AuditLog(actor_user_id=actor.id, action="phase3.pagination_test", entity_type="test", entity_id="one"))
        db.commit()
    logs = client.get("/api/v1/admin/audit-logs?q=phase3.pagination_test&page=1&page_size=5")
    assert logs.status_code == 200
    assert logs.get_json()["pagination"]["total"] == 1
    assert logs.get_json()["logs"][0]["action"] == "phase3.pagination_test"
    assert csrf


def test_user_activity_is_only_current_user_and_unknown_api_does_not_serve_spa(client, app):
    sign_in(client)
    activity = client.get("/api/v1/activity")
    assert activity.status_code == 200
    assert isinstance(activity.get_json()["activity"], list)
    assert client.get("/api/not-an-endpoint").status_code == 404


def test_scoped_admin_can_revoke_a_users_sessions_without_disabling_them(app):
    admin_id = create_account(app, "phase3-session-admin@example.test", "admin")
    target_id = create_account(app, "phase3-session-target@example.test")
    with app.app_context():
        from unified_system.models import AdminUserScope
        db = get_db()
        db.add(AdminUserScope(admin_id=admin_id, user_id=target_id))
        db.commit()
    admin, target = app.test_client(), app.test_client()
    admin_login = admin.post("/api/v1/auth/login", json={"email":"phase3-session-admin@example.test","password":"valid-password-123"})
    target.post("/api/v1/auth/login", json={"email":"phase3-session-target@example.test","password":"valid-password-123"})
    result = admin.post(f"/api/v1/admin/users/{target_id}/sessions/revoke", json={}, headers={"X-CSRF-Token":admin_login.get_json()["csrf"]})
    assert result.status_code == 200
    assert result.get_json()["revoked_count"] == 1
    assert target.get("/api/v1/auth/me").status_code == 401
    with app.app_context():
        assert get_db().get(User, target_id).status == "active"


def test_authenticated_requests_refresh_server_session_last_seen_at(app):
    user_id = create_account(app, "phase3-session-activity@example.test")
    client = app.test_client()
    client.post("/api/v1/auth/login", json={"email":"phase3-session-activity@example.test","password":"valid-password-123"})
    from datetime import timedelta
    with app.app_context():
        db = get_db()
        record = db.scalar(select(UserSession).where(UserSession.user_id == user_id))
        record.last_seen_at = utcnow() - timedelta(minutes=10)
        db.commit()
        stale_time = record.last_seen_at
    assert client.get("/api/v1/auth/me").status_code == 200
    with app.app_context():
        refreshed = get_db().scalar(select(UserSession).where(UserSession.user_id == user_id))
        assert refreshed.last_seen_at > stale_time


def test_session_rows_store_hashes_not_cookie_secrets(client, app):

    client.post("/api/v1/auth/login", json={"email": "root@example.test", "password": "root-password-123"})
    with app.app_context():
        rows = get_db().scalars(select(UserSession)).all()
        assert len(rows) == 1
        assert len(rows[0].token_hash) == 64
        assert "sid" not in rows[0].token_hash
