from __future__ import annotations

import re


_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


class AdapterError(RuntimeError):
    """Safe, typed adapter failure. Never put credentials or raw responses in `message`."""

    def __init__(self, code: str, message: str, *, retryable: bool = False):
        if not _CODE_RE.fullmatch(code):
            raise ValueError("adapter error code must be a short lowercase identifier")
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable

    def __str__(self) -> str:
        return self.message


class UnsupportedCapability(AdapterError):
    def __init__(self, capability: str):
        super().__init__("unsupported_capability", f"Capability is not supported: {capability}")
        self.capability = capability


class AdapterCancelled(AdapterError):
    def __init__(self):
        super().__init__("cancelled", "Operation cancelled")
