from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from flask import Blueprint, current_app, g, jsonify, request
from sqlalchemy import func, or_, select

from unified_system.api.common import permission_required
from unified_system.db import get_db
from unified_system.models import (
    AccountAccessGrant, AdminUserScope, AuditLog, Invitation, InvitationUse, LoginLog, PasswordResetToken, UserSession,
    PersonalPlatformAccount, PlatformAccount, PlatformAccountSecret, Role, SharedPlatformAccount,
    Task, User, UserRole, WorkerInstance, Platform, utcnow,
)
from unified_system.security import can_manage_user, hash_token, iso_utc, json_error, user_public, revoke_user_sessions, has_permission
from unified_system.services.tasks import cancel_account_tasks, cancel_user_tasks, revoke_user_grants

bp = Blueprint("admin_api", __name__, url_prefix="/api/v1/admin")


def _audit(db, actor: str, action: str, entity_type: str, entity_id: str = "", target: str | None = None, metadata=None):
    db.add(AuditLog(
        actor_user_id=actor, target_user_id=target, action=action,
        entity_type=entity_type, entity_id=entity_id, metadata_json=metadata or {},
    ))


def _has_role(db, user_id: str, role_name: str) -> bool:
    return db.scalar(
        select(Role.id).join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user_id, Role.name == role_name)
    ) is not None


def _is_superadmin(db, user_id: str) -> bool:
    return _has_role(db, user_id, "superadmin")


def _visible_user_ids(db, actor: User):
    if _is_superadmin(db, actor.id):
        return None
    return select(AdminUserScope.user_id).where(AdminUserScope.admin_id == actor.id)


def _page_args(default_size=20):
    try:
        page = max(1, int(request.args.get("page", "1")))
        page_size = min(100, max(1, int(request.args.get("page_size", str(default_size)))))
    except ValueError:
        return None, None
    return page, page_size


