# shellcheck shell=bash
# Shared by the deploy scripts. Sourced, not run.
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$DEPLOY_DIR/.." && pwd)"
PROFILE="${AWS_PROFILE:-lane}"
REGION="${AWS_REGION:-us-east-1}"
STACK="${LANE_STACK:-lane}"

aws() { command aws --profile "$PROFILE" --region "$REGION" "$@"; }
say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

# The stack's outputs, as LANE_OUT_<Name> variables
load_outputs() {
  local rows
  rows=$(aws cloudformation describe-stacks --stack-name "$STACK" \
    --query 'Stacks[0].Outputs[].[OutputKey,OutputValue]' --output text) \
    || fail "No stack named $STACK yet: run deploy/stack.sh up"
  while read -r key value; do
    [ -n "$key" ] && export "LANE_OUT_${key}=${value}"
  done <<< "$rows"
}

# Run a shell script on the instance over SSM (no SSH port) and print its output.
# Fails when the script fails.
on_host() {
  local script="$1" id status
  id=$(aws ssm send-command --instance-ids "$LANE_OUT_InstanceId" \
    --document-name AWS-RunShellScript --timeout-seconds 1800 \
    --parameters "$(python3 -c 'import json,sys; print(json.dumps({"commands": [sys.argv[1]], "executionTimeout": ["1800"]}))' "$script")" \
    --query 'Command.CommandId' --output text)
  while :; do
    sleep 3
    status=$(aws ssm get-command-invocation --command-id "$id" --instance-id "$LANE_OUT_InstanceId" \
      --query Status --output text 2>/dev/null || echo Pending)
    case "$status" in Pending|InProgress|Delayed) ;; *) break ;; esac
  done
  aws ssm get-command-invocation --command-id "$id" --instance-id "$LANE_OUT_InstanceId" \
    --query '[StandardOutputContent,StandardErrorContent]' --output text
  [ "$status" = Success ] || fail "On the instance: $status"
}
