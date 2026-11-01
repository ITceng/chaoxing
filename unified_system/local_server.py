"""Loopback-only, locally controlled Flask server for Windows single-user runs.

The controller writes a PID-specific stop request under the ignored runtime directory.
This module intentionally does not expose a network shutdown endpoint.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

from werkzeug.serving import make_server
from sqlalchemy.engine import make_url

from unified_system.app import create_app


def main() -> int:
    runtime_dir = Path(os.environ.get("UNIFIED_LOCAL_RUNTIME_DIR", "")).resolve()
    stop_file = Path(os.environ.get("UNIFIED_LOCAL_STOP_FILE", "")).resolve()
    if not runtime_dir.is_dir() or stop_file.parent != runtime_dir:
        raise RuntimeError("Local runtime control paths are not configured safely")
    if os.environ.get("APP_ENV") != "development":
        raise RuntimeError("The Windows local server requires APP_ENV=development")
    database_url = os.environ.get("DATABASE_URL", "")
    try:
        database = make_url(database_url)
    except Exception as exc:
        raise RuntimeError("The Windows local server requires a valid local PostgreSQL URL") from exc
    if database.drivername != "postgresql+psycopg" or database.host not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("The Windows local server requires PostgreSQL bound to 127.0.0.1/localhost; SQLite and remote DBs are refused")

    port = int(os.environ.get("UNIFIED_LOCAL_PORT", "8088"))
    app = create_app()
    server = make_server("127.0.0.1", port, app, threaded=True)
    stopping = threading.Event()

    def watch_stop_request() -> None:
        while not stopping.wait(0.25):
            try:
                request = stop_file.read_text(encoding="ascii").strip()
            except (FileNotFoundError, PermissionError, OSError):
                continue
            if request == str(os.getpid()):
                print("PID-matched local stop request received; closing server gracefully.", flush=True)
                server.shutdown()
                return

    watcher = threading.Thread(target=watch_stop_request, name="local-stop-watcher", daemon=True)
    watcher.start()
    print(f"Local Flask server listening on http://127.0.0.1:{port}/", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        stopping.set()
        server.server_close()
        watcher.join(timeout=2)
        print("Local Flask server stopped.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
