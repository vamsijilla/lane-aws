#!/usr/bin/env bash
# On the instance: start the given images, migrate, wait for health, and roll back to the images
# that ran before if anything fails. Used by deploy.sh (images from ECR) and by CI (images built
# here by ci-build.sh).
#   /opt/lane/remote-deploy.sh <api image> <web image>
set -euo pipefail
cd /opt/lane
api="$1"
web="$2"
dc() { docker compose -f docker-compose.yml -f docker-compose.aws.yml "$@"; }
image_of() { sed -n "s/^$1=//p" .env; }
set_images() {
  sed -i -e "s#^LANE_API_IMAGE=.*#LANE_API_IMAGE=$1#" -e "s#^LANE_WEB_IMAGE=.*#LANE_WEB_IMAGE=$2#" .env
}
healthy() {
  for _ in $(seq 1 60); do
    [ "$(dc ps --format '{{.Health}}' api web | sort -u | tr -d '\n')" = healthy ] && return 0
    sleep 5
  done
  return 1
}

prev_api=$(image_of LANE_API_IMAGE)
prev_web=$(image_of LANE_WEB_IMAGE)
echo "Deploying $api and $web (was $prev_api and $prev_web)"
set_images "$api" "$web"

rollback() {
  echo "ROLLING BACK to $prev_api and $prev_web: $1" >&2
  set_images "$prev_api" "$prev_web"
  dc up -d --remove-orphans || true
  healthy || echo "The previous images are not healthy either; look at docker compose logs" >&2
  exit 1
}

dc run --rm migrate || rollback "migration failed"
dc up -d --remove-orphans || rollback "the containers did not start"
healthy || rollback "api or web did not become healthy"
# Caddy reads its Caddyfile from a mounted file: a change takes effect only on reload
dc exec -T caddy caddy reload --config /etc/caddy/Caddyfile >/dev/null 2>&1 || dc restart caddy
dc ps --format 'table {{.Service}}\t{{.Status}}'

# Keep the three newest images of each, for rollback
for repo in lane-api lane-web "${api%%:*}" "${web%%:*}"; do
  docker image ls "$repo" --format '{{.CreatedAt}}\t{{.Repository}}:{{.Tag}}' | sort -r | tail -n +4 |
    cut -f2 | xargs -r docker image rm >/dev/null 2>&1 || true
done
echo "Deployed"
