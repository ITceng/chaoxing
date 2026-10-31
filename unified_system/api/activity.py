from __future__ import annotations

from flask import Blueprint, g, jsonify, request
from sqlalchemy import or_, select

from unified_system.api.common import permission_required
from unified_system.db import get_db
from unified_system.models import AuditLog, TaskEvent
from unified_system.security import iso_utc

bp = Blueprint("activity_api", __name__, url_prefix="/api/v1")


@bp.get("/activity")
@permission_required("profile.manage")
def list_my_activity():
    try:
        limit = min(50, max(1, int(request.args.get("limit", "20"))))
    except ValueError:
        return jsonify({"error": {"message": "limit must be an integer", "status": 400}}), 400
    db = get_db()
    user_id = g.current_user.id
    audits = db.scalars(select(AuditLog).where(
        or_(AuditLog.actor_user_id == user_id, AuditLog.target_user_id == user_id)
    ).order_by(AuditLog.created_at.desc()).limit(limit)).all()
    events = db.scalars(select(TaskEvent).where(
        TaskEvent.user_id == user_id
    ).order_by(TaskEvent.created_at.desc()).limit(limit)).all()
    items = [{
        "id": row.id, "type": "audit", "action": row.action,
        "entity_type": row.entity_type, "entity_id": row.entity_id,
        "details": row.metadata_json or {}, "created_at": iso_utc(row.created_at),
    } for row in audits]
    items.extend({
        "id": row.id, "type": "task", "action": row.event_type,
        "entity_type": "task", "entity_id": row.task_id,
        "details": row.details or {}, "created_at": iso_utc(row.created_at),
    } for row in events)
    items.sort(key=lambda item: item["created_at"] or "", reverse=True)
    return jsonify({"activity": items[:limit]})
