#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""超星多账号控制面板.

启动:
    PANEL_HOST=0.0.0.0 PANEL_PORT=8080 python panel.py

生产部署请放在 HTTPS 反向代理之后，并设置 PANEL_COOKIE_SECURE=1。
"""
from __future__ import annotations

import atexit
import json
import os
import secrets
import shutil
import socket
import sys
import time
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Optional

from flask import Flask, Response, jsonify, redirect, request, send_from_directory, session
from markupsafe import escape
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from api.answer import PROVIDER_REGISTRY
from api.logger import clear_account_logs, logger
from panel_core.auth import PanelAuth
from panel_core.runner import RunnerManager
from panel_core.security import (
    LoginThrottle,
    SecretBox,
    SlidingWindowLimiter,
    constant_time_equal,
    generate_token,
    get_client_ip,
    origin_allowed,
)
from panel_core.store import (
    DATA_DIR,
    AccountStore,
    AuditLog,
    SettingsStore,
    _atomic_write,
)

APP_VERSION = "4.0.0"
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "panel_static"

ERROR_PAGE_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
{refresh}
<title>{code} · {title}</title>
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 24px;
    background: #f5f6f8; color: #172033;
    font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
          "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
  }}
  .card {{
    width: min(520px, 100%); background: #fff; border: 1px solid #e4e7ec;
    border-radius: 16px; padding: 28px; box-shadow: 0 8px 30px rgba(16,24,40,.08);
  }}
  .code {{ font-size: 13px; font-weight: 700; letter-spacing: .12em; color: #b42318; }}
  h1 {{ margin: 6px 0 10px; font-size: 21px; }}
  p {{ margin: 0 0 10px; color: #475467; }}
  .hint {{
    margin-top: 14px; padding: 12px 14px; border-radius: 10px; font-size: 13px;
    background: #fffaeb; border: 1px solid #fedf89; color: #93370d;
  }}
  .rid {{ margin-top: 14px; font-size: 12px; color: #667085; word-break: break-all; }}
  a.btn {{
    display: inline-block; margin-top: 18px; padding: 10px 18px; border-radius: 10px;
    background: #175cd3; color: #fff; text-decoration: none; font-weight: 600;
  }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #0f1522; color: #e6ecf5; }}
    .card {{ background: #161d2c; border-color: #263044; }}
    p {{ color: #a9b6c9; }}
  }}
</style>
</head>
<body>
<main class="card">
  <div class="code">HTTP {code}</div>
  <h1>{title}</h1>
  <p>{message}</p>
  {hint}
  {rid}
  <a class="btn" href="/">返回控制面板</a>
</main>
</body>
</html>"""

PANEL_HOST = os.environ.get("PANEL_HOST", "127.0.0.1")
PANEL_PORT = int(os.environ.get("PANEL_PORT", "8080"))
PANEL_DEBUG = os.environ.get("PANEL_DEBUG", "0") == "1"
TRUST_PROXY = os.environ.get("PANEL_TRUST_PROXY", "0") == "1"
COOKIE_SECURE = os.environ.get("PANEL_COOKIE_SECURE", "0") == "1"
API_KEY = os.environ.get("PANEL_API_KEY", "").strip()
SESSION_HOURS = int(os.environ.get("PANEL_SESSION_HOURS", "12"))
ALLOWED_ORIGINS = [
    item.strip() for item in os.environ.get("PANEL_ALLOWED_ORIGINS", "").split(",") if item.strip()
]
FRAME_ANCESTORS = os.environ.get(
    "PANEL_FRAME_ANCESTORS", "'self' https://*.e2b.app"
).strip()


def _load_or_create_session_secret() -> str:
    env_secret = os.environ.get("PANEL_SESSION_SECRET", "").strip()
    if env_secret:
        return env_secret
    path = DATA_DIR / "session.key"
    try:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    secret = secrets.token_urlsafe(48)
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, secret)
    except OSError:
        pass
    return secret


