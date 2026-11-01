from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken
from flask import current_app, g, jsonify, request, session
from sqlalchemy import func, select

from unified_system.db import get_db
from unified_system.models import AdminUserScope, Permission, Role, RolePermission, User, UserRole, UserSession

_password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def normalize_email(value: str) -> str:
    return str(value or "").strip().lower()


def validate_password(value: str) -> str | None:
    if not isinstance(value, str) or len(value) < 12:
        return "密码至少 12 位"
    if len(value) > 256:
        return "密码长度不能超过 256 位"
    if value.strip() != value:
        return "密码首尾不能包含空格"
    return None


def valid_email(value: str) -> bool:
    return len(value) <= 320 and bool(_EMAIL_RE.match(value))


def hash_password(value: str) -> str:
    return _password_hasher.hash(value)


def verify_password(value: str, stored: str) -> bool:
    try:
        return _password_hasher.verify(stored, value)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def mask_account_hint(value: str) -> str:
    """Store/display only a masked identifier, even if callers submit a full account name."""
    raw = str(value or "").strip()[:120]
    if not raw:
        return ""
    if "@" in raw:
        local, domain = raw.rsplit("@", 1)
        if "*" not in local and "•" not in local:
            local = (local[:1] + "***") if local else "***"
        return f"{local}@{domain}"
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) >= 7:
        return "••••" + digits[-4:]
    if "*" in raw or "•" in raw:
        return raw
    if len(raw) <= 3:
        return "•" * len(raw)
    return raw[:1] + "•••" + raw[-2:]


_CREDENTIAL_KEY_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_CREDENTIAL_CIPHERTEXT_PREFIX = "v1:"


def _parse_credential_keyring(config: dict, *, allow_development_fallback: bool) -> tuple[dict[str, Fernet], str]:
    keys: dict[str, Fernet] = {}
    serialized = str(config.get("CREDENTIAL_ENCRYPTION_KEYS_JSON", "") or "").strip()
    if serialized:
        if len(serialized) > 16384:
            raise RuntimeError("CREDENTIAL_ENCRYPTION_KEYS_JSON exceeds the supported size")
        try:
            raw = json.loads(serialized)
        except json.JSONDecodeError as exc:
            raise RuntimeError("CREDENTIAL_ENCRYPTION_KEYS_JSON must be a JSON object") from exc
        if not isinstance(raw, dict) or not 1 <= len(raw) <= 32:
            raise RuntimeError("Credential keyring must contain between 1 and 32 key IDs")
        for key_id, key_value in raw.items():
            if not isinstance(key_id, str) or not _CREDENTIAL_KEY_ID_RE.fullmatch(key_id):
                raise RuntimeError("Credential key IDs may contain only letters, digits, underscore or hyphen")
            if not isinstance(key_value, str) or not key_value:
                raise RuntimeError(f"Credential key {key_id!r} is empty or invalid")
            try:
                keys[key_id] = Fernet(key_value.encode("ascii"))
            except (ValueError, TypeError, UnicodeEncodeError) as exc:
                raise RuntimeError(f"Credential key {key_id!r} is not a valid Fernet key") from exc

    legacy_key = str(config.get("CREDENTIAL_ENCRYPTION_KEY", "") or "").strip()
    if legacy_key:
        try:
            legacy_cipher = Fernet(legacy_key.encode("ascii"))
        except (ValueError, TypeError, UnicodeEncodeError) as exc:
            raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY is not a valid legacy Fernet key") from exc
        if "legacy" in keys:
            # Fernet tokens contain a timestamp/nonce, so compare by cross-decrypting a test token.
            try:
                keys["legacy"].decrypt(legacy_cipher.encrypt(b"key-check"))
            except InvalidToken as exc:
                raise RuntimeError("Keyring legacy key conflicts with CREDENTIAL_ENCRYPTION_KEY") from exc
        else:
            keys["legacy"] = legacy_cipher

    active_id = str(config.get("CREDENTIAL_ENCRYPTION_ACTIVE_KEY_ID", "") or "").strip()
    if not keys and allow_development_fallback and config.get("APP_ENV") != "production":
        data_dir = Path(config["DATA_DIR"])
        data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = data_dir / ".credential.key"
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(Fernet.generate_key())
        except FileExistsError:
            pass
        try:
            keys["legacy"] = Fernet(path.read_bytes().strip())
        except (OSError, ValueError) as exc:
            raise RuntimeError("Development credential key file is unavailable or invalid") from exc

    if not keys:
        raise RuntimeError("A credential-encryption keyring or legacy key must be supplied by a secret manager")
    if not active_id:
        if len(keys) == 1:
            active_id = next(iter(keys))
        else:
            raise RuntimeError("Set CREDENTIAL_ENCRYPTION_ACTIVE_KEY_ID when multiple credential keys are configured")
    if active_id not in keys:
        raise RuntimeError("CREDENTIAL_ENCRYPTION_ACTIVE_KEY_ID is not present in the credential keyring")
    return keys, active_id


