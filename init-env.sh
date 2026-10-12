#!/usr/bin/env sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$project_dir"

if ! command -v openssl >/dev/null 2>&1; then
  echo "openssl is required to generate .env" >&2
  exit 1
fi

if [ -e .env ]; then
  echo ".env already exists; refusing to overwrite it" >&2
  exit 1
fi

umask 077
superset_secret=$(openssl rand -base64 42 | tr -d '\n')
postgres_password=$(openssl rand -hex 32)
redis_password=$(openssl rand -hex 32)
mcp_jwt_secret=$(openssl rand -hex 32)
guest_token_jwt_secret=$(openssl rand -hex 32)
admin_password=$(openssl rand -base64 24 | tr -d '\n' | tr '/+' '_-')

sed \
  -e "s|__SUPERSET_SECRET_KEY__|${superset_secret}|" \
  -e "s|__POSTGRES_PASSWORD__|${postgres_password}|" \
  -e "s|__REDIS_PASSWORD__|${redis_password}|" \
  -e "s|__MCP_JWT_SECRET__|${mcp_jwt_secret}|" \
  -e "s|__GUEST_TOKEN_JWT_SECRET__|${guest_token_jwt_secret}|" \
  -e "s|__ADMIN_PASSWORD__|${admin_password}|" \
  .env.example > .env

chmod 600 .env
echo "Created .env with mode 600. Open it to retrieve the initial admin password."
