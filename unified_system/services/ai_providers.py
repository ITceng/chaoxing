from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Literal, Protocol, Sequence
from urllib.parse import urlsplit

import requests


Role = Literal["system", "user", "assistant"]
UsageSource = Literal["provider", "partial_provider", "estimated", "unavailable"]


@dataclass(frozen=True)
class ChatMessage:
    role: Role
    content: str


@dataclass(frozen=True)
class ProviderCapabilities:
    """Capabilities of this adapter, not a promise about every provider/model."""

    chat_completion: bool = True
    streaming: bool = False
    tools: bool = False
    vision: bool = False
    structured_output: bool = False
    document_upload: bool = False
    token_usage: str = "provider-reported-or-unavailable"


@dataclass(frozen=True)
class ProviderConfig:
    provider: str
    endpoint: str
    model: str
    api_key: str = field(repr=False)
    timeout_seconds: int = 30
    max_response_bytes: int = 1_000_000


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    source: UsageSource = "unavailable"

    @property
    def total_tokens(self) -> int | None:
        if self.input_tokens is None or self.output_tokens is None:
            return None
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class ChatResult:
    text: str
    usage: TokenUsage
    finish_reason: str | None = None


class ProviderError(Exception):
    """Safe, stable error: raw provider bodies and request details are never exposed."""

    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.public_message = message
        self.retryable = retryable


class ChatProvider(Protocol):
    capabilities: ProviderCapabilities

    def complete(
        self,
        config: ProviderConfig,
        messages: Sequence[ChatMessage],
        *,
        max_output_tokens: int,
    ) -> ChatResult: ...


# Exact host/path allowlists prevent user-supplied endpoints from becoming an SSRF
# primitive. Add a new host only after operator review; do not accept arbitrary URLs.
_OPENAI_ENDPOINT = ("api.openai.com", "/v1/chat/completions")
_BAILIAN_ENDPOINT = ("dashscope.aliyuncs.com", "/compatible-mode/v1/chat/completions")
_VOLCENGINE_ENDPOINTS = frozenset({
    ("ark.cn-beijing.volces.com", "/api/v3/chat/completions"),
    ("ark.cn-shanghai.volces.com", "/api/v3/chat/completions"),
    ("ark.cn-guangzhou.volces.com", "/api/v3/chat/completions"),
})
_SENSENOVA_ENDPOINTS = frozenset({
    ("api.sensenova.cn", "/v1/chat/completions"),
    ("token.sensenova.cn", "/v1/chat/completions"),
})
_SILICONFLOW_ENDPOINT = ("api.siliconflow.cn", "/v1/chat/completions")
_PROVIDER_ENDPOINTS: dict[str, frozenset[tuple[str, str]]] = {
    "openai": frozenset({_OPENAI_ENDPOINT}),
    "openai_compatible": frozenset({
        _OPENAI_ENDPOINT, _BAILIAN_ENDPOINT, *_VOLCENGINE_ENDPOINTS,
        *_SENSENOVA_ENDPOINTS, _SILICONFLOW_ENDPOINT,
    }),
    "bailian": frozenset({_BAILIAN_ENDPOINT}),
    "volcengine": _VOLCENGINE_ENDPOINTS,
    "sensenova": _SENSENOVA_ENDPOINTS,
    "siliconflow": frozenset({_SILICONFLOW_ENDPOINT}),
}
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$")
_MAX_MESSAGES = 100
_MAX_MESSAGE_CHARS = 24_000
_MAX_TOTAL_CHARS = 64_000
_MAX_OUTPUT_TOKENS = 16_384


def validate_endpoint(provider: str, endpoint: str) -> str:
    """Return a normalized HTTPS endpoint only when it matches a reviewed host/path."""
    if not isinstance(endpoint, str) or len(endpoint) > 500:
        raise ProviderError("invalid_provider_config", "Provider endpoint is invalid")
    try:
        parsed = urlsplit(endpoint)
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError as exc:
        raise ProviderError("invalid_provider_config", "Provider endpoint is invalid") from exc
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.query
        or parsed.fragment
        or (host, parsed.path) not in _PROVIDER_ENDPOINTS.get(provider, frozenset())
    ):
        raise ProviderError("provider_endpoint_not_allowed", "Provider endpoint is not on the trusted allowlist")
    return f"https://{host}{parsed.path}"


def _validate_messages(messages: Sequence[ChatMessage], max_output_tokens: int) -> None:
    if (
        not isinstance(max_output_tokens, int)
        or isinstance(max_output_tokens, bool)
        or not 1 <= max_output_tokens <= _MAX_OUTPUT_TOKENS
    ):
        raise ProviderError("invalid_request", "Output token limit is invalid")
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)) or not 1 <= len(messages) <= _MAX_MESSAGES:
        raise ProviderError("invalid_request", "Conversation message count is invalid")
    total_chars = 0
    for message in messages:
        if not isinstance(message, ChatMessage) or message.role not in {"system", "user", "assistant"}:
            raise ProviderError("invalid_request", "Conversation message is invalid")
        if not isinstance(message.content, str) or not message.content.strip() or len(message.content) > _MAX_MESSAGE_CHARS:
            raise ProviderError("invalid_request", "Conversation message length is invalid")
        total_chars += len(message.content)
        if total_chars > _MAX_TOTAL_CHARS:
            raise ProviderError("invalid_request", "Conversation is too long")


