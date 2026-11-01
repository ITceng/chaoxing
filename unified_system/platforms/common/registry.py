from __future__ import annotations

from threading import RLock

from unified_system.platforms.common.base_adapter import PlatformAdapter
from unified_system.platforms.common.errors import AdapterError


class AdapterRegistry:
    """Explicit in-process adapter registry; planned adapters are not auto-enabled."""

    def __init__(self):
        self._adapters: dict[str, PlatformAdapter] = {}
        self._lock = RLock()

    def register(self, adapter: PlatformAdapter) -> None:
        info = adapter.platform_info()
        if not info.key or not info.key.replace("_", "").replace("-", "").isalnum():
            raise ValueError("invalid platform key")
        if not info.adapter_version:
            raise ValueError("adapter version is required")
        with self._lock:
            if info.key in self._adapters:
                raise ValueError(f"adapter already registered: {info.key}")
            self._adapters[info.key] = adapter

    def get(self, platform_key: str) -> PlatformAdapter:
        with self._lock:
            adapter = self._adapters.get(platform_key)
        if adapter is None:
            raise AdapterError("adapter_unavailable", f"No verified adapter is registered for {platform_key}")
        return adapter

    def list_platforms(self) -> list[dict]:
        with self._lock:
            adapters = list(self._adapters.values())
        return [
            {"platform": adapter.platform_info().__dict__, "capabilities": adapter.capabilities().public()}
            for adapter in sorted(adapters, key=lambda value: value.platform_info().key)
        ]
