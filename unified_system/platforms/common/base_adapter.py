from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Mapping

from unified_system.platforms.common.errors import UnsupportedCapability
from unified_system.platforms.common.models import (
    AdapterCapabilities, AuthContext, CourseDetails, CourseSummary, HealthStatus,
    LearningProgress, LearningResource, PlatformInfo,
)


class PlatformAdapter(ABC):
    """Platform-specific, read-only learning-data contract for isolated workers.

    Mutating study, quiz, exam, attendance, progress-write and task-completion methods
    deliberately do not exist in this contract.
    """

    @abstractmethod
    def platform_info(self) -> PlatformInfo: ...

    @abstractmethod
    def capabilities(self) -> AdapterCapabilities: ...

    def validate_credentials(self, auth: AuthContext) -> bool:
        raise UnsupportedCapability("credential_validation")

    def start_authorization(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        raise UnsupportedCapability("authorization_start")

    def check_authorization(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        raise UnsupportedCapability("authorization_status")

    def revoke_authorization(self, auth: AuthContext) -> None:
        raise UnsupportedCapability("authorization_revoke")

    def list_courses(self, auth: AuthContext) -> list[CourseSummary]:
        raise UnsupportedCapability("course_list")

    def get_course_details(self, auth: AuthContext, course_id: str) -> CourseDetails:
        raise UnsupportedCapability("course_details")

    def get_learning_resources(self, auth: AuthContext, course_id: str) -> list[LearningResource]:
        raise UnsupportedCapability("learning_resources")

    def get_learning_progress(self, auth: AuthContext, course_id: str) -> LearningProgress:
        raise UnsupportedCapability("learning_progress_read")

    def health_check(self) -> HealthStatus:
        raise UnsupportedCapability("health_check")

    def execute_read_only(self, kind: str, request_data: Mapping[str, Any], auth: AuthContext, cancellation) -> Any:
        """Dispatch an explicit allowlist; arbitrary task kinds are never executed."""
        cancellation.checkpoint()
        if kind == "account_validate":
            return {"authorized": bool(self.validate_credentials(auth))}
        if kind in {"course_sync", "course_list"}:
            return self.list_courses(auth)
        course_id = str(request_data.get("course_id", "")).strip()
        if not course_id or len(course_id) > 160:
            raise ValueError("course_id is required and must be at most 160 characters")
        if kind == "course_details":
            return self.get_course_details(auth, course_id)
        if kind == "resource_list":
            return self.get_learning_resources(auth, course_id)
        if kind == "progress_read":
            return self.get_learning_progress(auth, course_id)
        raise UnsupportedCapability(f"task:{kind}")
