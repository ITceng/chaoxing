#!/bin/sh
set -eu

: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${APP_DB_NAME:?APP_DB_NAME is required}"
: "${APP_DB_OWNER:?APP_DB_OWNER is required}"
: "${APP_DB_OWNER_PASSWORD:?APP_DB_OWNER_PASSWORD is required}"
: "${APP_DB_USER:?APP_DB_USER is required}"
: "${APP_DB_PASSWORD:?APP_DB_PASSWORD is required}"

# Use simple SQL identifiers and hex passwords. psql's \getenv plus identifier/literal
# quoting keeps values out of shell command arguments and avoids SQL injection.
for identifier in "$POSTGRES_USER" "$APP_DB_NAME" "$APP_DB_OWNER" "$APP_DB_USER"; do
    case "$identifier" in
        ''|*[!A-Za-z0-9_]*) echo "database name/role names may contain only ASCII letters, digits, and underscore" >&2; exit 1 ;;
    esac
    if [ "${#identifier}" -gt 63 ]; then
        echo "database name/role names may not exceed 63 characters" >&2
        exit 1
    fi
done
for password in "$APP_DB_OWNER_PASSWORD" "$APP_DB_PASSWORD"; do
    case "$password" in
        ''|*[!A-Fa-f0-9]*) echo "database role passwords must be non-empty hexadecimal strings" >&2; exit 1 ;;
    esac
done
case "$APP_DB_NAME" in
    postgres|template0|template1) echo "APP_DB_NAME must be a dedicated application database name" >&2; exit 1 ;;
esac
if [ "$APP_DB_OWNER" = "$APP_DB_USER" ] || [ "$APP_DB_OWNER" = "$POSTGRES_USER" ] || [ "$APP_DB_USER" = "$POSTGRES_USER" ]; then
    echo "cluster admin, migration owner, and runtime database roles must be distinct" >&2
    exit 1
fi

psql --no-password --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set=ON_ERROR_STOP=1 <<'SQL'
\getenv app_db_name APP_DB_NAME
\getenv app_db_owner APP_DB_OWNER
\getenv app_db_owner_password APP_DB_OWNER_PASSWORD
\getenv app_db_user APP_DB_USER
\getenv app_db_password APP_DB_PASSWORD
CREATE ROLE :"app_db_owner" WITH LOGIN PASSWORD :'app_db_owner_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
CREATE ROLE :"app_db_user" WITH LOGIN PASSWORD :'app_db_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
CREATE DATABASE :"app_db_name" WITH OWNER = :"app_db_owner" ENCODING = 'UTF8';
SQL

psql --no-password --username "$POSTGRES_USER" --dbname "$APP_DB_NAME" --set=ON_ERROR_STOP=1 <<'SQL'
\getenv app_db_name APP_DB_NAME
\getenv app_db_owner APP_DB_OWNER
\getenv app_db_user APP_DB_USER
REVOKE CONNECT ON DATABASE :"app_db_name" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"app_db_name" TO :"app_db_owner", :"app_db_user";
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO :"app_db_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"app_db_owner" IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"app_db_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"app_db_owner" IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO :"app_db_user";
SQL
