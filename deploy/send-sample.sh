#!/usr/bin/env bash
# Send an email into Lane the way the agent would:
#   deploy/send-sample.sh               the sample packet into the stack's Mailpit (state A)
#   deploy/send-sample.sh reply         the agent's reply to the marked-sent request (state B)
#   deploy/send-sample.sh --ses [reply] the same as real email: through SES to the domain's inbox,
#                                       from agent@<domain>
#   deploy/send-sample.sh local [reply] against the local rehearsal (docker compose here)
source "$(dirname "$0")/lib.sh"
cd "$DEPLOY_DIR"

where=aws
via=mailpit
while [ $# -gt 0 ]; do
  case "$1" in
    local) where=local; shift ;;
    --ses) via=ses; shift ;;
    *) break ;;
  esac
done
what="${1:-sample}"
case "$what" in sample|reply) ;; *) sed -n '2,7p' "$0"; exit 1 ;; esac

if [ "$where" = local ]; then
  docker compose -f docker-compose.yml -f docker-compose.local.yml exec -T worker \
    python /app/scripts/demo_send.py mailpit "$what"
  exit 0
fi
load_outputs
inbox=""
if [ "$via" = ses ]; then
  [ -n "${LANE_OUT_InboxAddress:-}" ] || fail "No domain on the stack yet: set LANE_DOMAIN and run deploy/stack.sh up"
  inbox="--inbox $LANE_OUT_InboxAddress"
fi
on_host "cd /opt/lane && docker compose exec -T worker python /app/scripts/demo_send.py $via $what $inbox"
