#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""超星多账号控制面板.

启动:
    PANEL_HOST=0.0.0.0 PANEL_PORT=8080 python panel.py

生产部署请放在 HTTPS 反向代理之后，并设置 PANEL_COOKIE_SECURE=1。
"""
from __future__ import annotations

import atexit
import os
import secrets
import shutil
import sys
import time
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Optional

from flask import Flask, jsonify, request, send_from_directory, session
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


def create_app(data_dir: Optional[Path] = None) -> Flask:
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

    app.extensions["chaoxing_panel"] = {
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

    @app.errorhandler(HTTPException)
    def handle_http_error(exc: HTTPException):
        if request.path.startswith("/api/"):
            return api_error(exc.description or exc.name, exc.code or 500)
        return exc

    @app.errorhandler(Exception)
    def handle_unexpected_error(exc: Exception):
        logger.error(f"面板未处理异常: {type(exc).__name__}: {exc}")
        if request.path.startswith("/api/"):
            return api_error("服务器内部错误", 500)
        return api_error("服务器内部错误", 500)

    # ------------------------------------------------------------------- static
    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html", max_age=0)

    @app.get("/favicon.ico")
    def favicon():
        return ("", 204)

    # ------------------------------------------------------------------- health
    @app.get("/api/health")
    def health():
        return jsonify({
            "ok": True,
            "version": APP_VERSION,
            "time": int(time.time()),
            "authenticated_required": True,
        })

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
        })

    # --------------------------------------------------------------------- auth
    @app.post("/api/auth/setup")
    def setup():
        if not check_origin():
            return api_error("Origin 不被允许", 403)
        if auth.configured:
            return api_error("面板已初始化", 409)
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

    def _account_payload() -> dict[str, Any]:
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return {}
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


app = create_app()


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="超星多账号控制面板")
    parser.add_argument("--host", default=PANEL_HOST, help="监听地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=PANEL_PORT, help="监听端口（默认 8080）")
    parser.add_argument("--init-admin", action="store_true", help="交互式初始化管理员账号")
    parser.add_argument("--debug", action="store_true", help="Flask debug 模式")
    args = parser.parse_args()

    if args.init_admin:
        import getpass

        auth = app.extensions["chaoxing_panel"]["auth"]
        if auth.configured:
            print("管理员已初始化，如需重置请删除 data/admin.json")
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

    if not API_KEY:
        logger.warning("未设置 PANEL_API_KEY：远程 API 调用只能使用登录会话（浏览器）")
    if not COOKIE_SECURE and args.host not in ("127.0.0.1", "localhost"):
        logger.warning("面板监听在非本地地址但未开启 PANEL_COOKIE_SECURE，建议通过 HTTPS 反向代理访问")
    logger.info(f"面板启动: http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug or PANEL_DEBUG,
            threaded=True, use_reloader=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