@bp.get("/users")
@permission_required("users.read")
def list_users():
    db = get_db()
    page, page_size = _page_args()
    if page is None:
        return json_error("page and page_size must be integers", 400)
    stmt = select(User)
    visible_ids = _visible_user_ids(db, g.current_user)
    if visible_ids is not None:
        stmt = stmt.where(User.id.in_(visible_ids))
    query = request.args.get("q", "").strip()[:160]
    status = request.args.get("status", "").strip()
    role = request.args.get("role", "").strip()
    if query:
        pattern = f"%{query}%"
        stmt = stmt.where(or_(User.email.ilike(pattern), User.display_name.ilike(pattern)))
    if status:
        if status not in {"active", "disabled"}:
            return json_error("status must be active or disabled", 400)
        stmt = stmt.where(User.status == status)
    if role:
        if role not in {"user", "admin", "superadmin"}:
            return json_error("Unknown role filter", 400)
        stmt = stmt.where(User.id.in_(
            select(UserRole.user_id).join(Role, Role.id == UserRole.role_id).where(Role.name == role)
        ))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    users = db.scalars(stmt.order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return jsonify({
        "users": [user_public(db, item) for item in users],
        "pagination": {"page": page, "page_size": page_size, "total": total, "pages": (total + page_size - 1) // page_size},
    })


@bp.get("/dashboard")
@permission_required("users.read")
def dashboard_summary():
    db = get_db()
    db.execute(select(1))
    actor = g.current_user
    superadmin = _is_superadmin(db, actor.id)
    scoped = db.scalars(select(AdminUserScope.user_id).where(AdminUserScope.admin_id == actor.id)).all()
    visible_user_ids = None if superadmin else list(set(scoped + [actor.id]))

    user_stmt = select(User)
    if visible_user_ids is not None:
        user_stmt = user_stmt.where(User.id.in_(visible_user_ids))
    user_rows = db.scalars(user_stmt).all()
    users = {"total": len(user_rows), "active": sum(row.status == "active" for row in user_rows),
             "disabled": sum(row.status == "disabled" for row in user_rows)}

    if superadmin:
        account_rows = db.execute(
            select(Platform.key, Platform.name, func.count(PlatformAccount.id))
            .outerjoin(PlatformAccount, PlatformAccount.platform_id == Platform.id)
            .group_by(Platform.key, Platform.name).order_by(Platform.name)
        ).all()
    else:
        personal_counts = db.execute(
            select(Platform.key, Platform.name, func.count(PersonalPlatformAccount.account_id))
            .join(PlatformAccount, PlatformAccount.id == PersonalPlatformAccount.account_id)
            .join(Platform, Platform.id == PlatformAccount.platform_id)
            .where(PersonalPlatformAccount.owner_user_id == actor.id)
            .group_by(Platform.key, Platform.name)
        ).all()
        shared_counts = db.execute(
            select(Platform.key, Platform.name, func.count(SharedPlatformAccount.account_id))
            .join(PlatformAccount, PlatformAccount.id == SharedPlatformAccount.account_id)
            .join(Platform, Platform.id == PlatformAccount.platform_id)
            .where(SharedPlatformAccount.created_by == actor.id)
            .group_by(Platform.key, Platform.name)
        ).all()
        merged = {}
        for key, name, count in list(personal_counts) + list(shared_counts):
            if key not in merged:
                merged[key] = [name, 0]
            merged[key][1] += int(count or 0)
        account_rows = [(key, value[0], value[1]) for key, value in merged.items()]

    shared_stmt = select(SharedPlatformAccount)
    if not superadmin:
        shared_stmt = shared_stmt.where(SharedPlatformAccount.created_by == actor.id)
    shared_rows = db.scalars(shared_stmt).all()
    shared_summary = {"total": len(shared_rows), "open": sum(row.is_open for row in shared_rows),
                      "closed": sum(not row.is_open for row in shared_rows)}

    invitation_summary = None
    if has_permission(db, actor.id, "invitations.manage"):
        invites = db.scalars(select(Invitation)).all()
        now = utcnow()
        invitation_summary = {
            "total": len(invites),
            "usable": sum(row.revoked_at is None and row.expires_at > now and row.used_count < row.max_uses for row in invites),
            "exhausted": sum(row.revoked_at is None and row.used_count >= row.max_uses for row in invites),
            "revoked": sum(row.revoked_at is not None for row in invites),
        }

    task_filter = [] if visible_user_ids is None else [Task.user_id.in_(visible_user_ids)]
    task_status_rows = db.execute(select(Task.status, func.count(Task.id)).where(*task_filter).group_by(Task.status)).all()
    task_statuses = {status: 0 for status in ("queued", "leased", "running", "cancel_requested", "succeeded", "failed", "cancelled", "timed_out")}
    task_statuses.update({status: int(count) for status, count in task_status_rows})
    account_expr = func.coalesce(Task.personal_account_id, Task.shared_account_id)
    current_rows = db.execute(
        select(Platform.key, func.count(Task.id))
        .join(PlatformAccount, PlatformAccount.id == account_expr)
        .join(Platform, Platform.id == PlatformAccount.platform_id)
        .where(*task_filter, Task.status.in_(["leased", "running", "cancel_requested"]))
        .group_by(Platform.key)
    ).all()
    current_by_platform = {key: int(count) for key, count in current_rows}
    failed_rows = db.execute(
        select(Task, Platform.key)
        .join(PlatformAccount, PlatformAccount.id == account_expr)
        .join(Platform, Platform.id == PlatformAccount.platform_id)
        .where(*task_filter, Task.status.in_(["failed", "timed_out"]))
        .order_by(func.coalesce(Task.finished_at, Task.created_at).desc())
        .limit(8)
    ).all()
    recent_errors = [{
        "task_id": task.id, "platform_key": key, "worker_id": task.worker_id,
        "error_code": task.error_code or task.status,
        "occurred_at": iso_utc(task.finished_at or task.created_at),
    } for task, key in failed_rows]

    worker_summary = None
    if has_permission(db, actor.id, "platforms.manage"):
        cutoff = utcnow() - timedelta(seconds=current_app.config["WORKER_HEARTBEAT_TTL_SECONDS"])
        worker_rows = db.scalars(select(WorkerInstance)).all()
        worker_summary = {"total": len(worker_rows),
                          "online": sum(bool(row.last_seen_at and row.last_seen_at >= cutoff) for row in worker_rows),
                          "offline": sum(not row.last_seen_at or row.last_seen_at < cutoff for row in worker_rows)}

    audit_stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(8)
    if visible_user_ids is not None:
        audit_stmt = audit_stmt.where(or_(AuditLog.actor_user_id.in_(visible_user_ids), AuditLog.target_user_id.in_(visible_user_ids)))
    logs = db.scalars(audit_stmt).all()
    recent_operations = [{
        "id": row.id, "actor_user_id": row.actor_user_id, "target_user_id": row.target_user_id,
        "action": row.action, "entity_type": row.entity_type, "entity_id": row.entity_id,
        "created_at": iso_utc(row.created_at),
    } for row in logs]
    platforms = [{"key": key, "name": name, "account_count": int(count or 0)} for key, name, count in account_rows]

    return jsonify({
        "scope": "global" if superadmin else "managed users, own accounts and scoped tasks",
        "service": {"api": "ok", "database": "ok"},
        "users": users,
        "invitations": invitation_summary,
        "shared_accounts": shared_summary,
        "platform_accounts": platforms,
        "task_statuses": task_statuses,
        "current_tasks_by_platform": current_by_platform,
        "workers": worker_summary,
        "recent_task_errors": recent_errors,
        "recent_operations": recent_operations,
    })


@bp.patch("/users/<user_id>/status")
@permission_required("users.manage", csrf=True)
def update_user_status(user_id: str):
    data = request.get_json(silent=True) or {}
    status = data.get("status")
    if status not in ("active", "disabled"):
        return json_error("Status must be active or disabled", 400)
    db = get_db()
    target = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if not target or not can_manage_user(db, g.current_user, user_id):
        return json_error("User not found", 404)
    if _has_role(db, target.id, "superadmin"):
        return json_error("Superadmin accounts require the offline recovery procedure", 403)
    if target.id == g.current_user.id:
        return json_error("You cannot disable your own account", 400)
    target.status = status
    revoke_user_sessions(db, target.id)
    target.auth_version += 1
    if status == "disabled":
        if _has_role(db, target.id, "admin"):
            db.query(AdminUserScope).filter(AdminUserScope.admin_id == target.id).delete(synchronize_session=False)
        revoke_user_grants(db, target.id, g.current_user.id)
        cancel_user_tasks(db, target.id)
        personal = db.scalars(select(PersonalPlatformAccount).where(PersonalPlatformAccount.owner_user_id == target.id)).all()
        for owned in personal:
            base = db.get(PlatformAccount, owned.account_id)
            if base:
                base.status = "disabled"
                for secret in db.scalars(select(PlatformAccountSecret).where(
                    PlatformAccountSecret.account_id == base.id,
                    PlatformAccountSecret.status == "active",
                )).all():
                    secret.status = "revoked"
                    secret.revoked_at = utcnow()
                cancel_account_tasks(db, base.id, "user_disabled", user_id=target.id)
        shared_accounts = db.scalars(select(SharedPlatformAccount).where(SharedPlatformAccount.created_by == target.id)).all()
        for shared in shared_accounts:
            shared.is_open = False
            base = db.get(PlatformAccount, shared.account_id)
            if base:
                base.status = "disabled"
                cancel_account_tasks(db, base.id, "account_owner_disabled")
    _audit(db, g.current_user.id, f"user.{status}", "user", target.id, target.id)
    db.commit()
    return jsonify({"user": user_public(db, target)})


@bp.post("/users/<user_id>/sessions/revoke")
@permission_required("users.manage", csrf=True)
def revoke_user_browser_sessions(user_id: str):
    db = get_db()
    target = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if not target or not can_manage_user(db, g.current_user, user_id):
        return json_error("User not found", 404)
    if _has_role(db, target.id, "superadmin"):
        return json_error("Superadmin sessions require the offline recovery procedure", 403)
    active_count = db.scalar(select(func.count(UserSession.id)).where(
        UserSession.user_id == target.id, UserSession.revoked_at.is_(None),
        UserSession.auth_version == target.auth_version, UserSession.expires_at > utcnow(),
    )) or 0
    revoke_user_sessions(db, target.id)
    target.auth_version += 1  # also invalidates legacy phase-two cookies without a server-side row
    _audit(db, g.current_user.id, "user.sessions_revoked", "user", target.id, target.id, {"count": int(active_count)})
    db.commit()
    return jsonify({"ok": True, "revoked_count": int(active_count)})


@bp.post("/users/<user_id>/reset-link")
@permission_required("users.manage", csrf=True)
def issue_password_reset(user_id: str):
    db = get_db()
    target = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if not target or target.status != "active" or not can_manage_user(db, g.current_user, user_id):
        return json_error("User not found", 404)
    if _has_role(db, target.id, "superadmin"):
        return json_error("Superadmin resets require the offline recovery procedure", 403)
    token = secrets.token_urlsafe(32)
    now = utcnow()
    expires = now + timedelta(minutes=current_app.config["RESET_TOKEN_TTL_MINUTES"])
    for previous in db.scalars(select(PasswordResetToken).where(
        PasswordResetToken.user_id == target.id,
        PasswordResetToken.used_at.is_(None),
    )).all():
        previous.used_at = now
    reset = PasswordResetToken(
        user_id=target.id, token_hash=hash_token(token), created_by=g.current_user.id,
        expires_at=expires,
    )
    db.add(reset)
    _audit(db, g.current_user.id, "user.password_reset_link_issued", "user", target.id, target.id)
    db.commit()
    # The raw token is returned once and is never stored. Delivery by email is a later integration.
    return jsonify({"reset_token": token, "expires_at": iso_utc(expires), "delivery": "admin-assisted; share securely once"}), 201


@bp.post("/users/<user_id>/roles")
@permission_required("users.manage", csrf=True)
def set_user_roles(user_id: str):
    data = request.get_json(silent=True) or {}
    requested = data.get("roles")
    if not isinstance(requested, list) or not requested or any(r not in {"user", "admin"} for r in requested):
        return json_error("Only user/admin roles can be assigned through this endpoint", 400)
    db = get_db()
    if "superadmin" not in [r.name for r in db.scalars(
        select(Role).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == g.current_user.id)
    ).all()]:
        return json_error("Only a superadmin can assign roles", 403)
    target = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if not target:
        return json_error("User not found", 404)
    if _has_role(db, target.id, "superadmin"):
        return json_error("Superadmin roles require the offline recovery procedure", 403)
    was_admin = _has_role(db, target.id, "admin")
    db.query(UserRole).filter(UserRole.user_id == target.id).delete(synchronize_session=False)
    for role_name in set(requested):
        role = db.scalar(select(Role).where(Role.name == role_name))
        db.add(UserRole(user_id=target.id, role_id=role.id))
    if "admin" not in requested:
        db.query(AdminUserScope).filter(AdminUserScope.admin_id == target.id).delete(synchronize_session=False)
        if was_admin:
            shared_accounts = db.scalars(select(SharedPlatformAccount).where(SharedPlatformAccount.created_by == target.id)).all()
            for shared in shared_accounts:
                shared.is_open = False
                base = db.get(PlatformAccount, shared.account_id)
                if base:
                    base.status = "disabled"
                grants = db.scalars(select(AccountAccessGrant).where(
                    AccountAccessGrant.shared_account_id == shared.account_id,
                    AccountAccessGrant.revoked_at.is_(None),
                )).all()
                for grant in grants:
                    grant.revoked_at = utcnow()
                cancel_account_tasks(db, shared.account_id, "account_manager_role_removed")
    revoke_user_sessions(db, target.id)
    target.auth_version += 1
    _audit(db, g.current_user.id, "user.roles_updated", "user", target.id, target.id, {"roles": sorted(set(requested))})
    db.commit()
    return jsonify({"user": user_public(db, target)})


@bp.post("/scopes")
@permission_required("users.manage", csrf=True)
def grant_scope():
    data = request.get_json(silent=True) or {}
    admin_id, user_id = str(data.get("admin_id", "")), str(data.get("user_id", ""))
    db = get_db()
    if "superadmin" not in [r.name for r in db.scalars(
        select(Role).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == g.current_user.id)
    ).all()]:
        return json_error("Only a superadmin can assign admin scopes", 403)
    admin, target = db.get(User, admin_id), db.get(User, user_id)
    if not admin or not target or admin.id == target.id:
        return json_error("Admin or target user not found", 404)
    roles = db.scalars(select(Role.name).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == admin.id)).all()
    target_roles = db.scalars(select(Role.name).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == target.id)).all()
    if "admin" not in roles or admin.status != "active":
        return json_error("Target manager must be an active admin", 400)
    if any(role in target_roles for role in ("admin", "superadmin")):
        return json_error("Admin scopes can only be granted to ordinary users", 400)
    if db.get(AdminUserScope, (admin_id, user_id)) is None:
        db.add(AdminUserScope(admin_id=admin_id, user_id=user_id))
    _audit(db, g.current_user.id, "admin.scope_granted", "user", user_id, user_id, {"admin_id": admin_id})
    db.commit()
    return jsonify({"ok": True}), 201


