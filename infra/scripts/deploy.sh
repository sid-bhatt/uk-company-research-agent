#!/usr/bin/env bash
# Build the API image for ARM, push it to ECR, upload the release bundle, and have the
# server deploy it via SSM (no SSH). Used by .github/workflows/deploy.yml and by hand.
# Needs AWS credentials and these (non-secret) settings, from env or `terraform output`:
#   AWS_REGION ECR_REPOSITORY_URL OPS_BUCKET INSTANCE_ID API_HOST
set -euo pipefail
cd "$(dirname "$0")/../.."
: "${AWS_REGION:?}" "${ECR_REPOSITORY_URL:?}" "${OPS_BUCKET:?}" "${INSTANCE_ID:?}" "${API_HOST:?}"

SHA="${GIT_SHA:-$(git rev-parse --short=12 HEAD)}"
IMAGE="${ECR_REPOSITORY_URL}:${SHA}"
echo "==> Deploying ${SHA} to ${API_HOST}"

# 1. Image (tags are immutable: build only if this commit isn't in ECR yet).
if aws ecr describe-images --region "$AWS_REGION" --repository-name "${ECR_REPOSITORY_URL#*/}" \
  --image-ids imageTag="$SHA" >/dev/null 2>&1; then
  echo "==> Image ${SHA} already in ECR"
else
  aws ecr get-login-password --region "$AWS_REGION" |
    docker login --username AWS --password-stdin "${ECR_REPOSITORY_URL%%/*}" >/dev/null
  docker buildx build --platform linux/arm64 --build-arg GIT_SHA="$SHA" \
    --tag "$IMAGE" --push backend
fi

# 2. Release bundle: everything the server needs besides the image.
bundle="$(mktemp -d)/release.tgz"
tar czf "$bundle" deploy/compose.yml deploy/Caddyfile deploy/remote-deploy.sh db/init
aws s3 cp --region "$AWS_REGION" --quiet "$bundle" "s3://${OPS_BUCKET}/releases/${SHA}.tgz"

# 3. Tell the server to deploy it, and wait.
params=$(python3 - "$AWS_REGION" "$OPS_BUCKET" "$SHA" "$IMAGE" "$API_HOST" <<'PY'
import json, shlex, sys
region, bucket, sha, image, host = sys.argv[1:]
q = shlex.quote
print(json.dumps({
    "executionTimeout": ["600"],
    "commands": [
        "set -euo pipefail",
        "mkdir -p /opt/ukco && cd /opt/ukco",
        f"aws s3 cp --quiet --region {q(region)} s3://{bucket}/releases/{sha}.tgz /tmp/release.tgz",
        "tar xzf /tmp/release.tgz -C /opt/ukco",
        f"bash deploy/remote-deploy.sh {q(image)} {q(sha)} {q(host)} {q(region)}",
    ],
}))
PY
)
cid=$(aws ssm send-command --region "$AWS_REGION" --instance-ids "$INSTANCE_ID" \
  --document-name AWS-RunShellScript --comment "deploy ${SHA}" \
  --timeout-seconds 600 --parameters "$params" \
  --query Command.CommandId --output text)
echo "==> SSM command ${cid}"
status=Pending
for _ in $(seq 1 120); do
  status=$(aws ssm get-command-invocation --region "$AWS_REGION" --command-id "$cid" \
    --instance-id "$INSTANCE_ID" --query Status --output text 2>/dev/null || echo Pending)
  case "$status" in Success | Failed | TimedOut | Cancelled) break ;; esac
  sleep 5
done
aws ssm get-command-invocation --region "$AWS_REGION" --command-id "$cid" \
  --instance-id "$INSTANCE_ID" --query '[StandardOutputContent,StandardErrorContent]' --output text
[ "$status" = Success ] || { echo "deploy failed: ${status}" >&2; exit 1; }

# 4. Smoke test from outside, through Caddy and HTTPS (first deploy waits for the certificate).
for _ in $(seq 1 30); do
  if body=$(curl -fsS --max-time 5 "https://${API_HOST}/health"); then
    echo "==> https://${API_HOST}/health: ${body}"
    exit 0
  fi
  sleep 5
done
echo "health check failed" >&2
exit 1
