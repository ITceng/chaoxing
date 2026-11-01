from __future__ import annotations

import json
import logging
import queue
import threading
import time
from typing import Any

from unified_system.platforms.common import AdapterCancelled, AdapterError, AuthContext, CancellationToken, CourseSummary, HealthStatus, WorkerTask
from unified_system.workers.runtime.client import WorkerClient, WorkerProtocolError


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
        envelope = self.client.claim()
        if envelope is None:
            return False
        self._execute(envelope)
        return True

    def _execute(self, envelope: dict) -> None:
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
                    if self.client.task_heartbeat(task_id, lease_token):
                        cancellation.cancel()
                        cancel_at = cancel_at or now
                except WorkerProtocolError:
                    cancellation.cancel()
                    logger.warning("task lease heartbeat failed; allowing lease to expire")
                    return
                last_heartbeat = now
            thread.join(timeout=min(0.25, self.client.settings.heartbeat_interval_seconds))
            if cancel_at is not None and time.monotonic() - cancel_at >= self.client.settings.cancellation_grace_seconds:
                logger.warning("adapter did not stop within cancellation grace; lease will expire")
                return

        ok, value = result_queue.get_nowait()
        if cancellation.cancelled:
            self.client.complete_task(task_id, lease_token, "cancelled", result_summary={})
            return
        if not ok:
            code = value.code if isinstance(value, AdapterError) else "adapter_error"
            if isinstance(value, AdapterCancelled):
                code = "cancelled"
                self.client.complete_task(task_id, lease_token, "cancelled", error_code=code, result_summary={})
            else:
                self.client.complete_task(task_id, lease_token, "failed", error_code=code)
            logger.warning("task completed with adapter error code=%s", code)
            return
        try:
            summary = _result_summary(kind, value)
            course_catalog = _course_catalog_payload(kind, value, self.info.key)
            self.client.complete_task(task_id, lease_token, "succeeded", result_summary=summary, course_catalog=course_catalog)
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
    logger.info("worker starting platform=%s id=%s capabilities=%s", engine.info.key, engine.client.settings.worker_id, sorted(engine.task_kinds))
    while not engine.stop_event.is_set():
        try:
            claimed = engine.run_once()
        except WorkerProtocolError as exc:
            logger.warning("worker protocol failure code=%s status=%s", exc.code, exc.status)
            engine.stop_event.wait(engine.client.settings.poll_interval_seconds)
            continue
        except Exception as exc:  # no str(exc): third-party responses may contain credentials
            logger.error("worker loop error type=%s", type(exc).__name__)
            engine.stop_event.wait(engine.client.settings.poll_interval_seconds)
            continue
        if not claimed:
            engine.stop_event.wait(engine.client.settings.poll_interval_seconds)
    logger.info("worker stopping platform=%s id=%s", engine.info.key, engine.client.settings.worker_id)
