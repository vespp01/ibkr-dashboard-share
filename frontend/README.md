# IBKR Dashboard - Frontend

React + Vite + TypeScript SPA that consumes the FastAPI backend in
`../ibkr_backend/`.

## Stack

- React 19 + Vite 6 + TypeScript 5
- TanStack Query for server state caching
- Recharts for portfolio, dividend, withholding, and performance charts;
  lightweight-charts for the equity chart (`EquityChart.tsx`)
- Plain CSS with the design tokens from `src/styles/tokens.css`

## Dev Workflow

### Docker Compose

```bash
# From repo root
docker compose up -d --build
open http://localhost:5173
curl http://localhost:5173/api/health
```

The Docker image serves the built SPA with nginx and proxies `/api/*` to the
backend service.

### Local Node

```bash
cd frontend
npm install
npm run dev
```

By default, Vite proxies `/api/*` to `http://127.0.0.1:8001` (the compose backend).
Override the proxy target for local review with:

```bash
VITE_API_TARGET=http://127.0.0.1:8002 npm run dev
```

For a production build that calls an external backend directly, set
`VITE_API_BASE` at build time. The Docker/nginx deployment does not need this
because it uses the relative `/api` proxy.

## Layout

```text
frontend/
  index.html
  package.json
  tsconfig*.json
  vite.config.ts
  nginx.conf
  Dockerfile
  src/
    main.tsx
    App.tsx
    pages/            # Dashboard.tsx (shell/router) + Resumen, Cartera,
                      # Fiscal, Dividendos, Rentabilidad, Comisiones, Margen,
                      # Integraciones, Settings
    components/       # TradesPanel (Operaciones), OptionsPanel (Opciones),
                      # charts, KPI cards, sidebar
    lib/api.ts        # typed API client
    lib/format.ts     # shared formatters
    styles/
```

## Dashboard Scope

Ten pages plus Settings, selected from the sidebar (`PAGE_TITLES` in
`src/pages/Dashboard.tsx`):

- **Resumen** — account KPIs, NAV, PADI / portfolio yield, income projection
- **Cartera** — current positions, allocation, unrealized P&L per symbol
- **Fiscal** — read-only OptimizadorFiscal tax scenarios; hidden while
  `/tax/scenarios` is `not_configured`
- **Operaciones** — trade detail with status/symbol/strategy filters
- **Opciones** — option trades, E/A/E events, accumulated premium, assignment risk
- **Dividendos** — gross/withholding/net, monthly and yearly schedule, by country
- **Rentabilidad** — TWR + XIRR per currency/period, honest portfolio-vs-S&P 500 overlay
- **Comisiones** — operating costs per period with breakdown
- **Margen** — leverage, cash debt per currency, concentration, Reg-T estimates, interest paid
- **Integraciones** — control center: status of every integration edge (`GET /integrations/status`)
- **Settings** — language, setup guidance, Telegram notifications

All pages share the period/year filter chips in the page header; the currency
toggle is local to the TWR panel in Rentabilidad.
