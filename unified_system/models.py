from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, JSON, String, Text, UniqueConstraint, text,
)
from sqlalchemy.orm import Mapped, mapped_column

from unified_system.db import Base


def new_id() -> str:
    return uuid4().hex


def utcnow() -> datetime:
    # Naive UTC is used for portable SQLite/PostgreSQL comparisons; API serializes with Z.
    return datetime.utcnow()


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False, index=True)
    auth_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(200), default="", nullable=False)


class Permission(Base):
    __tablename__ = "permissions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(200), default="", nullable=False)


class UserRole(Base):
    __tablename__ = "user_roles"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role_id: Mapped[str] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)


class RolePermission(Base):
    __tablename__ = "role_permissions"
    role_id: Mapped[str] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    permission_id: Mapped[str] = mapped_column(ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True)


class AdminUserScope(Base):
    __tablename__ = "admin_user_scopes"
    admin_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    __table_args__ = (CheckConstraint("admin_id != user_id", name="ck_admin_scope_not_self"),)


class Invitation(Base):
    __tablename__ = "invitations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    code_hint: Mapped[str] = mapped_column(String(12), nullable=False)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    max_uses: Mapped[int] = mapped_column(Integer, nullable=False)
    used_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    note: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    __table_args__ = (
        CheckConstraint("max_uses >= 1 AND used_count >= 0 AND used_count <= max_uses", name="ck_invitation_usage_range"),
    )


