#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! git -C "$repo_root" diff --quiet --ignore-submodules --; then
  echo "Working tree has unstaged changes. Commit or stash before checking a clean clone." >&2
  exit 1
fi
if ! git -C "$repo_root" diff --cached --quiet --ignore-submodules --; then
  echo "Working tree has staged changes. Commit or unstage before checking a clean clone." >&2
  exit 1
fi

project="ibkrpub$RANDOM$$"
backend_port="${BACKEND_HOST_PORT:-18001}"
frontend_port="${FRONTEND_HOST_PORT:-15173}"
tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/ibkr-publication.XXXXXX")"
clone_dir="$tmp_dir/repo"
compose_config="$tmp_dir/compose.config.yml"

cleanup() {
  if [ -d "$clone_dir" ] && command -v docker >/dev/null 2>&1; then
    (
      cd "$clone_dir"
      docker compose -p "$project" down -v --remove-orphans >/dev/null 2>&1 || true
    )
  fi
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

echo "Cloning committed repo to $clone_dir"
git clone --quiet --no-hardlinks "$repo_root" "$clone_dir"

cat > "$clone_dir/.env" <<EOF
IB_FLEX_TOKEN=
IB_FLEX_QUERY_ID=
IB_FLEX_QUERY_ID_TC=
IB_FLEX_TOKEN_EXPIRY=
TOKEN_EXPIRY_WARN_DAYS=7
BASE_CURRENCY=CHF
ENABLE_LIVE_OVERLAY=false
DATABASE_URL=sqlite:///./ibkr_backend.db
PIPELINE_CRON=30 6 * * *
LOG_LEVEL=INFO
WATCHLIST_FILE=./watchlists.yaml
API_HOST=127.0.0.1
API_PORT=8000
BACKEND_HOST_PORT=$backend_port
FRONTEND_HOST_PORT=$frontend_port
CORS_ORIGINS=http://localhost:$frontend_port,http://127.0.0.1:$frontend_port
BACKEND_CONTAINER_NAME=$project-backend
FRONTEND_CONTAINER_NAME=$project-frontend
DB_VOLUME_NAME=${project}_db_data
BACKEND_IMAGE=$project-backend:clean
FRONTEND_IMAGE=$project-frontend:clean
EOF

cd "$clone_dir"

echo "Checking ignored fake .env"
git check-ignore -q .env

echo "Checking clean Extractos mount"
extra_extract="$(find Extractos -type f ! -name .gitkeep -print -quit)"
if [ -n "$extra_extract" ]; then
  echo "Clean clone contains a private statement/export under Extractos: $extra_extract" >&2
  exit 1
fi

echo "Checking publishable surfaces for absolute private paths"
if rg -n "/Users/[^[:space:]\"']+/(Documents|AgentBrain)" \
  --glob '!scripts/check_publication_clean.sh' \
  --glob '!frontend/node_modules/**' \
  --glob '!frontend/dist/**' \
  .; then
  echo "Found private absolute paths outside excluded internal handoff/audit docs." >&2
  exit 1
fi

if [ -n "${PRIVATE_NAME_RE:-}" ]; then
  echo "Checking source for caller-provided private name pattern"
  if rg -n "$PRIVATE_NAME_RE" \
    --glob '!scripts/check_publication_clean.sh' \
    --glob '!frontend/node_modules/**' \
    --glob '!frontend/dist/**' \
    .; then
    echo "Found caller-provided private name pattern outside excluded internal handoff/audit docs." >&2
    exit 1
  fi
fi

echo "Checking isolated Docker Compose config"
docker compose -p "$project" config > "$compose_config"
if grep -q "ibkr_dashboard_db_data" "$compose_config"; then
  echo "Compose config would reuse the default live DB volume in the clean clone." >&2
  exit 1
fi
if ! grep -q "${project}_db_data" "$compose_config"; then
  echo "Compose config does not include the isolated clean-clone DB volume." >&2
  exit 1
fi
if grep -q "container_name: ibkr-backend\|container_name: ibkr-frontend" "$compose_config"; then
  echo "Compose config would reuse default live container names in the clean clone." >&2
  exit 1
fi

if [ "${RUN_BUILD:-1}" = "1" ]; then
  echo "Building clean-clone images"
  docker compose -p "$project" build backend frontend
fi

if [ "${RUN_STACK:-1}" = "1" ]; then
  echo "Starting isolated clean-clone stack"
  docker compose -p "$project" up -d backend frontend
  curl --fail --silent "http://127.0.0.1:$frontend_port/api/health" >/dev/null
  curl --fail --silent "http://127.0.0.1:$backend_port/health" >/dev/null
fi

echo "Clean publication check passed for committed clone."
