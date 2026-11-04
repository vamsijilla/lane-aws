#!/usr/bin/env bash
# Make the site password: prints the three LANE_GATE_* lines for deploy/.env.
#   deploy/gate.sh > deploy/gate.env      asks for the password
# Only a bcrypt hash of the password is kept. Needs htpasswd (part of macOS), not Docker.
set -euo pipefail
read -r -s -p "Password for the site: " pw </dev/tty; echo >&2
[ ${#pw} -ge 12 ] || { echo "Use at least 12 characters" >&2; exit 1; }
read -r -s -p "Again: " again </dev/tty; echo >&2
[ "$pw" = "$again" ] || { echo "The two do not match" >&2; exit 1; }
# htpasswd writes bcrypt as $2y$, the same algorithm Caddy writes as $2a$
hash=$(htpasswd -nbBC 14 "" "$pw" | tr -d ':\n' | sed 's/^\$2y\$/$2a$/')
token=$(openssl rand -hex 32)
echo "LANE_GATE_USER=reviewer"
echo "LANE_GATE_HASH='$hash'"
echo "LANE_GATE_TOKEN=$token"
echo "Saved. Sign in to the site as reviewer with that password." >&2
