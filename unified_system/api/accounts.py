from __future__ import annotations

import json

from flask import Blueprint, g, jsonify, request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from unified_system.api.common import permission_required
from unified_system.db import get_db
from unified_system.models import (
    AccountAccessGrant, AuditLog, PersonalPlatformAccount, Platform, PlatformAccount,
    PlatformAccountSecret, SharedPlatformAccount, utcnow,
)
from unified_system.security import encrypt_secret, iso_utc, json_error, mask_account_hint
from unified_system.services.tasks import cancel_account_tasks

bp = Blueprint("accounts_api", __name__, url_prefix="/api/v1")


def _credential_payload(data):
    credentials = data.get("credentials")
    auth_type = str(data.get("auth_type", "")).strip()
    if not isinstance(credentials, dict) or not credentials or not auth_type or len(auth_type) > 32:
        return None, None, ("Provide credentials and a valid auth_type", 400)
    try:
        serialized = json.dumps(credentials, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return None, None, ("Credentials must be a JSON object", 400)
    if len(serialized.encode("utf-8")) > 16_384:
        return None, None, ("Credential payload is too large", 413)
    return auth_type, serialized, None


def _new_secret(db, account_id: str, created_by: str | None, auth_type: str, serialized: str):
    existing = db.scalars(select(PlatformAccountSecret).where(
        PlatformAccountSecret.account_id == account_id,
        PlatformAccountSecret.status == "active",
    ).with_for_update()).all()
    now = utcnow()
    for secret in existing:
        secret.status = "revoked"
        secret.revoked_at = now
    latest = db.scalar(select(PlatformAccountSecret.version).where(
        PlatformAccountSecret.account_id == account_id,
    ).order_by(PlatformAccountSecret.version.desc()).limit(1)) or 0
    secret = PlatformAccountSecret(
        account_id=account_id, created_by=created_by, auth_type=auth_type,
        ciphertext=encrypt_secret(serialized), version=latest + 1, status="active",
    )
    db.add(secret)
    return secret


def _base_account(account: PlatformAccount, platform: Platform, mode: str, hint: str, **extra):
    return {
        "id": account.id, "platform_key": platform.key, "platform_name": platform.name,
        "label": account.label, "account_hint": hint, "status": account.status,
        "account_mode": mode, "created_at": iso_utc(account.created_at),
        "updated_at": iso_utc(account.updated_at),
        "platform_last_verified_at": iso_utc(platform.last_verified_at), **extra,
    }


def _authorized_shared_accounts(db, user_id: str):
    now = utcnow()
    rows = db.execute(
        select(AccountAccessGrant, SharedPlatformAccount, PlatformAccount, Platform)
        .join(SharedPlatformAccount, SharedPlatformAccount.account_id == AccountAccessGrant.shared_account_id)
        .join(PlatformAccount, PlatformAccount.id == SharedPlatformAccount.account_id)
        .join(Platform, Platform.id == PlatformAccount.platform_id)
        .where(
            AccountAccessGrant.user_id == user_id,
            AccountAccessGrant.revoked_at.is_(None),
            AccountAccessGrant.valid_from <= now,
            (AccountAccessGrant.expires_at.is_(None) | (AccountAccessGrant.expires_at > now)),
            SharedPlatformAccount.is_open.is_(True),
            (SharedPlatformAccount.available_from.is_(None) | (SharedPlatformAccount.available_from <= now)),
            (SharedPlatformAccount.expires_at.is_(None) | (SharedPlatformAccount.expires_at > now)),
            PlatformAccount.status.notin_(["disabled", "expired"]),
        ).order_by(SharedPlatformAccount.created_at.desc())
    ).all()
    result = []
    for grant, shared, base, platform in rows:
        result.append(_base_account(
            base, platform, "shared", shared.account_hint,
            grant_id=grant.id,
            grant_expires_at=iso_utc(grant.expires_at),
            access_scope=grant.scope,
            available=bool(base.status == "active" and platform.integration_status == "active"),
        ))
    return result


@bp.get("/platform-accounts")
@permission_required("platform.accounts.manage")
def list_platform_accounts():
    db = get_db()
    personal_rows = db.execute(
        select(PersonalPlatformAccount, PlatformAccount, Platform)
        .join(PlatformAccount, PlatformAccount.id == PersonalPlatformAccount.account_id)
        .join(Platform, Platform.id == PlatformAccount.platform_id)
        .where(PersonalPlatformAccount.owner_user_id == g.current_user.id)
        .order_by(PersonalPlatformAccount.created_at.desc())
    ).all()
    personal = [_base_account(base, platform, "personal", owner.account_hint) for owner, base, platform in personal_rows]
    return jsonify({"personal_accounts": personal, "shared_accounts": _authorized_shared_accounts(db, g.current_user.id)})


@bp.post("/personal-accounts")
@permission_required("platform.accounts.manage", csrf=True)
def create_personal_account():
    data = request.get_json(silent=True) or {}
    platform_key = str(data.get("platform_key", "")).strip()
    label = str(data.get("label", "")).strip()[:120]
    hint = mask_account_hint(data.get("account_hint", ""))
    if not hint:
        return json_error("Provide a masked account_hint", 400)
    db = get_db()
    platform = db.scalar(select(Platform).where(Platform.key == platform_key))
    if not platform:
        return json_error("Platform not found", 404)
    auth_type, serialized, error = _credential_payload(data)
    if error:
        return json_error(*error)
    if platform.auth_methods and auth_type not in platform.auth_methods:
        return json_error("Authentication method is not listed for this platform", 400)
    account = PlatformAccount(platform_id=platform.id, account_mode="personal", label=label, status="unverified")
    db.add(account)
    db.flush()
    owner = PersonalPlatformAccount(
        account_id=account.id, owner_user_id=g.current_user.id,
        platform_id=platform.id, account_hint=hint,
    )
    db.add(owner)
    _new_secret(db, account.id, g.current_user.id, auth_type, serialized)
    db.add(AuditLog(
        actor_user_id=g.current_user.id, target_user_id=g.current_user.id,
        action="personal_account.created", entity_type="platform_account", entity_id=account.id,
        metadata_json={"platform_key": platform.key, "auth_type": auth_type},
    ))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return json_error("An account with this platform hint already exists", 409)
    return jsonify({"account": _base_account(account, platform, "personal", hint), "verified": False}), 201


@bp.patch("/personal-accounts/<account_id>")
@permission_required("platform.accounts.manage", csrf=True)
def update_personal_account(account_id: str):
    data = request.get_json(silent=True) or {}
    if "label" not in data or not isinstance(data["label"], str) or len(data["label"].strip()) > 120:
        return json_error("label must be a string no longer than 120 characters", 400)
    db = get_db()
    owner = db.scalar(select(PersonalPlatformAccount).where(
        PersonalPlatformAccount.account_id == account_id,
        PersonalPlatformAccount.owner_user_id == g.current_user.id,
    ))
    if not owner:
        return json_error("Personal account not found", 404)
    account = db.scalar(select(PlatformAccount).where(
        PlatformAccount.id == account_id,
        PlatformAccount.account_mode == "personal",
    ).with_for_update())
    if not account:
        return json_error("Personal account not found", 404)
    account.label = data["label"].strip()
    account.updated_at = utcnow()
    db.add(AuditLog(
        actor_user_id=g.current_user.id, target_user_id=g.current_user.id,
        action="personal_account.label_updated", entity_type="platform_account", entity_id=account_id,
    ))
    db.commit()
    platform = db.get(Platform, account.platform_id)
    return jsonify({"account": _base_account(account, platform, "personal", owner.account_hint)})


@bp.post("/personal-accounts/<account_id>/reauthorize")
@permission_required("platform.accounts.manage", csrf=True)
def reauthorize_personal_account(account_id: str):
    data = request.get_json(silent=True) or {}
    db = get_db()
    owner = db.scalar(select(PersonalPlatformAccount).where(
        PersonalPlatformAccount.account_id == account_id,
        PersonalPlatformAccount.owner_user_id == g.current_user.id,
    ).with_for_update())
    if not owner:
        return json_error("Personal account not found", 404)
    account = db.scalar(select(PlatformAccount).where(PlatformAccount.id == account_id).with_for_update())
    auth_type, serialized, error = _credential_payload(data)
    if error:
        return json_error(*error)
    platform = db.get(Platform, account.platform_id)
    if platform.auth_methods and auth_type not in platform.auth_methods:
        return json_error("Authentication method is not listed for this platform", 400)
    _new_secret(db, account_id, g.current_user.id, auth_type, serialized)
    account.status = "unverified"
    account.updated_at = utcnow()
    db.add(AuditLog(actor_user_id=g.current_user.id, target_user_id=g.current_user.id,
                    action="personal_account.reauthorized", entity_type="platform_account", entity_id=account_id,
                    metadata_json={"auth_type": auth_type}))
    db.commit()
    return jsonify({"ok": True, "status": account.status, "verified": False})


@bp.delete("/personal-accounts/<account_id>")
@permission_required("platform.accounts.manage", csrf=True)
def unbind_personal_account(account_id: str):
    db = get_db()
    owner = db.scalar(select(PersonalPlatformAccount).where(
        PersonalPlatformAccount.account_id == account_id,
        PersonalPlatformAccount.owner_user_id == g.current_user.id,
    ).with_for_update())
    if not owner:
        return json_error("Personal account not found", 404)
    account = db.scalar(select(PlatformAccount).where(PlatformAccount.id == account_id).with_for_update())
    account.status = "disabled"
    account.updated_at = utcnow()
    secrets = db.scalars(select(PlatformAccountSecret).where(
        PlatformAccountSecret.account_id == account_id,
        PlatformAccountSecret.status == "active",
    ).with_for_update()).all()
    for secret in secrets:
        secret.status = "revoked"
        secret.revoked_at = utcnow()
    cancel_account_tasks(db, account_id, "personal_account_unbound", user_id=g.current_user.id)
    db.add(AuditLog(actor_user_id=g.current_user.id, target_user_id=g.current_user.id,
                    action="personal_account.unbound", entity_type="platform_account", entity_id=account_id))
    db.commit()
    return jsonify({"ok": True})


@bp.get("/shared-accounts")
@permission_required("shared_accounts.read")
def list_authorized_shared_accounts():
    db = get_db()
    return jsonify({"shared_accounts": _authorized_shared_accounts(db, g.current_user.id)})
