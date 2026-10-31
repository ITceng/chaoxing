from __future__ import annotations

from sqlalchemy import select

from unified_system.models import (
    AccountAccessGrant, AccountExecutionLease, AccountUsageLog, Task, TaskEvent, utcnow,
)

TERMINAL_TASK_STATES = {"succeeded", "failed", "cancelled", "timed_out"}


def add_task_event(db, task: Task, event_type: str, details: dict | None = None) -> None:
    db.add(TaskEvent(task_id=task.id, user_id=task.user_id, event_type=event_type, details=details or {}))


def add_usage_event(db, task: Task, event_type: str, details: dict | None = None) -> None:
    if not task.shared_account_id or not task.access_grant_id:
        return
    db.add(AccountUsageLog(
        shared_account_id=task.shared_account_id,
        grant_id=task.access_grant_id,
        user_id=task.user_id,
        task_id=task.id,
        event_type=event_type,
        details=details or {},
    ))


def request_task_cancel(db, task: Task, reason: str) -> bool:
    """Cancel queued work immediately; fence leased/running work after worker acknowledgement/lease expiry."""
    if task.status in TERMINAL_TASK_STATES or task.status == "cancel_requested":
        return False
    now = utcnow()
    if task.status == "queued":
        task.status = "cancelled"
        task.finished_at = now
        task.error_code = reason[:64]
        add_task_event(db, task, "cancelled", {"reason": reason[:64]})
        add_usage_event(db, task, "task_cancelled", {"reason": reason[:64]})
    else:
        task.status = "cancel_requested"
        task.cancel_requested_at = task.cancel_requested_at or now
        task.error_code = reason[:64]
        add_task_event(db, task, "cancel_requested", {"reason": reason[:64]})
        add_usage_event(db, task, "cancel_requested", {"reason": reason[:64]})
    return True


def cancel_account_tasks(db, account_id: str, reason: str, user_id: str | None = None, grant_id: str | None = None) -> int:
    stmt = select(Task).where(
        (Task.personal_account_id == account_id) | (Task.shared_account_id == account_id)
    )
    if user_id:
        stmt = stmt.where(Task.user_id == user_id)
    if grant_id:
        stmt = stmt.where(Task.access_grant_id == grant_id)
    tasks = db.scalars(stmt.with_for_update()).all()
    return sum(1 for task in tasks if request_task_cancel(db, task, reason))


def cancel_user_tasks(db, user_id: str, reason: str = "user_disabled") -> int:
    tasks = db.scalars(select(Task).where(Task.user_id == user_id).with_for_update()).all()
    return sum(1 for task in tasks if request_task_cancel(db, task, reason))


def revoke_user_grants(db, user_id: str, actor_id: str) -> int:
    grants = db.scalars(select(AccountAccessGrant).where(
        AccountAccessGrant.user_id == user_id,
        AccountAccessGrant.revoked_at.is_(None),
    ).with_for_update()).all()
    now = utcnow()
    for grant in grants:
        grant.revoked_at = now
        cancel_account_tasks(db, grant.shared_account_id, "user_disabled", user_id=user_id, grant_id=grant.id)
    return len(grants)


def release_task_lease(db, task: Task, token_hash: str | None = None) -> bool:
    if task.personal_account_id:
        account_id = task.personal_account_id
    else:
        account_id = task.shared_account_id
    lease = db.scalar(select(AccountExecutionLease).where(
        AccountExecutionLease.account_id == account_id,
        AccountExecutionLease.task_id == task.id,
    ).with_for_update())
    if not lease or (token_hash and lease.lease_token_hash != token_hash):
        return False
    lease.task_id = None
    lease.lease_token_hash = None
    lease.worker_id = None
    lease.expires_at = None
    lease.heartbeat_at = None
    return True
