from __future__ import annotations

import os
import sys
import time
from pathlib import Path


DEFAULT_HEALTH_FILE = "/tmp/worker-health"
DEFAULT_MAX_AGE_SECONDS = 90


def health_file_path() -> Path:
    return Path(os.environ.get("WORKER_HEALTH_FILE", DEFAULT_HEALTH_FILE))


def clear_health_marker() -> None:
    """Remove any marker left over from a prior process before adapter startup."""
    try:
        health_file_path().unlink(missing_ok=True)
    except OSError:
        # The health command will fail closed if it cannot read a fresh marker.
        pass


def record_backend_heartbeat() -> bool:
    """Record that the Worker successfully contacted its private backend API."""
    try:
        health_file_path().touch(exist_ok=True)
    except OSError:
        return False
    return True


def is_healthy(*, now: float | None = None) -> bool:
    path = health_file_path()
    try:
        max_age = float(os.environ.get("WORKER_HEARTBEAT_TTL_SECONDS", str(DEFAULT_MAX_AGE_SECONDS)))
        if not 1 <= max_age <= 600:
            return False
        modified = path.stat().st_mtime
    except (OSError, TypeError, ValueError):
        return False
    current = time.time() if now is None else now
    age = current - modified
    return 0 <= age <= max_age


def main() -> int:
    return 0 if is_healthy() else 1


if __name__ == "__main__":
    sys.exit(main())
