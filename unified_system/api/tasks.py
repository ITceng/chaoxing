from __future__ import annotations

import json

from flask import Blueprint, g, jsonify, request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from unified_system.api.common import permission_required
from unified_system.db import get_db
from unified_system.models import (
    AccountAccessGrant, PersonalPlatformAccount, Platform, PlatformAccount,
    SharedPlatformAccount, Task, TaskEvent, User, utcnow,
)
from unified_system.security import iso_utc, json_error
from unified_system.services.tasks import add_task_event, add_usage_event, request_task_cancel

bp = Blueprint("tasks_api", __name__, url_prefix="/api/v1/tasks")
_SENSITIVE_KEYS = {"password", "cookie", "cookies", "token", "access_token", "refresh_token", "api_key", "authorization", "credential", "credentials"}


def _contains_secret_key(value):
    if isinstance(value, dict):
        return any(str(k).lower() in _SENSITIVE_KEYS or _contains_secret_key(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_contains_secret_key(v) for v in value)
    return False


def _task_public(task: Task, platform_key: str | None = None):
    return {
        "id": task.id, "status": task.status, "kind": task.kind,
        "account_mode": "personal" if task.personal_account_id else "shared",
        "account_id": task.personal_account_id or task.shared_account_id,
        "platform_key": platform_key,
        "request_data": task.request_data,
        "attempt": task.attempt, "timeout_seconds": task.timeout_seconds,
        "error_code": task.error_code,
        "result_summary": task.result_summary,
        "created_at": iso_utc(task.created_at), "started_at": iso_utc(task.started_at),
        "finished_at": iso_utc(task.finished_at),
    }


def _account_platform(db, account_id):
    base = db.get(PlatformAccount, account_id)
    if not base:
        return None, None
    return base, db.get(Platform, base.platform_id)


@bp.post("")
@permission_required("tasks.create", csrf=True)
def create_task():
    data = request.get_json(silent=True) or {}
    mode = data.get("account_mode")
    account_id = str(data.get("account_id", ""))
    kind = str(data.get("kind", "")).strip()
    request_data = data.get("request_data", {})
    if mode not in {"personal", "shared"} or not account_id or not kind or len(kind) > 48:
        return json_error("account_mode, account_id and a valid kind are required", 400)
    if not isinstance(request_data, dict) or len(json.dumps(request_data, ensure_ascii=False).encode("utf-8")) > 32_768:
        return json_error("request_data must be a JSON object no larger than 32 KiB", 400)
    if _contains_secret_key(request_data):
        return json_error("Credentials must never be placed in task request_data", 400)
    timeout_seconds = data.get("timeout_seconds", 900)
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or not 1 <= timeout_seconds <= 86400:
        return json_error("timeout_seconds must be between 1 and 86400", 400)
    idempotency_key = str(data.get("idempotency_key", "")).strip() or None
    if idempotency_key and len(idempotency_key) > 128:
        return json_error("idempotency_key is too long", 400)

    db = get_db()
    if idempotency_key:
        prior = db.scalar(select(Task).where(Task.user_id == g.current_user.id, Task.idempotency_key == idempotency_key))
        if prior:
            return jsonify({"task": _task_public(prior)}), 200
    actor = db.scalar(select(User).where(User.id == g.current_user.id).with_for_update())
    if not actor or actor.status != "active":
        return json_error("Active account required", 403)
    personal_id, shared_id, grant_id = None, None, None
    now = utcnow()
    if mode == "personal":
        owner_hint = db.get(PersonalPlatformAccount, account_id)
        if not owner_hint or owner_hint.owner_user_id != actor.id:
            return json_error("Personal platform account not found", 404)
        base = db.scalar(select(PlatformAccount).where(PlatformAccount.id == account_id).with_for_update())
        personal = db.scalar(select(PersonalPlatformAccount).where(
            PersonalPlatformAccount.account_id == account_id,
            PersonalPlatformAccount.owner_user_id == actor.id,
        ).with_for_update())
        if not personal or not base or base.account_mode != "personal":
            return json_error("Personal platform account not found", 404)
        personal_id = account_id
    else:
        grant_hint = db.scalar(select(AccountAccessGrant).where(
            AccountAccessGrant.shared_account_id == account_id,
            AccountAccessGrant.user_id == actor.id,
            AccountAccessGrant.revoked_at.is_(None),
            AccountAccessGrant.valid_from <= now,
            (AccountAccessGrant.expires_at.is_(None) | (AccountAccessGrant.expires_at > now)),
        ).order_by(AccountAccessGrant.created_at.desc()))
        if not grant_hint:
            return json_error("Shared account access is unavailable", 404)
        base = db.scalar(select(PlatformAccount).where(PlatformAccount.id == account_id).with_for_update())
        shared = db.scalar(select(SharedPlatformAccount).where(
            SharedPlatformAccount.account_id == account_id,
        ).with_for_update())
        grant = db.scalar(select(AccountAccessGrant).where(
            AccountAccessGrant.id == grant_hint.id,
            AccountAccessGrant.user_id == actor.id,
            AccountAccessGrant.revoked_at.is_(None),
            AccountAccessGrant.valid_from <= now,
            (AccountAccessGrant.expires_at.is_(None) | (AccountAccessGrant.expires_at > now)),
        ).with_for_update())
        if not grant or not shared or not base or base.account_mode != "shared" or not shared.is_open:
            return json_error("Shared account access is unavailable", 404)
        if shared.available_from and shared.available_from > now:
            return json_error("Shared account is not yet available", 409)
        if shared.expires_at and shared.expires_at <= now:
            return json_error("Shared account has expired", 409)
        allowed_kinds = (grant.scope or {}).get("task_kinds", [])
        if allowed_kinds and kind not in allowed_kinds:
            return json_error("Task kind is outside the grant scope", 403)
        shared_id, grant_id = account_id, grant.id

    platform = db.get(Platform, base.platform_id) if base else None
    if not base or not platform:
        return json_error("Platform account not found", 404)
    if base.status != "active":
        return json_error("Platform account has not been verified and activated", 409)
    if platform.integration_status != "active":
        return json_error("Platform integration is not active", 409)
    task = Task(
        user_id=g.current_user.id,
        personal_account_id=personal_id,
        shared_account_id=shared_id,
        access_grant_id=grant_id,
        kind=kind, request_data=request_data,
        idempotency_key=idempotency_key,
        timeout_seconds=timeout_seconds,
    )
    try:
        db.add(task)
        db.flush()
        add_task_event(db, task, "queued", {"platform_key": platform.key})
        add_usage_event(db, task, "task_queued", {"kind": kind})
        db.commit()
    except IntegrityError:
        db.rollback()
        if idempotency_key:
            prior = db.scalar(select(Task).where(Task.user_id == g.current_user.id, Task.idempotency_key == idempotency_key))
            if prior:
                return jsonify({"task": _task_public(prior)}), 200
        return json_error("Task idempotency key conflicts with an existing request", 409)
    return jsonify({"task": _task_public(task, platform.key)}), 202


@bp.get("")
@permission_required("tasks.read")
def list_tasks():
    db = get_db()
    rows = db.scalars(select(Task).where(Task.user_id == g.current_user.id).order_by(Task.created_at.desc()).limit(200)).all()
    result = []
    for task in rows:
        account_id = task.personal_account_id or task.shared_account_id
        _, platform = _account_platform(db, account_id)
        result.append(_task_public(task, platform.key if platform else None))
    return jsonify({"tasks": result})


@bp.get("/<task_id>")
@permission_required("tasks.read")
def get_task(task_id: str):
    db = get_db()
    task = db.get(Task, task_id)
    if not task or task.user_id != g.current_user.id:
        return json_error("Task not found", 404)
    account_id = task.personal_account_id or task.shared_account_id
    _, platform = _account_platform(db, account_id)
    events = db.scalars(select(TaskEvent).where(
        TaskEvent.task_id == task.id, TaskEvent.user_id == g.current_user.id,
    ).order_by(TaskEvent.created_at.asc())).all()
    return jsonify({
        "task": _task_public(task, platform.key if platform else None),
        "events": [{"type": row.event_type, "details": row.details, "created_at": iso_utc(row.created_at)} for row in events],
    })


@bp.post("/<task_id>/cancel")
@permission_required("tasks.read", csrf=True)
def cancel_task(task_id: str):
    db = get_db()
    task = db.get(Task, task_id)
    if not task or task.user_id != g.current_user.id:
        return json_error("Task not found", 404)
    if not request_task_cancel(db, task, "user_cancelled"):
        return json_error("Task is already complete", 409)
    db.commit()
    return jsonify({"task": _task_public(task)})
