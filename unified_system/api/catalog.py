from __future__ import annotations

from flask import Blueprint, jsonify
from sqlalchemy import select

from unified_system.api.common import login_required
from unified_system.db import get_db
from unified_system.models import Platform
from unified_system.security import iso_utc

bp = Blueprint("catalog_api", __name__, url_prefix="/api/v1")


@bp.get("/platforms")
@login_required
def platforms():
    db = get_db()
    rows = db.scalars(select(Platform).order_by(Platform.name)).all()
    return jsonify({"platforms": [{
        "key": p.key,
        "name": p.name,
        "code_available": p.code_available,
        "integration_status": p.integration_status,
        "integrated": p.integration_status == "active",
        "auth_methods": p.auth_methods or [],
        "capabilities": p.capabilities or [],
        "last_verified_at": iso_utc(p.last_verified_at),
        "status_note": p.status_note,
    } for p in rows]})
