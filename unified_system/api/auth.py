from __future__ import annotations

import secrets
from datetime import timedelta

from flask import Blueprint, current_app, g, jsonify, request, session
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from unified_system.api.common import csrf_required, login_required
from unified_system.db import get_db
from unified_system.models import (
    AuditLog, Invitation, InvitationUse, LoginLog, PasswordResetToken, Role, User, UserRole, UserSession, utcnow,
)
from unified_system.security import (
    hash_password, hash_token, iso_utc, json_error, new_csrf_token, normalize_email,
    request_fingerprint, user_permissions, user_public, user_roles, valid_email,
    validate_password, verify_password, ensure_current_session_record, revoke_user_sessions,
)

bp = Blueprint("auth_api", __name__, url_prefix="/api/v1/auth")


def _record_login(db, identifier: str, user: User | None, result: str) -> None:
    db.add(LoginLog(
        user_id=user.id if user else None,
        identifier=identifier[:320],
        remote_fingerprint=request_fingerprint(),
        result=result,
        user_agent=(request.headers.get("User-Agent", "") or "")[:256],
    ))
    db.commit()


def _locked(db, email: str) -> bool:
    cutoff = utcnow() - timedelta(seconds=current_app.config["LOGIN_FAILURE_WINDOW_SECONDS"])
    count = db.scalar(select(func.count(LoginLog.id)).where(
        LoginLog.identifier == email,
        LoginLog.remote_fingerprint == request_fingerprint(),
        LoginLog.result == "failure",
        LoginLog.created_at >= cutoff,
    )) or 0
    return count >= current_app.config["LOGIN_FAILURE_LIMIT"]


def _set_login_session(db, user: User) -> dict:
    session.clear()
    session.permanent = True
    session["uid"] = user.id
    session["av"] = user.auth_version
    session["csrf"] = new_csrf_token()
    sid = secrets.token_urlsafe(32)
    session["sid"] = sid
    now = utcnow()
    db.add(UserSession(
        user_id=user.id, token_hash=hash_token(sid), auth_version=user.auth_version,
        created_at=now,
        expires_at=now + timedelta(seconds=int(current_app.config["PERMANENT_SESSION_LIFETIME"])),
        last_seen_at=now, remote_fingerprint=request_fingerprint(),
        user_agent=(request.headers.get("User-Agent", "") or "")[:256],
    ))
    user.last_login_at = now
    db.commit()
    return {"user": user_public(db, user), "csrf": session["csrf"]}


@bp.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    email = normalize_email(data.get("email", ""))
    password = data.get("password", "")
    if not valid_email(email) or not isinstance(password, str):
        return json_error("Invalid email or password", 400)
    db = get_db()
    if _locked(db, email):
        _record_login(db, email, None, "throttled")
        return json_error("Too many failed attempts. Try again later.", 429)
    user = db.scalar(select(User).where(User.email == email))
    if not user or user.status != "active" or not verify_password(password, user.password_hash):
        _record_login(db, email, user, "failure")
        return json_error("Invalid email or password", 401)
    _record_login(db, email, user, "success")
    return jsonify(_set_login_session(db, user))


@bp.post("/register")
def register():
    data = request.get_json(silent=True) or {}
    email = normalize_email(data.get("email", ""))
    display_name = str(data.get("display_name", "")).strip()[:120]
    password = data.get("password", "")
    code = str(data.get("invitation_code", "")).strip()
    if not valid_email(email):
        return json_error("Enter a valid email address", 400)
    password_error = validate_password(password)
    if password_error:
        return json_error(password_error, 400)
    if len(code) < 16 or len(code) > 128:
        return json_error("A valid invitation code is required", 400)

    db = get_db()
    try:
        invitation = db.scalar(select(Invitation).where(Invitation.code_hash == hash_token(code)).with_for_update())
        now = utcnow()
        if (not invitation or invitation.revoked_at or invitation.expires_at <= now
                or invitation.used_count >= invitation.max_uses):
            return json_error("Invitation is invalid, expired, revoked, or fully used", 400)
        if db.scalar(select(User.id).where(User.email == email)):
            return json_error("Unable to register with these details", 409)
        consumed = db.execute(update(Invitation).where(
            Invitation.id == invitation.id,
            Invitation.revoked_at.is_(None),
            Invitation.expires_at > now,
            Invitation.used_count < Invitation.max_uses,
        ).values(used_count=Invitation.used_count + 1))
        if consumed.rowcount != 1:
            return json_error("Invitation is invalid, expired, revoked, or fully used", 400)
        user = User(email=email, display_name=display_name, password_hash=hash_password(password))
        db.add(user)
        db.flush()
        role = db.scalar(select(Role).where(Role.name == "user"))
        if role is None:
            return json_error("User role is not initialized", 503)
        db.add(UserRole(user_id=user.id, role_id=role.id))
        db.add(InvitationUse(invitation_id=invitation.id, user_id=user.id))
        db.add(AuditLog(
            actor_user_id=user.id, target_user_id=user.id, action="user.registered",
            entity_type="user", entity_id=user.id,
            metadata_json={"invitation_id": invitation.id},
        ))
        db.commit()
    except IntegrityError:
        db.rollback()
        return json_error("Unable to register with these details", 409)
    except Exception:
        db.rollback()
        raise
    return jsonify(_set_login_session(db, user)), 201


