# -*- coding: utf-8 -*-
"""多平台接入层.

调度器、面板、无人值守都不感知具体平台；新增平台只需实现
``platforms/base.py`` 里的契约并注册一个 ``PlatformSpec``。
"""
from platforms.base import (
    PLATFORM_API_VERSION,
    REQUIRED_ATTRIBUTES,
    REQUIRED_METHODS,
    STATUS_DEMO,
    STATUS_EXPERIMENTAL,
    STATUS_PLANNED,
    STATUS_STABLE,
    PlatformError,
    PlatformNotImplemented,
    PlatformSpec,
    UnsupportedCapability,
    validate_adapter,
)
from platforms.registry import (
    build_adapter,
    default_platform,
    get_platform,
    list_platforms,
    platform_keys,
    register_platform,
)

__all__ = [
    "PLATFORM_API_VERSION",
    "REQUIRED_ATTRIBUTES",
    "REQUIRED_METHODS",
    "STATUS_DEMO",
    "STATUS_EXPERIMENTAL",
    "STATUS_PLANNED",
    "STATUS_STABLE",
    "PlatformError",
    "PlatformNotImplemented",
    "PlatformSpec",
    "UnsupportedCapability",
    "build_adapter",
    "default_platform",
    "get_platform",
    "list_platforms",
    "platform_keys",
    "register_platform",
    "validate_adapter",
]
