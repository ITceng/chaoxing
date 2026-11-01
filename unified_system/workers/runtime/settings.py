from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit


_SUPPORTED_PLATFORMS = {"chaoxing", "zhihuishu", "yinghua"}


@dataclass(frozen=True, repr=False)
class WorkerSettings:
    backend_url: str
    platform_key: str
    worker_id: str
    token: str = field(repr=False)
    version: str = "0.1.0"
    poll_interval_seconds: float = 2.0
    heartbeat_interval_seconds: float = 15.0
    request_timeout_seconds: float = 10.0
    cancellation_grace_seconds: float = 5.0

    def __repr__(self) -> str:
        return (
            "WorkerSettings(backend_url={!r}, platform_key={!r}, worker_id={!r}, token=<redacted>, "
            "version={!r})"
        ).format(self.backend_url, self.platform_key, self.worker_id, self.version)

    @classmethod
    def from_env(cls, environ=None) -> "WorkerSettings":
        env = os.environ if environ is None else environ
        platform = str(env.get("WORKER_PLATFORM", "")).strip().lower()
        if platform not in _SUPPORTED_PLATFORMS:
            raise ValueError("WORKER_PLATFORM must be one of chaoxing, zhihuishu, yinghua")
        worker_id = str(env.get("WORKER_ID", "")).strip()
        if not worker_id or len(worker_id) > 120:
            raise ValueError("WORKER_ID must be set and no longer than 120 characters")
        token_name = f"WORKER_TOKEN_{platform.upper()}"
        token = str(env.get(token_name, ""))
        if len(token) < 32:
            raise ValueError(f"{token_name} must contain at least 32 characters")
        base = str(env.get("BACKEND_INTERNAL_URL", "http://backend:8088")).rstrip("/")
        parsed = urlsplit(base)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("BACKEND_INTERNAL_URL must be an HTTP(S) origin/base path without embedded credentials")
        if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1", "backend"}:
            raise ValueError("non-TLS Worker API URLs are allowed only for local/dev or the private Compose service name")
        poll = _positive_float(env.get("WORKER_POLL_INTERVAL_SECONDS", "2"), "WORKER_POLL_INTERVAL_SECONDS", 0.1, 60)
        heartbeat = _positive_float(env.get("WORKER_HEARTBEAT_INTERVAL_SECONDS", "15"), "WORKER_HEARTBEAT_INTERVAL_SECONDS", 1, 60)
        timeout = _positive_float(env.get("WORKER_REQUEST_TIMEOUT_SECONDS", "10"), "WORKER_REQUEST_TIMEOUT_SECONDS", 1, 60)
        grace = _positive_float(env.get("WORKER_CANCEL_GRACE_SECONDS", "5"), "WORKER_CANCEL_GRACE_SECONDS", 0, 60)
        return cls(base, platform, worker_id, token, str(env.get("WORKER_VERSION", "0.1.0"))[:80], poll, heartbeat, timeout, grace)


def _positive_float(value, name: str, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return parsed
