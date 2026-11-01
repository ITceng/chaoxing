from __future__ import annotations

from flask import Blueprint, current_app, jsonify
from sqlalchemy import text

from unified_system.api.common import permission_required
from unified_system.db import get_db
from unified_system.operations import build_ops_overview

bp = Blueprint("ops_api", __name__, url_prefix="/api/v1/admin/ops")


@bp.get("/overview")
@permission_required("ops.view")
def operational_overview():
    db = get_db()
    # A successful query is the database signal; no synthetic health value is supplied.
    db.execute(text("SELECT 1"))
    metrics_path = current_app.config.get("HOST_METRICS_PATH", "")
    return jsonify(build_ops_overview(
        db, metrics_path,
        worker_ttl_seconds=int(current_app.config.get("WORKER_HEARTBEAT_TTL_SECONDS", 90)),
    ))