def validate_credential_keyring(config: dict) -> None:
    """Validate configured production key material without creating or printing secrets."""
    _parse_credential_keyring(config, allow_development_fallback=False)


def credential_keyring() -> tuple[dict[str, Fernet], str]:
    cached = current_app.extensions.get("credential_keyring")
    if cached is None:
        cached = _parse_credential_keyring(current_app.config, allow_development_fallback=True)
        current_app.extensions["credential_keyring"] = cached
    return cached


def credential_cipher() -> Fernet:
    keys, active_id = credential_keyring()
    return keys[active_id]


def encrypt_secret(value: str) -> str:
    keys, active_id = credential_keyring()
    token = keys[active_id].encrypt(value.encode("utf-8")).decode("ascii")
    return f"{_CREDENTIAL_CIPHERTEXT_PREFIX}{active_id}:{token}"


def encrypted_with_active_key(value: str) -> bool:
    if not value.startswith(_CREDENTIAL_CIPHERTEXT_PREFIX):
        return False
    try:
        _prefix, key_id, _token = value.split(":", 2)
    except ValueError:
        return False
    _keys, active_id = credential_keyring()
    return key_id == active_id


def decrypt_secret(value: str) -> str:
    keys, _active_id = credential_keyring()
    try:
        if value.startswith(_CREDENTIAL_CIPHERTEXT_PREFIX):
            _prefix, key_id, token = value.split(":", 2)
            if not _CREDENTIAL_KEY_ID_RE.fullmatch(key_id) or key_id not in keys:
                raise InvalidToken
            cipher = keys[key_id]
        else:
            # Pre-versioned rows use the original CREDENTIAL_ENCRYPTION_KEY (key ID legacy).
            cipher = keys.get("legacy")
            token = value
            if cipher is None:
                raise InvalidToken
        return cipher.decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeEncodeError, ValueError) as exc:
        raise ValueError("credential could not be decrypted; verify the versioned keyring") from exc


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def request_fingerprint() -> str:
    addr = request.remote_addr or "unknown"
    secret = current_app.secret_key.encode("utf-8")
    return hmac.new(secret, addr.encode("utf-8"), hashlib.sha256).hexdigest()


def get_current_user() -> User | None:
    if getattr(g, "current_user_checked", False):
        return getattr(g, "current_user", None)
    g.current_user_checked = True
    user_id = session.get("uid")
    if not user_id:
        g.current_user = None
        return None
    db = get_db()
    user = db.get(User, user_id)
    if not user or user.status != "active" or user.auth_version != session.get("av"):
        session.clear()
        g.current_user = None
        return None
    sid = session.get("sid")
    if sid:
        server_session = db.scalar(select(UserSession).where(
            UserSession.user_id == user.id,
            UserSession.token_hash == hash_token(sid),
        ))
        if (not server_session or server_session.revoked_at is not None
                or server_session.expires_at <= utcnow()
                or server_session.auth_version != user.auth_version):
            session.clear()
            g.current_user = None
            return None
        now = utcnow()
        if server_session.last_seen_at <= now - timedelta(minutes=1):
            server_session.last_seen_at = now
            db.commit()  # throttle writes; session-list timestamps remain fresh within about one minute
        g.current_user_session = server_session
    # Unsigned-by-server legacy cookies from phase two are accepted until their normal
    # expiry; /auth/sessions upgrades the current cookie to a revocable server record.
    g.current_user = user
    return user


