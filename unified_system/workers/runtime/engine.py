from __future__ import annotations

import json
import logging
import queue
import threading
import time
from typing import Any

from unified_system.platforms.common import AdapterCancelled, AdapterError, AuthContext, CancellationToken, CourseSummary, HealthStatus, WorkerTask
from unified_system.workers.runtime.client import WorkerClient, WorkerProtocolError
from unified_system.workers.runtime.healthcheck import record_backend_heartbeat
from unified_system.logging_utils import configure_json_logging


logger = logging.getLogger("unified_system.worker")


class WorkerEngine:
    """Serial per-account task executor with background lease heartbeat and cooperative cancel."""

    def __init__(self, client: WorkerClient, adapter, *, stop_event: threading.Event | None = None):
        self.client = client
        self.adapter = adapter
        self.stop_event = stop_event or threading.Event()
        self.info = adapter.platform_info()
        if self.info.key != client.settings.platform_key:
            raise ValueError("worker platform does not match the adapter")
        self.capabilities = adapter.capabilities()
        self.task_kinds = frozenset(self.capabilities.supported_task_kinds)
        if not self.task_kinds or self.capabilities.verified_at is None:
            raise ValueError("refusing to start an online Worker without verified read-only task capabilities")

    def run_once(self) -> bool:
        health = self.adapter.health_check()
        if not isinstance(health, HealthStatus) or not health.healthy:
            raise AdapterError("adapter_unhealthy", "Platform adapter health check failed")
        self.client.heartbeat(sorted(self.task_kinds))
        record_backend_heartbeat()
        envelope = self.client.claim()
        if envelope is None:
            return False
        self._execute(envelope)
        return True

    def _execute(self, envelope: dict) -> None:
        try:
            self._execute_task(envelope)
        finally:
            credential = envelope.get("credential") if isinstance(envelope, dict) else None
            values = credential.get("values") if isinstance(credential, dict) else None
            if isinstance(values, dict):
                values.clear()
            task = envelope.get("task") if isinstance(envelope, dict) else None
            if isinstance(task, dict):
                task["lease_token"] = ""

    def _execute_task(self, envelope: dict) -> None:
        task = envelope["task"]
        credential = envelope["credential"]
        task_id = task["id"]
        lease_token = str(task.get("lease_token", ""))
        kind = str(task.get("kind", ""))
        if (task.get("platform_key") != self.info.key or task.get("platform_account_id") is None
                or not lease_token or credential.get("auth_type") is None):
            self.client.complete_task(task_id, lease_token, "failed", error_code="invalid_task_envelope")
            return
        if kind not in self.task_kinds:
            self.client.complete_task(task_id, lease_token, "failed", error_code="unsupported_task_kind")
            return
        if not self.client.start_task(task_id, lease_token):
            self.client.complete_task(task_id, lease_token, "cancelled", result_summary={})
            return
        logger.info("task started", extra={
            "event_type": "worker.task.started",
            "task_id": str(task_id),
            "worker_id": self.client.settings.worker_id,
            "platform_key": self.info.key,
        })

        auth = AuthContext(
            account_id=str(task["platform_account_id"]),
            auth_type=str(credential["auth_type"]),
            values=credential["values"],
        )
        worker_task = WorkerTask(
            task_id=task_id, platform_key=self.info.key, kind=kind,
            request_data=task["request_data"], account_id=auth.account_id,
            credential=auth, lease_token=lease_token,
            timeout_seconds=max(1, min(int(task.get("timeout_seconds", 900)), 86400)),
        )
        cancellation = CancellationToken()
        result_queue: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

        def invoke() -> None:
            try:
                value = self.adapter.execute_read_only(kind, worker_task.request_data, auth, cancellation)
                result_queue.put((True, value))
            except BaseException as exc:  # worker thread boundary; do not log exception text
                result_queue.put((False, exc))

        thread = threading.Thread(target=invoke, name=f"task-{task_id[:8]}", daemon=True)
        thread.start()
        started = time.monotonic()
        last_heartbeat = started
        cancel_at: float | None = None
        while thread.is_alive():
            now = time.monotonic()
            if self.stop_event.is_set() or now - started >= worker_task.timeout_seconds:
                cancellation.cancel()
                cancel_at = cancel_at or now
            if now - last_heartbeat >= self.client.settings.heartbeat_interval_seconds:
                try:
                    cancel_requested = self.client.task_heartbeat(task_id, lease_token)
                    record_backend_heartbeat()
                    if cancel_requested:
                        cancellation.cancel()
                        cancel_at = cancel_at or now
                except WorkerProtocolError:
                    cancellation.cancel()
                    self.stop_event.set()
                    logger.warning("task lease heartbeat failed; worker will stop to avoid overlapping execution")
                    return
                last_heartbeat = now
            thread.join(timeout=min(0.25, self.client.settings.heartbeat_interval_seconds))
            if cancel_at is not None and time.monotonic() - cancel_at >= self.client.settings.cancellation_grace_seconds:
                self.stop_event.set()
                logger.warning("adapter did not stop within cancellation grace; worker will exit and lease will expire")
                return

        ok, value = result_queue.get_nowait()
        if cancellation.cancelled:
            self.client.complete_task(task_id, lease_token, "cancelled", result_summary={})
            return
        if not ok:
            code = value.code if isinstance(value, AdapterError) else "adapter_error"
            if code in {"authorization_required", "session_expired"}:
                code = "authorization_expired"
            if isinstance(value, AdapterCancelled):
                code = "cancelled"
                self.client.complete_task(task_id, lease_token, "cancelled", error_code=code, result_summary={})
            else:
                self.client.complete_task(task_id, lease_token, "failed", error_code=code)
            logger.warning("task completed with adapter error", extra={
                "event_type": "worker.task.completed", "result": "failed", "error_type": code,
                "task_id": str(task_id), "worker_id": self.client.settings.worker_id,
                "platform_key": self.info.key,
            })
            return
        if kind == "account_validate":
            if not isinstance(value, dict) or type(value.get("authorized")) is not bool:
                self.client.complete_task(task_id, lease_token, "failed", error_code="unsafe_adapter_output")
                logger.warning("account validation result rejected by safe output boundary")
                return
            if not value["authorized"]:
                self.client.complete_task(task_id, lease_token, "failed", error_code="authorization_failed")
                return
            summary = {"operation": "account_validate", "authorized": True, "read_only": True}
            self.client.complete_task(task_id, lease_token, "succeeded", result_summary=summary)
            logger.info("task completed", extra={
                "event_type": "worker.task.completed", "result": "succeeded", "task_id": str(task_id),
                "worker_id": self.client.settings.worker_id, "platform_key": self.info.key,
            })
            return
        try:
            summary = _result_summary(kind, value)
            course_catalog = _course_catalog_payload(kind, value, self.info.key)
            self.client.complete_task(task_id, lease_token, "succeeded", result_summary=summary, course_catalog=course_catalog)
            logger.info("task completed", extra={
                "event_type": "worker.task.completed", "result": "succeeded", "task_id": str(task_id),
                "worker_id": self.client.settings.worker_id, "platform_key": self.info.key,
            })
        except (TypeError, ValueError):
            self.client.complete_task(task_id, lease_token, "failed", error_code="unsafe_adapter_output")
            logger.warning("task result rejected by safe output boundary")


