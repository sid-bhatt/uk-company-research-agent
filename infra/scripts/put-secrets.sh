#!/usr/bin/env bash
# Store the server's secrets in SSM Parameter Store (SecureString, under /ukco/).
# Reads COMPANIES_HOUSE_API_KEY and ANTHROPIC_API_KEY from the repo's .env; generates
# POSTGRES_PASSWORD and API_JWT_SECRET once (existing values are never overwritten, so
# re-running is safe). Prints names only, never values.
#   make secrets   (uses your current AWS CLI login)
set -euo pipefail
cd "$(dirname "$0")/../.."

value_from_env() { grep -E "^$1=" .env | head -1 | cut -d= -f2- | sed -E 's/^["'\'']|["'\'']$//g'; }

put() {  # name value overwrite
  local name="/ukco/$1" value="$2" overwrite="$3"
  if [ -z "$value" ]; then echo "  skip  $name (empty)"; return; fi
  if [ "$overwrite" = no ] && aws ssm get-parameter --name "$name" >/dev/null 2>&1; then
    echo "  keep  $name (already set)"; return
  fi
  aws ssm put-parameter --name "$name" --type SecureString --value "$value" --overwrite >/dev/null
  echo "  set   $name"
}

echo "Writing secrets to SSM Parameter Store (${AWS_REGION:-$(aws configure get region)}):"
put COMPANIES_HOUSE_API_KEY "$(value_from_env COMPANIES_HOUSE_API_KEY)" yes
put ANTHROPIC_API_KEY "$(value_from_env ANTHROPIC_API_KEY)" yes
put POSTGRES_PASSWORD "$(openssl rand -base64 33 | tr -d '/+=' )" no
put API_JWT_SECRET "$(openssl rand -base64 48 | tr -d '/+=')" no
echo "Done. Vercel needs the same API_JWT_SECRET (Day 17 copies it across)."
