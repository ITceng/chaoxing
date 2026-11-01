"""Task kinds that the unified task queue is permitted to dispatch.

This set is intentionally read-only: no study-progress writes, attendance,
quiz/exam, answer generation, or submission operations are part of this system.
"""
READ_ONLY_TASK_KINDS = frozenset({
    "account_validate",
    "course_sync",
    "course_list",
    "course_details",
    "resource_list",
    "progress_read",
})
