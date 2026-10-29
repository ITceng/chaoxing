# -*- coding: utf-8 -*-
"""平台注册表：按 key 查找平台、构造适配器实例.

用法::

    from platforms import list_platforms, build_adapter, get_platform

    for item in list_platforms():      # 面板下拉框用
        print(item["key"], item["name"], item["available"])

    adapter = build_adapter("chaoxing", common_config, tiku_config)

第三方平台适配器只需要在导入时调用 ``register_platform(spec)``，
或者写进本文件的 ``_BUILTIN_IMPORTS`` 列表即可。
"""
from __future__ import annotations

import importlib
from typing import Any, Optional

from platforms.base import (
    PLATFORM_API_VERSION,
    PlatformError,
    PlatformNotImplemented,
    PlatformSpec,
    validate_adapter,
)

_REGISTRY: dict[str, PlatformSpec] = {}

# 内置适配器模块：导入时自动注册。新增平台时在这里加一行即可。
_BUILTIN_IMPORTS = (
    "platforms.chaoxing",
    "platforms.demo",
    "platforms.zhihuishu",
)

_loaded = False


def _ensure_loaded() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    for module in _BUILTIN_IMPORTS:
        try:
            importlib.import_module(module)
        except Exception as exc:  # noqa: BLE001 - 单个平台导入失败不能拖垮整个面板
            import logging

            logging.getLogger(__name__).warning("平台适配器 %s 加载失败: %s", module, exc)


def register_platform(spec: PlatformSpec) -> PlatformSpec:
    """注册（或覆盖）一个平台."""
    if not spec.key or not spec.key.replace("_", "").replace("-", "").isalnum():
        raise ValueError(f"平台 key 不合法: {spec.key!r}（只允许字母、数字、下划线和短横线）")
    _REGISTRY[spec.key] = spec
    return spec


def list_platforms(include_planned: bool = False) -> list[dict[str, Any]]:
    """列出所有平台（面板 ``/api/meta``）."""
    _ensure_loaded()
    items = [spec.public() for spec in _REGISTRY.values()]
    if not include_planned:
        items = [item for item in items if item["available"]]
    return sorted(items, key=lambda item: (item["status"] != "stable", item["key"]))


def get_platform(key: str) -> PlatformSpec:
    _ensure_loaded()
    spec = _REGISTRY.get(str(key or "").strip() or "chaoxing")
    if spec is None:
        available = ", ".join(sorted(_REGISTRY))
        raise PlatformError(f"未知平台 {key!r}，可用平台: {available}")
    return spec


def build_adapter(
    key: str,
    common_config: dict[str, Any],
    tiku_config: Optional[dict[str, Any]] = None,
) -> Any:
    """构造并自检一个平台适配器."""
    spec = get_platform(key)
    if spec.status == "planned" or spec.builder is None:
        raise PlatformNotImplemented(
            f"平台「{spec.name}」目前只有适配器骨架，尚未实现登录与刷课逻辑。"
            "如果你有该平台的账号与抓包记录，可以按 docs/adding-a-platform.md 补全。"
        )
    if PLATFORM_API_VERSION < 1:  # pragma: no cover - 预留
        raise PlatformError("适配器接口版本不兼容")
    try:
        adapter = spec.builder(common_config, tiku_config or {})
    except PlatformError:
        raise
    except Exception as exc:  # noqa: BLE001 - 统一包装成可读错误
        raise PlatformError(f"初始化平台「{spec.name}」失败: {type(exc).__name__}: {exc}") from exc

    missing = validate_adapter(adapter)
    if missing:
        raise PlatformError(
            f"平台「{spec.name}」的适配器缺少必需接口: {', '.join(missing)}"
        )
    return adapter


def platform_keys() -> list[str]:
    _ensure_loaded()
    return sorted(_REGISTRY)


def default_platform() -> str:
    return "chaoxing"


__all__ = [
    "PLATFORM_API_VERSION",
    "PlatformError",
    "PlatformNotImplemented",
    "PlatformSpec",
    "build_adapter",
    "default_platform",
    "get_platform",
    "list_platforms",
    "platform_keys",
    "register_platform",
    "validate_adapter",
]