@bp.post("/logout")
@csrf_required
def logout():
    sid = session.get("sid")
    if sid:
        db = get_db()
        row = db.scalar(select(UserSession).where(
            UserSession.user_id == g.current_user.id,
            UserSession.token_hash == hash_token(sid),
            UserSession.revoked_at.is_(None),
        ))
        if row:
            row.revoked_at = utcnow()
            db.commit()
    session.clear()
    return jsonify({"ok": True})


@bp.get("/me")
@login_required
def me():
    db = get_db()
    return jsonify({
        "user": user_public(db, g.current_user),
        "roles": user_roles(db, g.current_user.id),
        "permissions": user_permissions(db, g.current_user.id),
        "csrf": session.get("csrf", ""),
    })


@bp.get("/sessions")
@login_required
def list_sessions():
    db = get_db()
    current = ensure_current_session_record(db, g.current_user)
    now = utcnow()
    rows = db.scalars(select(UserSession).where(
        UserSession.user_id == g.current_user.id,
        UserSession.auth_version == g.current_user.auth_version,
        UserSession.revoked_at.is_(None),
        UserSession.expires_at > now,
    ).order_by(UserSession.created_at.desc())).all()
    return jsonify({"sessions": [{
        "id": row.id,
        "created_at": iso_utc(row.created_at),
        "last_seen_at": iso_utc(row.last_seen_at),
        "expires_at": iso_utc(row.expires_at),
        "user_agent": row.user_agent,
        "current": row.id == current.id,
    } for row in rows]})


@bp.post("/sessions/<session_id>/revoke")
@csrf_required
def revoke_session(session_id: str):
    db = get_db()
    row = db.scalar(select(UserSession).where(
        UserSession.id == session_id,
        UserSession.user_id == g.current_user.id,
        UserSession.revoked_at.is_(None),
    ).with_for_update())
    if not row:
        return json_error("Session not found", 404)
    is_current = session.get("sid") and row.token_hash == hash_token(session.get("sid", ""))
    row.revoked_at = utcnow()
    db.commit()
    if is_current:
        session.clear()
    return jsonify({"ok": True, "current_session_revoked": bool(is_current)})


@bp.post("/sessions/revoke-others")
@csrf_required
def revoke_other_sessions():
    db = get_db()
    current = ensure_current_session_record(db, g.current_user)
    rows = db.scalars(select(UserSession).where(
        UserSession.user_id == g.current_user.id,
        UserSession.id != current.id,
        UserSession.revoked_at.is_(None),
    ).with_for_update()).all()
    now = utcnow()
    for row in rows:
        row.revoked_at = now
    db.commit()
    return jsonify({"ok": True, "revoked_count": len(rows)})


@bp.patch("/profile")
@csrf_required
def profile_update():
    data = request.get_json(silent=True) or {}
    name = str(data.get("display_name", "")).strip()
    if not name or len(name) > 120:
        return json_error("Display name must be between 1 and 120 characters", 400)
    db = get_db()
    g.current_user.display_name = name
    db.commit()
    return jsonify({"user": user_public(db, g.current_user)})


@bp.post("/password")
@csrf_required
def change_password():
    data = request.get_json(silent=True) or {}
    old = data.get("current_password", "")
    new = data.get("new_password", "")
    password_error = validate_password(new)
    if password_error:
        return json_error(password_error, 400)
    if old == new:
        return json_error("New password must differ from current password", 400)
    db = get_db()
    user = db.scalar(select(User).where(User.id == g.current_user.id).with_for_update())
    if not user or user.status != "active" or not verify_password(old, user.password_hash):
        return json_error("Current password is incorrect", 400)
    user.password_hash = hash_password(new)
    revoke_user_sessions(db, user.id)
    user.auth_version += 1
    db.commit()
    session.clear()
    return jsonify({"ok": True, "message": "Password updated; sign in again"})


@bp.post("/reset-password")
def reset_password():
    data = request.get_json(silent=True) or {}
    token = str(data.get("token", "")).strip()
    new_password = data.get("new_password", "")
    password_error = validate_password(new_password)
    if password_error:
        return json_error(password_error, 400)
    if len(token) < 24 or len(token) > 256:
        return json_error("Reset link is invalid or expired", 400)
    db = get_db()
    reset_hint = db.scalar(select(PasswordResetToken).where(
        PasswordResetToken.token_hash == hash_token(token)
    ))
    if not reset_hint:
        return json_error("Reset link is invalid or expired", 400)
    user = db.scalar(select(User).where(User.id == reset_hint.user_id).with_for_update())
    reset = db.scalar(select(PasswordResetToken).where(
        PasswordResetToken.id == reset_hint.id,
        PasswordResetToken.token_hash == hash_token(token),
    ).with_for_update())
    now = utcnow()
    if not reset or reset.used_at or reset.expires_at <= now or not user or user.status != "active":
        return json_error("Reset link is invalid or expired", 400)
    consumed = db.execute(update(PasswordResetToken).where(
        PasswordResetToken.id == reset.id,
        PasswordResetToken.used_at.is_(None),
        PasswordResetToken.expires_at > now,
    ).values(used_at=now))
    if consumed.rowcount != 1:
        return json_error("Reset link is invalid or expired", 400)
    user.password_hash = hash_password(new_password)
    revoke_user_sessions(db, user.id)
    user.auth_version += 1
    db.commit()
    return jsonify({"ok": True, "message": "Password reset; sign in with the new password"})
