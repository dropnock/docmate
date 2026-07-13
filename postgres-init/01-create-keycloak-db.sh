#!/bin/sh
# Docker only substitutes env vars into .sh init scripts, never .sql files —
# this used to be a hardcoded CREATE USER keycloak WITH PASSWORD 'keycloak',
# which silently ignored whatever KC_DB_PASSWORD was actually configured.
set -e

: "${KC_DB_USERNAME:?KC_DB_USERNAME must be set}"
: "${KC_DB_PASSWORD:?KC_DB_PASSWORD must be set}"
: "${KC_DB_NAME:?KC_DB_NAME must be set}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE USER "$KC_DB_USERNAME" WITH PASSWORD '$KC_DB_PASSWORD';
    CREATE DATABASE "$KC_DB_NAME" OWNER "$KC_DB_USERNAME";
    GRANT ALL PRIVILEGES ON DATABASE "$KC_DB_NAME" TO "$KC_DB_USERNAME";
EOSQL
