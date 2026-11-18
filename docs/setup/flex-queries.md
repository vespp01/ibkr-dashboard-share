# IBKR Flex Web Service — Setup Guide

The backend ingests data through IBKR's **Flex Web Service**. You need to
configure **two** Flex Queries in your IBKR Client Portal and wire their IDs
into `.env`. This guide walks through both from scratch.

Official IBKR references:

- Flex Web Service overview and token/query-id workflow:
  <https://www.interactivebrokers.com/campus/ibkr-api-page/flex-web-service/>
- Flex Query creation in Client Portal:
  <https://www.interactivebrokers.com/campus/trading-lessons/flex-queries/>

1. [Activity Flex (`dashboard_full_daily`)](#1-activity-flex-query) — main
   daily snapshot: trades, cash, positions, NAV, FIFO/MTM attribution.
2. [Trade Confirmation Flex (`dashboard_tc_daily`)](#2-trade-confirmation-flex-query)
   — per-fill executions with order-placement timestamps and full commission
   breakdown.

Both queries are saved against your IBKR account and produce XML; the backend
fetches them via HTTPS using a token you also generate in the portal. Nothing
is stored client-side at IBKR beyond the saved query definitions.

---

## 0. Prerequisites

Before creating queries you need a **Flex Web Service token**:

1. IBKR Client Portal → **Performance & Reports** → **Flex Queries**.
2. Scroll to the bottom: **Flex Web Service**.
3. Click **Generate Token**. Save the 20-30 digit number that appears.
4. The token expires on the date shown as its **Activation Period** end in the
   portal (often ~1 year — *not* always 90 days). Renew before then or the
   pipeline returns HTTP 604.

Add to `.env` at the project root. Set `IB_FLEX_TOKEN_EXPIRY` to that date so
the daily job can warn you on Telegram before the token lapses:

```ini
IB_FLEX_TOKEN=<your-token>
IB_FLEX_TOKEN_EXPIRY=<YYYY-MM-DD>   # Activation Period end date
```

---

## 1. Activity Flex Query

Captures the daily snapshot used by every dashboard tab (Resumen, Cartera,
Operaciones, Dividendos, Rentabilidad, Comisiones).

### 1.1. Create the query

1. Client Portal → **Performance & Reports** → **Flex Queries** →
   under **Activity Flex Query** click **Create**.
2. Header:

   | Field | Value |
   |---|---|
   | Query Name | `dashboard_full_daily` |
   | Period | `Last 30 Calendar Days` (recommended — `Year to Date` was delivered truncated by IBKR and froze all Activity tables 2026-08-17→09-12; the CLI/scheduler never override the period) |
   | Date Format | `yyyyMMdd` |
   | Time Format | `HHmmss` |
   | Date/Time Separator | `;` (semicolon) |
   | Profit and Loss | `Default` |
   | Include Canceled Trades? | **No** |
   | Include Currency Rates? | **Yes** |
   | Display Account Alias in Place of Account ID? | **No** |
   | Breakout by Day | **No** (each statement covers the saved period as one window) |
   | Format | **XML** |

### 1.2. Sections to enable

The backend silently skips sections it doesn't recognize, but it needs **all
of the following** to render every dashboard tab without holes:

- [x] **Account Information**
- [x] **Net Asset Value (NAV) in Base** ← needed for TWR / risk metrics
- [x] **Change in NAV in Base** ← waterfall attribution (MTM / realized / dividends / fees)
- [x] **Cash Report**
- [x] **Open Positions** (Summary level)
- [x] **Trades** (Execution level)
- [x] **Cash Transactions**
- [x] **Open Dividend Accruals**
- [x] **Interest Accruals**
- [x] **Transaction Taxes**
- [x] **Conversion Rates**
- [x] **Securities Info**
- [x] **Corporate Actions**
- [x] **FIFO Performance Summary in Base** ← per-symbol realized/unrealized split (ST/LT)
- [x] **MTM Performance Summary in Base** ← per-symbol daily MTM attribution
- [x] **Option Exercises, Assignments, Expirations** (defensive — empty for most accounts)
- [x] **Transfers** (defensive)

For each section enable **all** available columns. The parser only reads
what it needs and is tolerant of extras; missing required columns produce
silent-skip rows that are very hard to debug. If in doubt, check every box.

### 1.3. Save and grab the Query ID

After saving you'll see the query in the list with a 7–8-digit **Query ID**.
Add to `.env`:

```ini
IB_FLEX_QUERY_ID=<the-7-8-digit-id>
```

### 1.4. Test

```bash
docker exec ibkr-backend python -m ibkr_backend.cli pull
```

The CLI prints a per-section row count. Expect non-zero `nav_daily`,
`change_in_nav`, `trades`, `cash_transactions`,
`fifo_performance`, `mtm_performance`. The `accounts` and `instruments`
counts will be 1 / N depending on your account.

The APScheduler daily cron (`pipeline_cron = "30 6 * * *"` by default,
every day, 06:30 container TZ) runs this same command — the next morning
is the production smoke test.

---

## 2. Trade Confirmation Flex Query

Captures execution-level fills with millisecond timestamps, the full
commission breakdown (7 sub-fees), and the `orderTime` axis (when you
placed the limit order, separate from when it filled). This is the only
way to detect multi-slice fills and long-pending LMT orders.

### 2.1. Create the query

1. Client Portal → **Performance & Reports** → **Flex Queries** →
   under **Trade Confirmation Flex Query** (**not** Activity!) click **Create**.
2. Header:

   | Field | Value |
   |---|---|
   | Query Name | `dashboard_tc_daily` |
   | Period | `Today` (the only option IBKR exposes for TC — the CLI overrides with `--from`/`--to` for backfill) |
   | Output Format | **XML** |
   | Date Format | `yyyy-MM-dd` |
   | Time Format | `HH:mm:ss` |
   | Date/Time Separator | `;` |
   | Include Canceled Trades? | **No** |
   | Include Currency Rates? | **Yes** |
   | Display Currency | `Base Currency Summary` |
   | Profit and Loss | `Default` |

### 2.2. Sections and columns

There is only one section — **Trade Confirms**. Enable it, then **every
selectable column** inside it. The parser keeps the executions it needs
(filtered to `levelOfDetail=EXECUTION`) and ignores the rest.

Important fields the parser depends on:

| Column | Used for |
|---|---|
| `execID` | **Primary key** — idempotency per fill |
| `tradeID` | Joins back to Activity Flex `trades` |
| `orderID` | Groups multi-slice fills |
| `conid` | FK to `instruments` |
| `dateTime` | Execution timestamp |
| `orderTime` | When the LMT was placed (key TC value-add) |
| `code` | Open/Close indicator (`O` / `C`) — TC's substitute for `openCloseIndicator` |
| `brokerExecutionCommission` … `otherCommission` | Commission breakdown |

Fields **not available** in TC (silently ignored if absent):

- `fxRateToBase` — workaround: join `fx_rates` on `(trade_date, currency)`
- `liquidityFlag` / maker-taker indicator — IBKR doesn't surface this via Flex
- `timeInForce`, `closePrice`, `notesCodes` — TC schema simply omits them
- `fifoPnlRealized` / `mtmPnl` / `fxPnl` — already provided by Activity Flex

### 2.3. Save and wire

After saving, copy the Query ID into `.env`:

```ini
IB_FLEX_QUERY_ID_TC=<the-7-8-digit-id>
```

Recreate the backend container so the env var is picked up:

```bash
docker compose up -d --force-recreate backend
```

Do not print the container env to check it (it also holds the tokens). The test in
§2.4 confirms the ID: `pull-tc` succeeds only with a valid TC query ID. After the next
scheduled TC pull (:15), `docker compose logs backend | grep "SendRequest q="` shows it.

### 2.4. Test

The CLI accepts an explicit backfill window (TC has a 45-day rolling ceiling
on `fromDate`/`toDate`):

```bash
# Backfill the last 30 days
docker exec ibkr-backend python -m ibkr_backend.cli pull-tc \
    --from $(date -v-30d +%Y%m%d) --to $(date +%Y%m%d)

# Default: pull today only (what the cron does)
docker exec ibkr-backend python -m ibkr_backend.cli pull-tc
```

If your account had executions in the window you'll see `trade_executions`
rows inserted. The APScheduler TC cron (`tc_cron = "15 * * * *"` —
hourly at :15, every day; keeps the watchdog pull-attempt stamp warm)
re-pulls automatically.

---

## 3. Token renewal

The Flex Web Service token expires on its **Activation Period** end date (shown
in the portal — often ~1 year, not always 90 days). When the daily run starts
returning HTTP 604:

1. IBKR Client Portal → **Performance & Reports** → **Flex Queries** →
   **Flex Web Service** → **Generate Token**.
2. Replace **both** `IB_FLEX_TOKEN` and `IB_FLEX_TOKEN_EXPIRY` (the new date) in
   `.env` — if you forget the expiry, the Telegram pre-expiry warning fires on
   the wrong date.
3. `docker compose up -d --force-recreate backend`, then check
   `curl -sf http://localhost:5173/api/health` (nginx re-resolves the backend
   itself; no frontend restart needed).

The saved queries themselves do **not** expire — only the token does. With
Telegram configured, the daily job warns you `TOKEN_EXPIRY_WARN_DAYS` (default
7) days before `IB_FLEX_TOKEN_EXPIRY`, so this rarely surprises you.

---

## 4. Period: `Last 30 Calendar Days` vs `YTD` vs `LastBusinessDay`

Activity Flex queries accept a saved-period setting. There's a meaningful
choice:

- **`Last 30 Calendar Days`** (recommended): each daily run re-fetches the
  last 30 days. Upserts are idempotent so this is free, and any gap shorter
  than 30 days (missed crons, a dead token) is caught up by the next run.
- **`Year to Date`**: IBKR can deliver it truncated. From 2026-08-17 to
  09-12 every daily pull returned `success` with `rows_inserted=0` while all
  Activity tables stayed frozen. `data_freshness` in `/pipeline/status` now
  alerts when data stops advancing, but the fix is still this setting.
- **`LastBusinessDay`**: faster pulls, smaller XML; downside is that any
  missed cron leaves a gap that requires manual `--from` / `--to` backfill.

The CLI's `--period` flag overrides the saved value, but **most IBKR
queries reject period overrides** at fetch time, so the saved-period
setting is the one that matters in practice.

---

## 5. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `FlexError: HTTP 604` from `SendRequest` | Token expired or wrong | Regenerate token (§3) |
| `FlexError: HTTP 604` from `SendRequest` | `--period` (or `period` on `POST /pipeline/run`) passed to a fixed-period query | Drop `--period`; the saved period applies |
| `FlexError: HTTP 604` from `GetStatement` | Query ID deactivated | Re-save the query in the portal (don't change ID, just hit save) |
| Pull `success` but `data_freshness.data_is_stale` / `rows_inserted=0` for days | Query Period no longer covers recent days | Set Period to `Last 30 Calendar Days` (§4) |
| Empty `nav_daily` after pull | NAV section unchecked in Activity Flex | Re-enable in §1.2 |
| Empty `trade_executions` after `pull-tc` | TC query not run yet for the date / weekend | Try `--from`/`--to` on a known active trading day |
| `dashboard_has_data() == False` after first pull | Either the token isn't set or the saved query has no enabled sections | Inspect `flex_runs` table — last entry's `error` column shows the underlying cause |
| Trade IDs collide between Activity Flex and TC | None — by design `tradeID` is shared, `execID` is TC-only | Use `trade_executions.exec_id` as the unique key for fills |
