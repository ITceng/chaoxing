from __future__ import annotations

import pytest
from sqlalchemy import select

from unified_system.app import create_app
from unified_system.bootstrap import seed_catalog
from unified_system.db import Base, get_db
from unified_system.models import Role, User, UserRole
from unified_system.security import hash_password


@pytest.fixture()
def app(tmp_path):
    application = create_app({
        "TESTING": True,
        "APP_ENV": "testing",
        "SECRET_KEY": "test-session-secret-012345678901234567890123",
        "DATABASE_URL": "sqlite:///:memory:",
        "DATA_DIR": tmp_path,
        "CREDENTIAL_ENCRYPTION_KEY": "",
        "LOGIN_FAILURE_LIMIT": 5,
        "LOGIN_FAILURE_WINDOW_SECONDS": 900,
    })
    engine = application.extensions["unified_engine"]
    Base.metadata.create_all(engine)
    with application.app_context():
        seed_catalog()
        db = get_db()
        role = db.scalar(select(Role).where(Role.name == "superadmin"))
        user = User(email="root@example.test", display_name="Root", password_hash=hash_password("root-password-123"))
        db.add(user); db.flush()
        db.add(UserRole(user_id=user.id, role_id=role.id)); db.commit()
    yield application
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture()
def client(app):
    return app.test_client()