def _course_catalog_payload(kind: str, result: Any, platform_key: str) -> list[dict[str, str]] | None:
    if kind not in {"course_sync", "course_list"}:
        return None
    if not isinstance(result, (list, tuple)) or len(result) > 500:
        raise ValueError("course task must return a list of at most 500 CourseSummary values")
    catalog = []
    for course in result:
        if not isinstance(course, CourseSummary) or course.platform != platform_key:
            raise ValueError("course task returned an unverified course model")
        catalog.append({
            "course_id": course.course_id,
            "course_name": course.course_name,
            "teacher_name": course.teacher_name or "",
            "semester": course.semester or "",
            "resource_status": course.resource_status or "UNKNOWN",
        })
    return catalog


def _result_summary(kind: str, result: Any) -> dict[str, Any]:
    """Return a compact operation summary; full platform payloads never enter logs/events."""
    if isinstance(result, (list, tuple)):
        count = len(result)
    elif result is None:
        count = 0
    else:
        count = 1
    summary = {"operation": kind, "item_count": count, "read_only": True}
    encoded = json.dumps(summary, ensure_ascii=False).encode("utf-8")
    if len(encoded) > 4096:
        raise ValueError("summary exceeds limit")
    return summary


def run_forever(engine: WorkerEngine) -> None:
    configure_json_logging(logging.getLogger(), "worker")
    logger.info("worker starting", extra={
        "event_type": "worker.started",
        "platform_key": engine.info.key,
        "worker_id": engine.client.settings.worker_id,
    })
    try:
        while not engine.stop_event.is_set():
            try:
                claimed = engine.run_once()
            except WorkerProtocolError as exc:
                logger.warning("worker protocol failure", extra={
                    "event_type": "worker.protocol.failure", "error_type": exc.code,
                    "status": exc.status, "worker_id": engine.client.settings.worker_id,
                    "platform_key": engine.info.key,
                })
                engine.stop_event.wait(engine.client.settings.poll_interval_seconds)
                continue
            except Exception as exc:  # no str(exc): third-party responses may contain credentials
                logger.error("worker loop error", extra={
                    "event_type": "worker.loop.failure", "error_type": type(exc).__name__,
                    "worker_id": engine.client.settings.worker_id, "platform_key": engine.info.key,
                })
                engine.stop_event.wait(engine.client.settings.poll_interval_seconds)
                continue
            if not claimed:
                engine.stop_event.wait(engine.client.settings.poll_interval_seconds)
    finally:
        engine.stop_event.set()
        shutdown = getattr(engine.client, "shutdown", None)
        if callable(shutdown):
            try:
                shutdown()
            except WorkerProtocolError as exc:
                logger.warning("worker offline notification failed code=%s status=%s", exc.code, exc.status)
            except Exception as exc:
                logger.warning("worker offline notification failed type=%s", type(exc).__name__)
        close = getattr(engine.adapter, "close", None)
        if callable(close):
            try:
                close()
            except Exception as exc:
                logger.warning("adapter shutdown cleanup failed type=%s", type(exc).__name__)
        logger.info("worker stopping platform=%s id=%s", engine.info.key, engine.client.settings.worker_id)
