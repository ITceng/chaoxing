from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, g, jsonify, request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from unified_system.api.accounts import _credential_payload, _new_secret
from unified_system.api.common import permission_required
from unified_system.db import get_db
from unified_system.models import (
    AccountAccessGrant, AccountUsageLog, AdminUserScope, AuditLog, Platform,
    PlatformAccount, SharedPlatformAccount, User, utcnow,
)
from unified_system.security import iso_utc, json_error, mask_account_hint, user_roles
from unified_system.services.tasks import cancel_account_tasks

bp = Blueprint("shared_admin_api", __name__, url_prefix="/api/v1/admin/shared-accounts")


def _parse_datetime(value, nullable=False):
    if value in (None, "") and nullable:
        return None
    if not isinstance(value, str):
        raise ValueError("datetime must be an ISO 8601 string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _is_superadmin(db, user_id):
    return "superadmin" in user_roles(db, user_id)


def _managed_shared_account(db, account_id, lock=False):
    if lock:
        base = db.scalar(select(PlatformAccount).where(PlatformAccount.id == account_id).with_for_update())
        if not base:
            return None
        statement = select(SharedPlatformAccount).where(SharedPlatformAccount.account_id == account_id).with_for_update()
        shared = db.scalar(statement)
    else:
        shared = db.get(SharedPlatformAccount, account_id)
    if not shared:
        return None
    if _is_superadmin(db, g.current_user.id) or shared.created_by == g.current_user.id:
        return shared
    return None


def _audit(db, action, account_id, metadata=None, target_user_id=None):
    db.add(AuditLog(
        actor_user_id=g.current_user.id, target_user_id=target_user_id,
        action=action, entity_type="shared_platform_account", entity_id=account_id,
        metadata_json=metadata or {},
    ))


def _shared_public(db, shared):
    base = db.get(PlatformAccount, shared.account_id)
    platform = db.get(Platform, base.platform_id)
    grants = db.scalars(select(AccountAccessGrant).where(
        AccountAccessGrant.shared_account_id == shared.account_id,
    ).order_by(AccountAccessGrant.created_at.desc())).all()
    return {
        "id": base.id, "platform_key": platform.key, "platform_name": platform.name,
        "label": base.label, "account_hint": shared.account_hint,
        "status": base.status, "is_open": shared.is_open,
        "available_from": iso_utc(shared.available_from), "expires_at": iso_utc(shared.expires_at),
        "max_concurrency": shared.max_concurrency,
        "grants": [{
            "id": grant.id, "user_id": grant.user_id,
            "user_email": (db.get(User, grant.user_id).email if db.get(User, grant.user_id) else ""),
            "valid_from": iso_utc(grant.valid_from), "expires_at": iso_utc(grant.expires_at),
            "revoked_at": iso_utc(grant.revoked_at), "scope": grant.scope,
        } for grant in grants],
        "created_at": iso_utc(shared.created_at),
    }


@bp.get("")
@permission_required("shared_accounts.manage")
def list_shared_accounts():
    db = get_db()
    stmt = select(SharedPlatformAccount).order_by(SharedPlatformAccount.created_at.desc())
    if not _is_superadmin(db, g.current_user.id):
        stmt = stmt.where(SharedPlatformAccount.created_by == g.current_user.id)
    return jsonify({"shared_accounts": [_shared_public(db, row) for row in db.scalars(stmt).all()]})


@bp.post("")
@permission_required("shared_accounts.manage", csrf=True)
def create_shared_account():
    data = request.get_json(silent=True) or {}
    platform_key = str(data.get("platform_key", "")).strip()
    label = str(data.get("label", "")).strip()[:120]
    hint = mask_account_hint(data.get("account_hint", ""))
    if not hint:
        return json_error("Provide a masked account_hint", 400)
    is_open = data.get("is_open", False)
    concurrency = data.get("max_concurrency", 1)
    if not isinstance(is_open, bool) or not isinstance(concurrency, int) or isinstance(concurrency, bool) or not 1 <= concurrency <= 16:
        return json_error("is_open must be boolean and max_concurrency must be 1..16", 400)
    try:
        available_from = _parse_datetime(data.get("available_from"), nullable=True)
        expires_at = _parse_datetime(data.get("expires_at"), nullable=True)
    except (ValueError, TypeError):
        return json_error("Provide valid ISO 8601 availability times", 400)
    if expires_at and available_from and expires_at <= available_from:
        return json_error("Account expiry must be later than its start time", 400)
    auth_type, serialized, error = _credential_payload(data)
    if error:
        return json_error(*error)
    db = get_db()
    platform = db.scalar(select(Platform).where(Platform.key == platform_key))
    if not platform:
        return json_error("Platform not found", 404)
    if platform.auth_methods and auth_type not in platform.auth_methods:
        return json_error("Authentication method is not listed for this platform", 400)
    base = PlatformAccount(platform_id=platform.id, account_mode="shared", label=label, status="unverified")
    db.add(base)
    db.flush()
    shared = SharedPlatformAccount(
        account_id=base.id, created_by=g.current_user.id, account_hint=hint,
        is_open=is_open, available_from=available_from, expires_at=expires_at,
        max_concurrency=concurrency,
    )
    db.add(shared)
    _new_secret(db, base.id, g.current_user.id, auth_type, serialized)
    _audit(db, "shared_account.created", base.id, {"platform_key": platform.key, "auth_type": auth_type})
    db.commit()
    return jsonify({"shared_account": _shared_public(db, shared), "verified": False}), 201


@bp.patch("/<account_id>")
@permission_required("shared_accounts.manage", csrf=True)
def update_shared_account(account_id: str):
    data = request.get_json(silent=True) or {}
    db = get_db()
    shared = _managed_shared_account(db, account_id, lock=True)
    if not shared:
        return json_error("Shared account not found", 404)
    base = db.get(PlatformAccount, account_id)
    changed = {}
    if "label" in data:
        if not isinstance(data["label"], str) or len(data["label"].strip()) > 120:
            return json_error("label must be a string no longer than 120 characters", 400)
        base.label = data["label"].strip()
        base.updated_at = utcnow()
        changed["label"] = base.label
    if "is_open" in data:
        if not isinstance(data["is_open"], bool):
            return json_error("is_open must be boolean", 400)
        shared.is_open = data["is_open"]
        changed["is_open"] = shared.is_open
    if "status" in data:
        if data["status"] not in {"disabled", "unverified"}:
            return json_error("Only disabled or unverified can be set manually; active requires platform verification", 400)
        base.status = data["status"]
        changed["status"] = base.status
    if "max_concurrency" in data:
        value = data["max_concurrency"]
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 16:
            return json_error("max_concurrency must be 1..16", 400)
        shared.max_concurrency = value
        changed["max_concurrency"] = value
    try:
        for key in ("available_from", "expires_at"):
            if key in data:
                setattr(shared, key, _parse_datetime(data[key], nullable=True))
                changed[key] = iso_utc(getattr(shared, key))
    except (ValueError, TypeError):
        return json_error("Provide valid ISO 8601 availability times", 400)
    if shared.expires_at and shared.available_from and shared.expires_at <= shared.available_from:
        return json_error("Account expiry must be later than its start time", 400)
    if changed.get("is_open") is False or changed.get("status") == "disabled":
        cancel_account_tasks(db, account_id, "shared_account_disabled")
    _audit(db, "shared_account.updated", account_id, changed)
    db.commit()
    return jsonify({"shared_account": _shared_public(db, shared)})


@bp.post("/<account_id>/reauthorize")
@permission_required("shared_accounts.manage", csrf=True)
def reauthorize_shared_account(account_id: str):
    data = request.get_json(silent=True) or {}
    db = get_db()
    shared = _managed_shared_account(db, account_id, lock=True)
    if not shared:
        return json_error("Shared account not found", 404)
    base = db.get(PlatformAccount, account_id)
    platform = db.get(Platform, base.platform_id)
    auth_type, serialized, error = _credential_payload(data)
    if error:
        return json_error(*error)
    if platform.auth_methods and auth_type not in platform.auth_methods:
        return json_error("Authentication method is not listed for this platform", 400)
    _new_secret(db, account_id, g.current_user.id, auth_type, serialized)
    base.status = "unverified"
    _audit(db, "shared_account.reauthorized", account_id, {"auth_type": auth_type})
    db.commit()
    return jsonify({"ok": True, "status": "unverified", "verified": False})


@bp.post("/<account_id>/grants")
@permission_required("shared_accounts.manage", csrf=True)
def grant_shared_account(account_id: str):
    data = request.get_json(silent=True) or {}
    user_id = str(data.get("user_id", ""))
    scope = data.get("scope", {})
    if not isinstance(scope, dict) or set(scope) - {"task_kinds"}:
        return json_error("scope may only contain task_kinds", 400)
    kinds = scope.get("task_kinds", [])
    if not isinstance(kinds, list) or any(not isinstance(item, str) or len(item) > 48 for item in kinds):
        return json_error("scope.task_kinds must be a list of task names", 400)
    try:
        valid_from = _parse_datetime(data.get("valid_from"), nullable=True) or utcnow()
        expires_at = _parse_datetime(data.get("expires_at"), nullable=True)
    except (ValueError, TypeError):
        return json_error("Provide valid ISO 8601 grant times", 400)
    if expires_at and expires_at <= valid_from:
        return json_error("Grant expiry must be later than its start time", 400)

    db = get_db()
    target = db.scalar(select(User).where(User.id == user_id).with_for_update())
    shared = _managed_shared_account(db, account_id, lock=True)
    if not shared or not target:
        return json_error("Shared account or target user not found", 404)
    target_roles = user_roles(db, target.id)
    if target.status != "active" or "user" not in target_roles or any(role in target_roles for role in ("admin", "superadmin")):
        return json_error("Only active ordinary users can receive shared-account grants", 400)
    if not _is_superadmin(db, g.current_user.id):
        in_scope = db.scalar(select(AdminUserScope.user_id).where(
            AdminUserScope.admin_id == g.current_user.id,
            AdminUserScope.user_id == target.id,
        ))
        if not in_scope:
            return json_error("Target user is outside your administration scope", 404)
    now = utcnow()
    expired = db.scalars(select(AccountAccessGrant).where(
        AccountAccessGrant.shared_account_id == account_id,
        AccountAccessGrant.user_id == target.id,
        AccountAccessGrant.revoked_at.is_(None),
        AccountAccessGrant.expires_at.is_not(None),
        AccountAccessGrant.expires_at <= now,
    ).with_for_update()).all()
    for old_grant in expired:
        old_grant.revoked_at = now
    current = db.scalar(select(AccountAccessGrant).where(
        AccountAccessGrant.shared_account_id == account_id,
        AccountAccessGrant.user_id == target.id,
        AccountAccessGrant.revoked_at.is_(None),
        (AccountAccessGrant.expires_at.is_(None) | (AccountAccessGrant.expires_at > now)),
    ))
    if current:
        return json_error("An active grant already exists", 409)
    if shared.expires_at and valid_from >= shared.expires_at:
        return json_error("Grant must start before the shared account expires", 400)
    if shared.expires_at and (expires_at is None or expires_at > shared.expires_at):
        expires_at = shared.expires_at
    grant = AccountAccessGrant(
        shared_account_id=account_id, user_id=target.id, granted_by=g.current_user.id,
        valid_from=valid_from, expires_at=expires_at, scope=scope,
    )
    db.add(grant)
    _audit(db, "shared_account.grant_created", account_id, {"grant_id": grant.id}, target.id)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return json_error("An active grant already exists", 409)
    return jsonify({"grant_id": grant.id, "user_id": target.id, "expires_at": iso_utc(grant.expires_at)}), 201


@bp.post("/<account_id>/grants/<grant_id>/revoke")
@permission_required("shared_accounts.manage", csrf=True)
def revoke_shared_account_grant(account_id: str, grant_id: str):
    db = get_db()
    shared = _managed_shared_account(db, account_id, lock=True)
    grant = db.get(AccountAccessGrant, grant_id)
    if not shared or not grant or grant.shared_account_id != account_id:
        return json_error("Grant not found", 404)
    if grant.revoked_at is None:
        grant.revoked_at = utcnow()
        cancel_account_tasks(db, account_id, "shared_grant_revoked", user_id=grant.user_id, grant_id=grant.id)
        _audit(db, "shared_account.grant_revoked", account_id, {"grant_id": grant.id}, grant.user_id)
        db.commit()
    return jsonify({"ok": True})


@bp.get("/<account_id>/usage")
@permission_required("shared_accounts.manage")
def shared_account_usage(account_id: str):
    db = get_db()
    shared = _managed_shared_account(db, account_id)
    if not shared:
        return json_error("Shared account not found", 404)
    rows = db.scalars(select(AccountUsageLog).where(
        AccountUsageLog.shared_account_id == account_id,
    ).order_by(AccountUsageLog.created_at.desc()).limit(500)).all()
    return jsonify({"usage_logs": [{
        "id": row.id, "user_id": row.user_id,
        "user_email": (db.get(User, row.user_id).email if db.get(User, row.user_id) else ""),
        "grant_id": row.grant_id, "task_id": row.task_id,
        "event_type": row.event_type, "details": row.details,
        "created_at": iso_utc(row.created_at),
    } for row in rows]})