def create_app(data_dir: Optional[Path] = None, supervisor: bool = False) -> Flask:
    """构建面板应用.

    ``supervisor=True`` 时在第一次请求时启动无人值守线程（仅用于真正对外服务的
    实例；测试或嵌入式使用时保持关闭，避免自动拉起真实账号）。
    """
    data_dir = Path(data_dir) if data_dir else DATA_DIR
    app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")
    app.secret_key = _load_or_create_session_secret()
    app.config.update(
        SESSION_COOKIE_NAME="chaoxing_panel",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=COOKIE_SECURE,
        PERMANENT_SESSION_LIFETIME=int(SESSION_HOURS * 3600),
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
        JSON_AS_ASCII=False,
    )
    if TRUST_PROXY:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    # 可选：只接受指定 Host，抵御 Host 头伪造（尤其配合反代时建议开启）
    trusted_hosts = [
        item.strip() for item in os.environ.get("PANEL_TRUSTED_HOSTS", "").split(",") if item.strip()
    ]
    if trusted_hosts:
        app.config["TRUSTED_HOSTS"] = trusted_hosts

    secret_box = SecretBox(data_dir / "secret.key")
    accounts = AccountStore(secret_box, data_dir / "accounts.json")
    settings = SettingsStore(secret_box, data_dir / "settings.enc")
    audit = AuditLog(data_dir / "audit.log")
    auth = PanelAuth(data_dir / "admin.json")
    auth.bootstrap_from_env()
    manager = RunnerManager(accounts, settings)

    login_window = LoginThrottle(
        max_failures=int(os.environ.get("PANEL_LOGIN_ATTEMPTS", "5")),
        lock_seconds=int(os.environ.get("PANEL_LOGIN_LOCK_SECONDS", "900")),
    )
    login_ip_limiter = SlidingWindowLimiter()
    api_limiter = SlidingWindowLimiter()
    setup_limiter = SlidingWindowLimiter()

    app.extensions["chaoxing_panel"] = {
        "data_dir": data_dir,
        "secret_box": secret_box,
        "accounts": accounts,
        "settings": settings,
        "audit": audit,
        "auth": auth,
        "manager": manager,
    }

    # ------------------------------------------------------------------ helpers
    def client_ip() -> str:
        return get_client_ip(request.remote_addr, request.headers, TRUST_PROXY)

    def api_error(message: str, status: int = 400):
        return jsonify({"error": message}), status

    def wants_json() -> bool:
        """API 路径，或客户端明确只要 JSON 时返回 JSON；浏览器访问返回 HTML 页面."""
        if request.path.startswith("/api/"):
            return True
        best = request.accept_mimetypes.best
        if best != "application/json":
            return False
        return request.accept_mimetypes[best] > (request.accept_mimetypes["text/html"] or 0)

    def error_page(code: int, title: str, message: str, hint: str = "",
                   request_id: str = "", auto_back: bool = False):
        """渲染面板自己的错误页（不依赖任何外部资源，手机也能正常显示）."""
        html = ERROR_PAGE_TEMPLATE.format(
            code=code,
            title=str(escape(title)),
            message=str(escape(message)),
            hint=f'<div class="hint">{escape(hint)}</div>' if hint else "",
            rid=f'<div class="rid">错误编号：{escape(request_id)}</div>' if request_id else "",
            # 用 meta refresh 而不是内联脚本：CSP 禁止内联 JS
            refresh='<meta http-equiv="refresh" content="5;url=/">' if auto_back else "",
        )
        return Response(html, status=code, mimetype="text/html")

    def api_key_valid() -> bool:
        if not API_KEY:
            return False
        supplied = request.headers.get("X-API-Key", "")
        if not supplied:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.lower().startswith("bearer "):
                supplied = auth_header[7:].strip()
        return bool(supplied) and constant_time_equal(supplied, API_KEY)

    def session_valid() -> bool:
        if "user" not in session:
            return False
        if session.get("sv") != auth.session_version:
            session.clear()
            return False
        return True

    def check_origin() -> bool:
        return origin_allowed(request.headers.get("Origin"), request.host, ALLOWED_ORIGINS)

    def check_csrf() -> bool:
        token = session.get("csrf", "")
        supplied = request.headers.get("X-CSRF-Token", "")
        return bool(token) and bool(supplied) and constant_time_equal(token, supplied)

    def require_auth(write: bool = False):
        def decorator(fn: Callable):
            @wraps(fn)
            def wrapper(*args, **kwargs):
                if not check_origin():
                    return api_error("Origin 不被允许", 403)
                if api_key_valid():
                    return fn(*args, **kwargs)
                if not session_valid():
                    return api_error("未登录或会话已失效", 401)
                if write and not check_csrf():
                    return api_error("CSRF 校验失败，请刷新页面后重试", 403)
                return fn(*args, **kwargs)

            return wrapper

        return decorator

    def actor() -> str:
        if api_key_valid():
            return "api-key"
        return str(session.get("user", "unknown"))

    def audit_write(action: str, target: str = "", result: str = "ok", detail: Optional[dict] = None):
        try:
            audit.write(action, actor(), client_ip(), target, result, detail)
        except Exception:
            pass

    def ensure_dirs() -> None:
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(data_dir, 0o700)
        except OSError:
            pass

    ensure_dirs()

    # --------------------------------------------------------------- middleware
    @app.before_request
    def start_supervisor_once():
        if not supervisor or os.environ.get("PANEL_AUTOSTART", "1") != "1":
            return None
        if not app.config.get("_supervisor_started"):
            app.config["_supervisor_started"] = True
            manager.start_supervisor()
        return None

    @app.before_request
    def rate_limit_api():
        if not request.path.startswith("/api/"):
            return None
        allowed, retry_after = api_limiter.allow(f"api:{client_ip()}", 300, 60)
        if not allowed:
            response = jsonify({"error": "请求过于频繁，请稍后再试"})
            response.status_code = 429
            response.headers["Retry-After"] = str(max(1, int(retry_after)))
            return response
        return None

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        response.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")
        # 只在允许被同源嵌入时才加 X-Frame-Options（预览环境需要跨域 iframe）
        if FRAME_ANCESTORS.strip() == "'self'":
            response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        # 仅当本次请求确实是 HTTPS（或代理声明 HTTPS）时才发 HSTS，避免把本地 HTTP 锁死
        if request.is_secure or COOKIE_SECURE:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
            f"frame-ancestors {FRAME_ANCESTORS}; base-uri 'self'; form-action 'self'",
        )
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers.setdefault("Pragma", "no-cache")
        return response

    HTTP_MESSAGES = {
        400: ("请求格式不正确", "提交的数据格式不对，请刷新页面后重试。"),
        401: ("需要登录", "请先登录控制面板。"),
        403: ("没有权限", "Origin 校验失败，或当前账号无权执行该操作。"),
        404: ("页面不存在", "地址可能输错了，或者该功能已经改名。"),
        405: ("请求方法不被允许", "该地址不支持当前请求方式。"),
        413: ("请求内容过大", "上传的数据超过 2MB 限制。"),
        429: ("请求过于频繁", "请稍等一会儿再试。"),
        500: ("服务器内部错误", "面板处理请求时出错，详细信息已写入日志。"),
    }
    HTTP_HINTS = {
        403: "如果是从其他网站跳转过来的，请直接打开面板地址重试。",
        404: "如果你确信地址没错：请确认访问的是本面板的地址和端口——"
             "端口被其他程序（例如 Java/Spring 应用）占用时，浏览器会显示对方的错误页。",
        429: "频繁操作会触发保护，稍后再试即可。",
    }

    @app.errorhandler(HTTPException)
    def handle_http_error(exc: HTTPException):
        code = exc.code or 500
        title, message = HTTP_MESSAGES.get(code, ("请求失败", "服务器返回了错误。"))
        if wants_json():
            response, status = api_error(f"{title}: {message}", code)
            return response, status
        return error_page(
            code, title, message,
            hint=HTTP_HINTS.get(code, ""),
            auto_back=(code == 404),
        )

    @app.errorhandler(Exception)
    def handle_unexpected_error(exc: Exception):
        # 已知的 HTTP 异常交给上面的处理器，这里只兜底真正的程序错误
        if isinstance(exc, HTTPException):
            return handle_http_error(exc)
        import traceback

        request_id = secrets.token_hex(4)
        logger.error(f"[{request_id}] 面板未处理异常 {type(exc).__name__}: {exc}")
        logger.debug(traceback.format_exc())
        if wants_json():
            response, status = api_error("服务器内部错误，请查看面板日志", 500)
            response.headers["X-Request-Id"] = request_id
            return response, status
        return error_page(
            500, "服务器内部错误",
            "面板处理请求时出错，详细信息已写入日志。",
            hint="可以重试一次；如果持续出现，请查看 data/ 或终端里的日志，并带上错误编号反馈。",
            request_id=request_id,
        )

    # ------------------------------------------------------------------- static
    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html", max_age=0)

    @app.get("/favicon.ico")
    def favicon():
        return ("", 204)

    @app.get("/robots.txt")
    def robots():
        return Response("User-agent: *\nDisallow: /\n", mimetype="text/plain")

    @app.get("/error")
    def error_entry():
        """有些反向代理/网关会把错误重写到 /error，这里统一回面板首页."""
        return redirect("/", code=302)

    # ------------------------------------------------------------------- health
    def health_payload():
        return jsonify({
            "ok": True,
            "app": "chaoxing-panel",
            "version": APP_VERSION,
            "time": int(time.time()),
            "authenticated_required": True,
        })

    @app.get("/api/health")
    def health():
        return health_payload()

    # 常见探针路径（Docker/K8s/Nginx 默认会打这些），行为与 /api/health 一致
    @app.get("/healthz")
    @app.get("/livez")
    @app.get("/readyz")
    def health_alias():
        return health_payload()

    @app.get("/api/auth/status")
    def auth_status():
        return jsonify({
            "configured": auth.configured,
            "authenticated": session_valid(),
            "user": session.get("user") if session_valid() else None,
            "version": APP_VERSION,
        })

    @app.get("/api/meta")
    @require_auth()
    def meta():
        return jsonify({
            "version": APP_VERSION,
            "providers": sorted(PROVIDER_REGISTRY.keys()),
            "notopen_actions": ["retry", "ask", "continue"],
            "max_concurrent_accounts": settings.get().get("max_concurrent_accounts", 2),
            "supervisor": manager.supervisor_running,
            "max_auto_restarts": manager.max_auto_restarts,
        })

    # --------------------------------------------------------------------- auth
    @app.post("/api/auth/setup")
    def setup():
        if not check_origin():
            return api_error("Origin 不被允许", 403)
        if auth.configured:
            return api_error("面板已初始化", 409)
        # 初始化接口必须限流：否则公网上可以被用来反复试探 PANEL_SETUP_TOKEN
        allowed, retry_after = setup_limiter.allow(f"setup:{client_ip()}", 10, 900)
        if not allowed:
            response = api_error("初始化尝试过于频繁，请稍后再试", 429)
            response[0].headers["Retry-After"] = str(max(1, int(retry_after)))
            return response
        setup_token = os.environ.get("PANEL_SETUP_TOKEN", "").strip()
        if setup_token and not constant_time_equal(request.headers.get("X-Setup-Token", ""), setup_token):
            return api_error("初始化令牌不正确", 403)
        data = request.get_json(silent=True) or {}
        try:
            auth.set_admin(str(data.get("username", "")), str(data.get("password", "")))
        except ValueError as e:
            return api_error(str(e), 400)
        session.clear()
        session["user"] = auth.username
        session["sv"] = auth.session_version
        session["csrf"] = generate_token()
        session.permanent = True
        audit.write("auth.setup", auth.username, client_ip(), result="ok")
        return jsonify({"user": auth.username, "csrf": session["csrf"]})

    @app.post("/api/auth/login")
    def login():
        if not check_origin():
            return api_error("Origin 不被允许", 403)
        if not auth.configured:
            return api_error("面板尚未初始化", 409)
        # 管理员密码被外部重置后：清除失败锁定，避免刚重置完仍被锁死
        version = auth.session_version
        if app.config.get("_auth_version") != version:
            app.config["_auth_version"] = version
            login_window.clear()
            login_ip_limiter.clear()
        ip = client_ip()
        allowed, retry_after = login_ip_limiter.allow(f"login:{ip}", 10, 300)
        if not allowed:
            response = api_error("登录尝试过于频繁", 429)
            response[0].headers["Retry-After"] = str(max(1, int(retry_after)))
            return response
        data = request.get_json(silent=True) or {}
        username = str(data.get("username", "")).strip()
        password = str(data.get("password", ""))
        for key in (f"ip:{ip}", f"user:{username.lower()}"):
            locked, seconds = login_window.is_locked(key)
            if locked:
                response = api_error("失败次数过多，账号或 IP 已临时锁定", 429)
                response[0].headers["Retry-After"] = str(max(1, int(seconds)))
                return response
        if not auth.verify(username, password):
            login_window.record_failure(f"ip:{ip}")
            login_window.record_failure(f"user:{username.lower()}")
            audit.write("auth.login", auth.username, ip, result="failed")
            return api_error("用户名或密码错误", 401)
        login_window.reset(f"ip:{ip}")
        login_window.reset(f"user:{username.lower()}")
        session.clear()
        session["user"] = auth.username
        session["sv"] = auth.session_version
        session["csrf"] = generate_token()
        session.permanent = True
        audit.write("auth.login", auth.username, ip, result="ok")
        return jsonify({"user": auth.username, "csrf": session["csrf"]})

    @app.post("/api/auth/logout")
    def logout():
        if not check_origin():
            return api_error("Origin 不被允许", 403)
        user = session.get("user")
        session.clear()
        if user:
            audit.write("auth.logout", str(user), client_ip(), result="ok")
        return jsonify({"ok": True})

    @app.get("/api/auth/me")
    @require_auth()
    def me():
        return jsonify({
            "user": session.get("user", "api-key"),
            "csrf": session.get("csrf", ""),
            "version": APP_VERSION,
            "auth_method": "api-key" if api_key_valid() else "session",
        })

    @app.post("/api/auth/password")
    @require_auth(write=True)
    def change_password():
        data = request.get_json(silent=True) or {}
        try:
            auth.change_password(str(data.get("old_password", "")), str(data.get("new_password", "")))
        except ValueError as e:
            audit_write("auth.password", result="failed")
            return api_error(str(e), 400)
        session["sv"] = auth.session_version
        session["csrf"] = generate_token()
        audit_write("auth.password")
        return jsonify({"ok": True, "csrf": session["csrf"]})

    @app.post("/api/auth/logout-all")
    @require_auth(write=True)
    def logout_all():
        version = auth.bump_session_version()
        audit_write("auth.logout_all", detail={"session_version": version})
        session.clear()
        return jsonify({"ok": True})

    # ----------------------------------------------------------------- accounts
    def _validate_account_payload(data: dict[str, Any], creating: bool) -> Optional[str]:
        if "name" in data and len(str(data["name"])) > 64:
            return "账号名称过长"
        if "username" in data and len(str(data["username"])) > 64:
            return "用户名过长"
        if "password" in data and len(str(data["password"])) > 256:
            return "密码过长"
        if "cookies_text" in data and len(str(data["cookies_text"])) > 64 * 1024:
            return "Cookie 内容过长"
        if "course_list" in data and len(str(data["course_list"])) > 2000:
            return "课程列表过长"
        action = data.get("notopen_action", "retry")
        if action not in ("retry", "ask", "continue"):
            return "notopen_action 无效"
        try:
            if "speed" in data and not (1.0 <= float(data["speed"]) <= 2.0):
                return "倍速需在 1.0-2.0 之间"
            if "jobs" in data and not (1 <= int(data["jobs"]) <= 8):
                return "并发章节数需在 1-8 之间"
            if "work_max_retries" in data and not (1 <= int(data["work_max_retries"]) <= 10):
                return "重做次数需在 1-10 之间"
            if "target_count" in data and not (1 <= int(data["target_count"]) <= 10000):
                return "目标次数需在 1-10000 之间"
        except (TypeError, ValueError):
            return "数值字段格式不正确"
        provider = str(data.get("provider", "") or "")
        if provider and provider not in PROVIDER_REGISTRY:
            return "题库 provider 无效"
        if creating:
            has_pwd = bool(str(data.get("username", "")).strip() and str(data.get("password", "")))
            has_cookie = bool(str(data.get("cookies_text", "")).strip()) or bool(data.get("use_cookies"))
            if not (has_pwd or has_cookie):
                return "请填写账号密码，或提供 Cookie 并启用 Cookie 登录"
        return None

    def _strip_control(text: str) -> str:
        """去掉控制字符（换行/回车等），避免把日志或审计记录"劈开"伪造条目."""
        cleaned = "".join(
            ch for ch in str(text) if ch in ("\t",) or ord(ch) >= 32 and ord(ch) != 127
        )
        return cleaned

    def _account_payload() -> dict[str, Any]:
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return {}
        for key in ("name", "notes", "course_list", "username"):
            if isinstance(data.get(key), str):
                data[key] = _strip_control(data[key]).strip() if key != "notes" else _strip_control(data[key])
        return data

    @app.get("/api/accounts")
    @require_auth()
    def list_accounts():
        result = []
        for account in accounts.list_public():
            runner = manager.get(account["id"])
            runtime = runner.snapshot() if runner else {
                "status": "idle", "phase": "idle", "alive": False,
                "paused": False, "stopped": False, "error": "",
                "summary": {}, "counts": {}, "total": 0, "done": 0,
                "progress": 0.0, "current": [], "courses": [],
                "started_at": None, "finished_at": None,
            }
            result.append({**account, "runtime": runtime})
        return jsonify({"accounts": result})

    @app.post("/api/accounts")
    @require_auth(write=True)
    def create_account():
        data = _account_payload()
        error = _validate_account_payload(data, creating=True)
        if error:
            return api_error(error, 400)
        if not str(data.get("name", "")).strip():
            data["name"] = str(data.get("username", ""))[:64] or "未命名账号"
        if data.get("cookies_text"):
            data["use_cookies"] = True
        account = accounts.create(data)
        audit_write("account.create", account["id"], detail={"name": account["name"]})
        return jsonify({"account": account}), 201

    @app.patch("/api/accounts/<account_id>")
    @require_auth(write=True)
    def update_account(account_id: str):
        if not accounts.get_raw(account_id):
            return api_error("账号不存在", 404)
        data = _account_payload()
        error = _validate_account_payload(data, creating=False)
        if error:
            return api_error(error, 400)
        account = accounts.update(account_id, data)
        runner = manager.get(account_id)
        note = ""
        if runner and runner.alive:
            note = "账号正在运行，修改将在下次启动时生效"
        audit_write("account.update", account_id, detail={"name": account.get("name") if account else ""})
        return jsonify({"account": account, "note": note})

    @app.delete("/api/accounts/<account_id>")
    @require_auth(write=True)
    def delete_account(account_id: str):
        if not accounts.get_raw(account_id):
            return api_error("账号不存在", 404)
        runner = manager.get(account_id)
        if runner and runner.alive:
            runner.stop()
            thread = runner.thread
            if thread:
                thread.join(3.0)
            if runner.alive:
                return api_error("账号仍在停止中，请稍后重试", 409)
        manager.remove(account_id)
        accounts.delete(account_id)
        account_dir = accounts.account_dir(account_id)
        try:
            account_dir = account_dir.resolve()
            root = accounts.account_dir("x").parent.resolve()
            if str(account_dir).startswith(str(root)) and account_dir.exists():
                shutil.rmtree(account_dir, ignore_errors=True)
        except OSError:
            pass
        clear_account_logs(account_id)
        audit_write("account.delete", account_id)
        return jsonify({"ok": True})

    @app.get("/api/accounts/<account_id>")
    @require_auth()
    def get_account(account_id: str):
        account = accounts.get_public(account_id)
        if not account:
            return api_error("账号不存在", 404)
        runner = manager.get(account_id)
        runtime = runner.snapshot() if runner else {"status": "idle"}
        return jsonify({"account": account, "runtime": runtime})

    def _runner_action(account_id: str, action: str):
        if not accounts.get_raw(account_id):
            return api_error("账号不存在", 404)
        runner = manager.get_or_create(account_id)
        if action == "start":
            ok, message = runner.start()
        elif action == "pause":
            ok, message = runner.pause()
        elif action == "resume":
            ok, message = runner.resume()
        elif action == "stop":
            ok, message = runner.stop()
        else:
            return api_error("未知操作", 400)
        audit_write(f"account.{action}", account_id, result="ok" if ok else "rejected",
                    detail={"message": message})
        if not ok:
            return api_error(message, 409)
        return jsonify({"ok": True, "message": message, "runtime": runner.snapshot()})

    @app.post("/api/accounts/<account_id>/start")
    @require_auth(write=True)
    def start_account(account_id: str):
        return _runner_action(account_id, "start")

    @app.post("/api/accounts/<account_id>/pause")
    @require_auth(write=True)
    def pause_account(account_id: str):
        return _runner_action(account_id, "pause")

    @app.post("/api/accounts/<account_id>/resume")
    @require_auth(write=True)
    def resume_account(account_id: str):
        return _runner_action(account_id, "resume")

    @app.post("/api/accounts/<account_id>/stop")
    @require_auth(write=True)
    def stop_account(account_id: str):
        return _runner_action(account_id, "stop")

    @app.get("/api/accounts/<account_id>/logs")
    @require_auth()
    def account_logs(account_id: str):
        if not accounts.get_public(account_id):
            return api_error("账号不存在", 404)
        try:
            after = max(0, int(request.args.get("after", "0")))
            limit = min(500, max(1, int(request.args.get("limit", "200"))))
        except ValueError:
            return api_error("参数格式不正确", 400)
        runner = manager.get(account_id)
        items = runner.logs(after=after, limit=limit) if runner else []
        return jsonify({
            "logs": items,
            "next": items[-1]["seq"] if items else after,
        })

    @app.delete("/api/accounts/<account_id>/logs")
    @require_auth(write=True)
    def clear_logs(account_id: str):
        if not accounts.get_public(account_id):
            return api_error("账号不存在", 404)
        clear_account_logs(account_id)
        audit_write("account.logs.clear", account_id)
        return jsonify({"ok": True})

    @app.post("/api/actions/start-all")
    @require_auth(write=True)
    def start_all():
        results = []
        for account in accounts.list_public():
            runner = manager.get_or_create(account["id"])
            ok, message = runner.start()
            results.append({"id": account["id"], "ok": ok, "message": message})
        audit_write("accounts.start_all", detail={"count": len(results)})
        return jsonify({"results": results})

    @app.post("/api/actions/stop-all")
    @require_auth(write=True)
    def stop_all():
        results = []
        for account in accounts.list_public():
            runner = manager.get(account["id"])
            if runner and runner.alive:
                ok, message = runner.stop()
                results.append({"id": account["id"], "ok": ok, "message": message})
        audit_write("accounts.stop_all", detail={"count": len(results)})
        return jsonify({"results": results})

    @app.post("/api/actions/pause-all")
    @require_auth(write=True)
    def pause_all():
        results = []
        for account in accounts.list_public():
            runner = manager.get(account["id"])
            if runner and runner.alive:
                ok, message = runner.pause()
                results.append({"id": account["id"], "ok": ok, "message": message})
        audit_write("accounts.pause_all", detail={"count": len(results)})
        return jsonify({"results": results})

    @app.post("/api/actions/resume-all")
    @require_auth(write=True)
    def resume_all():
        results = []
        for account in accounts.list_public():
            runner = manager.get(account["id"])
            if runner and runner.alive:
                ok, message = runner.resume()
                results.append({"id": account["id"], "ok": ok, "message": message})
        audit_write("accounts.resume_all", detail={"count": len(results)})
        return jsonify({"results": results})

    # ----------------------------------------------------------------- settings
    @app.get("/api/settings")
    @require_auth()
    def get_settings():
        return jsonify({"settings": settings.get_masked()})

    @app.put("/api/settings")
    @require_auth(write=True)
    def update_settings():
        data = _account_payload()
        if not isinstance(data, dict):
            return api_error("请求体必须是 JSON 对象", 400)
        allowed_top = {"max_concurrent_accounts", "tiku", "notification"}
        if set(data.keys()) - allowed_top:
            return api_error("包含不允许修改的设置项", 400)
        if "max_concurrent_accounts" in data:
            try:
                value = int(data["max_concurrent_accounts"])
            except (TypeError, ValueError):
                return api_error("max_concurrent_accounts 必须是整数", 400)
            if not (1 <= value <= 50):
                return api_error("同时运行账号数需在 1-50 之间", 400)
            data["max_concurrent_accounts"] = value
        settings.update(data)
        manager.update_limit(int(settings.get().get("max_concurrent_accounts", 2)))
        audit_write("settings.update", detail={"keys": sorted(data.keys())})
        return jsonify({"settings": settings.get_masked()})

    @app.get("/api/audit")
    @require_auth()
    def get_audit():
        try:
            limit = min(500, max(1, int(request.args.get("limit", "100"))))
        except ValueError:
            limit = 100
        return jsonify({"logs": audit.tail(limit)})

    # --------------------------------------------------------------- teardown
    atexit.register(manager.shutdown)
    return app


