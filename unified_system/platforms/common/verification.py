from __future__ import annotations

from enum import StrEnum


class VerificationState(StrEnum):
    """Explicit lifecycle states; passing a Mock must never enable capabilities."""

    NOT_CONFIGURED = "NOT_CONFIGURED"
    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    VERIFIED = "VERIFIED"
    EXPIRED = "EXPIRED"
    ERROR = "ERROR"
    DISABLED = "DISABLED"
