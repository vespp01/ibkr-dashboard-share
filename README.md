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
5. Set output format to **XML**. The dashboard parser expects XML, not CSV/text.
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
- MTD/YTD Performance Summary
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

By default, the frontend is published on `:5173` and the backend/API on
`:8001` (`/docs` for Swagger). If you expose it through a private VPN, add that
origin to `CORS_ORIGINS` in `.env`.

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

- `backend`: FastAPI on port `8000`, persistent SQLite volume at
  `/app/data/ibkr_backend.db`; exposes 50 JSON endpoints
  (47 GET read-only + 3 utility POST)
- `frontend`: nginx on port `5173`, serves the SPA and proxies `/api/*` to the
  backend service

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

To wipe the container database:

```bash
docker compose down -v
```
