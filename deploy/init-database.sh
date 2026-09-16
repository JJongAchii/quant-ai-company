#!/usr/bin/env bash
set -euo pipefail
# Invoked once by the official PostgreSQL entrypoint on an empty volume.
app_password="$(cat /run/secrets/database_password)"
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=ON_ERROR_STOP=1 --set=app_password="$app_password" <<'SQL'
CREATE ROLE company LOGIN PASSWORD :'app_password';
ALTER DATABASE quant_company OWNER TO company;
SQL
unset app_password
