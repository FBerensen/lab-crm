#!/bin/sh
set -eu
psql -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  -v app_password="$LAB_DB_PASSWORD" <<'SQL'
CREATE ROLE labcrm LOGIN PASSWORD :'app_password'
  NOSUPERUSER NOCREATEDB NOCREATEROLE;
ALTER DATABASE labcrm OWNER TO labcrm;
GRANT ALL ON SCHEMA public TO labcrm;
SQL