@bp.get("/scopes")
@permission_required("users.manage")
def list_scopes():
    db = get_db()
    roles = db.scalars(select(Role.name).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == g.current_user.id)).all()
    if "superadmin" not in roles:
        return json_error("Only a superadmin can view global admin scopes", 403)
    rows = db.scalars(select(AdminUserScope).order_by(AdminUserScope.created_at.desc())).all()
    result = []
    for row in rows:
        admin, target = db.get(User, row.admin_id), db.get(User, row.user_id)
        result.append({
            "admin_id": row.admin_id, "admin_email": admin.email if admin else "",
            "user_id": row.user_id, "user_email": target.email if target else "",
        })
    return jsonify({"scopes": result})


@bp.delete("/scopes/<admin_id>/<user_id>")
@permission_required("users.manage", csrf=True)
def revoke_scope(admin_id: str, user_id: str):
    db = get_db()
    roles = db.scalars(select(Role.name).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == g.current_user.id)).all()
    if "superadmin" not in roles:
        return json_error("Only a superadmin can revoke admin scopes", 403)
    scope = db.get(AdminUserScope, (admin_id, user_id))
    if not scope:
        return json_error("Scope not found", 404)
    db.delete(scope)
    _audit(db, g.current_user.id, "admin.scope_revoked", "user", user_id, user_id, {"admin_id": admin_id})
    db.commit()
    return jsonify({"ok": True})


