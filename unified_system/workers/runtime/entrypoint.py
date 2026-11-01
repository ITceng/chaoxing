from __future__ import annotations

import importlib
import logging
import os
import signal
import sys

from unified_system.platforms.common import HealthStatus, PlatformAdapter
from unified_system.workers.runtime.client import WorkerClient
from unified_system.workers.runtime.engine import WorkerEngine, run_forever
from unified_system.workers.runtime.settings import WorkerSettings


def load_adapter(factory_path: str) -> PlatformAdapter:
    module_name, separator, attribute = factory_path.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("WORKER_ADAPTER_FACTORY must be module.path:factory")
    factory = getattr(importlib.import_module(module_name), attribute)
    adapter = factory()
    if not isinstance(adapter, PlatformAdapter):
        raise TypeError("configured factory did not return a PlatformAdapter")
    return adapter


def main() -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        settings = WorkerSettings.from_env()
        factory_path = os.environ.get("WORKER_ADAPTER_FACTORY", "").strip()
        if not factory_path:
            raise ValueError("WORKER_ADAPTER_FACTORY is required; no placeholder adapter is started")
        adapter = load_adapter(factory_path)
        if adapter.platform_info().key != settings.platform_key:
            raise ValueError("adapter platform key does not match WORKER_PLATFORM")
        health = adapter.health_check()
        if not isinstance(health, HealthStatus) or not health.healthy:
            raise AdapterError("adapter_unhealthy", "Configured platform adapter is not healthy")
        engine = WorkerEngine(WorkerClient(settings), adapter)
    except Exception as exc:  # third-party startup errors can contain credential-bearing response data
        logging.getLogger("unified_system.worker").error("worker startup rejected: %s", type(exc).__name__)
        return 2

    def request_stop(_signal, _frame):
        engine.stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    run_forever(engine)
    return 0


if __name__ == "__main__":
    sys.exit(main())
