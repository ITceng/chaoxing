from __future__ import annotations

import json
import logging

from unified_system.logging_utils import JsonLogFormatter


def test_request_id_is_validated_and_returned(client):
    response = client.get("/api/v1/health", headers={"X-Request-ID": "trace_12345678"})
    assert response.headers["X-Request-ID"] == "trace_12345678"

    invalid = client.get("/api/v1/health", headers={"X-Request-ID": "bad id?"})
    generated = invalid.headers["X-Request-ID"]
    assert len(generated) == 32
    assert generated.isalnum()


def test_json_log_formatter_redacts_secrets_and_omits_traceback():
    record = logging.LogRecord(
        name="unified_system.test", level=logging.WARNING, pathname=__file__, lineno=1,
        msg="request rejected password=%s token=%s Cookie: %s",
        args=("pass-value", "token-value", "session-cookie"), exc_info=None,
    )
    record.event_type = "security.test"
    record.request_id = "req_12345678"
    record.user_id = "user-7"
    record.permission = "ops.view"

    rendered = JsonLogFormatter("backend").format(record)
    payload = json.loads(rendered)

    assert payload["service"] == "backend"
    assert payload["level"] == "WARNING"
    assert payload["event_type"] == "security.test"
    assert payload["request_id"] == "req_12345678"
    assert payload["user_id"] == "user-7"
    assert payload["permission"] == "ops.view"
    assert "[REDACTED]" in payload["message"]
    for secret in ("pass-value", "token-value", "session-cookie"):
        assert secret not in rendered
    assert "exc_info" not in payload


def test_json_log_formatter_keeps_safe_operational_fields():
    record = logging.LogRecord(
        name="unified_system.worker", level=logging.INFO, pathname=__file__, lineno=1,
        msg="task completed", args=(), exc_info=None,
    )
    record.event_type = "worker.task.completed"
    record.task_id = "task-123"
    record.worker_id = "worker-1"
    record.platform_key = "chaoxing"
    record.result = "succeeded"

    payload = json.loads(JsonLogFormatter("worker").format(record))
    assert payload["service"] == "worker"
    assert payload["task_id"] == "task-123"
    assert payload["worker_id"] == "worker-1"
    assert payload["platform_key"] == "chaoxing"
    assert payload["result"] == "succeeded"
