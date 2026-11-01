"""Opt-in local smoke check; never changes the adapter's disabled capability state.

Run only in an authorized environment and paste a session cookie obtained by the
account owner. The value is read without terminal echo and is never printed or
persisted; output contains only an outcome code and course count.
"""
from __future__ import annotations

import getpass
import json
import sys
from datetime import datetime, timezone

from unified_system.platforms.common import AdapterError, AuthContext
from unified_system.platforms.chaoxing.adapter import ChaoxingAdapter


def check_cookie(adapter: ChaoxingAdapter, cookie: str) -> dict[str, object]:
    courses = adapter.list_courses(AuthContext(
        account_id="local-manual-check", auth_type="cookies", values={"cookies": cookie},
    ))
    return {
        "platform": "chaoxing",
        "result": "candidate_read_only_request_succeeded",
        "course_count": len(courses),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "capabilities_enabled": False,
    }


def main() -> int:
    cookie = getpass.getpass("Paste the authorized session cookie (input hidden): ")
    if not cookie.strip():
        print(json.dumps({"platform": "chaoxing", "result": "cookie_not_provided"}))
        return 2
    try:
        report = check_cookie(ChaoxingAdapter(), cookie)
    except AdapterError as exc:
        print(json.dumps({"platform": "chaoxing", "result": "candidate_check_failed", "error_code": exc.code}))
        return 1
    except Exception as exc:  # do not display third-party exception text or response bodies
        print(json.dumps({"platform": "chaoxing", "result": "candidate_check_failed", "error_code": type(exc).__name__}))
        return 1
    finally:
        cookie = ""
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