class InvitationUse(Base):
    __tablename__ = "invitation_uses"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    invitation_id: Mapped[str] = mapped_column(ForeignKey("invitations.id", ondelete="RESTRICT"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    used_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    __table_args__ = (UniqueConstraint("invitation_id", "user_id", name="uq_invitation_user_use"),)


class UserSession(Base):
    """Server-side revocation metadata for the signed, HttpOnly browser session."""
    __tablename__ = "user_sessions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    auth_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    remote_fingerprint: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    user_agent: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    __table_args__ = (Index("ix_user_sessions_user_active", "user_id", "revoked_at", "expires_at"),)


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class LoginLog(Base):
    __tablename__ = "login_logs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    identifier: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    remote_fingerprint: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    result: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    user_agent: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)


class Platform(Base):
    __tablename__ = "platforms"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    key: Mapped[str] = mapped_column(String(48), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    code_available: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    integration_status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    auth_methods: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    capabilities: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status_note: Mapped[str] = mapped_column(Text, default="", nullable=False)


class PlatformAccount(Base):
    """Shared identity metadata; ownership is represented by one explicit subtype."""
    __tablename__ = "platform_accounts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    platform_id: Mapped[str] = mapped_column(ForeignKey("platforms.id", ondelete="RESTRICT"), nullable=False, index=True)
    account_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    label: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="unverified", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    __table_args__ = (
        UniqueConstraint("id", "platform_id", name="uq_platform_account_id_platform"),
        UniqueConstraint("id", "account_mode", name="uq_platform_account_id_mode"),
        CheckConstraint("account_mode IN ('personal', 'shared')", name="ck_platform_account_mode"),
        CheckConstraint("status IN ('unverified', 'active', 'disabled', 'expired', 'error')", name="ck_platform_account_status"),
    )


class PersonalPlatformAccount(Base):
    __tablename__ = "personal_platform_accounts"
    account_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_mode: Mapped[str] = mapped_column(String(16), default="personal", nullable=False)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    platform_id: Mapped[str] = mapped_column(String(32), nullable=False)
    account_hint: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(["account_id", "platform_id"], ["platform_accounts.id", "platform_accounts.platform_id"], ondelete="CASCADE", name="fk_personal_account_platform"),
        ForeignKeyConstraint(["account_id", "account_mode"], ["platform_accounts.id", "platform_accounts.account_mode"], ondelete="CASCADE", name="fk_personal_account_mode"),
        CheckConstraint("account_mode = 'personal'", name="ck_personal_account_mode"),
        UniqueConstraint("account_id", "owner_user_id", name="uq_personal_account_owner"),
        UniqueConstraint("owner_user_id", "platform_id", "account_hint", name="uq_personal_owner_platform_hint"),
    )


class SharedPlatformAccount(Base):
    __tablename__ = "shared_platform_accounts"
    account_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_mode: Mapped[str] = mapped_column(String(16), default="shared", nullable=False)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    account_hint: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    is_open: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    available_from: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    max_concurrency: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(["account_id", "account_mode"], ["platform_accounts.id", "platform_accounts.account_mode"], ondelete="CASCADE", name="fk_shared_account_mode"),
        CheckConstraint("account_mode = 'shared'", name="ck_shared_account_mode"),
        CheckConstraint("expires_at IS NULL OR available_from IS NULL OR expires_at > available_from", name="ck_shared_account_validity"),
        CheckConstraint("max_concurrency >= 1 AND max_concurrency <= 16", name="ck_shared_account_concurrency"),
    )


class PlatformAccountSecret(Base):
    __tablename__ = "platform_account_secrets"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(ForeignKey("platform_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    auth_type: Mapped[str] = mapped_column(String(32), nullable=False)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    __table_args__ = (
        UniqueConstraint("account_id", "version", name="uq_account_secret_version"),
        Index("ix_account_secret_active_version", "account_id", "status", "version"),
    )


class AccountAccessGrant(Base):
    __tablename__ = "account_access_grants"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    shared_account_id: Mapped[str] = mapped_column(ForeignKey("shared_platform_accounts.account_id", ondelete="RESTRICT"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    granted_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    scope: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    __table_args__ = (
        UniqueConstraint("id", "shared_account_id", "user_id", name="uq_grant_account_user"),
        Index("ix_grant_account_user_revoked", "shared_account_id", "user_id", "revoked_at"),
        Index("uq_active_account_user_grant", "shared_account_id", "user_id", unique=True,
              sqlite_where=text("revoked_at IS NULL"), postgresql_where=text("revoked_at IS NULL")),
        CheckConstraint("expires_at IS NULL OR expires_at > valid_from", name="ck_grant_validity_range"),
    )


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    personal_account_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    shared_account_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    access_grant_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="queued", nullable=False, index=True)
    request_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=900, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    worker_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    result_summary: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    __table_args__ = (
        CheckConstraint(
            "(personal_account_id IS NOT NULL AND shared_account_id IS NULL AND access_grant_id IS NULL) OR "
            "(personal_account_id IS NULL AND shared_account_id IS NOT NULL AND access_grant_id IS NOT NULL)",
            name="ck_task_exactly_one_account_mode",
        ),
        CheckConstraint("timeout_seconds >= 1 AND timeout_seconds <= 86400", name="ck_task_timeout_range"),
        CheckConstraint("status IN ('queued', 'leased', 'running', 'cancel_requested', 'succeeded', 'failed', 'cancelled', 'timed_out')", name="ck_task_status"),
        CheckConstraint("attempt >= 0 AND max_attempts >= 1", name="ck_task_attempts"),
        Index("ix_tasks_user_created", "user_id", "created_at"),
        Index("ix_tasks_queue", "status", "created_at"),
        UniqueConstraint("id", "user_id", name="uq_tasks_id_user"),
        UniqueConstraint("user_id", "idempotency_key", name="uq_task_user_idempotency"),
        UniqueConstraint("id", "user_id", "shared_account_id", "access_grant_id", name="uq_task_shared_owner_grant"),
        ForeignKeyConstraint(["personal_account_id", "user_id"], ["personal_platform_accounts.account_id", "personal_platform_accounts.owner_user_id"], ondelete="RESTRICT", name="fk_task_personal_owner"),
        ForeignKeyConstraint(["shared_account_id"], ["shared_platform_accounts.account_id"], ondelete="RESTRICT", name="fk_task_shared_account"),
        ForeignKeyConstraint(["access_grant_id", "shared_account_id", "user_id"], ["account_access_grants.id", "account_access_grants.shared_account_id", "account_access_grants.user_id"], ondelete="RESTRICT", name="fk_task_shared_grant_owner"),
    )


class TaskEvent(Base):
    __tablename__ = "task_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)
    __table_args__ = (
        ForeignKeyConstraint(["task_id", "user_id"], ["tasks.id", "tasks.user_id"], ondelete="CASCADE", name="fk_task_event_owner"),
        Index("ix_task_events_task_created", "task_id", "created_at"),
    )


class AccountExecutionLease(Base):
    __tablename__ = "account_execution_leases"
    account_id: Mapped[str] = mapped_column(ForeignKey("platform_accounts.id", ondelete="CASCADE"), primary_key=True)
    slot_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id", ondelete="RESTRICT"), nullable=True, unique=True)
    lease_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    acquired_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)


class AccountUsageLog(Base):
    __tablename__ = "account_usage_logs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    shared_account_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    grant_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    task_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)
    __table_args__ = (
        ForeignKeyConstraint(["grant_id", "shared_account_id", "user_id"], ["account_access_grants.id", "account_access_grants.shared_account_id", "account_access_grants.user_id"], ondelete="RESTRICT", name="fk_usage_grant_owner"),
        ForeignKeyConstraint(["task_id", "user_id", "shared_account_id", "grant_id"], ["tasks.id", "tasks.user_id", "tasks.shared_account_id", "tasks.access_grant_id"], ondelete="RESTRICT", name="fk_usage_task_owner"),
        Index("ix_usage_account_created", "shared_account_id", "created_at"),
    )


class WorkerInstance(Base):
    __tablename__ = "worker_instances"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    platform_id: Mapped[str] = mapped_column(ForeignKey("platforms.id", ondelete="RESTRICT"), nullable=False, index=True)
    worker_id: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    version: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="offline", nullable=False, index=True)
    capabilities: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class AIProviderConfig(Base):
    __tablename__ = "ai_provider_configs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(48), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    model: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    secret_ciphertext: Mapped[str] = mapped_column(Text, default="", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    __table_args__ = (UniqueConstraint("owner_user_id", "provider", name="uq_ai_owner_provider"),)


class AIUsageLog(Base):
    __tablename__ = "ai_usage_logs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(48), nullable=False)
    model: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    purpose: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    actor_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    target_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(48), default="", nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)
