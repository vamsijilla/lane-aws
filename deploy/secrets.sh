#!/usr/bin/env bash
# Put the Anthropic key into Secrets Manager (lane/app), keeping the generated session secret.
#   deploy/secrets.sh            reads ANTHROPIC_API_KEY from the repo's .env; prints nothing secret
# Run it once after the stack exists, and again only to change the key.
source "$(dirname "$0")/lib.sh"
load_outputs

key=$(sed -n 's/^ANTHROPIC_API_KEY=//p' "$REPO_DIR/.env" | tail -1)
[ -n "$key" ] || fail "ANTHROPIC_API_KEY is not in $REPO_DIR/.env"
current=$(aws secretsmanager get-secret-value --secret-id "$LANE_OUT_AppSecretArn" \
  --query SecretString --output text)
# Merge in Python, reading both from stdin, so neither value is on a command line
updated=$(printf '%s\n%s' "$current" "$key" | python3 -c '
import json, sys
current, key = sys.stdin.read().split("\n", 1)
d = json.loads(current)
d["ANTHROPIC_API_KEY"] = key.strip()
print(json.dumps(d))')
aws secretsmanager put-secret-value --secret-id "$LANE_OUT_AppSecretArn" \
  --secret-string "$updated" --query VersionId --output text >/dev/null
say "Anthropic key stored in Secrets Manager (lane/app)"
