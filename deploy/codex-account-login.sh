#!/usr/bin/env bash
# Run interactively on the company host. Device codes belong in this terminal, never Slack.
set -euo pipefail

case "${1:-}" in
  primary) account_home=/state/auth ;;
  backup) account_home=/state/backup-auth ;;
  *) echo 'Usage: bash deploy/codex-account-login.sh primary|backup [status]' >&2; exit 2 ;;
esac

case "${2:-login}" in
  login) login_args=(--device-auth); terminal_args=(-it) ;;
  status) login_args=(status); terminal_args=() ;;
  *) echo 'Only interactive login or status is supported.' >&2; exit 2 ;;
esac

exec docker exec "${terminal_args[@]}" --user 10001:10001 -e "CODEX_HOME=$account_home" \
  quant-company-codex-runtime-1 codex \
  -c 'cli_auth_credentials_store="file"' -c 'forced_login_method="chatgpt"' \
  login "${login_args[@]}"
