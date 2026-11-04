#!/usr/bin/env bash
# Deploy from this Mac: build both images (arm64, the same as the Graviton host), push them to
# ECR, write the instance's settings (no secrets: those are in Secrets Manager), and start them
# there with remote-deploy.sh, which migrates, waits for health and rolls back on failure.
#   deploy/deploy.sh
# Pushes to main deploy on their own (.github/workflows/deploy.yml); this is the manual path,
# and the one that writes the instance's settings the first time.
# Needs: the stack (deploy/stack.sh up), the key in Secrets Manager (deploy/secrets.sh), and
# deploy/gate.env (deploy/gate.sh > deploy/gate.env) for the /mail password.
source "$(dirname "$0")/lib.sh"
cd "$DEPLOY_DIR"

load_outputs
[ -f gate.env ] || fail "deploy/gate.env is missing: run deploy/gate.sh > deploy/gate.env"
# shellcheck disable=SC1091
source gate.env
docker info >/dev/null 2>&1 || fail "Docker is not running"

site="$LANE_OUT_Site"
second="$LANE_OUT_SslipSite"  # kept working beside the domain
# shellcheck disable=SC1091
[ -f stack.env ] && source stack.env
senders="${LANE_ALLOWED_SENDERS:-}"
[ -n "${LANE_OUT_InboxAddress:-}" ] && senders="${senders:+$senders,}agent@${LANE_OUT_InboxAddress#*@}"
allowlist=$(python3 -c 'import json,sys; print(json.dumps([s.strip() for s in sys.argv[1].split(",") if s.strip()]))' "$senders")
tag="$(git -C "$REPO_DIR" rev-parse --short HEAD)$(git -C "$REPO_DIR" diff --quiet HEAD || echo "-dirty")"
api="$LANE_OUT_ApiRepositoryUri:$tag"
web="$LANE_OUT_WebRepositoryUri:$tag"

say "Building images $tag"
docker build --platform linux/arm64 -f "$REPO_DIR/backend/Dockerfile" -t "$api" "$REPO_DIR"
docker build --platform linux/arm64 -f "$REPO_DIR/frontend/Dockerfile" \
  --build-arg NEXT_PUBLIC_LANE_API_URL="https://$site" \
  --build-arg NEXT_PUBLIC_LANE_COGNITO_DOMAIN="$LANE_OUT_CognitoDomain" \
  --build-arg NEXT_PUBLIC_LANE_COGNITO_CLIENT_ID="$LANE_OUT_UserPoolClientId" \
  -t "$web" "$REPO_DIR"

say "Pushing to ECR"
registry="${LANE_OUT_ApiRepositoryUri%%/*}"
aws ecr get-login-password | docker login --username AWS --password-stdin "$registry" >/dev/null
docker push --quiet "$api"
docker push --quiet "$web"

say "Writing the stack's settings"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
current_images=""
# Keep the images the instance runs now in the new settings, so remote-deploy.sh can roll back
current_images=$(on_host "sed -n 's/^LANE_\(API\|WEB\)_IMAGE=/&/p' /opt/lane/.env 2>/dev/null || true" | grep '^LANE_' || true)
cat > "$work/.env" <<EOF
LANE_ENV=production
LANE_SITE=$( [ "$site" = "$second" ] && echo "$site" || echo "$site, $second" )
LANE_CORS_ORIGINS=$( [ "$site" = "$second" ] && echo "[\"https://$site\"]" || echo "[\"https://$site\", \"https://$second\"]" )
LANE_GATE_USER=$LANE_GATE_USER
LANE_GATE_HASH='$LANE_GATE_HASH'
LANE_HTTP_PORT=80
LANE_HTTPS_PORT=443
${current_images:-LANE_API_IMAGE=$api
LANE_WEB_IMAGE=$web}
LANE_SECRETS_ID=$LANE_OUT_AppSecretArn
LANE_DB_SECRET_ID=$LANE_OUT_DatabaseSecretArn
LANE_SECRETS_REGION=$REGION
LANE_DATABASE_URL=postgresql+psycopg://lane:{password}@$LANE_OUT_DatabaseHost:5432/lane?sslmode=require
LANE_REQUIRE_SIGN_IN=true
LANE_DEMO_LOGIN=false
LANE_COGNITO_REGION=$REGION
LANE_COGNITO_USER_POOL_ID=$LANE_OUT_UserPoolId
LANE_COGNITO_CLIENT_ID=$LANE_OUT_UserPoolClientId
LANE_LOG_JSON=true
LANE_RUN_MODE=queue
LANE_MAILPIT_URL=http://mailpit:8025/mail
LANE_SES_INBOX_PREFIX=$( [ -n "${LANE_OUT_InboxAddress:-}" ] && echo inbound/ )
LANE_INTAKE_ALLOWED_SENDERS=$allowlist
LANE_BLOB_BACKEND=s3
LANE_S3_BUCKET=$LANE_OUT_BucketName
LANE_S3_REGION=$REGION
LANE_S3_ENDPOINT_URL=
LANE_S3_ACCESS_KEY_ID=
LANE_S3_SECRET_ACCESS_KEY=
LANE_ANALYZE_AUTO_BUDGET_USD=0
EOF
# What a CI build on the instance needs for the web image
cat > "$work/build.env" <<EOF
LANE_SITE=$site
LANE_COGNITO_DOMAIN=$LANE_OUT_CognitoDomain
LANE_COGNITO_CLIENT_ID=$LANE_OUT_UserPoolClientId
EOF
cp docker-compose.yml docker-compose.aws.yml Caddyfile remote-deploy.sh ci-build.sh "$work/"

handoff="s3://$LANE_OUT_BucketName/deploy"
aws s3 cp --quiet "$work/" "$handoff/" --recursive

say "Starting on the instance"
on_host "set -e
export AWS_DEFAULT_REGION=$REGION
cd /opt/lane
aws s3 cp --quiet $handoff/ . --recursive --exclude 'cache/*' --exclude 'src-*'
aws s3 rm --quiet $handoff/.env
chmod 600 .env
chmod +x remote-deploy.sh ci-build.sh
aws ecr get-login-password | docker login --username AWS --password-stdin $registry >/dev/null
docker pull --quiet $api >/dev/null
docker pull --quiet $web >/dev/null
./remote-deploy.sh $api $web
docker image prune -f >/dev/null"

say "Checking https://$site"
for _ in $(seq 1 30); do
  page=$(curl -s -o /dev/null -w '%{http_code}' "https://$site/" || true)
  [ "$page" = 200 ] && break
  sleep 5
done
health=$(curl -s -o /dev/null -w '%{http_code}' "https://$site/api/v1/health" || true)
anon=$(curl -s -o /dev/null -w '%{http_code}' "https://$site/api/v1/submissions" || true)
also=$(curl -s -o /dev/null -w '%{http_code}' "https://$second/api/v1/health" || true)
mail=$(curl -s -o /dev/null -w '%{http_code}' "https://$site/mail/" || true)
echo "Site: https://$site   (page: $page, health: $health, API without sign-in: $anon, /mail without its password: $mail; https://$second health: $also)"
[ "$page" = 200 ] && [ "$health" = 200 ] && [ "$anon" = 401 ] && [ "$mail" = 401 ] && [ "$also" = 200 ] ||
  fail "The site is not answering as expected"
say "Deployed $tag"