@bp.get("/invitations")
@permission_required("invitations.manage")
def list_invitations():
    db = get_db()
    rows = db.scalars(select(Invitation).order_by(Invitation.created_at.desc()).limit(500)).all()
    return jsonify({"invitations": [{
        "id": row.id, "code_hint": row.code_hint, "created_at": iso_utc(row.created_at),
        "created_by": row.created_by,
        "created_by_email": (db.get(User, row.created_by).email if db.get(User, row.created_by) else ""),
        "expires_at": iso_utc(row.expires_at), "max_uses": row.max_uses,
        "used_count": row.used_count, "revoked": row.revoked_at is not None,
        "note": row.note,
    } for row in rows]})


@bp.get("/invitations/<invitation_id>/uses")
@permission_required("invitations.manage")
def list_invitation_uses(invitation_id: str):
    db = get_db()
    if not db.get(Invitation, invitation_id):
        return json_error("Invitation not found", 404)
    rows = db.scalars(select(InvitationUse).where(
        InvitationUse.invitation_id == invitation_id,
    ).order_by(InvitationUse.used_at.desc()).limit(1000)).all()
    return jsonify({"uses": [{
        "user_id": row.user_id,
        "user_email": (db.get(User, row.user_id).email if db.get(User, row.user_id) else ""),
        "used_at": iso_utc(row.used_at),
    } for row in rows]})


