from __future__ import annotations

import os
from pathlib import Path


def settings_from_env() -> dict:
    app_env = os.environ.get("APP_ENV", "development").strip().lower()
    production = app_env == "production"
    secret = os.environ.get("APP_SECRET_KEY", "").strip()
    if production and len(secret) < 32:
        raise RuntimeError("APP_SECRET_KEY must be configured with at least 32 characters in production")
    if not secret:
        # Development-only ephemeral key: restarting invalidates all sessions.
        import secrets
        secret = secrets.token_urlsafe(48)

    database_url = os.environ.get("DATABASE_URL", "sqlite:///./data/unified-dev.db").strip()
    if database_url.startswith("postgres://"):
        database_url = "postgresql+psycopg://" + database_url.removeprefix("postgres://")
    elif database_url.startswith("postgresql://") and "+" not in database_url.split(":", 1)[0]:
        database_url = "postgresql+psycopg://" + database_url.removeprefix("postgresql://")

    data_dir = Path(os.environ.get("DATA_DIR", "unified_system/data")).expanduser().resolve()
    return {
        "APP_ENV": app_env,
        "SECRET_KEY": secret,
        "DATABASE_URL": database_url,
        "DATA_DIR": data_dir,
        "COOKIE_SECURE": os.environ.get("COOKIE_SECURE", "1" if production else "0") == "1",
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "PERMANENT_SESSION_LIFETIME": 60 * 60 * 12,
        "MAX_CONTENT_LENGTH": 1024 * 1024,
        "CREDENTIAL_ENCRYPTION_KEY": os.environ.get("CREDENTIAL_ENCRYPTION_KEY", "").strip(),
        "BOOTSTRAP_ADMIN_EMAIL": os.environ.get("BOOTSTRAP_ADMIN_EMAIL", "").strip().lower(),
        "BOOTSTRAP_ADMIN_PASSWORD": os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", ""),
        "LOGIN_FAILURE_LIMIT": int(os.environ.get("LOGIN_FAILURE_LIMIT", "5")),
        "LOGIN_FAILURE_WINDOW_SECONDS": int(os.environ.get("LOGIN_FAILURE_WINDOW_SECONDS", "900")),
        "RESET_TOKEN_TTL_MINUTES": int(os.environ.get("RESET_TOKEN_TTL_MINUTES", "30")),
        "INVITATION_MAX_TTL_DAYS": int(os.environ.get("INVITATION_MAX_TTL_DAYS", "365")),
        "WORKER_LEASE_TTL_SECONDS": int(os.environ.get("WORKER_LEASE_TTL_SECONDS", "60")),
        "WORKER_HEARTBEAT_TTL_SECONDS": int(os.environ.get("WORKER_HEARTBEAT_TTL_SECONDS", "90")),
        "WORKER_TOKENS": {
            "chaoxing": os.environ.get("WORKER_TOKEN_CHAOXING", ""),
            "zhihuishu": os.environ.get("WORKER_TOKEN_ZHIHUISU", ""),
            "yinghua": os.environ.get("WORKER_TOKEN_YINGHUA", ""),
        },
    }
