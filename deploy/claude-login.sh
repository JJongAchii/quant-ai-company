#!/usr/bin/env bash
# Renew the claude-runtime subscription login (docs/claude-runtime.md). Run it interactively on the company host:
#   ssh -t <host> sudo bash /opt/quant-company/current/deploy/claude-login.sh [login|status]
# The login code belongs in this terminal, never Slack or chat. Only the login state and its expiry are printed.
set -euo pipefail

release=/opt/quant-company/current
credentials=/var/lib/quant-company/claude/auth/.credentials.json

status() {
  docker exec quant-company-claude-runtime-1 claude auth status | python3 -c '
import json, sys
d = json.load(sys.stdin)
print("loggedIn={} authMethod={} subscriptionType={}".format(d.get("loggedIn"), d.get("authMethod"), d.get("subscriptionType")))'
  python3 - "$credentials" <<'PY'
import json, sys
from datetime import datetime, timedelta, timezone
login = json.load(open(sys.argv[1])).get("claudeAiOauth") or {}
expiry = login.get("refreshTokenExpiresAt")
when = datetime.fromtimestamp(expiry / 1000, timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M KST") if expiry else "unknown"
print("next renewal before:", when)
PY
}

case "${1:-login}" in
  login)
    cd "$release/deploy"
    docker compose -p quant-company --profile claude --env-file /var/lib/quant-company/config/runtime.env \
      -f compose.yaml run --rm --no-deps claude-runtime claude auth login
    status ;;
  status) status ;;
  *) echo 'Usage: sudo bash deploy/claude-login.sh [login|status]' >&2; exit 2 ;;
esac
