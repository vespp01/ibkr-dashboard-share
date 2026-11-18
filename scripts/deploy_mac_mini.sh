#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example. Fill IB_FLEX_TOKEN and IB_FLEX_QUERY_ID, then rerun this script."
  exit 1
fi

if grep -q "replace_with_your_flex_token\|replace_with_query_id" .env; then
  echo ".env still contains placeholder Flex credentials. Fill IB_FLEX_TOKEN and IB_FLEX_QUERY_ID first."
  exit 1
fi

docker compose up -d --build
docker compose run --rm bootstrap
docker compose ps

curl --fail --silent http://localhost:5173/api/health
curl --fail --silent http://localhost:8001/health
echo
echo "IBKR Dashboard is ready at:"
echo "  dashboard: http://localhost:5173"
echo "  API docs:  http://localhost:8001/docs"

# Ports bind to 127.0.0.1 only; publish the dashboard on the tailnet (idempotent).
frontend_addr="$(docker compose port frontend 80)"   # e.g. 127.0.0.1:5173
frontend_port="${frontend_addr##*:}"
ts="$(command -v tailscale || true)"
[ -n "$ts" ] || [ ! -x /Applications/Tailscale.app/Contents/MacOS/Tailscale ] || ts=/Applications/Tailscale.app/Contents/MacOS/Tailscale
serve_cmd="tailscale serve --bg --tcp $frontend_port tcp://$frontend_addr"
if [ -n "$ts" ] && "$ts" serve --bg --tcp "$frontend_port" "tcp://$frontend_addr" >/dev/null 2>&1; then
  echo "  tailnet:   http://$("$ts" ip -4 | head -n 1):$frontend_port"
else
  echo "  tailnet:   not published; run '$serve_cmd' to reach it over Tailscale"
fi
