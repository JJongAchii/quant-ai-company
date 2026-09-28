#!/usr/bin/env bash
# Run only inside an SSH session forwarding local port 1455 to this host's loopback.
# This container runs official login only. It has no Slack/database/API credentials.
set -euo pipefail

account_dir=/var/lib/quant-company/codex/backup-auth
[[ -d "$account_dir" && ! -L "$account_dir" ]] || {
  echo 'Prepare the backup account directory before login.' >&2; exit 2;
}
runtime_image=$(docker inspect --format '{{.Config.Image}}' quant-company-codex-runtime-1)
[[ "$runtime_image" =~ ^quant-company-codex:[0-9a-f]{40}$ ]] || {
  echo 'An exact deployed Codex image is required.' >&2; exit 2;
}

exec docker run --rm -it --name quant-company-backup-browser-login \
  --user 10001:10001 --read-only --cap-drop ALL --security-opt no-new-privileges:true \
  --network host --memory 192m --cpus 0.25 --pids-limit 64 \
  --tmpfs /tmp:size=64m,mode=1777 \
  --mount "type=bind,source=$account_dir,target=/state/auth" \
  -e CODEX_HOME=/state/auth --entrypoint codex "$runtime_image" \
  -c 'cli_auth_credentials_store="file"' -c 'forced_login_method="chatgpt"' login
