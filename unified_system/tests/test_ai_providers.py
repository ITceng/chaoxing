from __future__ import annotations

import json

import pytest
import requests

from unified_system.services.ai_providers import (
    ChatMessage,
    MockChatProvider,
    OpenAICompatibleProvider,
    ProviderConfig,
    ProviderError,
    validate_endpoint,
)


KEY = "test-provider-key-that-is-not-real"
CONFIG = ProviderConfig(
    provider="siliconflow",
    endpoint="https://api.siliconflow.cn/v1/chat/completions",
    model="test-model",
    api_key=KEY,
)
MESSAGES = [ChatMessage("user", "Explain spaced repetition in one sentence.")]


class FakeResponse:
    def __init__(self, payload: object, status_code: int = 200, *, raw: bytes | None = None):
        self.status_code = status_code
        self.closed = False
        self.content = raw if raw is not None else json.dumps(payload).encode("utf-8")

    def iter_content(self, chunk_size=16_384):
        for start in range(0, len(self.content), chunk_size):
            yield self.content[start : start + chunk_size]

    def close(self):
        self.closed = True


def test_endpoint_allowlist_accepts_provider_specific_official_endpoints():
    assert validate_endpoint("openai", "https://api.openai.com/v1/chat/completions") == \
        "https://api.openai.com/v1/chat/completions"
    assert validate_endpoint("bailian", "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions") == \
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    assert validate_endpoint("volcengine", "https://ark.cn-beijing.volces.com/api/v3/chat/completions") == \
        "https://ark.cn-beijing.volces.com/api/v3/chat/completions"


@pytest.mark.parametrize("endpoint", [
    "http://api.siliconflow.cn/v1/chat/completions",
    "https://localhost/v1/chat/completions",
    "https://127.0.0.1/v1/chat/completions",
    "https://10.0.0.4/v1/chat/completions",
    "https://api.siliconflow.cn.attacker.example/v1/chat/completions",
    "https://user:pass@api.siliconflow.cn/v1/chat/completions",
    "https://api.siliconflow.cn:8443/v1/chat/completions",
    "https://api.siliconflow.cn/v1/chat/completions?redirect=1",
    "https://api.siliconflow.cn/other",
])
def test_endpoint_allowlist_rejects_unsafe_or_unknown_urls(endpoint):
    with pytest.raises(ProviderError):
        validate_endpoint("siliconflow", endpoint)


def test_provider_config_repr_never_displays_the_api_key():
    assert KEY not in repr(CONFIG)


def test_openai_compatible_request_is_tls_verified_bounded_and_parses_provider_usage(monkeypatch, caplog):
    response = FakeResponse({
        "choices": [{"message": {"content": "Use short reviews over time."}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 19, "completion_tokens": 8},
    })
    captured = {}

    def post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return response

    monkeypatch.setattr("unified_system.services.ai_providers.requests.post", post)
    result = OpenAICompatibleProvider().complete(CONFIG, MESSAGES, max_output_tokens=128)

    assert result.text == "Use short reviews over time."
    assert result.finish_reason == "stop"
    assert result.usage.input_tokens == 19
    assert result.usage.output_tokens == 8
    assert result.usage.total_tokens == 27
    assert result.usage.source == "provider"
    assert captured["verify"] is True
    assert captured["allow_redirects"] is False
    assert captured["stream"] is True  # bounded response reading; model generation is non-streaming
    assert captured["timeout"] == (10, 30)
    assert captured["headers"]["Authorization"] == f"Bearer {KEY}"
    assert captured["json"]["stream"] is False
    assert response.closed
    assert KEY not in caplog.text


def test_missing_provider_usage_is_never_fabricated(monkeypatch):
    monkeypatch.setattr(
        "unified_system.services.ai_providers.requests.post",
        lambda *_args, **_kwargs: FakeResponse({"choices": [{"message": {"content": "No estimate."}}]}),
    )
    usage = OpenAICompatibleProvider().complete(CONFIG, MESSAGES).usage
    assert usage.input_tokens is None
    assert usage.output_tokens is None
    assert usage.total_tokens is None
    assert usage.source == "unavailable"


def test_partial_provider_usage_is_explicit(monkeypatch):
    monkeypatch.setattr(
        "unified_system.services.ai_providers.requests.post",
        lambda *_args, **_kwargs: FakeResponse({
            "choices": [{"message": {"content": "Partial."}}],
            "usage": {"prompt_tokens": 11},
        }),
    )
    usage = OpenAICompatibleProvider().complete(CONFIG, MESSAGES).usage
    assert usage.input_tokens == 11
    assert usage.output_tokens is None
    assert usage.source == "partial_provider"


def test_provider_error_body_is_never_exposed_or_consumed(monkeypatch):
    response = FakeResponse({"error": f"authorization failed: {KEY}"}, status_code=401)
    monkeypatch.setattr("unified_system.services.ai_providers.requests.post", lambda *_a, **_kw: response)

    with pytest.raises(ProviderError) as caught:
        OpenAICompatibleProvider().complete(CONFIG, MESSAGES)

    assert caught.value.code == "provider_auth_failed"
    assert KEY not in str(caught.value)
    assert response.closed


def test_redirect_is_rejected_without_following(monkeypatch):
    response = FakeResponse({}, status_code=302)
    captured = {}

    def post(_url, **kwargs):
        captured.update(kwargs)
        return response

    monkeypatch.setattr("unified_system.services.ai_providers.requests.post", post)
    with pytest.raises(ProviderError) as caught:
        OpenAICompatibleProvider().complete(CONFIG, MESSAGES)
    assert caught.value.code == "provider_redirect_rejected"
    assert captured["allow_redirects"] is False
    assert response.closed


def test_response_size_limit_is_enforced(monkeypatch):
    config = ProviderConfig(**{**CONFIG.__dict__, "max_response_bytes": 1024})
    response = FakeResponse({}, raw=b"x" * 1025)
    monkeypatch.setattr("unified_system.services.ai_providers.requests.post", lambda *_a, **_kw: response)
    with pytest.raises(ProviderError) as caught:
        OpenAICompatibleProvider().complete(config, MESSAGES)
    assert caught.value.code == "provider_response_too_large"
    assert response.closed


def test_transport_errors_are_sanitized(monkeypatch):
    monkeypatch.setattr(
        "unified_system.services.ai_providers.requests.post",
        lambda *_a, **_kw: (_ for _ in ()).throw(requests.Timeout(f"timeout for {KEY}")),
    )
    with pytest.raises(ProviderError) as caught:
        OpenAICompatibleProvider().complete(CONFIG, MESSAGES)
    assert caught.value.code == "provider_timeout"
    assert caught.value.public_message == "Provider request timed out"


def test_mock_provider_is_offline_and_never_claims_token_usage():
    config = ProviderConfig(provider="mock", endpoint="", model="mock-model", api_key="")
    result = MockChatProvider().complete(config, MESSAGES)
    assert "Mock" in result.text
    assert result.usage.source == "unavailable"
    assert result.usage.total_tokens is None
    assert result.finish_reason == "mock"
