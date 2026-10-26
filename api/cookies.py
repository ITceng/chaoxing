# -*- coding: utf-8 -*-
"""Cookie 持久化.

使用 ``threading.local`` 按线程隔离 Cookie 路径：Web 面板中每个账号的任务
跑在独立线程里，只要在启动线程时调用 :func:`bind_cookies_path`，整个线程内
（包括第三方库调用链）的 ``save_cookies`` / ``use_cookies`` 都会落到该账号
自己的文件，避免多账号互相覆盖。

CLI 行为保持不变：未绑定时使用全局默认路径 ``cookies.txt``。
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Optional

import requests

from api.config import GlobalConst as gc

# 定义全局 Cookie 文件锁，保证读写绝对安全
cookie_lock = threading.RLock()
_local = threading.local()


def bind_cookies_path(path: Optional[str | Path]) -> None:
    """把当前线程的 Cookie 文件绑定到指定路径（传 None 恢复默认）."""
    _local.cookies_path = str(path) if path else None


def current_cookies_path() -> str:
    return getattr(_local, "cookies_path", None) or gc.COOKIES_PATH


def save_cookies(session: requests.Session, path: Optional[str | Path] = None) -> None:
    target = Path(path or current_cookies_path())
    buffer = ""
    for k, v in session.cookies.items():
        buffer += f"{k}={v};"
    buffer = buffer.removesuffix(";")
    with cookie_lock:
        if target.parent and str(target.parent) not in ("", "."):
            target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as f:
            f.write(buffer)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass


def use_cookies(path: Optional[str | Path] = None) -> dict:
    source = Path(path or current_cookies_path())
    with cookie_lock:
        if not source.exists():
            return {}

        cookies = {}
        try:
            with source.open("r", encoding="utf-8") as f:
                buffer = f.read().strip()
                if not buffer:
                    return {}
                for item in buffer.split(";"):
                    item = item.strip()
                    if not item:
                        continue
                    parts = item.split("=", 1)
                    if len(parts) == 2:
                        cookies[parts[0]] = parts[1]
        except (OSError, UnicodeDecodeError):
            return {}

        return cookies
