#!/bin/bash
# Load backend/schema.sql into the server's database, only when that database is brand new and empty.
# Works never creates or migrates its schema by itself in production (deploy/README.md); this is the one place it is
# loaded, and it refuses to touch a database that already holds any table.
#   deploy/server/init-database.sh
# Exit 0: the database already has a schema, or it was just created. Exit 1: it holds tables but no schema version.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
dc() { "$here/dc" "$@"; }
psql() { dc exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -X -q -U "$POSTGRES_USER" -d "$POSTGRES_DB" "$@"' psql "$@"; }

dc up -d --wait postgres
tables=$(psql -tAc "select count(*) from pg_tables where schemaname = 'public'")
if [ "$tables" != 0 ]; then
  if version=$(psql -tAc "select max(version) from schema_version" 2>/dev/null) && [ -n "$version" ]; then
    echo "init-database: schema version $version is already installed; nothing to do."
    exit 0
  fi
  echo "init-database: the database holds $tables tables but no schema version; refusing to change it." >&2
  exit 1
fi
echo "init-database: loading backend/schema.sql into the new empty database."
psql < "$here/../../backend/schema.sql"
echo "init-database: installed schema version $(psql -tAc 'select max(version) from schema_version')."
