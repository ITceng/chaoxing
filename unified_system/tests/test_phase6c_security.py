from __future__ import annotations

import pytest


def test_security_headers_are_strict_and_hsts_is_not_emitted_by_http_capable_app(app, client):
    # The application may also be reached over private HTTP; only the verified host TLS
    # termination point should emit HSTS.
    app.config.update(APP_ENV="production", COOKIE_SECURE=True)
    response = client.get("/api/v1/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert response.headers["Permissions-Policy"] == "camera=(), microphone=(), geolocation=()"
    policy = response.headers["Content-Security-Policy"]
    assert "object-src 'none'" in policy
    assert "base-uri 'self'" in policy
    assert "frame-ancestors 'none'" in policy
    assert "script-src 'self'" in policy
    assert "connect-src 'self'" in policy
    assert "unsafe-inline" not in policy.split("script-src", 1)[1].split(";", 1)[0]
    assert "https://fonts.googleapis.com" not in policy
    assert "Strict-Transport-Security" not in response.headers


def test_production_session_cookie_is_secure_httponly_samesite_lax(app, client):
    app.config.update(
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    response = client.post("/api/v1/auth/login", json={
        "email": "root@example.test", "password": "root-password-123",
    })
    assert response.status_code == 200
    cookie = response.headers.get("Set-Cookie", "")
    assert "Secure" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie
    assert "Domain=" not in cookie


@pytest.mark.parametrize("path,payload", [
    ("/api/v1/auth/login", {"email": "root@example.test", "password": "root-password-123"}),
    ("/api/v1/auth/register", {}),
    ("/api/v1/auth/reset-password", {}),
])
def test_anonymous_auth_mutations_reject_cross_origin_requests(client, path, payload):
    response = client.post(path, json=payload, headers={"Origin": "https://attacker.invalid"})
    assert response.status_code == 403
    assert response.get_json()["error"]["message"] == "Cross-origin request rejected"


def test_authenticated_mutation_rejects_cross_origin_even_with_valid_csrf(client):
    login = client.post("/api/v1/auth/login", json={
        "email": "root@example.test", "password": "root-password-123",
    })
    assert login.status_code == 200
    response = client.post(
        "/api/v1/admin/invitations",
        json={"count": 1},
        headers={
            "X-CSRF-Token": login.get_json()["csrf"],
            "Origin": "https://attacker.invalid",
        },
    )
    assert response.status_code == 403
    assert response.get_json()["error"]["message"] == "CSRF validation failed"


def test_same_origin_check_requires_matching_scheme_and_origin_shape(app, client):
    from unified_system.security import origin_is_same_site

    with app.test_request_context("/", headers={"Host": "learn.example.test", "Origin": "http://learn.example.test"}):
        assert origin_is_same_site()
    with app.test_request_context("/", headers={"Host": "learn.example.test", "Origin": "https://learn.example.test"}):
        assert not origin_is_same_site()
    with app.test_request_context("/", headers={"Host": "learn.example.test", "Origin": "http://learn.example.test/other"}):
        assert not origin_is_same_site()
    with app.test_request_context("/", headers={"Host": "learn.example.test", "Origin": "http://[invalid"}):
        assert not origin_is_same_site()


def test_api_does_not_enable_cross_origin_resource_sharing(client):
    response = client.options("/api/v1/health", headers={
        "Origin": "https://untrusted.invalid",
        "Access-Control-Request-Method": "GET",
    })
    assert "Access-Control-Allow-Origin" not in response.headers
