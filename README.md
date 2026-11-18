# IBKR Dashboard

Single-user Interactive Brokers portfolio dashboard with a FastAPI backend and
React/Vite frontend. It reads IBKR Flex Web Service XML into a local SQLite
database and serves the dashboard from Docker Compose.

## Clean Public Snapshot

This repository was published from a clean snapshot with a new Git history:

- Local-first, single-user app. There is no authentication, multi-user mode, or
  hosted SaaS deployment.
- Historical portfolio analytics only. It is not trading software and does not
  provide financial advice.
- Users must provide their own IBKR Flex Web Service token and Flex Query IDs.
- Do not publish real `.env` files, SQLite databases, IBKR statements, or
  screenshots containing account numbers/tokens.
- Use **Settings -> Privacy mode** before sharing screenshots or demoing the
  dashboard with real data.

See [`docs/publication-readiness.md`](docs/publication-readiness.md) for the
data-safety checklist.

The local
`GET /portfolio/investmentlab-snapshot` endpoint publishes the strict
`ibkr-dashboard.portfolio.v1` contract. It omits account identifiers and is
context-only for Investmentlab.

The backend can also send two margin notices through the existing Telegram
bot. Live safety alerts use IBKR `Cushion`, `ExcessLiquidity`, maintenance
margin, and look-ahead values. A separate weekly review compares current IBKR
Pro loan rates with a historical ECB replay against CHF. The review is
informational. It does not forecast FX, convert cash, or place orders. See
[`docs/specs/margin-alerts.md`](docs/specs/margin-alerts.md).

The Cartera positions table ends with one `CASH <currency>` row per currency
(native balance, base value at the IBKR FX rate, % of NAV). Resumen, Margen and
the weekly margin-usage message show liquidity as the % of NAV held in cash.
See [`docs/methodology.md`](docs/methodology.md) §9.

## Quick Start

Prerequisites:

- Docker Desktop, Docker Engine with Compose, or OrbStack
- Git
- An active IBKR account with Client Portal access
- IBKR Flex Web Service token and at least one Activity Flex Query ID

Setup:

```bash
git clone https://github.com/vespp01/ibkr-dashboard-share.git
cd ibkr-dashboard-share
cp .env.example .env
```

Edit `.env` and set:

```text
IB_FLEX_TOKEN=...
IB_FLEX_QUERY_ID=...
IB_FLEX_QUERY_ID_TC=...        # optional — only if you want intraday TC ingestion
BASE_CURRENCY=CHF
ENABLE_LIVE_OVERLAY=false
TELEGRAM_BOT_TOKEN=...         # optional — needed for notifications
TELEGRAM_CHAT_ID=...           # optional — needed for notifications
```

Start the stack:

```bash
docker compose up -d --build
docker compose run --rm bootstrap
```

Open:

```text
http://localhost:5173
```

The Settings page lets each tester choose Spanish, English, or German and turn
on Privacy mode, which masks formatted amounts and percentages locally.

Health checks:

```bash
curl http://localhost:5173/api/health
curl http://localhost:8001/health
docker compose ps
```

Before sharing a build, verify that a committed clean clone starts
with fake credentials and isolated Docker names/volumes:

```bash
scripts/check_publication_clean.sh
```

The check uses ports `15173` and `18001` by default so it can run next to the
normal local stack.

Shortcut after `.env` is filled:

```bash
sh scripts/deploy_mac_mini.sh
```

The script builds the images, starts the stack, runs the one-shot bootstrap
import only if the database is empty, checks health, and prints the dashboard
URL.

## Connect To IBKR

IBKR Flex Web Service setup has two parts: a token and query IDs. IBKR's own
Flex Web Service guide confirms that programmatic access requires an access
token plus one or more Flex Query IDs, both obtained inside Client Portal:
<https://www.interactivebrokers.com/campus/ibkr-api-page/flex-web-service/>.

1. Log in to IBKR Client Portal.
2. Go to **Performance & Reports → Flex Queries**. IBKR also documents the
   alternate menu path **Menu → Reporting → Flex Queries**.