app = create_app(supervisor=True)


def apply_admin_reset(auth: PanelAuth, username: str, password: str) -> tuple[bool, str]:
    """写入新的管理员账号；成功返回 (True, 提示)，失败返回 (False, 原因)."""
    try:
        auth.set_admin(username, password)
    except ValueError as e:
        return False, f"重置失败: {e}"
    return True, f"管理员已更新为 {auth.username}，所有旧会话已失效"


def _panel_reachable(port: int) -> bool:
    """检测本机是否已有面板进程在跑（用于提示必须重启才能生效）."""
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1.5) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001 - 只是探测
        return False


def reset_admin_command(app: Flask, port: int = PANEL_PORT) -> int:
    """`--reset-admin`：忘记密码时在服务器/本机重置管理员."""
    import getpass

    info = app.extensions["chaoxing_panel"]
    auth: PanelAuth = info["auth"]
    print("=" * 52)
    print("  重置面板管理员")
    print("=" * 52)
    print(f"数据目录  : {info.get('data_dir')}")
    print(f"管理员文件: {auth.path}")
    if auth.configured:
        print(f"当前用户名: {auth.username}")
    else:
        print("当前状态  : 尚未初始化，本次将直接创建管理员")
    print()

    env_user = os.environ.get("PANEL_ADMIN_USER", "").strip()
    env_password = os.environ.get("PANEL_ADMIN_PASSWORD", "")
    if env_user and env_password:
        username, password = env_user, env_password
        print("使用环境变量 PANEL_ADMIN_USER / PANEL_ADMIN_PASSWORD")
    else:
        if not sys.stdin.isatty():
            print("当前不是交互式终端，无法输入密码。")
            print("请改用环境变量方式（注意保留引号）：")
            print('  PANEL_ADMIN_USER=admin PANEL_ADMIN_PASSWORD="新密码至少10位" python panel.py --reset-admin')
            return 2
        default_user = auth.username if auth.configured else "admin"
        username = input(f"管理员用户名 [{default_user}]: ").strip() or default_user
        password = getpass.getpass("新密码（至少 10 位）: ")
        confirm = getpass.getpass("再输入一次: ")
        if password != confirm:
            print("两次输入不一致，未做任何修改。")
            return 1

    ok, message = apply_admin_reset(auth, username, password)
    print(("✅ " if ok else "❌ ") + message)
    if not ok:
        return 1

    if _panel_reachable(port):
        print()
        print("面板正在运行：新密码已立即生效，无需重启。")
        print("旧密码已失效，已登录的会话会被要求重新登录（连续输错造成的临时锁定也已清除）。")
    else:
        print("现在启动面板即可用新密码登录。")
    print("账号、课程、题库、通知等数据都不受影响。")
    return 0


