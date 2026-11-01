from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--connect-only", action="store_true", help="test PostgreSQL without requiring current Alembic head")
    args = parser.parse_args()
    url = os.environ.get("DATABASE_URL", "")
    try:
        parsed_url = make_url(url)
    except Exception:
        parsed_url = None
    if (
        parsed_url is None
        or parsed_url.drivername != "postgresql+psycopg"
        or parsed_url.host not in {"127.0.0.1", "localhost"}
    ):
        print(json.dumps({"ok": False, "reason": "DATABASE_URL must use local PostgreSQL/psycopg"}))
        return 2
    expected_database = os.environ.get("LOCAL_DATABASE_NAME")
    if expected_database and parsed_url.database != expected_database:
        print(json.dumps({"ok": False, "reason": "DATABASE_URL database does not match LOCAL_DATABASE_NAME"}))
        return 2
    expected_port = os.environ.get("LOCAL_DATABASE_PORT")
    if expected_port:
        try:
            port_matches = 1 <= int(expected_port) <= 65535 and parsed_url.port == int(expected_port)
        except ValueError:
            port_matches = False
        if not port_matches:
            print(json.dumps({"ok": False, "reason": "DATABASE_URL port does not match LOCAL_DATABASE_PORT"}))
            return 2

    config = Config(str(ROOT / "unified_system" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "unified_system" / "migrations"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    scripts = ScriptDirectory.from_config(config)
    expected = sorted(scripts.get_heads())
    engine = create_engine(url, pool_pre_ping=True, hide_parameters=True)
    try:
        with engine.connect() as connection:
            database_name = connection.execute(text("SELECT current_database()")).scalar_one()
            server_version = connection.execute(text("SHOW server_version" )).scalar_one()
            current = sorted(MigrationContext.configure(connection).get_current_heads())
    except Exception as exc:
        print(json.dumps({"ok": False, "reason": "database connection failed", "error_type": type(exc).__name__}))
        return 3
    finally:
        engine.dispose()

    matches = current == expected
    print(json.dumps({
        "ok": True,
        "database": database_name,
        "postgres_version": server_version,
        "current_revisions": current,
        "expected_heads": expected,
        "migration_current": matches,
    }, sort_keys=True))
    return 0 if args.connect_only or matches else 4


if __name__ == "__main__":
    raise SystemExit(main())