3. Open **Flex Web Service Configuration**, enable the service, and generate a
   token. Save the token and its activation end date.
4. Create an **Activity Flex Query** named `dashboard_full_daily`.
5. Set output format to **XML** and Period **Last 30 Calendar Days**. The
   dashboard parser expects XML, not CSV/text; `Year to Date` was delivered
   truncated by IBKR and froze the data for 26 days in 2026.
6. Save the query and copy its Query ID into `IB_FLEX_QUERY_ID`.
7. Optional: create a **Trade Confirmation Flex Query** named
   `dashboard_tc_daily` for execution-level fills and copy its Query ID into
   `IB_FLEX_QUERY_ID_TC`.

The detailed project checklist is here:
[`docs/setup/flex-queries.md`](docs/setup/flex-queries.md). It lists every
section to enable, the recommended date/time settings, test commands, token
renewal, and HTTP 604 troubleshooting.

### Required Activity Flex Sections

Enable all available columns for these sections:

- Account Information
- Net Asset Value (NAV) in Base
- Change in NAV in Base
- Cash Report
- Open Positions
- Trades
- Cash Transactions
- Open Dividend Accruals
- Interest Accruals
- Transaction Taxes
- Conversion Rates
- Securities Info
- Corporate Actions
- FIFO Performance Summary in Base
- MTM Performance Summary in Base
- Option Exercises, Assignments, Expirations
- Transfers

Missing sections usually do not crash the import; they create empty dashboard
panels. If a tab looks blank after import, re-check the Flex Query first.

## Data And Secrets

Do not commit `.env`, SQLite databases, or IBKR statements. They are ignored by
Git and Docker build contexts.

Local statement exports can be placed under `Extractos/`. The directory is
mounted read-only into the backend container for legacy imports, but the files
are not baked into Docker images and are not tracked by Git.

The backend database is stored in the Docker named volume
`ibkr_dashboard_db_data`.

By default, the frontend is published on `127.0.0.1:5173` and the backend/API on
`127.0.0.1:8001` (`/docs` for Swagger), so the LAN sees neither. To reach the
dashboard over Tailscale, run `tailscale serve --bg --tcp 5173 tcp://127.0.0.1:5173`
once (`scripts/deploy_mac_mini.sh` does it when `tailscale` is installed). If a
browser calls the API directly from another origin, add it to `CORS_ORIGINS` in `.env`.

A `pre-commit` hook running `detect-secrets` (config in
`.pre-commit-config.yaml`, known false positives in `.secrets.baseline`)
blocks commits that contain new secret-shaped strings. Install once per
clone:

```bash
python3 -m venv .venv
.venv/bin/pip install pre-commit detect-secrets
.venv/bin/pre-commit install
```

## Services

- `backend`: FastAPI (container `:8000`, host `:8001`), persistent SQLite
  volume at `/app/data/ibkr_backend.db`; nightly backup to
  `${IBKR_BACKUP_DIR:-${HOME}/.ibkr-backups}`; exposes 52 JSON endpoints
  (49 GET read-only + 3 utility POST)
- `frontend`: nginx on port `5173`, serves the SPA and proxies `/api/*` to the
  backend service
- `watchdog`: `docker:29.7.2-cli` sidecar (pinned by digest); restarts the
  backend on a scheduler wedge (mounts `docker.sock`)

## Useful Commands

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f backend
docker compose logs -f frontend
docker compose run --rm bootstrap
docker compose restart backend
docker compose down
```

Margin diagnostics:

```bash
# Preview the current live safety band. Does not send or mutate state.
docker exec ibkr-backend python -m ibkr_backend.cli margin-alert --source live --test

# Preview the EOD estimate when TWS/Gateway is unavailable.
docker exec ibkr-backend python -m ibkr_backend.cli margin-alert --source eod --test

# Print the funding-currency comparison. Does not send Telegram.
docker exec ibkr-backend python -m ibkr_backend.cli margin-financing
```

To wipe the container database:

```bash
docker compose down -v
```
