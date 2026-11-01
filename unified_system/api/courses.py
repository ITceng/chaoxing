from __future__ import annotations

from flask import Blueprint, g, jsonify, request
from sqlalchemy import func, or_, select

from unified_system.api.common import permission_required
from unified_system.db import get_db
from unified_system.models import (
    AccountAccessGrant, Platform, PlatformAccount, PlatformCourse,
    PersonalPlatformAccount, SharedPlatformAccount, utcnow,
)
from unified_system.security import iso_utc, json_error

bp = Blueprint("courses_api", __name__, url_prefix="/api/v1/courses")


def _visible_account_ids(db) -> set[str]:
    now = utcnow()
    personal = db.scalars(
        select(PersonalPlatformAccount.account_id)
        .join(PlatformAccount, PlatformAccount.id == PersonalPlatformAccount.account_id)
        .join(Platform, Platform.id == PlatformAccount.platform_id)
        .where(
            PersonalPlatformAccount.owner_user_id == g.current_user.id,
            PlatformAccount.status == "active",
            Platform.integration_status == "active",
        )
    ).all()
    shared_rows = db.execute(
        select(AccountAccessGrant.shared_account_id, AccountAccessGrant.scope)
        .join(SharedPlatformAccount, SharedPlatformAccount.account_id == AccountAccessGrant.shared_account_id)
        .join(PlatformAccount, PlatformAccount.id == SharedPlatformAccount.account_id)
        .join(Platform, Platform.id == PlatformAccount.platform_id)
        .where(
            AccountAccessGrant.user_id == g.current_user.id,
            AccountAccessGrant.revoked_at.is_(None),
            AccountAccessGrant.valid_from <= now,
            or_(AccountAccessGrant.expires_at.is_(None), AccountAccessGrant.expires_at > now),
            SharedPlatformAccount.is_open.is_(True),
            or_(SharedPlatformAccount.available_from.is_(None), SharedPlatformAccount.available_from <= now),
            or_(SharedPlatformAccount.expires_at.is_(None), SharedPlatformAccount.expires_at > now),
            PlatformAccount.status == "active",
            Platform.integration_status == "active",
        )
    ).all()
    shared = []
    for account_id, scope in shared_rows:
        task_kinds = (scope or {}).get("task_kinds", [])
        if not task_kinds or {"course_sync", "course_list"}.intersection(task_kinds):
            shared.append(account_id)
    return set(personal) | set(shared)


@bp.get("")
@permission_required("tasks.read")
def list_courses():
    db = get_db()
    account_ids = _visible_account_ids(db)
    requested_account = request.args.get("account_id", "").strip()
    if requested_account:
        if requested_account not in account_ids:
            return json_error("Platform account not found", 404)
        account_ids = {requested_account}
    if not account_ids:
        return jsonify({"courses": [], "total": 0})

    platform_key = request.args.get("platform_key", "").strip().lower()
    limit_raw = request.args.get("limit", "500")
    try:
        limit = min(500, max(1, int(limit_raw)))
    except (TypeError, ValueError):
        return json_error("limit must be an integer between 1 and 500", 400)

    stmt = (
        select(PlatformCourse, PlatformAccount, Platform)
        .join(PlatformAccount, PlatformAccount.id == PlatformCourse.account_id)
        .join(Platform, Platform.id == PlatformAccount.platform_id)
        .where(PlatformCourse.account_id.in_(account_ids))
    )
    if platform_key:
        stmt = stmt.where(Platform.key == platform_key)
    total = db.scalar(select(func.count()).select_from(
        stmt.with_only_columns(PlatformCourse.id).order_by(None).subquery()
    )) or 0
    rows = db.execute(stmt.order_by(PlatformCourse.last_synced_at.desc(), PlatformCourse.course_name).limit(limit)).all()
    courses = [{
        "id": course.id,
        "account_id": account.id,
        "account_mode": account.account_mode,
        "platform_key": platform.key,
        "course_id": course.course_key,
        "course_name": course.course_name,
        "teacher_name": course.teacher_name,
        "semester": course.semester,
        "resource_status": course.resource_status,
        "source_verified": course.source_verified,
        "last_synced_at": iso_utc(course.last_synced_at),
    } for course, account, platform in rows]
    return jsonify({"courses": courses, "total": total})
