# -*- coding: utf-8 -*-
"""面板安全基础设施：密码哈希、敏感字段加密、限流、CSRF、客户端识别."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Optional


class SecurityError(RuntimeError):
    pass


# --------------------------------------------------------------------- password
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1


def hash_password(password: str) -> str:
    """使用 scrypt 哈希密码，格式: scrypt$n$r$p$salt_b64$hash_b64."""
    if not isinstance(password, str) or not password:
        raise ValueError("password must be a non-empty string")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt,
        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32,
    )
    return "$".join([
        "scrypt", str(_SCRYPT_N), str(_SCRYPT_R), str(_SCRYPT_P),
        base64.urlsafe_b64encode(salt).decode(),
        base64.urlsafe_b64encode(digest).decode(),
    ])


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, hash_b64 = str(stored).split("$", 5)
        if scheme != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_b64.encode())
        expected = base64.urlsafe_b64decode(hash_b64.encode())
        actual = hashlib.scrypt(
            str(password).encode("utf-8"), salt=salt,
            n=int(n), r=int(r), p=int(p), dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def generate_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def constant_time_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(str(a).encode("utf-8"), str(b).encode("utf-8"))


# ------------------------------------------------------------------ encryption
class SecretBox:
    """使用 Fernet 加密落盘的敏感字段（账号密码、Cookie、题库 token）.

    密钥保存在 ``data/secret.key``（权限 0600），也可通过环境变量
    ``PANEL_SECRET_KEY`` 以 urlsafe-base64 形式注入（适合容器/KMS 挂载）。
    """

    def __init__(self, key_path: str | Path | None = None):
        try:
            from cryptography.fernet import Fernet
        except ImportError as exc:  # pragma: no cover - 依赖缺失时给出可读错误
            raise SecurityError(
                "缺少 cryptography 依赖，请执行 pip install cryptography"
            ) from exc
        self._fernet_cls = Fernet
        self.key_path = Path(key_path or os.environ.get("PANEL_SECRET_KEY_FILE", "data/secret.key"))
        self._lock = threading.Lock()
        self._fernet = Fernet(self._load_or_create_key())

    def _load_or_create_key(self) -> bytes:
        env_key = os.environ.get("PANEL_SECRET_KEY", "").strip()
        if env_key:
            key = env_key.encode()
            try:
                self._fernet_cls(key)
            except Exception as exc:
                raise SecurityError("环境变量 PANEL_SECRET_KEY 不是合法的 Fernet key") from exc
            return key

        with self._lock:
            try:
                if self.key_path.exists():
                    key = self.key_path.read_bytes().strip()
                    self._fernet_cls(key)  # 校验
                    return key
            except (OSError, ValueError) as exc:
                raise SecurityError(f"读取加密密钥失败: {exc}") from exc

            self.key_path.parent.mkdir(parents=True, exist_ok=True)
            key = self._fernet_cls.generate_key()
            tmp = self.key_path.with_suffix(self.key_path.suffix + ".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, "wb") as fp:
                    fp.write(key)
                    fp.flush()
                    os.fsync(fp.fileno())
                os.replace(tmp, self.key_path)
            finally:
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
            try:
                os.chmod(self.key_path, 0o600)
            except OSError:
                pass
            return key

    def encrypt(self, plaintext: str) -> str:
        if plaintext is None:
            return ""
        return self._fernet.encrypt(str(plaintext).encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        if not token:
            return ""
        try:
            return self._fernet.decrypt(str(token).encode("ascii")).decode("utf-8")
        except Exception as exc:
            raise SecurityError("敏感字段解密失败（密钥可能已更换）") from exc

    def encrypt_json(self, data: Any) -> str:
        return self.encrypt(json.dumps(data, ensure_ascii=False))

    def decrypt_json(self, token: str) -> Any:
        raw = self.decrypt(token)
        return json.loads(raw) if raw else {}


# --------------------------------------------------------------------- rate limit
class SlidingWindowLimiter:
    """进程内滑动窗口限流（单实例部署足够；多实例请换 Redis）.

    注意：调用方可能传入攻击者可控的 key（IP、用户名），因此这里会
    定期清理过期桶并限制 key 总数，避免内存被无限撑大。
    """

    def __init__(self, max_keys: int = 10000):
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self.max_keys = max(100, int(max_keys))
        self._last_sweep = 0.0

    def _sweep(self, now: float, window: float) -> None:
        """清理已过期的空桶；仍超限时丢弃最旧的一批（宁可放宽限流也不能 OOM）."""
        if now - self._last_sweep < max(1.0, window) and len(self._events) <= self.max_keys:
            return
        self._last_sweep = now
        stale = [k for k, v in self._events.items() if not v or v[-1] <= now - window]
        for key in stale:
            self._events.pop(key, None)
        if len(self._events) > self.max_keys:
            overflow = len(self._events) - self.max_keys
            for key in sorted(self._events, key=lambda k: self._events[k][-1] if self._events[k] else 0)[:overflow]:
                self._events.pop(key, None)

    def allow(self, key: str, limit: int, window: float) -> tuple[bool, float]:
        now = time.monotonic()
        with self._lock:
            self._sweep(now, window)
            bucket = self._events.setdefault(key, deque())
            while bucket and bucket[0] <= now - window:
                bucket.popleft()
            if len(bucket) >= limit:
                retry_after = max(0.0, window - (now - bucket[0]))
                return False, retry_after
            bucket.append(now)
            return True, 0.0

    def reset(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


class LoginThrottle:
    """登录失败锁定：同一 IP 或同一用户名连续失败后临时锁定."""

    def __init__(self, max_failures: int = 5, lock_seconds: int = 900, max_keys: int = 5000):
        self.max_failures = max_failures
        self.lock_seconds = lock_seconds
        self.max_keys = max(100, int(max_keys))
        self._failures: dict[str, deque[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        """清理过期记录；攻击者用随机用户名刷登录时不能把内存刷爆."""
        expired = [k for k, until in self._locked_until.items() if until <= now]
        for key in expired:
            self._locked_until.pop(key, None)
        stale = [k for k, v in self._failures.items()
                 if not v or v[-1] <= now - self.lock_seconds]
        for key in stale:
            self._failures.pop(key, None)
        if len(self._failures) > self.max_keys:
            overflow = len(self._failures) - self.max_keys
            for key in sorted(self._failures, key=lambda k: self._failures[k][-1])[:overflow]:
                self._failures.pop(key, None)

    def is_locked(self, key: str) -> tuple[bool, float]:
        now = time.monotonic()
        with self._lock:
            until = self._locked_until.get(key, 0)
            if until > now:
                return True, until - now
            return False, 0.0

    def record_failure(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            if len(self._failures) > self.max_keys or len(self._locked_until) > self.max_keys:
                self._prune(now)
            bucket = self._failures.setdefault(key, deque())
            bucket.append(now)
            while bucket and bucket[0] <= now - self.lock_seconds:
                bucket.popleft()
            if len(bucket) >= self.max_failures:
                self._locked_until[key] = now + self.lock_seconds

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
            self._locked_until.pop(key, None)

    def clear(self) -> None:
        """清空所有失败记录（管理员密码被重置后调用）."""
        with self._lock:
            self._failures.clear()
            self._locked_until.clear()


def get_client_ip(remote_addr: Optional[str], headers: Any, trust_proxy: bool = False) -> str:
    if trust_proxy and headers is not None:
        forwarded = headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return remote_addr or "unknown"


def origin_allowed(origin: Optional[str], host: Optional[str], extra: Optional[list[str]] = None) -> bool:
    """校验浏览器 Origin，防止跨站请求携带 Cookie 调用管理接口."""
    if not origin:
        # 非浏览器客户端（curl / API key）通常不带 Origin，交给 CSRF/API key 校验
        return True
    from urllib.parse import urlparse

    parsed = urlparse(origin)
    origin_host = parsed.netloc
    if not origin_host:
        return False
    allowed = {host} if host else set()
    for item in (extra or []):
        allowed.add(urlparse(item).netloc or item)
    return origin_host in allowed
