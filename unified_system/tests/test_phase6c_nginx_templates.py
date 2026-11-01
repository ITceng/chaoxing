from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RENDERER_PATH = ROOT / "deploy" / "nginx" / "render-nginx-config.py"
SPEC = importlib.util.spec_from_file_location("phase6c_nginx_renderer", RENDERER_PATH)
assert SPEC and SPEC.loader
renderer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(renderer)


def test_http_bootstrap_renders_only_explicit_fqdn_and_keeps_acme_only():
    rendered = renderer.render_config("http", {"PUBLIC_DOMAIN": "learn.example.test"})
    assert "server_name learn.example.test;" in rendered
    assert "${PUBLIC_DOMAIN}" not in rendered
    assert "location ^~ /.well-known/acme-challenge/" in rendered
    assert "return 503;" in rendered
    assert "return 308" not in rendered
    assert "Strict-Transport-Security" not in rendered
    assert '"method":"$request_method"' in rendered
    assert '"uri":"$uri"' in rendered
    assert "$request_uri" not in rendered
    assert "$request " not in rendered


@pytest.mark.parametrize("domain", [
    "", "Learn.example.test", "learn.example.test/path", "learn.example.test;return 200",
    "learn.example.test$host", "example", "-bad.example.test", "bad-.example.test",
])
def test_domain_validation_rejects_injection_and_non_fqdn_values(domain):
    with pytest.raises(renderer.ConfigError):
        renderer.validate_domain(domain)


@pytest.mark.parametrize("path", [
    "", "relative/key.pem", "/etc/ssl/../shadow/key.pem", "/etc/ssl/my key.pem",
    "/etc/ssl/${KEY}.pem", "/etc/ssl/key.pem;return 200",
])
def test_tls_path_validation_rejects_unsafe_values(path):
    with pytest.raises(renderer.ConfigError):
        renderer.validate_absolute_path(path, "TLS path")


def test_https_template_withholds_hsts_by_default_and_explicitly_gates_it(tmp_path, monkeypatch):
    cert = tmp_path / "fullchain.pem"
    key = tmp_path / "privkey.pem"
    cert.write_text("test-only placeholder; certificate validation is mocked")
    key.write_text("test-only placeholder; certificate validation is mocked")
    monkeypatch.setattr(renderer, "validate_tls_files", lambda *_args: None)
    env = {
        "PUBLIC_DOMAIN": "learn.example.test",
        "TLS_FULLCHAIN_PATH": str(cert),
        "TLS_PRIVATE_KEY_PATH": str(key),
    }
    without_hsts = renderer.render_config("https", env)
    assert "server_name learn.example.test;" in without_hsts
    assert f"ssl_certificate {cert};" in without_hsts
    assert "HSTS withheld" in without_hsts
    assert "add_header Strict-Transport-Security" not in without_hsts

    with_hsts = renderer.render_config("https", env, enable_hsts=True)
    assert 'add_header Strict-Transport-Security "max-age=15552000" always;' in with_hsts
    assert "includeSubDomains" not in with_hsts
    assert "preload" not in with_hsts
    with pytest.raises(renderer.ConfigError):
        renderer.render_config("http", {"PUBLIC_DOMAIN": "learn.example.test"}, enable_hsts=True)


def test_https_template_has_tls_redirect_limits_and_no_public_internal_routes():
    template = (ROOT / "deploy" / "nginx" / "unified-learning.conf.template").read_text()
    assert "listen 80 default_server" in template
    assert "return 308 https://${PUBLIC_DOMAIN}$request_uri" in template
    assert "listen 443 ssl http2 default_server" in template
    assert "ssl_protocols TLSv1.2 TLSv1.3" in template
    assert "client_max_body_size 1m" in template
    assert "limit_req zone=unified_login" in template
    assert "limit_req zone=unified_api" in template
    assert "location = /api/internal/workers { return 404; }" in template
    assert "location ^~ /api/internal/workers/ { return 404; }" in template
    assert "location = /healthz { return 404; }" in template
    assert "${HSTS_HEADER}" in template
    assert "includeSubDomains" not in template
    assert "preload" not in template
    assert '"method":"$request_method"' in template
    assert '"uri":"$uri"' in template
    log_format = next(line for line in template.splitlines() if line.startswith("log_format unified_safe"))
    assert "$request_uri" not in log_format
    assert "$request\"" not in template


def test_edge_nginx_keeps_same_origin_headers_cache_and_worker_denial():
    config = (ROOT / "frontend" / "nginx" / "nginx.conf").read_text()
    headers = (ROOT / "frontend" / "nginx" / "security-headers.conf").read_text()
    assert "client_max_body_size 1m" in config
    assert "include /etc/nginx/snippets/unified-learning-security-headers.conf;" in config
    assert "location ^~ /assets/" in config
    assert 'add_header Cache-Control "public, max-age=31536000, immutable";' in config
    assert 'add_header Cache-Control "no-store";' in config
    assert "location ^~ /api/internal/workers/" in config
    assert "$http_x_real_ip" in config
    assert "proxy_set_header Upgrade" not in config
    assert "Strict-Transport-Security" not in headers
    assert "style-src-attr 'unsafe-inline'" in headers
    assert "script-src 'self'" in headers


def test_certbot_deploy_hook_tests_before_reload():
    hook = (ROOT / "deploy" / "nginx" / "certbot-deploy-hook.sh").read_text()
    assert hook.index("nginx -t") < hook.index("systemctl reload nginx")
