#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
: "${UNIFIED_MIGRATION_TEST_URL:?Set a separate disposable PostgreSQL migration-test URL}"
: "${UNIFIED_TEST_POSTGRESQL_URL:?Set a separate disposable PostgreSQL API/concurrency-test URL}"
if [[ "${UNIFIED_TEST_POSTGRESQL_CONFIRM:-}" != "I_UNDERSTAND_TEST_DATABASES_WILL_BE_DROPPED" ]]; then
  printf 'Set UNIFIED_TEST_POSTGRESQL_CONFIRM=I_UNDERSTAND_TEST_DATABASES_WILL_BE_DROPPED to proceed.\n' >&2
  exit 2
fi

python3 <<'PY'
import os
from sqlalchemy.engine import make_url

urls = []
for value in (os.environ["UNIFIED_MIGRATION_TEST_URL"], os.environ["UNIFIED_TEST_POSTGRESQL_URL"]):
    parsed = make_url(value)
    if parsed.get_backend_name() != "postgresql" or not parsed.database or not parsed.database.lower().endswith(("_test", "_testdb")):
        raise SystemExit("Both PostgreSQL test database names must end in _test or _testdb")
    urls.append(parsed)
if urls[0].database == urls[1].database or urls[0].host != urls[1].host:
    raise SystemExit("Use two separate disposable PostgreSQL databases on the same test server")
print("PostgreSQL test URL safety checks passed; credentials are not displayed.")
PY

cd "$ROOT_DIR"
export DATABASE_URL="$UNIFIED_MIGRATION_TEST_URL"
printf '%s\n' '== Alembic upgrade/check/downgrade on migration-test database =='
alembic -c unified_system/alembic.ini upgrade head
alembic -c unified_system/alembic.ini check
alembic -c unified_system/alembic.ini downgrade base

printf '\n%s\n' '== Full unified backend suite on disposable PostgreSQL test database =='
export UNIFIED_TEST_POSTGRESQL_CONFIRM=I_UNDERSTAND_TEST_DB_WILL_BE_DROPPED
python3 -m pytest -q unified_system/tests
printf '\nPostgreSQL migration and runtime tests completed. This script is destructive to the two explicitly named *_test databases; never set these URLs to production.\n'