def _http_get(url: str, timeout: float = 2.0) -> tuple[int, str]:
    """极简 HTTP GET（只用标准库），失败返回 (0, 错误信息)."""
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read(64 * 1024).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(16 * 1024).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            body = ""
        return exc.code, body
    except Exception as exc:  # noqa: BLE001
        return 0, f"{type(exc).__name__}: {exc}"


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.6)
        return sock.connect_ex((host if host not in ("0.0.0.0", "::") else "127.0.0.1", port)) != 0


def _explain_bind_failure(port: int, host: str = "127.0.0.1") -> int:
    """端口被占用时给出可操作的中文提示（很多"打不开/显示别的页面"都是这个原因）."""
    print()
    print(f"❌ 无法在 {host}:{port} 启动：端口可能已被其他程序占用。")
    status, body = _http_get(f"http://127.0.0.1:{port}/api/health")
    running_panel = False
    if status == 200:
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = None
        running_panel = isinstance(parsed, dict) and parsed.get("app") == "chaoxing-panel"
    if running_panel:
        print(f"   这个端口上已经有一个本面板在运行了（http://127.0.0.1:{port}）。")
        print("   直接打开它即可；如果想同时开第二个，请用 --port 换一个端口。")
        return 1
    status, body = _http_get(f"http://127.0.0.1:{port}/")
    if "Whitelabel Error Page" in body or "no explicit mapping for /error" in body:
        print("   该端口上跑着一个 Spring Boot（Java）应用 ——")
        print("   浏览器里看到的 \"Whitelabel Error Page\" 是它返回的，不是本面板。")
    elif status:
        print(f"   该端口上已有其他服务在响应（HTTP {status}），不是本面板。")
    print()
    print(f"   解决办法一：换个端口   python panel.py --port {port + 1}")
    print("   解决办法二：停掉占用程序，然后重新启动面板")
    print(f"      Windows : netstat -ano | findstr :{port}  (再用 taskkill /PID <pid> /F)")
    print(f"      macOS   : lsof -i :{port}")
    print(f"      Linux   : ss -lptn 'sport = :{port}'")
    print("   更多自检：python panel.py --doctor")
    return 1


