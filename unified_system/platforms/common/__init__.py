from unified_system.platforms.common.base_adapter import PlatformAdapter
from unified_system.platforms.common.errors import AdapterCancelled, AdapterError, UnsupportedCapability
from unified_system.platforms.common.models import (
    AdapterCapabilities, AuthContext, CancellationToken, CourseDetails, CourseSummary, HealthStatus,
    LearningProgress, LearningResource, PlatformInfo, WorkerTask,
)
from unified_system.platforms.common.registry import AdapterRegistry
from unified_system.platforms.common.verification import VerificationState

__all__ = [
    "AdapterCancelled", "AdapterCapabilities", "AdapterError", "AdapterRegistry",
    "AuthContext", "CancellationToken", "CourseDetails", "CourseSummary", "HealthStatus",
    "LearningProgress", "LearningResource", "PlatformAdapter", "PlatformInfo",
    "UnsupportedCapability", "VerificationState", "WorkerTask",
]