def revoke_user_sessions(db, user_id: str, except_session_id: str | None = None) -> None:
    now = utcnow()
    rows = db.scalars(select(UserSession).where(
        UserSession.user_id == user_id,
        UserSession.revoked_at.is_(None),
    )).all()
    for row in rows:
        if row.id != except_session_id:
            row.revoked_at = now


def ensure_current_session_record(db, user: User) -> UserSession:
    sid = session.get("sid")
    current = getattr(g, "current_user_session", None)
    if current:
        return current
    if not sid:
        sid = secrets.token_urlsafe(32)
        session["sid"] = sid
    now = utcnow()
    record = db.scalar(select(UserSession).where(
        UserSession.user_id == user.id,
        UserSession.token_hash == hash_token(sid),
    ))
    if record is None:
        record = UserSession(
            user_id=user.id,
            token_hash=hash_token(sid),
            auth_version=user.auth_version,
            created_at=now,
            expires_at=now + timedelta(
                seconds=int(current_app.config["PERMANENT_SESSION_LIFETIME"])
            ),
            last_seen_at=now,
            remote_fingerprint=request_fingerprint(),
            user_agent=(request.headers.get("User-Agent", "") or "")[:256],
        )
        db.add(record)
    record.last_seen_at = now
    db.commit()
    g.current_user_session = record
    return record


def user_roles(db, user_id: str) -> list[str]:
    return list(db.scalars(
        select(Role.name).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == user_id)
    ).all())


def user_permissions(db, user_id: str) -> list[str]:
    if is_superadmin(db, user_id):
        return list(db.scalars(select(Permission.name).order_by(Permission.name)).all())
    return list(db.scalars(
        select(Permission.name)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(UserRole, UserRole.role_id == RolePermission.role_id)
        .where(UserRole.user_id == user_id)
        .distinct()
    ).all())


def is_superadmin(db, user_id: str) -> bool:
    return "superadmin" in user_roles(db, user_id)


def has_permission(db, user_id: str, permission: str) -> bool:
    if is_superadmin(db, user_id):
        return True
    return permission in user_permissions(db, user_id)


def can_manage_user(db, actor: User, target_id: str) -> bool:
    if actor.id == target_id:
        return False
    if is_superadmin(db, actor.id):
        return True
    return db.scalar(select(AdminUserScope.user_id).where(
        AdminUserScope.admin_id == actor.id, AdminUserScope.user_id == target_id
    )) is not None


def user_public(db, user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "status": user.status,
        "roles": user_roles(db, user.id),
        "created_at": iso_utc(user.created_at),
        "last_login_at": iso_utc(user.last_login_at),
    }


def json_error(message: str, status: int):
    return jsonify({"error": {"message": message, "status": status}}), status


def origin_is_same_site() -> bool:
    origin = request.headers.get("Origin")
    if not origin:
        return True
    from urllib.parse import urlsplit
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    return (
        parsed.scheme.lower() == request.scheme.lower()
        and parsed.netloc.lower() == request.host.lower()
        and parsed.username is None
        and parsed.password is None
        and not parsed.path
        and not parsed.query
        and not parsed.fragment
    )


def require_csrf() -> bool:
    expected = session.get("csrf", "")
    supplied = request.headers.get("X-CSRF-Token", "")
    return bool(expected and supplied and hmac.compare_digest(expected, supplied) and origin_is_same_site())


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)
