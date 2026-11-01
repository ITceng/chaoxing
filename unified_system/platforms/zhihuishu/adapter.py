"""Isolated, cookies-only Zhihuishu course-catalog candidate.

This extracts only the legacy project's course-list request shapes and AES
request envelope. The legacy Fucker class is deliberately not imported because
its constructor/API logger exposes cookies and its public methods include study,
progress-write and exam mutations. This candidate remains unregistered.
"""
from __future__ import annotations

import base64
import json
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote

import requests
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from unified_system.platforms.common import (
    AdapterCapabilities, AdapterError, AuthContext, CancellationToken,
    CourseSummary, HealthStatus, PlatformAdapter, PlatformInfo, VerificationState,
)

_ZHIDAO_LIST_URL = "https://onlineservice-api.zhihuishu.com/gateway/t/v1/student/course/share/queryShareCourseInfo"
_HIKE_LIST_URL = "https://hikeservice.zhihuishu.com/student/course/aided/getMyCourseList"
_HOME_KEY = b"7q9oko0vqb3la20r"
_IV = b"1g3qqdh4jvbskb9x"
_COOKIE_NAME_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}$")
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_COURSES = 500
_PAGE_SIZE = 5


class ZhihuishuAdapter(PlatformAdapter):
    """Read-only course list candidate, one fresh CookieJar per invocation."""

    def __init__(self, *, session_factory=requests.Session, timeout: tuple[float, float] = (4.0, 12.0)):
        self._session_factory = session_factory
        self._timeout = timeout

    def platform_info(self) -> PlatformInfo:
        return PlatformInfo("zhihuishu", "智慧树", "0.1.0-unverified")

    def capabilities(self) -> AdapterCapabilities:
        # A passing mock and imported legacy logic do not constitute platform verification.
        return AdapterCapabilities()

    @property
    def integration_state(self) -> VerificationState:
        return VerificationState.DISABLED

    def health_check(self) -> HealthStatus:
        return HealthStatus(False, datetime.now(timezone.utc), VerificationState.PENDING_VERIFICATION.value)

    def validate_credentials(self, auth: AuthContext) -> bool:
        self.list_courses(auth)
        return True

    def list_courses(self, auth: AuthContext) -> list[CourseSummary]:
        return self._list_courses(auth, None)

    def execute_read_only(self, kind: str, request_data, auth: AuthContext, cancellation: CancellationToken):
        if kind in {"course_sync", "course_list"}:
            return self._list_courses(auth, cancellation)
        return super().execute_read_only(kind, request_data, auth, cancellation)

    def _list_courses(self, auth: AuthContext, cancellation: CancellationToken | None) -> list[CourseSummary]:
        if auth.auth_type != "cookies":
            raise AdapterError("unsupported_auth_type", "This candidate accepts only user-authorized session cookies")
        cookies = _parse_cookies(auth.values.get("cookies"))
        uuid = _uuid_from_cas_cookie(cookies)
        session = self._session_factory()
        courses: list[CourseSummary] = []
        try:
            session.headers.update({
                "Accept": "application/json, text/plain, */*",
                "User-Agent": "UnifiedReadOnlyWorker/0.1 (authorized catalog read)",
            })
            for name, value in cookies.items():
                session.cookies.set(name, value, domain=".zhihuishu.com", path="/")

            page_no = 1
            total_count: int | None = None
            while page_no <= 100:
                if cancellation:
                    cancellation.checkpoint()
                timestamp_ms = int(time.time()) * 1000
                payload = {"status": 0, "pageNo": page_no, "pageSize": _PAGE_SIZE, "dateFormate": timestamp_ms}
                form = {
                    "secretStr": _encrypt_home_payload(payload),
                    "dateFormate": timestamp_ms,
                }
                response, status = self._request_json(
                    session, "POST", _ZHIDAO_LIST_URL, data=form,
                )
                data = _result_object(response, status)
                if total_count is None:
                    total_count = _bounded_int(data.get("totalCount"), 0, _MAX_COURSES, "course_count_invalid")
                page_items = data.get("courseOpenDtos") or []
                if not isinstance(page_items, list) or len(page_items) > _PAGE_SIZE:
                    raise AdapterError("course_parse_failed", "Zhihuishu returned an invalid course page")
                for item in page_items:
                    courses.append(_zhidao_course(item))
                if len(courses) > _MAX_COURSES:
                    raise AdapterError("course_limit_exceeded", "Zhihuishu returned too many courses")
                if not page_items or len(courses) >= total_count:
                    break
                page_no += 1
            else:
                raise AdapterError("course_page_limit_exceeded", "Zhihuishu course pagination exceeded the safety limit")

            if cancellation:
                cancellation.checkpoint()
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
            hike_response, hike_status = self._request_json(
                session, "GET", _HIKE_LIST_URL,
                params={"uuid": uuid, "data": timestamp},
            )
            hike_data = _result_object(hike_response, hike_status)
            hike_items = hike_data.get("startInngcourseList") or []
            if not isinstance(hike_items, list) or len(hike_items) > _MAX_COURSES:
                raise AdapterError("course_parse_failed", "Zhihuishu returned an invalid campus-course list")
            courses.extend(_hike_course(item) for item in hike_items)
            if len(courses) > _MAX_COURSES:
                raise AdapterError("course_limit_exceeded", "Zhihuishu returned too many courses")
            if not courses:
                raise AdapterError("course_parse_failed", "No recognized course record was returned; authorization was not verified")
        except AdapterError:
            raise
        except requests.Timeout as exc:
            raise AdapterError("network_timeout", "Zhihuishu request timed out", retryable=True) from exc
        except requests.RequestException as exc:
            raise AdapterError("platform_unreachable", "Zhihuishu request failed", retryable=True) from exc
        except (TypeError, ValueError, KeyError, UnicodeError) as exc:
            raise AdapterError("platform_response_invalid", "Zhihuishu returned an unsupported response") from exc
        finally:
            session.cookies.clear()
            session.close()

        unique: dict[str, CourseSummary] = {}
        for course in courses:
            unique[course.course_id] = course
        return list(unique.values())

    def _request_json(self, session, method: str, url: str, **kwargs) -> tuple[dict[str, Any], int]:
        response = session.request(
            method, url, timeout=self._timeout, allow_redirects=False,
            stream=True, **kwargs,
        )
        try:
            status = int(response.status_code)
            if 300 <= status < 400:
                raise AdapterError("authorization_required", "Zhihuishu requested an interactive login", http_status=status)
            if status in {401, 403}:
                raise AdapterError("authorization_required", "Zhihuishu rejected the supplied session", http_status=status)
            if status != 200:
                raise AdapterError("platform_response_invalid", "Zhihuishu returned an unsupported HTTP response", http_status=status)
            content = bytearray()
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if chunk:
                    content.extend(chunk)
                    if len(content) > _MAX_RESPONSE_BYTES:
                        raise AdapterError("platform_response_too_large", "Zhihuishu response exceeded the size limit", http_status=status)
            parsed = json.loads(bytes(content).decode(getattr(response, "encoding", None) or "utf-8"))
            content.clear()
            if not isinstance(parsed, dict):
                raise AdapterError("platform_response_invalid", "Zhihuishu returned an invalid JSON shape", http_status=status)
            return parsed, status
        finally:
            response.close()