def run_doctor(app: Flask, port: int = PANEL_PORT) -> int:
    """`--doctor`：自检并判断"我访问的到底是不是本面板"."""
    ok_all = True
    print("=" * 56)
    print("  超星控制面板 · 自检")
    print("=" * 56)

    def line(status: str, text: str) -> None:
        print(f"{status} {text}")

    # 1. Python / 依赖
    version = ".".join(str(v) for v in sys.version_info[:3])
    if sys.version_info >= (3, 10):
        line("✅", f"Python {version}")
    else:
        ok_all = False
        line("❌", f"Python {version} 版本过低，需要 3.10 及以上")

    missing = []
    for module, package in (("flask", "flask"), ("cryptography", "cryptography")):
        try:
            __import__(module)
        except Exception:  # noqa: BLE001
            missing.append(package)
    if missing:
        ok_all = False
        line("❌", f"缺少依赖: {', '.join(missing)} → pip install -r requirements-panel.txt")
    else:
        line("✅", "核心依赖完整（flask / cryptography）")
    for module in ("ddddocr", "fontTools", "httpx", "tenacity"):
        try:
            __import__(module)
        except Exception:  # noqa: BLE001
            line("⚠️ ", f"可选依赖缺失: {module}（验证码/字体解密会受影响）")
            break
    else:
        line("✅", "可选依赖完整（ddddocr / fontTools / httpx / tenacity）")

    # 2. 数据目录
    info = app.extensions["chaoxing_panel"]
    data_dir = Path(info["data_dir"])
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".doctor-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        line("✅", f"数据目录可写: {data_dir}")
    except OSError as exc:
        ok_all = False
        line("❌", f"数据目录不可写: {data_dir} ({exc})")

    key_file = Path(info["secret_box"].key_path)
    if key_file.exists():
        mode = oct(key_file.stat().st_mode & 0o777)
        note = "" if mode in ("0o600", "0o400") else "（建议 chmod 600，当前权限过宽）"
        line("✅" if not note else "⚠️ ", f"加密密钥: {key_file} 权限 {mode}{note}")
    elif os.environ.get("PANEL_SECRET_KEY"):
        line("✅", "加密密钥来自环境变量 PANEL_SECRET_KEY")
    else:
        line("⚠️ ", f"加密密钥尚未生成（首次保存账号时会自动创建于 {key_file}）")

    auth = info["auth"]
    if auth.configured:
        line("✅", f"管理员已初始化: {auth.username}")
    else:
        ok_all = False
        line("❌", "管理员尚未初始化：启动面板后打开网页初始化，或执行 python panel.py --init-admin")

    try:
        count = len(info["accounts"].list_public())
        line("✅" if count else "⚠️ ", f"已保存账号数: {count}")
    except Exception as exc:  # noqa: BLE001
        line("⚠️ ", f"读取账号失败: {exc}")

    # 3. 端口 / 对面到底是什么服务（重点）
    print("-" * 56)
    if _port_free("127.0.0.1", port):
        line("✅", f"端口 {port} 空闲：可以直接启动面板")
    else:
        status, body = _http_get(f"http://127.0.0.1:{port}/api/health")
        parsed = None
        if status == 200:
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                parsed = None
        if isinstance(parsed, dict) and parsed.get("app") == "chaoxing-panel":
            line("✅", f"端口 {port} 上运行的是本面板 v{parsed.get('version', '?')}（已在运行）")
        elif isinstance(parsed, dict) and parsed.get("ok"):
            line("⚠️ ", f"端口 {port} 上是一个返回 JSON 的服务，但不像本面板")
            ok_all = False
        else:
            ok_all = False
            page_status, page_body = _http_get(f"http://127.0.0.1:{port}/")
            if "Whitelabel Error Page" in page_body or "no explicit mapping for /error" in page_body:
                line("❌", f"端口 {port} 被一个 Spring Boot（Java）应用占用！")
                print()
                print("   你截图里的 \"Whitelabel Error Page\" 就是这个应用返回的，不是本面板。")
                print("   解决办法（任选其一）：")
                print(f"     1) 换一个端口启动面板：python panel.py --port {port + 1}")
                print("     2) 停掉占用程序后重启面板；Windows 可用:  netstat -ano | findstr :%d" % port)
                print("        Linux/macOS 可用:  lsof -i :%d" % port)
            else:
                line("❌", f"端口 {port} 被其他程序占用（返回 HTTP {page_status}），不是本面板")
                print()
                print("   浏览器访问该端口会显示对方的页面/错误页。请换端口启动：")
                print(f"     python panel.py --port {port + 1}")
                print("   或先停掉占用程序（Windows: netstat -ano | findstr :%d）" % port)

    print("-" * 56)
    print("结论：" + ("一切正常，可以启动 `python panel.py`" if ok_all else "有问题需要处理，见上面的 ❌ 项"))
    return 0 if ok_all else 1


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="超星多账号控制面板")
    parser.add_argument("--host", default=PANEL_HOST, help="监听地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=PANEL_PORT, help="监听端口（默认 8080）")
    parser.add_argument("--init-admin", action="store_true", help="交互式初始化管理员账号")
    parser.add_argument("--reset-admin", action="store_true",
                        help="重置管理员密码（忘记密码时用；可用 PANEL_ADMIN_USER/PANEL_ADMIN_PASSWORD 免交互）")
    parser.add_argument("--debug", action="store_true", help="Flask debug 模式")
    parser.add_argument("--open", action="store_true", help="启动后自动用浏览器打开面板")
    parser.add_argument("--doctor", action="store_true",
                        help="自检：依赖、数据目录、端口占用（看不清访问的是不是本面板时用）")
    args = parser.parse_args()

    if args.doctor:
        return run_doctor(app, port=args.port)

    if args.init_admin:
        import getpass

        auth = app.extensions["chaoxing_panel"]["auth"]
        if auth.configured:
            print("管理员已初始化。忘记密码请执行：python panel.py --reset-admin")
            return 1
        username = input("管理员用户名: ").strip()
        password = getpass.getpass("管理员密码（至少 10 位）: ")
        confirm = getpass.getpass("确认密码: ")
        if password != confirm:
            print("两次密码不一致")
            return 1
        try:
            auth.set_admin(username, password)
        except ValueError as e:
            print(f"初始化失败: {e}")
            return 1
        print("管理员初始化完成")
        return 0

    if args.reset_admin:
        return reset_admin_command(app, port=args.port)

    if not API_KEY:
        logger.warning("未设置 PANEL_API_KEY：远程 API 调用只能使用登录会话（浏览器）")
    if not COOKIE_SECURE and args.host not in ("127.0.0.1", "localhost"):
        logger.warning("面板监听在非本地地址但未开启 PANEL_COOKIE_SECURE，建议通过 HTTPS 反向代理访问")
    if os.environ.get("PANEL_AUTOSTART", "1") == "1":
        app.extensions["chaoxing_panel"]["manager"].start_supervisor()
    logger.info(f"面板启动: http://{args.host}:{args.port}")
    if args.open:
        import threading
        import webbrowser

        host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
        threading.Timer(1.5, lambda: webbrowser.open(f"http://{host}:{args.port}")).start()

    debug = args.debug or PANEL_DEBUG
    serve = None
    if not debug:
        try:
            from waitress import serve as _waitress_serve  # 跨平台（含 Windows）的 WSGI 服务器

            serve = _waitress_serve
        except ImportError:
            serve = None

    try:
        if serve is not None:
            serve(app, host=args.host, port=args.port, threads=8, ident="chaoxing-panel")
        else:
            app.run(host=args.host, port=args.port, debug=debug,
                    threaded=True, use_reloader=False)
    except OSError as exc:
        logger.error(f"启动失败: {exc}")
        return _explain_bind_failure(args.port, args.host)
    except SystemExit as exc:              # app.run 退出
        return int(exc.code or 0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
