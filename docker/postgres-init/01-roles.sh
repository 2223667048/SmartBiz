#!/bin/sh
set -eu
# Runs once on EMPTY PostgreSQL volume. Secrets are safely quoted by PostgreSQL %L.
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -v app_password="$PG_APP_PASSWORD" -v worker_password="$PG_WORKER_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE smartbiz_app LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS', :'app_password') \gexec
SELECT format('CREATE ROLE smartbiz_worker LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS', :'worker_password') \gexec
SQL
