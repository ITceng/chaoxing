from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone
from typing import Any

from flask import g, has_request_context, request


_SECRET_HEADER_PATTERN = re.compile(r"(?i)\b(authorization|cookie)\b\s*[:=]\s*[^\r\n]*")
_SECRET_PATTERN = re.compile(
    r"(?i)\b(password|passwd|api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|session[_-]?id)"
    r"(\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)


def _redact_message(value: str) -> str:
    value = _SECRET_HEADER_PATTERN.sub(r"\1=[REDACTED]", value)
    return _SECRET_PATTERN.sub(r"\1\2[REDACTED]", value)[:2048]


class JsonLogFormatter(logging.Formatter):
    """Emit bounded JSON records; intentionally exclude traceback, query and headers."""

    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "service": getattr(record, "service_name", self.service),
            "logger": record.name[:96],
            "event_type": getattr(record, "event_type", "application.log"),
            "message": _redact_message(record.getMessage()),
        }
        for field in (
            "request_id", "task_id", "worker_id", "platform_key", "user_id", "error_type",
            "endpoint", "method", "status", "duration_ms", "permission", "result",
            "alert_code", "severity", "fingerprint",
        ):
            value = getattr(record, field, None)
            if value is not None:
                if isinstance(value, (int, float, bool)):
                    payload[field] = value
                elif isinstance(value, str):
                    payload[field] = _redact_message(value)[:256]
        if has_request_context():
            payload.setdefault("request_id", getattr(g, "request_id", ""))
            user = getattr(g, "current_user", None)
            if user is not None:
                payload.setdefault("user_id", str(getattr(user, "id", ""))[:64])
            if not payload.get("endpoint") and request.endpoint:
                payload["endpoint"] = request.endpoint[:128]
            if not payload.get("method"):
                payload["method"] = request.method[:16]
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_json_logging(logger: logging.Logger, service: str, level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter(service))
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