def _parse_cookies(raw: Any) -> dict[str, str]:
    parsed = raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            raise AdapterError("credential_format_invalid", "Cookie collection is empty")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = {}
            for part in text.split(";"):
                name, separator, value = part.strip().partition("=")
                if separator and name:
                    parsed[name.strip()] = value.strip()
    if isinstance(parsed, list):
        values = {}
        for item in parsed:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not isinstance(item.get("value"), str):
                raise AdapterError("credential_format_invalid", "Cookie list must contain name/value objects")
            values[item["name"]] = item["value"]
        parsed = values
    if not isinstance(parsed, dict) or not parsed or len(parsed) > 128:
        raise AdapterError("credential_format_invalid", "Cookie collection is invalid or too large")
    result: dict[str, str] = {}
    for name, value in parsed.items():
        if (not isinstance(name, str) or not isinstance(value, str)
                or not _COOKIE_NAME_RE.fullmatch(name) or len(value) > 8192
                or any(ord(char) < 32 or ord(char) == 127 for char in value)):
            raise AdapterError("credential_format_invalid", "Cookie collection contains an invalid entry")
        result[name] = value
    return result


def _uuid_from_cas_cookie(cookies: dict[str, str]) -> str:
    raw = cookies.get("CASLOGC")
    if not raw:
        raise AdapterError("credential_format_invalid", "CASLOGC cookie is required for the course-list endpoints")
    try:
        value = json.loads(unquote(raw)).get("uuid")
    except (ValueError, TypeError, AttributeError) as exc:
        raise AdapterError("credential_format_invalid", "CASLOGC cookie format is not recognized") from exc
    if not isinstance(value, (str, int)) or not str(value) or len(str(value)) > 128:
        raise AdapterError("credential_format_invalid", "CASLOGC cookie omitted a valid account identifier")
    return str(value)


