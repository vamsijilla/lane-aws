#!/usr/bin/env bash
# The AWS stack: deploy/stack.sh up | outputs | down
#   up       create it, or update it to cloudformation.yml (safe to run again)
#   outputs  the site, the instance and the rest
#   down     delete everything it made, including the database and the files
source "$(dirname "$0")/lib.sh"
cd "$DEPLOY_DIR"

case "${1:-}" in
up)
  # Where alarms are emailed, asked once and kept here (never committed). Passwords and keys
  # are no longer kept here: Secrets Manager generates and holds them.
  [ -f stack.env ] && source stack.env
  if [ -z "${LANE_ALERT_EMAIL:-}" ]; then
    read -r -p "Email for alarms (you confirm it once): " LANE_ALERT_EMAIL
    [[ "$LANE_ALERT_EMAIL" == *@*.* ]] || fail "That does not look like an email address"
    umask 077
    echo "LANE_ALERT_EMAIL=$LANE_ALERT_EMAIL" > stack.env
  fi

  vpc=$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)
  [ "$vpc" != None ] || fail "This region has no default VPC"
  # Zones that offer the instance type; the instance goes in the first, the database spans all
  type="${LANE_INSTANCE_TYPE:-t4g.large}"
  zones=$(aws ec2 describe-instance-type-offerings --location-type availability-zone \
    --filters Name=instance-type,Values="$type" --query 'InstanceTypeOfferings[].Location' --output text)
  subnets=$(aws ec2 describe-subnets --filters Name=vpc-id,Values="$vpc" Name=default-for-az,Values=true \
    --query 'Subnets[].[AvailabilityZone,SubnetId]' --output text | sort)
  instance_subnet=$(awk -v z="$zones" 'BEGIN{split(z,a," "); for(i in a) ok[a[i]]=1} ok[$1]{print $2; exit}' <<< "$subnets")
  [ -n "$instance_subnet" ] || fail "No default subnet offers $type"
  all_subnets=$(awk '{print $2}' <<< "$subnets" | paste -sd, -)

  say "Creating or updating stack $STACK in $REGION (RDS takes about 10 minutes the first time)"
  aws cloudformation deploy --stack-name "$STACK" --template-file cloudformation.yml \
    --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM --no-fail-on-empty-changeset \
    --parameter-overrides VpcId="$vpc" SubnetIds="$all_subnets" \
      InstanceSubnetId="$instance_subnet" InstanceType="$type" AlertEmail="$LANE_ALERT_EMAIL" \
      DomainName="${LANE_DOMAIN:-}"
  "$DEPLOY_DIR/stack.sh" outputs
  if [ -n "${LANE_DOMAIN:-}" ]; then
    # CloudFormation makes the receipt rules but cannot switch them on
    aws ses set-active-receipt-rule-set --rule-set-name lane-inbound
    say "Inbound email on: submissions@$LANE_DOMAIN"
  fi

  say "Waiting for the instance to finish installing Docker"
  load_outputs
  aws ec2 wait instance-status-ok --instance-ids "$LANE_OUT_InstanceId"
  for _ in $(seq 1 40); do
    ping=$(aws ssm describe-instance-information --filters Key=InstanceIds,Values="$LANE_OUT_InstanceId" \
      --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null || true)
    [ "$ping" = Online ] && break
    sleep 10
  done
  [ "$ping" = Online ] || fail "The instance is not reachable over SSM yet; run this again in a few minutes"
  on_host 'for i in $(seq 1 60); do [ -f /opt/lane/.ready ] && docker compose version && exit 0; sleep 5; done; exit 1'
  say "Stack ready. Next: deploy/deploy.sh"
  ;;
outputs)
  load_outputs
  env | grep '^LANE_OUT_' | sed 's/^LANE_OUT_//' | sort
  ;;
down)
  load_outputs
  read -r -p "Delete stack $STACK, its database and every file in $LANE_OUT_BucketName? Type the stack name: " answer
  [ "$answer" = "$STACK" ] || fail "Not deleted"
  # An active receipt rule set cannot be deleted
  aws ses set-active-receipt-rule-set 2>/dev/null || true
  say "Emptying the bucket"
  aws s3 rm "s3://$LANE_OUT_BucketName" --recursive --quiet
  say "Deleting the stack (about 10 minutes)"
  aws cloudformation delete-stack --stack-name "$STACK"
  aws cloudformation wait stack-delete-complete --stack-name "$STACK"
  rm -f stack.env
  say "Deleted. Nothing from this stack is left running or billed."
  ;;
*)
  sed -n "2,6p" "$DEPLOY_DIR/stack.sh"
  exit 1
  ;;
esac
