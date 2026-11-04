#!/usr/bin/env bash
# People who may sign in to Lane on AWS (Cognito, invitation only).
#   deploy/users.sh add <email> <role> ["Full Name"]   invite; Cognito emails a temporary password
#   deploy/users.sh role <email> <role>                change someone's role
#   deploy/users.sh list                               everyone, with their role
#   deploy/users.sh remove <email>                     they can no longer sign in
# Roles: underwriter, senior_underwriter, viewer, ops, admin
source "$(dirname "$0")/lib.sh"
load_outputs
pool="$LANE_OUT_UserPoolId"
roles="underwriter senior_underwriter viewer ops admin"
check_role() { [[ " $roles " == *" $1 "* ]] || fail "Role must be one of: $roles"; }

case "${1:-}" in
add)
  email="${2:?email}"; role="${3:?role}"; name="${4:-$2}"
  check_role "$role"
  aws cognito-idp admin-create-user --user-pool-id "$pool" --username "$email" \
    --user-attributes Name=email,Value="$email" Name=email_verified,Value=true Name=name,Value="$name" \
    --desired-delivery-mediums EMAIL >/dev/null
  aws cognito-idp admin-add-user-to-group --user-pool-id "$pool" --username "$email" --group-name "$role"
  say "Invited $email as $role. Cognito has emailed them a temporary password."
  ;;
role)
  email="${2:?email}"; role="${3:?role}"
  check_role "$role"
  for g in $roles; do
    aws cognito-idp admin-remove-user-from-group --user-pool-id "$pool" --username "$email" --group-name "$g" 2>/dev/null || true
  done
  aws cognito-idp admin-add-user-to-group --user-pool-id "$pool" --username "$email" --group-name "$role"
  say "$email is now $role (from their next sign-in)"
  ;;
list)
  for g in $roles; do
    aws cognito-idp list-users-in-group --user-pool-id "$pool" --group-name "$g" \
      --query 'Users[].[Attributes[?Name==`email`].Value|[0], UserStatus]' --output text |
      while read -r email status; do [ -n "$email" ] && printf '%-40s %-20s %s\n' "$email" "$g" "$status"; done
  done
  ;;
remove)
  email="${2:?email}"
  aws cognito-idp admin-delete-user --user-pool-id "$pool" --username "$email"
  say "Removed $email"
  ;;
*)
  sed -n '2,7p' "$0"; exit 1
  ;;
esac
