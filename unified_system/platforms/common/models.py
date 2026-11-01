from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from threading import Event
from typing import Any, Mapping


@dataclass(frozen=True)
class PlatformInfo:
    key: str
    name: str
    adapter_version: str


@dataclass(frozen=True)
class AdapterCapabilities:
    """Only capabilities implemented and independently verified may be true/listed."""

    auth_methods: tuple[str, ...] = ()
    password_auth: bool = False
    official_qr_auth: bool = False
    authorized_session_import: bool = False
    authorization_status: bool = False
    credential_validation: bool = False
    course_list: bool = False
    course_details: bool = False
    learning_resources: bool = False
    learning_progress_read: bool = False
    supported_task_kinds: tuple[str, ...] = ()
    verified_at: datetime | None = None

    def __post_init__(self) -> None:
        capability_for_task = {
            "account_validate": "credential_validation",
            "course_sync": "course_list",
            "course_list": "course_list",
            "course_details": "course_details",
            "resource_list": "learning_resources",
            "progress_read": "learning_progress_read",
        }
        unknown = set(self.supported_task_kinds) - set(capability_for_task)
        if unknown:
            raise ValueError("unknown or non-read-only task capability declared")
        if any(not getattr(self, capability_for_task[kind]) for kind in self.supported_task_kinds):
            raise ValueError("supported task kind must have its matching platform capability enabled")

    def worker_capabilities(self) -> list[str]:
        return sorted(self.supported_task_kinds)

    def public(self) -> dict[str, Any]:
        return {
            "auth_methods": list(self.auth_methods),
            "password_auth": self.password_auth,
            "official_qr_auth": self.official_qr_auth,
            "authorized_session_import": self.authorized_session_import,
            "authorization_status": self.authorization_status,
            "credential_validation": self.credential_validation,
            "course_list": self.course_list,
            "course_details": self.course_details,
            "learning_resources": self.learning_resources,
            "learning_progress_read": self.learning_progress_read,
            "supported_task_kinds": list(self.supported_task_kinds),
            "verified_at": self.verified_at.isoformat() if self.verified_at else None,
        }


@dataclass(frozen=True)
class AuthContext:
    """Credentials are short-lived worker input and intentionally excluded from repr."""

    account_id: str
    auth_type: str
    values: Mapping[str, Any] = field(repr=False)


@dataclass(frozen=True)
class CourseSummary:
    platform: str
    course_id: str
    course_name: str
    teacher_name: str | None = None
    semester: str | None = None
    resource_status: str = "UNKNOWN"
    last_synced_at: datetime | None = None
    source_verified: bool = False


@dataclass(frozen=True)
class CourseDetails:
    course: CourseSummary
    description: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LearningResource:
    resource_id: str
    course_id: str
    title: str
    resource_type: str
    resource_status: str = "AVAILABLE"


@dataclass(frozen=True)
class LearningProgress:
    course_id: str
    progress_status: str
    completed_count: int | None = None
    total_count: int | None = None
    observed_at: datetime | None = None
    source_verified: bool = False


@dataclass(frozen=True)
class HealthStatus:
    healthy: bool
    checked_at: datetime
    detail_code: str = "ok"


@dataclass(frozen=True)
class WorkerTask:
    task_id: str
    platform_key: str
    kind: str
    request_data: Mapping[str, Any]
    account_id: str
    credential: AuthContext = field(repr=False)
    lease_token: str = field(repr=False)
    timeout_seconds: int = 900


class CancellationToken:
    def __init__(self, event: Event | None = None):
        self._event = event or Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def checkpoint(self) -> None:
        if self.cancelled:
            from unified_system.platforms.common.errors import AdapterCancelled
            raise AdapterCancelled()
