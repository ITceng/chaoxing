from __future__ import annotations

import hmac
import json
import secrets
from datetime import timedelta

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import select

from unified_system.db import get_db
from unified_system.models import (
    AccountAccessGrant, AccountExecutionLease, PersonalPlatformAccount,
    Platform, PlatformAccount, PlatformAccountSecret, SharedPlatformAccount, Task,
    User, WorkerInstance, utcnow,
)
from unified_system.security import decrypt_secret, hash_token, iso_utc, json_error
from unified_system.services.tasks import add_task_event, add_usage_event, release_task_lease

bp = Blueprint("worker_api", __name__, url_prefix="/api/internal/workers")


def _has_sensitive_key(value):
    sensitive = {"password", "cookie", "cookies", "token", "access_token", "refresh_token", "api_key", "authorization", "credential", "credentials"}
    if isinstance(value, dict):
        return any(str(key).lower() in sensitive or _has_sensitive_key(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_has_sensitive_key(item) for item in value)
    return False


def _authorize_worker():
    db = get_db()
    platform_key = request.headers.get("X-Worker-Platform", "").strip()
    worker_id = request.headers.get("X-Worker-ID", "").strip()
    token = request.headers.get("X-Worker-Token", "")
    expected = current_app.config.get("WORKER_TOKENS", {}).get(platform_key, "")
    if not expected:
        return None, None, None, (json_error("Worker channel is not configured", 503))
    if not worker_id or len(worker_id) > 120 or not token or not hmac.compare_digest(expected, token):
        return None, None, None, json_error("Worker authentication failed", 401)
    platform = db.scalar(select(Platform).where(Platform.key == platform_key))
    if not platform:
        return None, None, None, json_error("Worker platform is unknown", 404)
    return db, platform, worker_id, None


def _worker_route(fn):
    from functools import wraps
    @wraps(fn)
    def wrapped(*args, **kwargs):
        db, platform, worker_id, error = _authorize_worker()
        if error:
            return error
        return fn(db, platform, worker_id, *args, **kwargs)
    return wrapped


def _account_details(db, task):
    account_id = task.personal_account_id or task.shared_account_id
    base = db.get(PlatformAccount, account_id)
    platform = db.get(Platform, base.platform_id) if base else None
    if task.personal_account_id:
        personal = db.get(PersonalPlatformAccount, account_id)
        owner = db.get(User, personal.owner_user_id) if personal else None
        return account_id, base, platform, None, owner
    shared = db.get(SharedPlatformAccount, account_id)
    owner = db.get(User, task.user_id)
    return account_id, base, platform, shared, owner


def _task_is_eligible(db, task):
    now = utcnow()
    account_id, base, platform, shared, user = _account_details(db, task)
    if not base or not platform or not user or user.status != "active":
        return False, "owner_or_account_unavailable", None
    if platform.integration_status != "active" or base.status != "active":
        return False, "platform_or_account_not_active", None
    if task.personal_account_id:
        personal = db.get(PersonalPlatformAccount, account_id)
        if not personal or personal.owner_user_id != task.user_id:
            return False, "personal_owner_mismatch", None
    else:
        grant = db.get(AccountAccessGrant, task.access_grant_id)
        if not shared or not grant or grant.user_id != task.user_id or grant.shared_account_id != account_id:
            return False, "shared_grant_missing", None
        if not shared.is_open or grant.revoked_at or grant.valid_from > now or (grant.expires_at and grant.expires_at <= now):
            return False, "shared_access_revoked_or_expired", None
        if shared.expires_at and shared.expires_at <= now:
            return False, "shared_account_expired", None
        if shared.available_from and shared.available_from > now:
            return False, "shared_account_not_yet_available", None
        allowed = (grant.scope or {}).get("task_kinds", [])
        if allowed and task.kind not in allowed:
            return False, "task_outside_grant_scope", None
    secret = db.scalar(select(PlatformAccountSecret).where(
        PlatformAccountSecret.account_id == account_id,
        PlatformAccountSecret.status == "active",
        PlatformAccountSecret.revoked_at.is_(None),
    ).order_by(PlatformAccountSecret.version.desc()).limit(1))
    if not secret:
        return False, "active_credential_missing", None
    return True, account_id, secret


def _close_stale_lease(db, lease, now):
    old = db.get(Task, lease.task_id) if lease.task_id else None
    if old and old.status not in {"succeeded", "failed", "cancelled", "timed_out"}:
        final_state = "timed_out" if old.status != "cancel_requested" or old.error_code == "task_timeout" else "cancelled"
        old.status = final_state
        old.finished_at = now
        old.worker_id = None
        old.lease_expires_at = None
        old.error_code = old.error_code or "worker_lease_expired"
        add_task_event(db, old, final_state, {"reason": old.error_code[:64]})
        add_usage_event(db, old, "lease_expired", {"final_state": final_state})
    lease.task_id = None
    lease.lease_token_hash = None
    lease.worker_id = None
    lease.expires_at = None
    lease.heartbeat_at = None


@bp.post("/heartbeat")
@_worker_route
def heartbeat(db, platform, worker_id):
    data = request.get_json(silent=True) or {}
    version = str(data.get("version", ""))[:80]
    capabilities = data.get("capabilities", [])
    if not isinstance(capabilities, list) or len(capabilities) > 64 or any(not isinstance(x, str) or len(x) > 80 for x in capabilities):
        return json_error("capabilities must be a list of short strings", 400)
    worker = db.scalar(select(WorkerInstance).where(WorkerInstance.worker_id == worker_id).with_for_update())
    if worker and worker.platform_id != platform.id:
        return json_error("Worker ID is already registered for another platform", 409)
    if not worker:
        worker = WorkerInstance(platform_id=platform.id, worker_id=worker_id)
        db.add(worker)
    worker.version = version
    worker.capabilities = capabilities
    worker.status = "online"
    worker.last_seen_at = utcnow()
    db.commit()
    return jsonify({"ok": True, "server_time": iso_utc(utcnow()), "platform_status": platform.integration_status})


@bp.post("/claim")
@_worker_route
def claim_task(db, platform, worker_id):
    if platform.integration_status != "active":
        return json_error("Platform integration is not active", 409)
    worker = db.scalar(select(WorkerInstance).where(WorkerInstance.worker_id == worker_id))
    now = utcnow()
    last_seen_cutoff = now - timedelta(seconds=current_app.config["WORKER_HEARTBEAT_TTL_SECONDS"])
    if not worker or worker.platform_id != platform.id or worker.status != "online" or not worker.last_seen_at or worker.last_seen_at < last_seen_cutoff:
        return json_error("Worker must send a recent heartbeat before claiming tasks", 409)
    personal_candidates = db.execute(
        select(Task.id, Task.created_at)
        .join(PersonalPlatformAccount, PersonalPlatformAccount.account_id == Task.personal_account_id)
        .join(PlatformAccount, PlatformAccount.id == PersonalPlatformAccount.account_id)
        .where(Task.status == "queued", PlatformAccount.platform_id == platform.id)
    ).all()
    shared_candidates = db.execute(
        select(Task.id, Task.created_at)
        .join(SharedPlatformAccount, SharedPlatformAccount.account_id == Task.shared_account_id)
        .join(PlatformAccount, PlatformAccount.id == SharedPlatformAccount.account_id)
        .where(Task.status == "queued", PlatformAccount.platform_id == platform.id)
    ).all()
    candidates = sorted([*personal_candidates, *shared_candidates], key=lambda row: row.created_at)[:100]

    # Reap expired leases even when no new task exists for the same account. Lock owner/account
    # parents before task then lease, matching cancellation/claim lock order.
    stale_pairs = db.execute(
        select(AccountExecutionLease.account_id, AccountExecutionLease.task_id)
        .join(PlatformAccount, PlatformAccount.id == AccountExecutionLease.account_id)
        .where(PlatformAccount.platform_id == platform.id,
               AccountExecutionLease.task_id.is_not(None),
               AccountExecutionLease.expires_at <= now)
    ).all()
    for stale_account_id, stale_task_id in stale_pairs:
        stale_hint = db.get(Task, stale_task_id)
        if stale_hint:
            db.scalar(select(User).where(User.id == stale_hint.user_id).with_for_update())
        db.scalar(select(PlatformAccount).where(PlatformAccount.id == stale_account_id).with_for_update())
        if stale_hint and stale_hint.shared_account_id:
            db.scalar(select(SharedPlatformAccount).where(SharedPlatformAccount.account_id == stale_account_id).with_for_update())
            db.scalar(select(AccountAccessGrant).where(AccountAccessGrant.id == stale_hint.access_grant_id).with_for_update())
        stale_task = db.scalar(select(Task).where(Task.id == stale_task_id).with_for_update(skip_locked=True))
        if not stale_task:
            continue
        stale_lease = db.scalar(select(AccountExecutionLease).where(
            AccountExecutionLease.account_id == stale_account_id,
            AccountExecutionLease.task_id == stale_task_id,
        ).with_for_update())
        if stale_lease and stale_lease.expires_at and stale_lease.expires_at <= now:
            _close_stale_lease(db, stale_lease, now)

    for candidate in candidates:
        task_id = candidate.id
        hint = db.get(Task, task_id)
        if not hint or hint.status != "queued":
            continue
        # Global lock order: owner -> base account -> subtype/grant -> task -> lease.
        owner = db.scalar(select(User).where(User.id == hint.user_id).with_for_update())
        account_id = hint.personal_account_id or hint.shared_account_id
        base = db.scalar(select(PlatformAccount).where(PlatformAccount.id == account_id).with_for_update())
        if not base or base.platform_id != platform.id:
            continue
        shared = None
        if hint.shared_account_id:
            shared = db.scalar(select(SharedPlatformAccount).where(
                SharedPlatformAccount.account_id == account_id,
            ).with_for_update())
            db.scalar(select(AccountAccessGrant).where(AccountAccessGrant.id == hint.access_grant_id).with_for_update())
        task = db.scalar(select(Task).where(Task.id == task_id, Task.status == "queued").with_for_update(skip_locked=True))
        if not task:
            continue
        eligible, reason_or_account, secret = _task_is_eligible(db, task)
        if not eligible:
            terminal = "cancelled" if reason_or_account in {
                "owner_or_account_unavailable", "platform_or_account_not_active",
                "shared_grant_missing", "shared_access_revoked_or_expired", "shared_account_expired",
                "shared_account_not_yet_available",
            } else "failed"
            task.status = terminal
            task.error_code = reason_or_account[:64]
            task.finished_at = now
            add_task_event(db, task, terminal, {"code": reason_or_account[:64]})
            add_usage_event(db, task, "task_failed" if terminal == "failed" else "task_cancelled", {"code": reason_or_account[:64]})
            continue
        limit = shared.max_concurrency if shared else 1
        if limit > 1 and "parallel_account_sessions_verified" not in (task_platform.capabilities or []):
            limit = 1
        chosen = None
        for slot_no in range(limit):
            lease = db.get(AccountExecutionLease, (account_id, slot_no))
            if lease and lease.task_id and lease.expires_at and lease.expires_at > now:
                continue
            if lease and lease.task_id:
                _close_stale_lease(db, lease, now)
            if not lease:
                lease = AccountExecutionLease(account_id=account_id, slot_no=slot_no)
                db.add(lease)
                db.flush()
            chosen = lease
            break
        if chosen is None:
            continue
        lease_token = secrets.token_urlsafe(32)
        expires = now + timedelta(seconds=current_app.config["WORKER_LEASE_TTL_SECONDS"])
        chosen.task_id = task.id
        chosen.lease_token_hash = hash_token(lease_token)
        chosen.worker_id = worker_id
        chosen.acquired_at = now
        chosen.heartbeat_at = now
        chosen.expires_at = expires
        task.status = "leased"
        task.worker_id = worker_id
        task.attempt += 1
        task.lease_expires_at = expires
        add_task_event(db, task, "leased", {"worker_id": worker_id, "lease_expires_at": iso_utc(expires)})
        add_usage_event(db, task, "lease_acquired", {"worker_id": worker_id})
        try:
            credentials = json.loads(decrypt_secret(secret.ciphertext))
        except (ValueError, TypeError, json.JSONDecodeError):
            task.status = "failed"
            task.finished_at = now
            task.error_code = "credential_decryption_failed"
            task.worker_id = None
            task.lease_expires_at = None
            add_task_event(db, task, "failed", {"code": task.error_code})
            add_usage_event(db, task, "task_failed", {"code": task.error_code})
            release_task_lease(db, task, chosen.lease_token_hash)
            db.commit()
            continue
        account_id, _, _, _, _ = _account_details(db, task)
        db.commit()
        return jsonify({
            "task": {
                "id": task.id, "user_id": task.user_id,
                "platform_key": platform.key, "platform_account_id": account_id,
                "kind": task.kind, "request_data": task.request_data,
                "attempt": task.attempt, "timeout_seconds": task.timeout_seconds,
                "lease_token": lease_token, "lease_expires_at": iso_utc(expires),
            },
            "credential": {"auth_type": secret.auth_type, "values": credentials},
        })
    db.commit()
    return "", 204


def _validate_lease(db, platform, worker_id, task_id, body):
    raw_token = str(body.get("lease_token", ""))
    if len(raw_token) < 24:
        return None, None, json_error("A valid lease token is required", 400)
    task = db.scalar(select(Task).where(Task.id == task_id).with_for_update())
    if not task or task.worker_id != worker_id:
        return None, None, json_error("Task lease not found", 404)
    account_id = task.personal_account_id or task.shared_account_id
    base, task_platform = _account_details(db, task)[1:3]
    if not base or not task_platform or task_platform.id != platform.id:
        return None, None, json_error("Task belongs to another platform", 404)
    lease = db.scalar(select(AccountExecutionLease).where(
        AccountExecutionLease.account_id == account_id,
        AccountExecutionLease.task_id == task_id,
    ).with_for_update())
    if (not lease or lease.worker_id != worker_id or not lease.expires_at or lease.expires_at <= utcnow()
            or not hmac.compare_digest(lease.lease_token_hash or "", hash_token(raw_token))):
        return None, None, json_error("Task lease expired or was fenced", 409)
    return task, lease, None


@bp.post("/<task_id>/start")
@_worker_route
def start_task(db, platform, worker_id, task_id):
    body = request.get_json(silent=True) or {}
    task, lease, error = _validate_lease(db, platform, worker_id, task_id, body)
    if error:
        return error
    if task.status == "cancel_requested":
        return jsonify({"accepted": False, "cancel_requested": True}), 409
    if task.status != "leased":
        return json_error("Task is not in leased state", 409)
    task.status = "running"
    task.started_at = utcnow()
    add_task_event(db, task, "running", {"worker_id": worker_id})
    db.commit()
    return jsonify({"accepted": True})


@bp.post("/<task_id>/heartbeat")
@_worker_route
def task_heartbeat(db, platform, worker_id, task_id):
    body = request.get_json(silent=True) or {}
    task, lease, error = _validate_lease(db, platform, worker_id, task_id, body)
    if error:
        return error
    if task.status not in {"leased", "running", "cancel_requested"}:
        return json_error("Task is not active", 409)
    now = utcnow()
    timed_out = bool(task.started_at and now >= task.started_at + timedelta(seconds=task.timeout_seconds))
    if timed_out and task.status != "cancel_requested":
        task.status = "cancel_requested"
        task.cancel_requested_at = now
        task.error_code = "task_timeout"
        add_task_event(db, task, "cancel_requested", {"reason": "task_timeout"})
    if task.status == "cancel_requested":
        # Do not renew a canceled/timed-out lease. It remains fenced until this TTL elapses
        # or the worker explicitly acknowledges cancellation through /complete.
        expires = lease.expires_at
    else:
        expires = now + timedelta(seconds=current_app.config["WORKER_LEASE_TTL_SECONDS"])
        lease.heartbeat_at = now
        lease.expires_at = expires
        task.lease_expires_at = expires
    db.commit()
    return jsonify({"ok": True, "cancel_requested": task.status == "cancel_requested", "lease_expires_at": iso_utc(expires)})


@bp.post("/<task_id>/complete")
@_worker_route
def complete_task(db, platform, worker_id, task_id):
    body = request.get_json(silent=True) or {}
    task, lease, error = _validate_lease(db, platform, worker_id, task_id, body)
    if error:
        return error
    outcome = body.get("outcome")
    if outcome not in {"succeeded", "failed", "cancelled"}:
        return json_error("outcome must be succeeded, failed, or cancelled", 400)
    if task.status not in {"leased", "running", "cancel_requested"}:
        return json_error("Task is not active", 409)
    if task.status == "leased" and outcome == "succeeded":
        return json_error("A task must be started before it can succeed", 409)
    summary = body.get("result_summary", {})
    if not isinstance(summary, dict) or len(json.dumps(summary, ensure_ascii=False).encode("utf-8")) > 4096:
        return json_error("result_summary must be an object no larger than 4 KiB", 400)
    if _has_sensitive_key(summary):
        return json_error("Sensitive values must not be included in result_summary", 400)
    now = utcnow()
    if task.status == "cancel_requested":
        outcome = "timed_out" if task.error_code == "task_timeout" else "cancelled"
    elif task.started_at and now >= task.started_at + timedelta(seconds=task.timeout_seconds):
        outcome = "timed_out"
        task.error_code = "task_timeout"
    task.status = outcome
    task.result_summary = summary if outcome == "succeeded" else {}
    task.finished_at = now
    if outcome == "failed":
        task.error_code = str(body.get("error_code", "worker_failed"))[:64]
    add_task_event(db, task, outcome, {"worker_id": worker_id, "error_code": task.error_code or ""})
    add_usage_event(db, task, "task_finished", {"outcome": outcome})
    release_task_lease(db, task, lease.lease_token_hash)
    task.lease_expires_at = None
    task.worker_id = None
    db.commit()
    return jsonify({"ok": True, "status": task.status})
