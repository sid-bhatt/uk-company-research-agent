#!/usr/bin/env bash
# Runs ON the server (as root, via SSM Run Command), from the unpacked release in /opt/ukco.
# Builds deploy/.env from SSM Parameter Store, pulls the image, migrates, restarts, waits
# for health. Re-running with the same arguments is safe.
#   remote-deploy.sh <image-uri> <git-sha> <api-host> <region>
set -euo pipefail
IMAGE="$1" SHA="$2" API_HOST="$3" REGION="$4"
cd /opt/ukco

# Secrets → .env (root-only). Parameter names under /ukco/ are the env var names.
umask 077
{
  echo "ENV=production"
  echo "API_HOST=${API_HOST}"
  echo "API_IMAGE=${IMAGE}"
  echo "GIT_SHA=${SHA}"
  aws ssm get-parameters-by-path --region "$REGION" --path /ukco/ --with-decryption \
    --query 'Parameters[].[Name,Value]' --output text |
    while IFS=$'\t' read -r name value; do printf '%s=%s\n' "${name#/ukco/}" "$value"; done
} > deploy/.env.new
mv deploy/.env.new deploy/.env

registry="${IMAGE%%/*}"
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$registry" >/dev/null

compose=(docker compose -f deploy/compose.yml --env-file deploy/.env)
"${compose[@]}" pull --quiet
# `migrate` runs to completion before `api` starts (depends_on); --wait blocks until healthy.
"${compose[@]}" up -d --remove-orphans --wait --wait-timeout 180
"${compose[@]}" ps --format '{{.Service}}: {{.Status}}'
docker image prune -af --filter "until=168h" >/dev/null
echo "deployed ${SHA}"
