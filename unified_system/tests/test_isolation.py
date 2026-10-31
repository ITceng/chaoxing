from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from unified_system.db import get_db
from unified_system.models import AdminUserScope, AccountAccessGrant, Platform, PlatformAccount, PersonalPlatformAccount, Role, SharedPlatformAccount, Task, User, UserRole
from unified_system.security import hash_password


def create_account(app, email, role_name="user"):
    with app.app_context():
        db = get_db()
        role = db.scalar(select(Role).where(Role.name == role_name))
        user = User(email=email, display_name=email.split("@")[0], password_hash=hash_password("valid-password-123"))
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()
        return user.id


def test_scoped_admin_cannot_list_users_outside_scope(client, app):
    admin_id = create_account(app, "scoped-admin@example.test", "admin")
    allowed_id = create_account(app, "allowed@example.test")
    denied_id = create_account(app, "denied@example.test")
    with app.app_context():
        db = get_db()
        db.add(AdminUserScope(admin_id=admin_id, user_id=allowed_id))
        db.commit()
    response = client.post("/api/v1/auth/login", json={"email": "scoped-admin@example.test", "password": "valid-password-123"})
    assert response.status_code == 200
    result = client.get("/api/v1/admin/users")
    assert result.status_code == 200
    ids = {user["id"] for user in result.get_json()["users"]}
    assert ids == {allowed_id}
    csrf = client.get("/api/v1/auth/me").get_json()["csrf"]
    denied = client.patch(f"/api/v1/admin/users/{denied_id}/status", json={"status": "disabled"}, headers={"X-CSRF-Token": csrf})
    assert denied.status_code == 404
    assert admin_id not in ids
    login_logs = client.get("/api/v1/admin/login-logs").get_json()["login_logs"]
    assert {row["identifier"] for row in login_logs} == {"scoped-admin@example.test"}


def test_superadmin_can_list_and_revoke_scopes(client, app):
    admin_id = create_account(app, "managed-admin@example.test", "admin")
    other_admin_id = create_account(app, "other-admin@example.test", "admin")
    target_id = create_account(app, "managed-user@example.test")
    login = client.post("/api/v1/auth/login", json={"email": "root@example.test", "password": "root-password-123"})
    csrf = login.get_json()["csrf"]
    root_id = login.get_json()["user"]["id"]
    protected = client.post(f"/api/v1/admin/users/{root_id}/roles", json={"roles": ["admin"]}, headers={"X-CSRF-Token": csrf})
    assert protected.status_code == 403
    cannot_scope_admin = client.post("/api/v1/admin/scopes", json={"admin_id": admin_id, "user_id": other_admin_id}, headers={"X-CSRF-Token": csrf})
    assert cannot_scope_admin.status_code == 400
    granted = client.post("/api/v1/admin/scopes", json={"admin_id": admin_id, "user_id": target_id}, headers={"X-CSRF-Token": csrf})
    assert granted.status_code == 201
    listed = client.get("/api/v1/admin/scopes").get_json()["scopes"]
    assert any(row["admin_id"] == admin_id and row["user_id"] == target_id for row in listed)
    revoked = client.delete(f"/api/v1/admin/scopes/{admin_id}/{target_id}", headers={"X-CSRF-Token": csrf})
    assert revoked.status_code == 200
    assert client.get("/api/v1/admin/scopes").get_json()["scopes"] == []


def test_removing_admin_role_also_removes_old_scopes(client, app):
    admin_id = create_account(app, "temporary-admin@example.test", "admin")
    target_id = create_account(app, "scoped-user@example.test")
    login = client.post("/api/v1/auth/login", json={"email": "root@example.test", "password": "root-password-123"})
    csrf = login.get_json()["csrf"]
    assert client.post("/api/v1/admin/scopes", json={"admin_id": admin_id, "user_id": target_id}, headers={"X-CSRF-Token": csrf}).status_code == 201
    assert client.post(f"/api/v1/admin/users/{admin_id}/roles", json={"roles": ["user"]}, headers={"X-CSRF-Token": csrf}).status_code == 200
    assert client.get("/api/v1/admin/scopes").get_json()["scopes"] == []
    assert client.post(f"/api/v1/admin/users/{admin_id}/roles", json={"roles": ["admin"]}, headers={"X-CSRF-Token": csrf}).status_code == 200
    assert client.get("/api/v1/admin/scopes").get_json()["scopes"] == []


def test_user_sees_only_own_identity_and_admin_api_is_forbidden(client):
    create_account(client.application, "user-one@example.test")
    create_account(client.application, "user-two@example.test")
    client.post("/api/v1/auth/login", json={"email": "user-one@example.test", "password": "valid-password-123"})
    me = client.get("/api/v1/auth/me").get_json()
    assert me["user"]["email"] == "user-one@example.test"
    assert "password_hash" not in str(me)
    assert client.get("/api/v1/admin/users").status_code == 403


def test_database_rejects_cross_user_task_to_account_link(app):
    user_a = create_account(app, "owner-a@example.test")
    user_b = create_account(app, "owner-b@example.test")
    with app.app_context():
        db = get_db()
        platform = db.scalar(select(Platform).where(Platform.key == "chaoxing"))
        account = PlatformAccount(platform_id=platform.id, account_mode="personal", label="A")
        db.add(account)
        db.flush()
        personal = PersonalPlatformAccount(account_id=account.id, owner_user_id=user_a, platform_id=platform.id, account_hint="masked")
        db.add(personal)
        db.commit()
        db.add(Task(user_id=user_b, personal_account_id=account.id, kind="placeholder", request_data={}))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_database_rejects_shared_task_with_another_users_grant(app):
    owner = create_account(app, "shared-owner@example.test")
    another = create_account(app, "shared-other@example.test")
    admin = create_account(app, "shared-admin@example.test", "admin")
    with app.app_context():
        db = get_db()
        platform = db.scalar(select(Platform).where(Platform.key == "chaoxing"))
        base = PlatformAccount(platform_id=platform.id, account_mode="shared", label="Shared")
        db.add(base)
        db.flush()
        shared = SharedPlatformAccount(account_id=base.id, created_by=admin, account_hint="masked")
        grant = AccountAccessGrant(shared_account_id=base.id, user_id=owner, granted_by=admin)
        db.add(shared)
        db.flush()
        db.add(grant)
        db.commit()
        db.add(Task(user_id=another, shared_account_id=base.id, access_grant_id=grant.id, kind="placeholder", request_data={}))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_account_mode_discriminator_prevents_cross_subtype_mixing(app):
    admin = create_account(app, "mode-admin@example.test", "admin")
    with app.app_context():
        db = get_db()
        platform = db.scalar(select(Platform).where(Platform.key == "chaoxing"))
        base = PlatformAccount(platform_id=platform.id, account_mode="personal", label="Personal")
        db.add(base)
        db.flush()
        db.add(SharedPlatformAccount(account_id=base.id, created_by=admin, account_hint="masked"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
