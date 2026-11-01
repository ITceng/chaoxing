from __future__ import annotations

import logging

import pytest
from flask import jsonify, request
from sqlalchemy.exc import OperationalError

from unified_system.app import create_app
from unified_system.config import settings_from_env


def test_engine_is_configured_to_hide_sql_parameters(app):
    assert app.extensions["unified_engine"].hide_parameters is True


def test_database_readiness_uses_a_real_select(client):
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ready", "checks": {"database": "ok"}}


def test_readiness_and_database_error_responses_do_not_leak_exception_details(app, client, caplog):
    secret_marker = "do-not-emit-db-secret-marker"

    def fail():
        raise OperationalError("select 1", {"value": secret_marker}, RuntimeError(secret_marker))

    app.add_url_rule("/_test/database-error", view_func=fail)
    with caplog.at_level(logging.ERROR):
        response = client.get("/_test/database-error")
    assert response.status_code == 503
    assert response.get_json() == {"error": {"message": "Database temporarily unavailable", "status": 503}}
    assert secret_marker not in response.get_data(as_text=True)
    assert secret_marker not in caplog.text


def test_init_db_cli_is_disabled_in_production(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("APP_SECRET_KEY", "production-test-secret-012345678901234567890123")
    app = create_app({
        "TESTING": True,
        "APP_ENV": "production",
        "SECRET_KEY": "production-test-secret-012345678901234567890123",
        "DATABASE_URL": "sqlite:///:memory:",
        "DATA_DIR": tmp_path,
        "CREDENTIAL_ENCRYPTION_KEY": Fernet.generate_key().decode("ascii"),
    })
    result = app.test_cli_runner().invoke(args=["init-db"])
    assert result.exit_code != 0
    assert "disabled in production; use Alembic" in result.output
    app.extensions["unified_engine"].dispose()


def test_seed_catalog_is_an_explicit_idempotent_cli_command(app):
    runner = app.test_cli_runner()
    first = runner.invoke(args=["seed-catalog"])
    second = runner.invoke(args=["seed-catalog"])
    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output


def test_proxyfix_trust_is_explicit_and_uses_configured_hop_count(tmp_path):
    app = create_app({
        "TESTING": True,
        "APP_ENV": "testing",
        "SECRET_KEY": "test-session-secret-012345678901234567890123",
        "DATABASE_URL": "sqlite:///:memory:",
        "DATA_DIR": tmp_path,
        "TRUSTED_PROXY_HOPS": 1,
    })

    @app.get("/_test/proxy")
    def proxy_info():
        return jsonify({"remote": request.remote_addr, "scheme": request.scheme, "host": request.host})

    response = app.test_client().get("/_test/proxy", headers={
        "X-Forwarded-For": "198.51.100.20",
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": "learn.example.test",
    })
    assert response.get_json() == {
        "remote": "198.51.100.20", "scheme": "https", "host": "learn.example.test",
    }
    app.extensions["unified_engine"].dispose()


def test_proxy_hop_count_out_of_range_fails_closed(tmp_path):
    with pytest.raises(RuntimeError, match="TRUSTED_PROXY_HOPS"):
        create_app({
            "TESTING": True,
            "APP_ENV": "testing",
            "SECRET_KEY": "test-session-secret-012345678901234567890123",
            "DATABASE_URL": "sqlite:///:memory:",
            "DATA_DIR": tmp_path,
            "TRUSTED_PROXY_HOPS": 3,
        })


def test_zhihuishu_backend_token_uses_the_platform_key_environment_name(monkeypatch):
    token = "zhihuishu-backend-token-with-at-least-32-characters"
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("WORKER_TOKEN_CHAOXING", "")
    monkeypatch.setenv("WORKER_TOKEN_ZHIHUISHU", token)
    monkeypatch.setenv("WORKER_TOKEN_YINGHUA", "")
    config = settings_from_env()
    assert config["WORKER_TOKENS"]["zhihuishu"] == token


def test_production_pool_settings_are_bounded(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("APP_SECRET_KEY", "s" * 48)
    monkeypatch.setenv("DB_POOL_SIZE", "8")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "4")
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "1")
    config = settings_from_env()
    assert config["DB_POOL_SIZE"] == 8
    assert config["DB_MAX_OVERFLOW"] == 4
    assert config["TRUSTED_PROXY_HOPS"] == 1


def test_postgresql_pool_uses_the_configured_limits(tmp_path):
    app = create_app({
        "TESTING": True,
        "APP_ENV": "testing",
        "SECRET_KEY": "test-session-secret-012345678901234567890123",
        "DATABASE_URL": "postgresql+psycopg://test:test@127.0.0.1/unified_pool_test",
        "DATA_DIR": tmp_path,
        "CREDENTIAL_ENCRYPTION_KEY": "",
        "DB_POOL_SIZE": 3,
        "DB_MAX_OVERFLOW": 1,
        "DB_POOL_TIMEOUT_SECONDS": 7,
        "DB_POOL_RECYCLE_SECONDS": 90,
    })
    pool = app.extensions["unified_engine"].pool
    assert pool.size() == 3
    assert pool._max_overflow == 1
    assert pool._timeout == 7
    assert app.extensions["unified_engine"].hide_parameters is True
    app.extensions["unified_engine"].dispose()


def test_invalid_production_pool_settings_are_rejected(monkeypatch):
    monkeypatch.setenv("DB_POOL_SIZE", "0")
    with pytest.raises(RuntimeError, match="DB_POOL_SIZE"):
        settings_from_env()
