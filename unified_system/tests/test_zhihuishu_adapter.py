from __future__ import annotations

import json

import pytest
import requests

from unified_system.platforms.common import AdapterCancelled, AdapterError, AuthContext, CancellationToken, VerificationState
from unified_system.platforms.zhihuishu.adapter import ZhihuishuAdapter, _encrypt_home_payload


class CookieJar:
    def __init__(self):
        self.values = []

    def set(self, name, value, **kwargs):
        self.values.append((name, value, kwargs))

    def clear(self):
        self.values.clear()


class Response:
    def __init__(self, payload, status=200, raw=None):
        self.status_code = status
        self.encoding = "utf-8"
        self.body = raw if raw is not None else json.dumps(payload).encode()
        self.closed = False

    def iter_content(self, chunk_size=65536):
        yield self.body

    def close(self):
        self.closed = True


class Session:
    def __init__(self, responses, on_request=None):
        self.responses = list(responses)
        self.on_request = on_request
        self.cookies = CookieJar()
        self.headers = {}
        self.calls = []
        self.closed = False

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.on_request:
            self.on_request()
        return self.responses.pop(0)

    def close(self):
        self.closed = True


def auth(cookie="CASLOGC=%7B%22uuid%22%3A%22owner-id%22%7D; SID=user-specific"):
    return AuthContext("account-123", "cookies", {"cookies": cookie})


def success_responses():
    return [
        Response({"code": 200, "result": {
            "totalCount": 1,
            "courseOpenDtos": [{"secret": "share-1", "courseName": "Shared Course"}],
        }}),
        Response({"code": 200, "result": {
            "startInngcourseList": [{"courseId": 81, "courseName": "Campus Course"}],
        }}),
    ]


def test_candidate_is_disabled_and_reads_only_course_summary_with_isolated_session():
    session = Session(success_responses())
    adapter = ZhihuishuAdapter(session_factory=lambda: session)

    assert adapter.capabilities().worker_capabilities() == []
    assert adapter.integration_state is VerificationState.DISABLED
    assert adapter.health_check().healthy is False
    assert adapter.health_check().detail_code == VerificationState.PENDING_VERIFICATION.value
    courses = adapter.list_courses(auth())

    assert [(course.course_id, course.course_name) for course in courses] == [
        ("zhidao:share-1", "Shared Course"), ("hike:81", "Campus Course"),
    ]
    assert all(course.source_verified is False for course in courses)
    assert session.closed and session.cookies.values == []
    assert [call[0] for call in session.calls] == ["POST", "GET"]
    first_form = session.calls[0][2]["data"]
    assert first_form["secretStr"] == _encrypt_home_payload({
        "status": 0, "pageNo": 1, "pageSize": 5,
        "dateFormate": first_form["dateFormate"],
    })
    assert session.calls[1][2]["params"]["uuid"] == "owner-id"
    assert all(call[2]["allow_redirects"] is False and call[2]["stream"] is True for call in session.calls)
    assert all(call[2]["timeout"] == (4.0, 12.0) for call in session.calls)
    assert "user-specific" not in repr(courses)


def test_candidate_creates_a_new_cookie_jar_for_each_account():
    observed = []
    sessions = []

    class InspectSession(Session):
        def request(self, method, url, **kwargs):
            observed.append({name: value for name, value, _options in self.cookies.values})
            return super().request(method, url, **kwargs)

    def make_session():
        session = InspectSession(success_responses())
        sessions.append(session)
        return session

    adapter = ZhihuishuAdapter(session_factory=make_session)
    first = "CASLOGC=%7B%22uuid%22%3A%22first-id%22%7D; SID=first-secret"
    second = "CASLOGC=%7B%22uuid%22%3A%22second-id%22%7D; SID=second-secret"
    adapter.list_courses(auth(first))
    adapter.list_courses(auth(second))

    assert len(sessions) == 2 and sessions[0] is not sessions[1]
    assert observed[0]["SID"] == "first-secret" and "second-secret" not in observed[0].values()
    assert observed[2]["SID"] == "second-secret" and "first-secret" not in observed[2].values()
    assert sessions[0].cookies.values == [] and sessions[1].cookies.values == []


def test_candidate_rejects_missing_identity_cookie_before_network_access():
    session = Session([])
    adapter = ZhihuishuAdapter(session_factory=lambda: session)
    with pytest.raises(AdapterError) as error:
        adapter.list_courses(auth("SID=user-specific"))
    assert error.value.code == "credential_format_invalid"
    assert session.calls == []


def test_candidate_returns_interaction_required_without_auto_retry_or_bypass():
    response = Response({"code": -12, "message": "sensitive server detail"})
    session = Session([response])
    adapter = ZhihuishuAdapter(session_factory=lambda: session)
    with pytest.raises(AdapterError) as error:
        adapter.list_courses(auth())
    assert error.value.code == "interaction_required"
    assert error.value.business_code == -12
    assert "sensitive server detail" not in repr(error.value)
    assert len(session.calls) == 1 and session.closed and session.cookies.values == []


def test_candidate_maps_timeouts_without_leaking_exception_messages():
    class TimeoutSession(Session):
        def request(self, method, url, **kwargs):
            raise requests.Timeout("private session data")

    session = TimeoutSession([])
    adapter = ZhihuishuAdapter(session_factory=lambda: session)
    with pytest.raises(AdapterError) as error:
        adapter.list_courses(auth())
    assert error.value.code == "network_timeout" and error.value.retryable
    assert "private session data" not in repr(error.value)
    assert session.closed and session.cookies.values == []


def test_candidate_checks_cancellation_between_platform_requests():
    token = CancellationToken()
    session = Session(success_responses(), on_request=token.cancel)
    adapter = ZhihuishuAdapter(session_factory=lambda: session)
    with pytest.raises(AdapterCancelled):
        adapter.execute_read_only("course_list", {}, auth(), token)
    assert len(session.calls) == 1
    assert session.closed and session.cookies.values == []


def test_candidate_enforces_response_size_limit_and_closes_response():
    response = Response({}, raw=b"x" * (2 * 1024 * 1024 + 1))
    session = Session([response])
    adapter = ZhihuishuAdapter(session_factory=lambda: session)
    with pytest.raises(AdapterError) as error:
        adapter.list_courses(auth())
    assert error.value.code == "platform_response_too_large"
    assert response.closed and session.closed


def test_candidate_enforces_pagination_and_course_limits_without_leaking_response():
    page1 = Response({"code": 200, "result": {
        "totalCount": 6,
        "courseOpenDtos": [{"secret": f"c-{i}", "courseName": f"Course {i}"} for i in range(5)],
    }})
    page2 = Response({"code": 200, "result": {
        "totalCount": 6,
        "courseOpenDtos": [{"secret": "c-5", "courseName": "Course 5"}],
    }})
    hike = Response({"code": 200, "result": {"startInngcourseList": []}})
    session = Session([page1, page2, hike])
    courses = ZhihuishuAdapter(session_factory=lambda: session).list_courses(auth())
    assert len(courses) == 6
    assert len(session.calls) == 3
    assert session.calls[0][2]["data"]["secretStr"] != session.calls[1][2]["data"]["secretStr"]
    assert all("dateFormate" in call[2]["data"] for call in session.calls[:2])
    assert session.closed
