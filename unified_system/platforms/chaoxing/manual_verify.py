"""Opt-in local smoke check; does not change the disabled capability state.

Run only in an authorized environment. The owner enters a session cookie through
getpass; it is not echoed, printed, persisted, or included in diagnostic output.
"""
from __future__ import annotations

import getpass
import json
import sys
from datetime import datetime, timezone

from unified_system.platforms.common import AdapterError, AuthContext, VerificationState
from unified_system.platforms.chaoxing.adapter import ChaoxingAdapter


def check_cookie(adapter, cookie: str, *, live_check: bool = False) -> dict[str, object]:
    checked_at = datetime.now(timezone.utc).isoformat()
    if not cookie.strip():
        return {
            "integration_state": VerificationState.DISABLED.value,
            "credential_state": VerificationState.NOT_CONFIGURED.value,
            "result": "cookie_not_provided",
            "course_count": None,
            "http_status_codes": [],
            "business_result_code": None,
            "checked_at": checked_at,
            "error_code": None,
        }
    try:
        courses, http_codes = adapter.check_session_authorization(AuthContext(
            account_id="local-manual-check", auth_type="cookies", values={"cookies": cookie},
        ))
    except AdapterError as exc:
        state = VerificationState.EXPIRED if exc.code == "authorization_required" else VerificationState.ERROR
        return {
            "integration_state": VerificationState.DISABLED.value,
            "credential_state": state.value,
            "result": "candidate_check_failed",
            "course_count": None,
            "http_status_codes": list(exc.http_statuses),
            "business_result_code": exc.business_code,
            "checked_at": checked_at,
            "error_code": exc.code,
        }
    except Exception as exc:  # redact response bodies, URLs, headers and exception messages
        return {
            "integration_state": VerificationState.DISABLED.value,
            "credential_state": VerificationState.ERROR.value,
            "result": "candidate_check_failed",
            "course_count": None,
            "http_status_codes": [],
            "business_result_code": None,
            "checked_at": checked_at,
            "error_code": type(exc).__name__,
        }
    return {
        "integration_state": VerificationState.DISABLED.value,
        "credential_state": (
            VerificationState.VERIFIED.value if live_check else VerificationState.PENDING_VERIFICATION.value
        ),
        "result": "live_candidate_check_succeeded" if live_check else "mock_or_unconfirmed_check_succeeded",
        "course_count": len(courses),
        "http_status_codes": http_codes,
        "business_result_code": None,
        "checked_at": checked_at,
        "error_code": None,
    }


def main() -> int:
    raw_cookie = getpass.getpass("Paste your authorized session cookie (input hidden): ")
    cookie_buffer = bytearray(raw_cookie.encode("utf-8"))
    try:
        cookie = cookie_buffer.decode("utf-8")
        report = check_cookie(ChaoxingAdapter(), cookie, live_check=True)
    finally:
        cookie = ""
        raw_cookie = ""
        for index in range(len(cookie_buffer)):
            cookie_buffer[index] = 0
        cookie_buffer.clear()
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["credential_state"] == VerificationState.VERIFIED.value else 1


if __name__ == "__main__":
    sys.exit(main())
