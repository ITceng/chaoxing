from __future__ import annotations

import secrets
from datetime import timedelta

from sqlalchemy import select

from unified_system.db import Base, get_db
from unified_system.models import Invitation, User, UserRole, Role, utcnow
from unified_system.security import hash_password, hash_token


def seed_invite(app, code="test-invitation-code-0001", uses=1):
    with app.app_context():
        db = get_db()
        admin = db.scalar(select(User).where(User.email == "root@example.test"))
        db.add(Invitation(
            code_hash=hash_token(code), code_hint=code[-8:], created_by=admin.id,
            expires_at=utcnow() + timedelta(days=1), max_uses=uses,
        ))
        db.commit()
    return code


def make_user(app, email, role_name="user"):
    with app.app_context():
        db = get_db()
        role = db.scalar(select(Role).where(Role.name == role_name))
        user = User(email=email, display_name=email.split("@")[0], password_hash=hash_password("correct-horse-battery"))
        db.add(user); db.flush()
        db.add(UserRole(user_id=user.id, role_id=role.id)); db.commit()
        return user.id


def csrf(client):
    response = client.get("/api/v1/auth/me")
    return response.get_json()["csrf"]


def test_login_register_invitation_single_use(client, app):
    code = seed_invite(app, uses=1)
    created = client.post("/api/v1/auth/register", json={
        "email": "new@example.test", "display_name": "New", "password": "a-secure-password-1", "invitation_code": code,
    })
    assert created.status_code == 201
    assert created.get_json()["user"]["email"] == "new@example.test"
    duplicate_use = client.post("/api/v1/auth/register", json={
        "email": "another@example.test", "display_name": "Another", "password": "a-secure-password-2", "invitation_code": code,
    })
    assert duplicate_use.status_code == 400
    with app.app_context():
        db = get_db()
        invitation = db.scalar(select(Invitation).where(Invitation.code_hash == hash_token(code)))
        invitation_id = invitation.id
        assert invitation.used_count == 1
    admin_client = app.test_client()
    admin_client.post("/api/v1/auth/login", json={"email": "root@example.test", "password": "root-password-123"})
    uses = admin_client.get(f"/api/v1/admin/invitations/{invitation_id}/uses")
    assert uses.status_code == 200
    assert [item["user_email"] for item in uses.get_json()["uses"]] == ["new@example.test"]
    client.post("/api/v1/auth/logout", json={}, headers={"X-CSRF-Token": csrf(client)})
    login = client.post("/api/v1/auth/login", json={"email": "new@example.test", "password": "a-secure-password-1"})
    assert login.status_code == 200
    assert login.get_json()["user"]["display_name"] == "New"


def test_password_change_invalidates_existing_session(client):
    assert client.post("/api/v1/auth/login", json={"email": "root@example.test", "password": "root-password-123"}).status_code == 200
    response = client.post("/api/v1/auth/password", json={
        "current_password": "root-password-123", "new_password": "root-password-456",
    }, headers={"X-CSRF-Token": csrf(client)})
    assert response.status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": "root@example.test", "password": "root-password-123"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": "root@example.test", "password": "root-password-456"}).status_code == 200


def test_invitation_code_is_hashed_and_admin_route_requires_csrf(client, app):
    assert client.post("/api/v1/auth/login", json={"email": "root@example.test", "password": "root-password-123"}).status_code == 200
    missing_csrf = client.post("/api/v1/admin/invitations", json={"count": 1})
    assert missing_csrf.status_code == 403
    result = client.post("/api/v1/admin/invitations", json={"count": 2, "max_uses": 2, "ttl_days": 2}, headers={"X-CSRF-Token": csrf(client)})
    assert result.status_code == 201
    codes = [item["code"] for item in result.get_json()["invitations"]]
    assert len(codes) == 2 and all(len(code) >= 24 for code in codes)
    with app.app_context():
        db = get_db()
        stored = db.scalars(select(Invitation)).all()
        assert all(code not in {i.code_hash for i in stored} for code in codes)
        assert all(i.code_hash == hash_token(codes[idx]) for idx, i in enumerate(stored[-2:]))


def test_login_throttle_and_safe_error(client):
    for _ in range(5):
        response = client.post("/api/v1/auth/login", json={"email": "root@example.test", "password": "wrong-password"})
        assert response.status_code == 401
    response = client.post("/api/v1/auth/login", json={"email": "root@example.test", "password": "wrong-password"})
    assert response.status_code == 429
    assert "root-password" not in response.get_data(as_text=True)


def test_health_and_platform_catalog_are_dynamic(client):
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/platforms").status_code == 401
    client.post("/api/v1/auth/login", json={"email": "root@example.test", "password": "root-password-123"})
    result = client.get("/api/v1/platforms").get_json()["platforms"]
    assert {p["key"] for p in result} == {"chaoxing", "zhihuishu", "yinghua"}
    assert not any(p["integrated"] for p in result)