def _validate_request(config: ProviderConfig, messages: Sequence[ChatMessage], max_output_tokens: int) -> str:
    _validate_messages(messages, max_output_tokens)
    endpoint = validate_endpoint(config.provider, config.endpoint)
    if (
        not isinstance(config.api_key, str)
        or not config.api_key
        or len(config.api_key) > 4096
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in config.api_key)
    ):
        raise ProviderError("provider_not_configured", "Provider credentials are not configured")
    if not isinstance(config.model, str) or not _MODEL_RE.fullmatch(config.model):
        raise ProviderError("invalid_provider_config", "Provider model identifier is invalid")
    if (
        not isinstance(config.timeout_seconds, int)
        or isinstance(config.timeout_seconds, bool)
        or not 1 <= config.timeout_seconds <= 120
    ):
        raise ProviderError("invalid_provider_config", "Provider timeout is outside the supported range")
    if (
        not isinstance(config.max_response_bytes, int)
        or isinstance(config.max_response_bytes, bool)
        or not 1024 <= config.max_response_bytes <= 4_000_000
    ):
        raise ProviderError("invalid_provider_config", "Provider response limit is outside the supported range")
    return endpoint


def _read_bounded_response(response: requests.Response, limit: int) -> bytes:
    body = bytearray()
    try:
        for chunk in response.iter_content(chunk_size=16_384):
            if not chunk:
                continue
            if len(body) + len(chunk) > limit:
                raise ProviderError("provider_response_too_large", "Provider response exceeded the size limit")
            body.extend(chunk)
    except requests.RequestException as exc:
        raise ProviderError("provider_invalid_response", "Provider response could not be read", retryable=True) from exc
    finally:
        response.close()
    return bytes(body)


def _usage_from_payload(payload: object) -> TokenUsage:
    if not isinstance(payload, dict):
        return TokenUsage()
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return TokenUsage()

    def token_value(*keys: str) -> int | None:
        for key in keys:
            value = usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 2_000_000_000:
                return value
        return None

    input_tokens = token_value("prompt_tokens", "input_tokens")
    output_tokens = token_value("completion_tokens", "output_tokens")
    if input_tokens is not None and output_tokens is not None:
        return TokenUsage(input_tokens, output_tokens, "provider")
    if input_tokens is not None or output_tokens is not None:
        return TokenUsage(input_tokens, output_tokens, "partial_provider")
    return TokenUsage()


class OpenAICompatibleProvider:
    """Non-streaming JSON chat adapter for reviewed OpenAI-compatible endpoints."""

    capabilities = ProviderCapabilities()

    def complete(
        self,
        config: ProviderConfig,
        messages: Sequence[ChatMessage],
        *,
        max_output_tokens: int = 1024,
    ) -> ChatResult:
        endpoint = _validate_request(config, messages, max_output_tokens)
        payload = {
            "model": config.model,
            "messages": [{"role": item.role, "content": item.content} for item in messages],
            "max_tokens": max_output_tokens,
            "stream": False,
        }
        try:
            response = requests.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {config.api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json=payload,
                timeout=(min(config.timeout_seconds, 10), config.timeout_seconds),
                verify=True,
                allow_redirects=False,
                stream=True,
            )
        except requests.Timeout as exc:
            raise ProviderError("provider_timeout", "Provider request timed out", retryable=True) from exc
        except requests.RequestException as exc:
            raise ProviderError("provider_unavailable", "Provider could not be reached", retryable=True) from exc

        if response.status_code in (301, 302, 303, 307, 308):
            response.close()
            raise ProviderError("provider_redirect_rejected", "Provider redirect was rejected")
        if response.status_code in (401, 403):
            response.close()
            raise ProviderError("provider_auth_failed", "Provider rejected the configured credentials")
        if response.status_code == 429:
            response.close()
            raise ProviderError("provider_rate_limited", "Provider rate limit was reached", retryable=True)
        if response.status_code >= 500:
            response.close()
            raise ProviderError("provider_unavailable", "Provider is temporarily unavailable", retryable=True)
        if response.status_code < 200 or response.status_code >= 300:
            response.close()
            raise ProviderError("provider_request_failed", "Provider rejected the request")
        body = _read_bounded_response(response, config.max_response_bytes)
        try:
            result = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderError("provider_invalid_response", "Provider returned an invalid response") from exc
        try:
            choice = result["choices"][0]
            text = choice["message"]["content"]
        except (TypeError, KeyError, IndexError) as exc:
            raise ProviderError("provider_invalid_response", "Provider response did not contain a chat completion") from exc
        if not isinstance(text, str):
            raise ProviderError("provider_unsupported_response", "Provider returned an unsupported content format")
        finish_reason = choice.get("finish_reason")
        if not isinstance(finish_reason, str):
            finish_reason = None
        else:
            finish_reason = finish_reason[:40]
        return ChatResult(text=text, usage=_usage_from_payload(result), finish_reason=finish_reason)


class MockChatProvider:
    """Deterministic local adapter for tests and explicitly marked mock environments."""

    capabilities = ProviderCapabilities()
    is_mock = True

    def complete(
        self,
        config: ProviderConfig,
        messages: Sequence[ChatMessage],
        *,
        max_output_tokens: int = 1024,
    ) -> ChatResult:
        _validate_messages(messages, max_output_tokens)
        return ChatResult(
            text="这是 Mock 回复：尚未连接或验证真实 AI Provider。",
            usage=TokenUsage(source="unavailable"),
            finish_reason="mock",
        )