@bp.post("/invitations")
@permission_required("invitations.manage", csrf=True)
def create_invitations():
    data = request.get_json(silent=True) or {}
    count = data.get("count", 1)
    uses = data.get("max_uses", 1)
    ttl_days = data.get("ttl_days", 7)
    if any(not isinstance(v, int) or isinstance(v, bool) for v in (count, uses, ttl_days)):
        return json_error("count, max_uses and ttl_days must be integers", 400)
    if not (1 <= count <= 100 and 1 <= uses <= 1000 and 1 <= ttl_days <= current_app.config["INVITATION_MAX_TTL_DAYS"]):
        return json_error("Invitation limits are out of range", 400)
    note = str(data.get("note", "")).strip()[:200]
    db = get_db()
    created = []
    expires = utcnow() + timedelta(days=ttl_days)
    for _ in range(count):
        code = secrets.token_urlsafe(24)
        invitation = Invitation(
            code_hash=hash_token(code), code_hint=code[-8:], created_by=g.current_user.id,
            expires_at=expires, max_uses=uses, note=note,
        )
        db.add(invitation)
        created.append({"code": code, "expires_at": iso_utc(expires), "max_uses": uses})
    _audit(db, g.current_user.id, "invitations.created", "invitation", metadata={"count": count, "max_uses": uses})
    db.commit()
    return jsonify({"invitations": created, "shown_once": True}), 201


@bp.post("/invitations/<invitation_id>/disable")
@permission_required("invitations.manage", csrf=True)
def disable_invitation(invitation_id: str):
    db = get_db()
    invitation = db.get(Invitation, invitation_id)
    if not invitation:
        return json_error("Invitation not found", 404)
    if invitation.revoked_at is None:
        invitation.revoked_at = utcnow()
        _audit(db, g.current_user.id, "invitation.disabled", "invitation", invitation.id)
        db.commit()
    return jsonify({"ok": True})


