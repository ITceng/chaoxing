# -*- coding: utf-8 -*-
"""账号 / 设置 / 审计日志的持久化.

- 账号密码、Cookie 使用 Fernet 加密后落盘；
- 所有文件原子写入，权限 0600；
- 账号之间互相隔离：每个账号有独立的 cookie 文件、日志文件与运行实例。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from panel_core.security import SecretBox, SecurityError

DATA_DIR = Path(os.environ.get("PANEL_DATA_DIR", "data")).expanduser()
ACCOUNTS_DIR = DATA_DIR / "accounts"
ACCOUNTS_FILE = DATA_DIR / "accounts.json"
SETTINGS_FILE = DATA_DIR / "settings.enc"
AUDIT_FILE = DATA_DIR / "audit.log"

_SECRET_SETTING_PATHS = {
    ("tiku", "tokens"),
    ("tiku", "key"),
    ("tiku", "siliconflow_key"),
    ("tiku", "go_authorization"),
    ("notification", "url"),
}

DEFAULT_SETTINGS: dict[str, Any] = {
    "max_concurrent_accounts": 2,
    "tiku": {
        "provider": "",
        "submit": "false",
        "cover_rate": 0.9,
        "delay": 1.0,
        "tokens": "",
        "url": "",
        "endpoint": "",
        "key": "",
        "model": "",
        "min_interval_seconds": 3,
        "http_proxy": "",
        "check_llm_connection": "true",
        "siliconflow_key": "",
        "siliconflow_model": "deepseek-ai/DeepSeek-V3",
        "siliconflow_endpoint": "https://api.siliconflow.cn/v1/chat/completions",
        "go_authorization": "",
        "go_min_interval": 1.0,
        "go_retry_times": 3,
        "go_retry_backoff": 1.2,
        "true_list": "正确,对,√,是",
        "false_list": "错误,错,×,否,不对,不正确",
        "manual_mode_default": "batch",
        "manual_mode_separator": ";",
        "likeapi_search": "false",
        "likeapi_vision": "true",
        "likeapi_model": "glm-4.5-air",
        "likeapi_retry": "true",
        "likeapi_retry_times": 3,
    },
    "notification": {
        "provider": "",
        "url": "",
        "tg_chat_id": "",
    },
}

DEFAULT_ACCOUNT: dict[str, Any] = {
    "name": "",
    "username_enc": "",
    "username_hint": "",
    "password_enc": "",
    "cookies_enc": "",
    "use_cookies": False,
    "course_list": "",
    "speed": 1.0,
    "jobs": 2,
    "notopen_action": "retry",
    "course_serial": True,
    "work_redo_enabled": False,
    "work_max_retries": 3,
    "add_learning_count": False,
    "target_count": 100,
    "provider": "",
    "notes": "",
}


def _atomic_write(path: Path, data: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}.{threading.get_ident()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            fp.write(data)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tmp, path)
        try:
            os.chmod(path, mode)
        except OSError:
            pass
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _read_json(path: Path, default):
    try:
        with path.open("r", encoding="utf-8") as fp:
            return json.load(fp)
    except (OSError, json.JSONDecodeError):
        return default


class AccountStore:
    def __init__(self, secret_box: SecretBox, path: Path = ACCOUNTS_FILE):
        self.secret_box = secret_box
        self.path = Path(path)
        self.accounts_dir = self.path.parent / "accounts"
        self._lock = threading.RLock()
        self._ensure()

    def _ensure(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(self.path.parent, 0o700)
            except OSError:
                pass
            if not self.path.exists():
                _atomic_write(self.path, json.dumps({"accounts": []}, ensure_ascii=False, indent=2))

    def _load(self) -> list[dict[str, Any]]:
        data = _read_json(self.path, {"accounts": []})
        accounts = data.get("accounts") if isinstance(data, dict) else []
        return accounts if isinstance(accounts, list) else []

    def _save(self, accounts: list[dict[str, Any]]) -> None:
        _atomic_write(self.path, json.dumps({"accounts": accounts}, ensure_ascii=False, indent=2))

    # ------------------------------------------------------------------ public
    def list_public(self) -> list[dict[str, Any]]:
        with self._lock:
            return [public_account(a, self.accounts_dir) for a in self._load()]

    def get_public(self, account_id: str) -> Optional[dict[str, Any]]:
        raw = self.get_raw(account_id)
        return public_account(raw, self.accounts_dir) if raw else None

    def get_raw(self, account_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            for account in self._load():
                if account.get("id") == account_id:
                    return dict(account)
        return None

    def get_for_run(self, account_id: str) -> dict[str, Any]:
        raw = self.get_raw(account_id)
        if not raw:
            raise KeyError("账号不存在")
        return {
            **raw,
            "username": self.secret_box.decrypt(raw.get("username_enc", "")),
            "password": self.secret_box.decrypt(raw.get("password_enc", "")),
            "cookies_text": self.secret_box.decrypt(raw.get("cookies_enc", "")),
        }

    # ------------------------------------------------------------------- write
    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            accounts = self._load()
            account_id = uuid.uuid4().hex
            record = dict(DEFAULT_ACCOUNT)
            record.update({
                "id": account_id,
                "created_at": int(time.time()),
                "updated_at": int(time.time()),
            })
            self._apply_payload(record, payload, creating=True)
            accounts.append(record)
            self._save(accounts)
            return public_account(record, self.accounts_dir)

    def update(self, account_id: str, payload: dict[str, Any]) -> Optional[dict[str, Any]]:
        with self._lock:
            accounts = self._load()
            for account in accounts:
                if account.get("id") == account_id:
                    self._apply_payload(account, payload, creating=False)
                    account["updated_at"] = int(time.time())
                    self._save(accounts)
                    return public_account(account, self.accounts_dir)
        return None

    def delete(self, account_id: str) -> bool:
        with self._lock:
            accounts = self._load()
            remaining = [a for a in accounts if a.get("id") != account_id]
            if len(remaining) == len(accounts):
                return False
            self._save(remaining)
            return True

    def _apply_payload(self, record: dict[str, Any], payload: dict[str, Any], creating: bool) -> None:
        # 非敏感字段白名单
        for key in [
            "name", "use_cookies", "course_list", "speed", "jobs", "notopen_action",
            "course_serial", "work_redo_enabled", "work_max_retries",
            "add_learning_count", "target_count", "provider", "notes",
        ]:
            if key in payload and payload[key] is not None:
                record[key] = payload[key]

        username = payload.get("username")
        if username:
            username = str(username).strip()
            record["username_enc"] = self.secret_box.encrypt(username)
            record["username_hint"] = _mask_username(username)

        password = payload.get("password")
        if password:
            record["password_enc"] = self.secret_box.encrypt(str(password))

        cookies_text = payload.get("cookies_text")
        if cookies_text is not None and str(cookies_text).strip():
            record["cookies_enc"] = self.secret_box.encrypt(str(cookies_text).strip())

        if payload.get("clear_cookies"):
            record["cookies_enc"] = ""

        if record.get("use_cookies") and not record.get("cookies_enc") and not creating:
            # 允许使用已存在的 cookie 文件
            pass

    # ------------------------------------------------------------------ paths
    def account_dir(self, account_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", str(account_id))
        return self.accounts_dir / safe

    def cookies_path(self, account_id: str) -> Path:
        return self.account_dir(account_id) / "cookies.txt"

    def write_cookies(self, account_id: str, cookies_text: str) -> Path:
        path = self.cookies_path(account_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
        _atomic_write(path, cookies_text, mode=0o600)
        return path


def _mask_username(username: str) -> str:
    text = str(username or "")
    if len(text) <= 4:
        return "*" * len(text)
    return text[:3] + "*" * max(2, len(text) - 7) + text[-4:]


def public_account(record: dict[str, Any], accounts_dir: Path = ACCOUNTS_DIR) -> dict[str, Any]:
    return {
        "id": record.get("id"),
        "name": record.get("name") or record.get("username_hint") or "未命名账号",
        "username_hint": record.get("username_hint", ""),
        "has_password": bool(record.get("password_enc")),
        "has_cookies": bool(record.get("cookies_enc")) or _cookie_file_exists(record.get("id", ""), accounts_dir),
        "use_cookies": bool(record.get("use_cookies")),
        "course_list": record.get("course_list", ""),
        "speed": record.get("speed", 1.0),
        "jobs": record.get("jobs", 2),
        "notopen_action": record.get("notopen_action", "retry"),
        "course_serial": bool(record.get("course_serial", True)),
        "work_redo_enabled": bool(record.get("work_redo_enabled", False)),
        "work_max_retries": record.get("work_max_retries", 3),
        "add_learning_count": bool(record.get("add_learning_count", False)),
        "target_count": record.get("target_count", 100),
        "provider": record.get("provider", ""),
        "notes": record.get("notes", ""),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
    }


def _cookie_file_exists(account_id: str, accounts_dir: Path = ACCOUNTS_DIR) -> bool:
    if not account_id:
        return False
    try:
        return (accounts_dir / re.sub(r"[^A-Za-z0-9_-]", "_", str(account_id)) / "cookies.txt").exists()
    except OSError:
        return False


class SettingsStore:
    def __init__(self, secret_box: SecretBox, path: Path = SETTINGS_FILE):
        self.secret_box = secret_box
        self.path = Path(path)
        self._lock = threading.RLock()
        self._settings: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return json.loads(json.dumps(DEFAULT_SETTINGS))
        try:
            raw = self.secret_box.decrypt(self.path.read_text(encoding="utf-8"))
            data = json.loads(raw)
        except (OSError, SecurityError, json.JSONDecodeError):
            data = {}
        merged = json.loads(json.dumps(DEFAULT_SETTINGS))
        _deep_merge(merged, data if isinstance(data, dict) else {})
        return merged

    def _save(self) -> None:
        _atomic_write(self.path, self.secret_box.encrypt_json(self._settings))

    def get(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._settings))

    def get_masked(self) -> dict[str, Any]:
        data = self.get()
        for path in _SECRET_SETTING_PATHS:
            node = data
            for key in path[:-1]:
                node = node.setdefault(key, {})
            if node.get(path[-1]):
                node[path[-1]] = "***"
        data["secret_fields"] = [".".join(p) for p in sorted(_SECRET_SETTING_PATHS)]
        return data

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            current = json.loads(json.dumps(self._settings))
            _deep_merge(current, _drop_masked(patch or {}))
            self._settings = current
            self._save()
            return self.get()

    def set_max_concurrent(self, value: int) -> None:
        self.update({"max_concurrent_accounts": max(1, int(value))})


def _deep_merge(base: dict, patch: dict) -> None:
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def _drop_masked(patch: Any) -> Any:
    """移除前端回传的 *** 占位符，避免覆盖真实密钥."""
    if isinstance(patch, dict):
        return {k: _drop_masked(v) for k, v in patch.items() if v != "***"}
    if isinstance(patch, list):
        return [_drop_masked(v) for v in patch]
    return patch


class AuditLog:
    def __init__(self, path: Path = AUDIT_FILE):
        self.path = Path(path)
        self._lock = threading.Lock()

    def write(self, action: str, actor: str, ip: str, target: str = "", result: str = "ok",
              detail: dict | None = None) -> None:
        record = {
            "ts": int(time.time()),
            "action": action,
            "actor": actor,
            "ip": ip,
            "target": target,
            "result": result,
            "detail": detail or {},
        }
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self.path.exists() and self.path.stat().st_size > 5 * 1024 * 1024:
                    self.path.replace(self.path.with_suffix(".log.1"))
                is_new = not self.path.exists()
                with self.path.open("a", encoding="utf-8") as fp:
                    fp.write(line + "\n")
                if is_new:
                    os.chmod(self.path, 0o600)
            except OSError:
                pass

    def tail(self, limit: int = 100) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
            return [json.loads(line) for line in lines if line.strip()]
        except (OSError, json.JSONDecodeError):
            return []
