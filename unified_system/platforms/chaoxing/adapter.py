"""Isolated candidate reader for the Learning Platform course catalog.

This is deliberately not registered or advertised yet: its parser and request
shape have only offline Mock coverage. `health_check()` stays unhealthy and
capabilities stay false until a real, user-authorized integration test passes.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import requests
from bs4 import BeautifulSoup

from unified_system.platforms.common import (
    AdapterCapabilities, AdapterError, AuthContext, CourseSummary,
    HealthStatus, PlatformAdapter, PlatformInfo, VerificationState,
)

_BASE_URL = "https://mooc2-ans.chaoxing.com"
_COURSE_LIST_PATH = "/mooc2-ans/visit/courselistdata"
_INTERACTION_PATH = "/mooc2-ans/visit/interaction"
_COOKIE_NAME_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}$")


class ChaoxingAdapter(PlatformAdapter):
    """Read-only course list candidate; creates a fresh CookieJar per call/task."""

    def __init__(self, *, session_factory=requests.Session, timeout: tuple[float, float] = (4.0, 12.0)):
        self._session_factory = session_factory
        self._timeout = timeout

    def platform_info(self) -> PlatformInfo:
        return PlatformInfo("chaoxing", "学习通 / 超星", "0.1.0-unverified")

    def capabilities(self) -> AdapterCapabilities:
        # Do not enable this adapter from code review or Mock tests alone.
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

    def check_session_authorization(self, auth: AuthContext) -> tuple[list[CourseSummary], list[int]]:
        """Perform a read-only live check; this never changes capability registration."""
        response_codes: list[int] = []
        try:
            return self._list_courses(auth, None, response_codes), response_codes
        except AdapterError as exc:
            exc.http_statuses = tuple(response_codes)
            raise

    def execute_read_only(self, kind, request_data, auth, cancellation):
        if kind in {"course_sync", "course_list"}:
            return self._list_courses(auth, cancellation)
        return super().execute_read_only(kind, request_data, auth, cancellation)

    def _list_courses(self, auth: AuthContext, cancellation, response_codes: list[int] | None = None) -> list[CourseSummary]:
        session = self._session(auth)
        try:
            if cancellation:
                cancellation.checkpoint()
            first_html = self._request(session, "POST", _COURSE_LIST_PATH, response_codes=response_codes, data={
                "courseType": 1, "courseFolderId": 0, "query": "", "superstarClass": 0,
            }, headers={
                "Referer": _BASE_URL + "/mooc2-ans/visit/interaction?moocDomain=https://mooc1-1.chaoxing.com/mooc-ans",
            })
            cards = _parse_course_cards(first_html)
            if len(cards) > 500:
                raise AdapterError("course_limit_exceeded", "Learning Platform returned too many courses")
            if cancellation:
                cancellation.checkpoint()
            interaction_html = self._request(session, "GET", _INTERACTION_PATH, response_codes=response_codes)
            folder_ids = _parse_folder_ids(interaction_html)
            if len(folder_ids) > 100:
                raise AdapterError("folder_limit_exceeded", "Learning Platform returned too many course folders")
            for folder_id in folder_ids:
                if cancellation:
                    cancellation.checkpoint()
                html = self._request(session, "POST", _COURSE_LIST_PATH, response_codes=response_codes, data={
                    "courseType": 1, "courseFolderId": folder_id, "query": "", "superstarClass": 0,
                })
                cards.extend(_parse_course_cards(html))
                if len(cards) > 500:
                    raise AdapterError("course_limit_exceeded", "Learning Platform returned too many courses")
        except AdapterError:
            raise
        except requests.Timeout as exc:
            raise AdapterError("network_timeout", "Learning Platform request timed out", retryable=True) from exc
        except requests.RequestException as exc:
            raise AdapterError("platform_unreachable", "Learning Platform request failed", retryable=True) from exc
        finally:
            session.cookies.clear()
            session.close()
        unique: dict[str, CourseSummary] = {}
        for course in cards:
            unique[course.course_id] = course
        if not unique:
            raise AdapterError("course_parse_failed", "No recognized course record was returned; authorization was not verified")
        return list(unique.values())

    def _session(self, auth: AuthContext) -> requests.Session:
        if auth.auth_type != "cookies":
            raise AdapterError("unsupported_auth_type", "This candidate currently accepts only user-provided cookies")
        raw = auth.values.get("cookies")
        pairs: list[tuple[str, str]] = []
        if isinstance(raw, str):
            for part in raw.split(";"):
                name, separator, value = part.strip().partition("=")
                if separator and name:
                    pairs.append((name.strip(), value.strip()))
        elif isinstance(raw, dict):
            if any(not isinstance(name, str) or not isinstance(value, str) for name, value in raw.items()):
                raise AdapterError("credential_format_invalid", "Cookie names and values must be strings")
            pairs.extend((name, value) for name, value in raw.items())
        else:
            raise AdapterError("credential_format_invalid", "Expected a cookies string or name/value object")
        if not pairs or len(pairs) > 128:
            raise AdapterError("credential_format_invalid", "Cookie collection is empty or too large")
        session = self._session_factory()
        session.headers.update({
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": "Mozilla/5.0 (compatible; UnifiedReadOnlyWorker/0.1)",
        })
        for name, value in pairs:
            if (not _COOKIE_NAME_RE.fullmatch(name) or len(value) > 8192
                    or any(ord(char) < 32 or ord(char) == 127 for char in value)):
                session.cookies.clear()
                session.close()
                raise AdapterError("credential_format_invalid", "Cookie collection contains an invalid entry")
            session.cookies.set(name, value, domain=".chaoxing.com", path="/")
        return session

    def _request(self, session, method: str, path: str, *, response_codes: list[int] | None = None, **kwargs) -> str:
        response = session.request(
            method, _BASE_URL + path, timeout=self._timeout,
            allow_redirects=False, stream=True, **kwargs,
        )
        try:
            if response_codes is not None:
                response_codes.append(int(response.status_code))
            if 300 <= response.status_code < 400:
                raise AdapterError("authorization_required", "Learning Platform requested an interactive login", http_status=response.status_code)
            if response.status_code in {401, 403}:
                raise AdapterError("authorization_required", "Learning Platform rejected the supplied session", http_status=response.status_code)
            if response.status_code != 200:
                raise AdapterError("platform_response_invalid", "Learning Platform returned an unsupported response", http_status=response.status_code)
            content = bytearray()
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                content.extend(chunk)
                if len(content) > 2 * 1024 * 1024:
                    raise AdapterError("platform_response_too_large", "Learning Platform response exceeded the size limit")
            encoding = getattr(response, "encoding", None) or "utf-8"
            text = bytes(content).decode(encoding, errors="replace")
            content.clear()
            lowered = text[:200_000].lower()
            if "passport2.chaoxing.com" in lowered or "name=\"password\"" in lowered:
                raise AdapterError("authorization_required", "Learning Platform returned a login page")
            return text
        finally:
            response.close()


def _parse_course_cards(html: str) -> list[CourseSummary]:
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for card in soup.select("div.course"):
        if card.select_one("a.not-open-tip") or card.select_one("div.not-open-tip"):
            continue
        course_node = card.select_one("input.courseId")
        class_node = card.select_one("input.clazzId")
        title_node = card.select_one("span.course-name")
        if not course_node or not class_node or not title_node:
            raise AdapterError("course_parse_failed", "Learning Platform course markup was not recognized")
        course_id = str(course_node.get("value", "")).strip()
        class_id = str(class_node.get("value", "")).strip()
        title = str(title_node.get("title") or title_node.get_text(" ", strip=True)).strip()
        teacher_node = card.select_one("p.color3")
        teacher = str(teacher_node.get("title") or teacher_node.get_text(" ", strip=True)).strip() if teacher_node else ""
        if (not course_id or not class_id or not title or len(course_id) + len(class_id) + 1 > 160
                or len(title) > 240 or len(teacher) > 160):
            raise AdapterError("course_parse_failed", "Learning Platform course markup omitted or exceeded bounded fields")
        results.append(CourseSummary(
            platform="chaoxing", course_id=f"{course_id}:{class_id}",
            course_name=title, teacher_name=teacher or None,
            resource_status="UNKNOWN", source_verified=False,
        ))
    return results


def _parse_folder_ids(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    folder_ids = [str(node.get("fileid", "")).strip() for node in soup.select("ul.file-list > li[fileid]")]
    if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", folder_id) for folder_id in folder_ids):
        raise AdapterError("folder_parse_failed", "Learning Platform returned an invalid course folder identifier")
    return folder_ids


def create_adapter() -> ChaoxingAdapter:
    """Factory exists for isolated tests; WorkerEngine refuses its unverified capabilities."""
    return ChaoxingAdapter()
