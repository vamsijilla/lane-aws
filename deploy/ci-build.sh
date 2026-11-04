#!/usr/bin/env bash
# On the instance, from CI: unpack a commit, build both images natively (the instance is arm64),
# then hand over to remote-deploy.sh, which migrates, restarts and rolls back on failure.
#   ci-build.sh <commit sha> <s3 url of the source tarball>
set -euo pipefail
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}"
sha="$1"
src_url="$2"
work="/opt/lane/build/$sha"
rm -rf "$work" && mkdir -p "$work"
aws s3 cp --quiet "$src_url" "$work/src.tgz"
tar -xzf "$work/src.tgz" -C "$work"
rm "$work/src.tgz"

# The site and Cognito settings the web build needs (written by deploy.sh)
# shellcheck disable=SC1091
source /opt/lane/build.env

tag="${sha:0:7}"
docker build -q -f "$work/backend/Dockerfile" -t "lane-api:$tag" "$work" >/dev/null
docker build -q -f "$work/frontend/Dockerfile" -t "lane-web:$tag" \
  --build-arg NEXT_PUBLIC_LANE_API_URL="https://$LANE_SITE" \
  --build-arg NEXT_PUBLIC_LANE_COGNITO_DOMAIN="$LANE_COGNITO_DOMAIN" \
  --build-arg NEXT_PUBLIC_LANE_COGNITO_CLIENT_ID="$LANE_COGNITO_CLIENT_ID" \
  "$work" >/dev/null

# The deploy files from this commit, so they always match its images
cp "$work/deploy/docker-compose.yml" "$work/deploy/docker-compose.aws.yml" "$work/deploy/Caddyfile" /opt/lane/
# Scripts are swapped in with mv (a new file), never overwritten in place: this one is running
for f in remote-deploy.sh ci-build.sh; do
  install -m 755 "$work/deploy/$f" "/opt/lane/$f.new" && mv "/opt/lane/$f.new" "/opt/lane/$f"
done
rm -rf "$work"
/opt/lane/remote-deploy.sh "lane-api:$tag" "lane-web:$tag"
