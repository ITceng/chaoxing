from __future__ import annotations

from functools import wraps

from flask import g, jsonify, session

from unified_system.db import get_db
from unified_system.security import get_current_user, has_permission, json_error, require_csrf


def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        user = get_current_user()
        if user is None:
            return json_error("Authentication required", 401)
        g.current_user = user
        return fn(*args, **kwargs)
    return wrapped


def permission_required(permission: str, csrf: bool = False):
    def decorate(fn):
        @wraps(fn)
        @login_required
        def wrapped(*args, **kwargs):
            db = get_db()
            if not has_permission(db, g.current_user.id, permission):
                return json_error("Permission denied", 403)
            if csrf and not require_csrf():
                return json_error("CSRF validation failed", 403)
            return fn(*args, **kwargs)
        return wrapped
    return decorate


def csrf_required(fn):
    @wraps(fn)
    @login_required
    def wrapped(*args, **kwargs):
        if not require_csrf():
            return json_error("CSRF validation failed", 403)
        return fn(*args, **kwargs)
    return wrapped
