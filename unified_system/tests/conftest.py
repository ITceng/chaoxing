from __future__ import annotations

import os

import pytest
from sqlalchemy import select
from sqlalchemy.engine import make_url

from unified_system.app import create_app
from unified_system.bootstrap import seed_catalog
from unified_system.db import Base, get_db
from unified_system.models import Role, User, UserRole
from unified_system.security import hash_password


@pytest.fixture()
def app(tmp_path):
    postgres_test_url = os.environ.get("UNIFIED_TEST_POSTGRESQL_URL", "").strip()
    database_url = "sqlite:///:memory:"
    if postgres_test_url:
        parsed = make_url(postgres_test_url)
        if (
            parsed.get_backend_name() != "postgresql"
            or not parsed.database
            or not parsed.database.lower().endswith(("_test", "_testdb"))
            or os.environ.get("UNIFIED_TEST_POSTGRESQL_CONFIRM") != "I_UNDERSTAND_TEST_DB_WILL_BE_DROPPED"
        ):
            raise RuntimeError("PostgreSQL tests require an explicitly disposable *_test database and confirmation")
        database_url = postgres_test_url
    application = create_app({
        "TESTING": True,
        "APP_ENV": "testing",
        "SECRET_KEY": "test-session-secret-012345678901234567890123",
        "DATABASE_URL": database_url,
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
