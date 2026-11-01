from __future__ import annotations

from typing import Any, Callable, Mapping
from urllib.parse import urljoin

import requests

from unified_system.workers.runtime.settings import WorkerSettings


class WorkerProtocolError(RuntimeError):
    def __init__(self, code: str, status: int | None = None):
        super().__init__(code)
        self.code = code
        self.status = status


class WorkerClient:
    """HTTP client for the existing private worker contract; no browser/user cookies."""

    def __init__(self, settings: WorkerSettings, request: Callable = requests.request):
        self.settings = settings
        self._request = request
        self._base = settings.backend_url.rstrip("/")
        self._headers = {
            "Accept": "application/json",
            "X-Worker-Platform": settings.platform_key,
            "X-Worker-ID": settings.worker_id,
            "X-Worker-Token": settings.token,
        }

    def _call(self, method: str, path: str, body: Mapping[str, Any] | None = None, *, allow_status=()) -> tuple[int, dict]:
        try:
            response = self._request(
                method,
                urljoin(self._base + "/", "api/internal/workers/" + path.lstrip("/")),
                headers=self._headers,
                json=dict(body) if body is not None else None,
                timeout=self.settings.request_timeout_seconds,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            # Request exceptions can embed URLs; expose only a stable code.
            raise WorkerProtocolError("worker_api_unreachable") from exc
        try:
            status = int(response.status_code)
            if status in allow_status:
                payload = {}
                if response.content:
                    try:
                        payload = response.json()
                    except (ValueError, requests.JSONDecodeError):
                        raise WorkerProtocolError("worker_api_invalid_json", status)
                return status, payload
            if not 200 <= status < 300:
                raise WorkerProtocolError("worker_api_rejected", status)
            if status == 204 or not response.content:
                return status, {}
            try:
                payload = response.json()
            except (ValueError, requests.JSONDecodeError) as exc:
                raise WorkerProtocolError("worker_api_invalid_json", status) from exc
            if not isinstance(payload, dict):
                raise WorkerProtocolError("worker_api_invalid_shape", status)
            return status, payload
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

    def heartbeat(self, capabilities: list[str]) -> dict:
        _, payload = self._call("POST", "heartbeat", {"version": self.settings.version, "capabilities": capabilities})
        return payload

    def shutdown(self) -> dict:
        _, payload = self._call("POST", "shutdown", {})
        return payload

    def claim(self) -> dict | None:
        status, payload = self._call("POST", "claim", {}, allow_status=(204,))
        if status == 204:
            return None
        task, credential = payload.get("task"), payload.get("credential")
        if not isinstance(task, dict) or not isinstance(credential, dict):
            raise WorkerProtocolError("worker_api_invalid_task_envelope", status)
        if not isinstance(task.get("id"), str) or not isinstance(task.get("request_data"), dict):
            raise WorkerProtocolError("worker_api_invalid_task_envelope", status)
        if not isinstance(credential.get("values"), dict):
            raise WorkerProtocolError("worker_api_invalid_credential_envelope", status)
        return payload

    def start_task(self, task_id: str, lease_token: str) -> bool:
        status, payload = self._call("POST", f"{task_id}/start", {"lease_token": lease_token}, allow_status=(409,))
        return bool(payload.get("accepted", status == 200))

    def task_heartbeat(self, task_id: str, lease_token: str) -> bool:
        _, payload = self._call("POST", f"{task_id}/heartbeat", {"lease_token": lease_token})
        return bool(payload.get("cancel_requested", False))

    def complete_task(self, task_id: str, lease_token: str, outcome: str, *, error_code: str | None = None, result_summary: Mapping[str, Any] | None = None, course_catalog: list[dict[str, str]] | None = None) -> dict:
        body: dict[str, Any] = {"lease_token": lease_token, "outcome": outcome}
        if error_code:
            body["error_code"] = error_code
        if result_summary is not None:
            body["result_summary"] = dict(result_summary)
        if course_catalog is not None:
            body["course_catalog"] = course_catalog
        _, payload = self._call("POST", f"{task_id}/complete", body)
        return payload
