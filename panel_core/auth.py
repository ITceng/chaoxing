# -*- coding: utf-8 -*-
"""面板管理员账号：密码哈希、会话版本、首次初始化."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from panel_core.security import hash_password, verify_password
from panel_core.store import DATA_DIR, _atomic_write, _read_json

AUTH_FILE = DATA_DIR / "admin.json"


class PanelAuth:
    def __init__(self, path: Path = AUTH_FILE):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._data: dict[str, Any] = _read_json(self.path, {})
        if not isinstance(self._data, dict):
            self._data = {}
        self._stamp = self._file_stamp()

    def _file_stamp(self) -> tuple[int, int]:
        try:
            stat = self.path.stat()
        except OSError:
            return (0, 0)
        return (stat.st_mtime_ns, stat.st_size)

    def _reload_if_changed(self) -> None:
        """admin.json 被外部修改（例如命令行 --reset-admin）后自动生效，无需重启面板."""
        stamp = self._file_stamp()
        if stamp == self._stamp:
            return
        with self._lock:
            stamp = self._file_stamp()
            if stamp == self._stamp:
                return
            data = _read_json(self.path, {})
            if isinstance(data, dict):
                self._data = data
            self._stamp = stamp

    @property
    def configured(self) -> bool:
        self._reload_if_changed()
        with self._lock:
            return bool(self._data.get("password_hash"))

    @property
    def username(self) -> str:
        self._reload_if_changed()
        with self._lock:
            return str(self._data.get("username") or "admin")

    @property
    def session_version(self) -> int:
        self._reload_if_changed()
        with self._lock:
            return int(self._data.get("session_version") or 1)

    def bootstrap_from_env(self) -> None:
        """容器首次部署时通过环境变量初始化管理员（已配置则忽略）."""
        if self.configured:
            return
        username = os.environ.get("PANEL_ADMIN_USER", "").strip()
        password = os.environ.get("PANEL_ADMIN_PASSWORD", "")
        if username and password:
            self.set_admin(username, password)

    def set_admin(self, username: str, password: str) -> None:
        username = (username or "").strip()
        if not (3 <= len(username) <= 64):
            raise ValueError("用户名长度需为 3-64 个字符")
        if not (10 <= len(password) <= 256):
            raise ValueError("密码长度需为 10-256 个字符")
        with self._lock:
            self._data = {
                "username": username,
                "password_hash": hash_password(password),
                "session_version": int(self._data.get("session_version") or 1) + 1,
                "created_at": self._data.get("created_at") or int(time.time()),
                "updated_at": int(time.time()),
            }
            self._save()

    def verify(self, username: str, password: str) -> bool:
        self._reload_if_changed()
        with self._lock:
            if not self.configured:
                return False
            if str(username or "").strip() != self.username:
                # 仍执行一次哈希，降低用户名枚举的时序差异
                verify_password(password, self._data.get("password_hash", ""))
                return False
            return verify_password(password, self._data.get("password_hash", ""))

    def change_password(self, old_password: str, new_password: str) -> None:
        if not self.verify(self.username, old_password):
            raise ValueError("原密码不正确")
        self.set_admin(self.username, new_password)

    def bump_session_version(self) -> int:
        with self._lock:
            self._data["session_version"] = int(self._data.get("session_version") or 1) + 1
            self._data["updated_at"] = int(time.time())
            self._save()
            return self._data["session_version"]

    def _save(self) -> None:
        _atomic_write(self.path, json.dumps(self._data, ensure_ascii=False, indent=2))
        self._stamp = self._file_stamp()
