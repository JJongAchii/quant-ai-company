#!/usr/bin/env bash
# Store the Gmail app password of the evening brief email sender (docs/newsletter.md). Run it interactively:
#   ssh -t <host> sudo bash /opt/quant-company/current/deploy/set-newsletter-secret.sh [set|status|clear]
# The password is typed into this terminal only (never echoed, never an argument) and written in place, mode 0600,
# owned by the dispatcher's UID, into the directory mounted read-only into dispatch. The dispatcher reads it at its
# next check, without a restart or a release. Only whether a password is set is printed.
set -euo pipefail
umask 077

directory=/var/lib/quant-company/secrets/newsletter
file=$directory/app_password

status() {
  if [ -s "$file" ]; then echo "newsletter app password: set"; else echo "newsletter app password: not set"; fi
}

if [ "$(id -u)" -ne 0 ]; then
  echo 'Run it with sudo on the company host.' >&2
  exit 2
fi

case "${1:-set}" in
  set)
    IFS= read -rs -p 'Gmail app password for the newsletter sender (not shown): ' password
    echo
    password="${password//[[:space:]]/}"
    if ! [[ "$password" =~ ^[A-Za-z]{16}$ ]]; then
      unset password
      echo 'A Gmail app password is 16 letters (spaces are ignored); nothing was changed.' >&2
      exit 1
    fi
    # Docker creates the directory empty on the first release that mounts it; create it here if that has not run.
    install -d -m 0755 -o root -g root "$directory"
    [ -e "$file" ] || install -m 0600 -o 10001 -g 10001 /dev/null "$file"
    chown 10001:10001 "$file"
    chmod 0600 "$file"
    # Truncate and write the same inode (printf is a shell builtin, so the value never appears in a process list).
    printf '%s' "$password" > "$file"
    unset password
    status ;;
  status) status ;;
  clear)
    if [ -e "$file" ]; then : > "$file"; fi
    status ;;
  *) echo 'Usage: sudo bash deploy/set-newsletter-secret.sh [set|status|clear]' >&2; exit 2 ;;
esac