def _encrypt_home_payload(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload).encode("utf-8")
    padder = padding.PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(raw) + padder.finalize()
    encryptor = Cipher(algorithms.AES(_HOME_KEY), modes.CBC(_IV)).encryptor()
    return base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode("ascii")


def _result_object(payload: dict[str, Any], http_status: int) -> dict[str, Any]:
    code = payload.get("code")
    if code in {-12, "-12"}:
        raise AdapterError("interaction_required", "Zhihuishu requires a user security or CAPTCHA interaction", http_status=http_status, business_code=code)
    if code in {401, 403, -1, -2, -3, "401", "403", "-1", "-2", "-3"}:
        raise AdapterError("authorization_required", "Zhihuishu rejected the supplied session", http_status=http_status, business_code=code)
    if code not in {200, "200"}:
        safe_code = code if isinstance(code, (str, int)) else None
        raise AdapterError("platform_business_error", "Zhihuishu returned an unsuccessful business status", http_status=http_status, business_code=safe_code)
    result = payload.get("result")
    if not isinstance(result, dict):
        raise AdapterError("platform_response_invalid", "Zhihuishu response omitted the expected result object", http_status=http_status, business_code=code)
    return result


def _bounded_int(value: Any, minimum: int, maximum: int, error_code: str) -> int:
    if type(value) is int:
        result = value
    elif isinstance(value, str) and re.fullmatch(r"\d{1,9}", value):
        result = int(value)
    else:
        raise AdapterError(error_code, "Zhihuishu returned an invalid numeric field")
    if result < minimum or result > maximum:
        raise AdapterError(error_code, "Zhihuishu numeric field exceeded the safety limit")
    return result


def _zhidao_course(item: Any) -> CourseSummary:
    if not isinstance(item, dict):
        raise AdapterError("course_parse_failed", "Zhihuishu returned an invalid course record")
    course_id, name = item.get("secret"), item.get("courseName")
    if (not isinstance(course_id, (str, int)) or not isinstance(name, str)
            or not str(course_id).strip() or not name.strip()
            or len(str(course_id)) > 153 or len(name) > 240):
        raise AdapterError("course_parse_failed", "Zhihuishu course record omitted or exceeded bounded fields")
    return CourseSummary(
        platform="zhihuishu", course_id=f"zhidao:{course_id}", course_name=name.strip(),
        resource_status="UNKNOWN", source_verified=False,
    )


def _hike_course(item: Any) -> CourseSummary:
    if not isinstance(item, dict):
        raise AdapterError("course_parse_failed", "Zhihuishu returned an invalid campus-course record")
    course_id, name = item.get("courseId"), item.get("courseName")
    if (not isinstance(course_id, (str, int)) or not isinstance(name, str)
            or not str(course_id).strip() or not name.strip()
            or len(str(course_id)) > 155 or len(name) > 240):
        raise AdapterError("course_parse_failed", "Zhihuishu campus-course record omitted or exceeded bounded fields")
    return CourseSummary(
        platform="zhihuishu", course_id=f"hike:{course_id}", course_name=name.strip(),
        resource_status="UNKNOWN", source_verified=False,
    )


def create_adapter() -> ZhihuishuAdapter:
    return ZhihuishuAdapter()