@bp.get("/login-logs")
@permission_required("logs.read")
def list_login_logs():
    db = get_db()
    roles = db.scalars(select(Role.name).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == g.current_user.id)).all()
    stmt = select(LoginLog).order_by(LoginLog.created_at.desc()).limit(200)
    if "superadmin" not in roles:
        ids = db.scalars(select(AdminUserScope.user_id).where(AdminUserScope.admin_id == g.current_user.id)).all()
        visible = list(set(ids + [g.current_user.id]))
        stmt = stmt.where(LoginLog.user_id.in_(visible))
    rows = db.scalars(stmt).all()
    return jsonify({"login_logs": [{
        "id": row.id, "user_id": row.user_id, "identifier": row.identifier,
        "result": row.result, "user_agent": row.user_agent,
        "created_at": iso_utc(row.created_at),
    } for row in rows]})


@bp.get("/platform-workers")
@permission_required("platforms.manage")
def list_platform_workers():
    db = get_db()
    cutoff = utcnow() - timedelta(seconds=current_app.config["WORKER_HEARTBEAT_TTL_SECONDS"])
    rows = db.scalars(select(WorkerInstance).order_by(WorkerInstance.platform_id, WorkerInstance.worker_id)).all()
    result = []
    for row in rows:
        platform = db.get(Platform, row.platform_id)
        online = bool(row.last_seen_at and row.last_seen_at >= cutoff)
        result.append({
            "worker_id": row.worker_id, "platform_key": platform.key if platform else "unknown",
            "status": "online" if online else "offline", "version": row.version,
            "capabilities": row.capabilities, "last_seen_at": iso_utc(row.last_seen_at),
        })
    return jsonify({"workers": result})


@bp.get("/audit-logs")
@permission_required("logs.read")
def list_audit_logs():
    db = get_db()
    page, page_size = _page_args(default_size=25)
    if page is None:
        return json_error("page and page_size must be integers", 400)
    stmt = select(AuditLog)
    if not _is_superadmin(db, g.current_user.id):
        ids = db.scalars(select(AdminUserScope.user_id).where(AdminUserScope.admin_id == g.current_user.id)).all()
        visible = list(set(ids + [g.current_user.id]))
        stmt = stmt.where(or_(AuditLog.actor_user_id.in_(visible), AuditLog.target_user_id.in_(visible)))
    query = request.args.get("q", "").strip()[:160]
    action = request.args.get("action", "").strip()[:80]
    entity_type = request.args.get("entity_type", "").strip()[:48]
    if query:
        pattern = f"%{query}%"
        stmt = stmt.where(or_(AuditLog.action.ilike(pattern), AuditLog.entity_type.ilike(pattern), AuditLog.entity_id.ilike(pattern)))
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    try:
        if request.args.get("from"):
            start = datetime.fromisoformat(request.args["from"].replace("Z", "+00:00"))
            if start.tzinfo:
                start = start.astimezone(timezone.utc).replace(tzinfo=None)
            stmt = stmt.where(AuditLog.created_at >= start)
        if request.args.get("to"):
            end = datetime.fromisoformat(request.args["to"].replace("Z", "+00:00"))
            if end.tzinfo:
                end = end.astimezone(timezone.utc).replace(tzinfo=None)
            stmt = stmt.where(AuditLog.created_at <= end)
    except ValueError:
        return json_error("from and to must be ISO 8601 timestamps", 400)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(AuditLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return jsonify({
        "logs": [{
            "id": row.id, "actor_user_id": row.actor_user_id,
            "actor_email": (db.get(User, row.actor_user_id).email if row.actor_user_id and db.get(User, row.actor_user_id) else "system"),
            "target_user_id": row.target_user_id,
            "target_email": (db.get(User, row.target_user_id).email if row.target_user_id and db.get(User, row.target_user_id) else "—"),
            "action": row.action, "entity_type": row.entity_type, "entity_id": row.entity_id,
            "metadata": row.metadata_json, "created_at": iso_utc(row.created_at),
        } for row in rows],
        "pagination": {"page": page, "page_size": page_size, "total": total, "pages": (total + page_size - 1) // page_size},
    })
