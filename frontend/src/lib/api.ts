/**
 * Thin client for the FastAPI backend.
 * In dev, all calls go through Vite's `/api` proxy to the configured backend.
 * In prod, set VITE_API_BASE to the backend URL at build time.
 */

const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? '/api';

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function get<T>(
  path: string,
  params?: Record<string, string | number | boolean | undefined>,
): Promise<T> {
  const qs = params
    ? '?' +
      Object.entries(params)
        .filter(([, v]) => v !== undefined && v !== null && v !== '')
        .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
        .join('&')
    : '';
  const res = await fetch(`${BASE}${path}${qs}`);
  if (!res.ok) {
    throw new ApiError(`GET ${path} -> ${res.status}`, res.status);
  }
  return (await res.json()) as T;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  });
  if (!res.ok) {
    throw new ApiError(`POST ${path} -> ${res.status}`, res.status);
  }
  return (await res.json()) as T;
}

// ---- Endpoint signatures (subset) - extend as the dashboard grows. ----

export interface HealthResponse {
  status: string;
}

export interface PortfolioOverview {
  as_of?: string;
  nav_base?: number;
  base_currency?: string;
  freshness?: {
    positions_as_of?: string;
    positions_days_stale?: number;
    nav_as_of?: string;
    nav_days_stale?: number;
  };
  unrealized?: {
    snapshot_date?: string;
    days_stale?: number;
    unrealized_pnl_base?: number;
    market_value_base?: number;
    unrealized_pct?: number;
    positions?: number;
  };
  dividends?: DividendsSummary;
  commissions?: CommissionsSummary;
  positions_consistency?: {
    market_value_base?: number;
    nav_positions_base?: number;
    coverage_ratio?: number;
  };
  income_projection?: {
    as_of?: string;
    days_stale?: number;
    padi_base?: number;
    monthly_base?: number;
    yield_pct?: number;
    yield_on_nav_pct?: number;
    positions_total?: number;
    positions_covered?: number;
    coverage_ratio?: number;
    nav_base?: number;
    by_symbol?: Array<{
      symbol: string;
      annual_income_base?: number;
      annual_dividend_per_share?: number;
      yield_pct?: number;
    }>;
  };
  [key: string]: unknown;
}

export interface DividendsSummary {
  gross_base?: number;
  withholding_base?: number;
  net_base?: number;
  [key: string]: unknown;
}

export interface DividendsTimeseriesRow {
  bucket: string;
  gross?: number;
  withholding?: number;
  net?: number;
}

export interface PositionRow {
  snapshot_date: string;
  symbol: string;
  description?: string;
  asset_class?: string;
  country?: string;
  currency?: string;
  quantity?: number;
  position_value_base?: number;
  cost_basis_money_base?: number;
  fifo_pnl_unrealized_base?: number;
  /** position_value_base / NAV x 100 */
  weight?: number | null;
  /** Flex percentOfNAV (share within the asset class) */
  pct_of_asset_class?: number | null;
}

export interface WithholdingCountryRow {
  country: string;
  withholding?: number;
  rows?: number;
}

export interface DividendsCountryRow {
  country: string;
  gross?: number;
  withholding?: number;
  net?: number;
}

export interface UpcomingDividendEvent {
  symbol: string | null;
  conid: number;
  ex_date: string | null;
  pay_date: string;
  quantity: number | null;
  gross_rate: number | null;
  currency: string;
  gross_amount: number | null;
  tax: number | null;
  net_amount: number | null;
  fx_rate_to_base: number | null;
  gross_base: number | null;
  net_base: number | null;
}

export interface UpcomingDividends {
  as_of: string | null;
  base_currency: string;
  events: UpcomingDividendEvent[];
  totals: { count: number; gross_base: number; net_base: number };
  stale_open_events: number;
}

export interface CommissionsSummary {
  total_base?: number;
  trade_count?: number;
  avg_per_trade_base?: number;
  [key: string]: unknown;
}

export interface PerformanceTwr {
  /** Chained IBKR ChangeInNAV daily TWR (null when unreliable or no FX rate). */
  twr_pct?: number | null;
  /** Null for windows under 365 days (GIPS). */
  annualised_pct?: number | null;
  annualised_note?: string;
  raw_twr_pct?: number | null;
  n_days?: number;
  from_date?: string;
  to_date?: string;
  /** Last NAV day before the window (the base the first return is measured from). */
  base_date?: string | null;
  display_currency?: string;
  fx_source?: 'identity' | 'ibkr_fx_rates' | 'frankfurter';
  fx_degraded?: boolean;
  note?: string;
  /** Modified Dietz on the same rows, shown only as a reconciliation. */
  reconciliation?: { dietz_twr_pct: number; canonical_base_twr_pct: number; diff_pp: number };
  /** Absent with `?summary=1`. */
  series?: Array<{ date: string; nav: number; cashflow: number; daily_return_pct: number; cumulative_pct: number }>;
  quality?: {
    is_reliable?: boolean;
    reason?: string;
    rows?: number;
    expected_days?: number;
    coverage_ratio?: number;
    max_gap_days?: number;
  };
  [key: string]: unknown;
}

export interface XirrResponse {
  /** Null for windows under 365 days (not annualised). */
  xirr_pct?: number | null;
  annualised?: boolean;
  window_days?: number;
  terminal_nav_base?: number;
  terminal_date?: string;
  flow_count?: number;
  flows?: Array<{ date: string; amount: number }>;
  error?: string;
  note?: string | null;
}

export interface PeriodFilters {
  period?: 'ytd' | 'mtd' | 'qtd' | '1m' | '3m' | '6m' | '1y' | '3y' | '5y' | 'all';
  year?: number;
  month?: number;
  from?: string;
  to?: string;
  account_id?: string;
}

export interface DataCoverage {
  first_date?: string;
  last_date?: string;
  days_stale?: number;
  sources?: {
    trades?: { first?: string; last?: string; count?: number };
    cash_transactions?: { first?: string; last?: string; count?: number };
    positions?: { last_snapshot?: string };
  };
}

export interface PipelineStatus {
  configured: boolean;
  activity_configured?: boolean;
  tc_configured?: boolean;
  scheduler_enabled: boolean;
  base_currency?: string;
  token_expiry?: string | null;
  live_overlay_enabled?: boolean;
  staleness?: {
    last_success_at?: string | null;
    age_hours?: number | null;
    is_stale?: boolean;
    threshold_hours?: number;
    reason?: string | null;
  };
  data_freshness?: {
    last_data_date?: string | null;
    age_business_days?: number | null;
    data_is_stale?: boolean;
    threshold_business_days?: number;
    reason?: string | null;
  };
  scheduler?: {
    heartbeat_at?: string | null;
    heartbeat_age_seconds?: number | null;
    last_pull_attempt_at?: string | null;
    last_pull_attempt_age_seconds?: number | null;
  };
  latest_run?: {
    fetched_at?: string;
    from_date?: string;
    to_date?: string;
    status?: string;
    rows_inserted?: number;
    rows_skipped_dup?: number;
    has_error?: boolean;
  } | null;
}

export type TaxScenarioStatus = 'draft' | 'reviewed' | 'approved' | 'blocked';
export type TaxSaleCandidateSource = 'manual' | 'metodo_tendencia_exit';
export type TaxSaleSignalKind = 'exit_mp' | 'exit_lp' | null;

export interface TaxSaleCandidate {
  candidate_id: string;
  instrument_id: string;
  selection_source: TaxSaleCandidateSource;
  source_ref: string;
  selected_at: string;
  signal_kind: TaxSaleSignalKind;
}

export interface TaxSaleScenario {
  scenario_id: string;
  status: TaxScenarioStatus;
  objective: string;
  sales: Array<{
    candidate_id: string;
    instrument_id: string;
    quantity: string;
    assumed_sale_date: string;
    assumed_price: string;
    currency: string;
    fifo_allocations: Array<{ acquisition_event_id: string; quantity: string }>;
    estimated_gain_loss_eur: string;
    deferred_loss_eur: string;
  }>;
  summary: {
    estimated_proceeds_eur: string;
    estimated_gain_loss_eur: string;
    estimated_tax_delta_eur: string;
    estimated_transaction_cost_eur: string;
    portfolio_deviation_score: string;
  };
  warnings: string[];
  blockers: string[];
}

export interface TaxScenarioSet {
  schema_version: 'tax_sale_scenarios.v1';
  scenario_set_id: string;
  generated_at: string;
  snapshot_id: string;
  snapshot_hash: string;
  tax_profile: {
    jurisdiction: 'ES';
    tax_year: number;
    residency_status: 'confirmed' | 'unconfirmed';
    regime: string;
    region: string;
  };
  rule_pack: {
    id: string;
    version: string;
    review_status: TaxScenarioStatus | 'expired';
    content_hash: string;
  };
  candidate_set: {
    candidate_set_id: string;
    generated_at: string;
    candidates: TaxSaleCandidate[];
  };
  status: TaxScenarioStatus;
  scenarios: TaxSaleScenario[];
  warnings: string[];
  blockers: string[];
}

export interface TaxIntegrationResponse {
  integration_status: 'not_configured' | 'missing' | 'invalid' | 'ready';
  reason_code?: string | null;
  expected_schema_version: 'tax_sale_scenarios.v1';
  document_hash?: string | null;
  scenario_set?: TaxScenarioSet | null;
}

export type IntegrationCategory = 'project' | 'broker' | 'service';
export type IntegrationDirection = 'outbound' | 'inbound' | 'bidirectional';
export type IntegrationLevel = 'ok' | 'warn' | 'error' | 'off' | 'info';
export type IntegrationControlKind = 'env' | 'route' | 'none';

export interface IntegrationStatus {
  id: string;
  category: IntegrationCategory;
  name: string;
  contract: string | null;
  direction: IntegrationDirection;
  mechanism: string;
  enabled: boolean;
  implemented: boolean;
  removable: boolean;
  status: string;
  level: IntegrationLevel;
  control: string | null;
  control_kind: IntegrationControlKind;
  reason_code?: string | null;
  as_of?: string | null;
  days_stale?: number | null;
  days_to_expiry?: number | null;
}

export interface IntegrationsStatusResponse {
  generated_at: string;
  integrations: IntegrationStatus[];
}

export type NotificationFreq = 'off' | 'daily' | 'weekly' | 'biweekly' | 'monthly';
export type NotificationScheduleKind = 'chained' | 'interval' | 'cron';

export interface NotificationCadence {
  freq: NotificationFreq;
  weekday: number | null;
  day_of_month: number | null;
  time: string | null;
}

export interface NotificationMessageConfig {
  enabled: boolean;
  sections?: string[];
  cadence: NotificationCadence;
  alert_bands?: boolean;
}

export interface NotificationConfig {
  version: number;
  messages: Record<string, NotificationMessageConfig>;
}

export interface NotificationMessageSchema {
  id: string;
  schedule_kind: NotificationScheduleKind;
  requires_live_overlay: boolean;
  supports_sections: boolean;
  sections: string[];
  supports_alert_bands: boolean;
  editable: string[];
  cadence_fields: string[];
  interval_cron?: string;
}

export interface NotificationSchema {
  version: number;
  frequencies: NotificationFreq[];
  messages: NotificationMessageSchema[];
}

export interface NotificationCapabilities {
  telegram_configured: boolean;
  live_overlay_enabled: boolean;
  scheduler_running: boolean;
}

export interface NotificationConfigResponse {
  config: NotificationConfig;
  schema: NotificationSchema;
  capabilities: NotificationCapabilities;
}

export interface NotificationSaveResponse {
  config: NotificationConfig;
  applied: { applied: boolean; reason?: string; jobs?: Record<string, string> };
}

export interface NotificationConfigPatch {
  messages: Record<string, NotificationMessageConfig>;
}

export const api = {
  health: () => get<HealthResponse>('/health'),
  dataCoverage: () => get<DataCoverage>('/data/coverage'),
  pipelineStatus: () => get<PipelineStatus>('/pipeline/status'),
  taxScenarios: () => get<TaxIntegrationResponse>('/tax/scenarios'),
  integrationsStatus: () => get<IntegrationsStatusResponse>('/integrations/status'),
  notificationConfig: () => get<NotificationConfigResponse>('/config/notifications'),
  saveNotificationConfig: (body: NotificationConfigPatch) =>
    post<NotificationSaveResponse>('/config/notifications', body),
  testNotification: () => post<{ sent: boolean; error?: string }>('/config/notifications/test', {}),
  portfolioOverview: (filters: PeriodFilters = {}) =>
    get<PortfolioOverview>('/portfolio/overview', filters as Record<string, string | number | undefined>),
  portfolioPositions: (filters: Pick<PeriodFilters, 'account_id'> = {}) =>
    get<PositionRow[]>('/portfolio/positions', filters as Record<string, string | number | undefined>),
  dividendsSummary: (filters: PeriodFilters = {}) =>
    get<DividendsSummary>('/dividends/summary', filters as Record<string, string | number | undefined>),
  dividendsTimeseries: (filters: PeriodFilters = {}, bucket: 'year' | 'month' | 'day' = 'month') =>
    get<DividendsTimeseriesRow[]>('/dividends/timeseries', {
      ...(filters as Record<string, string | number | undefined>),
      bucket,
    }),
  withholdingByCountry: (filters: PeriodFilters = {}) =>
    get<WithholdingCountryRow[]>('/dividends/withholding-by-country', filters as Record<string, string | number | undefined>),
  dividendsByCountry: (filters: PeriodFilters = {}) =>
    get<DividendsCountryRow[]>('/dividends/by-country', filters as Record<string, string | number | undefined>),
  dividendsUpcoming: (filters: Pick<PeriodFilters, 'account_id'> = {}) =>
    get<UpcomingDividends>('/dividends/upcoming', filters as Record<string, string | number | undefined>),
  commissionsSummary: (filters: PeriodFilters = {}) =>
    get<CommissionsSummary>('/commissions/summary', filters as Record<string, string | number | undefined>),
  /** `summary` omits the daily series (< 2 KB instead of ~100 KB). */
  performanceTwr: (filters: PeriodFilters = {}, currency?: string, summary = false) =>
    get<PerformanceTwr>('/performance/twr', {
      ...(filters as Record<string, string | number | undefined>),
      ...(currency ? { currency } : {}),
      ...(summary ? { summary: 1 } : {}),
    }),
  performanceXirr: (filters: PeriodFilters = {}) =>
    get<XirrResponse>('/performance/xirr', filters as Record<string, string | number | undefined>),
  realizedTimeseries: (filters: PeriodFilters = {}, bucket: 'year' | 'month' | 'day' = 'day') =>
    get<RealizedTimeseriesRow[]>('/portfolio/realized-timeseries', {
      ...(filters as Record<string, string | number | undefined>),
      bucket,
    }),
  trades: (filters: PeriodFilters & { symbol?: string; asset_class?: string; strategy?: string; status?: 'all' | 'open' | 'closed'; limit?: number; offset?: number } = {}) =>
    get<TradesResponse>('/trades', filters as Record<string, string | number | undefined>),
  openShorts: (filters: { account_id?: string } = {}) =>
    get<OpenShortRow[]>('/portfolio/open-shorts', filters as Record<string, string | number | undefined>),
  attribution: (filters: PeriodFilters = {}) =>
    get<AttributionResponse>('/portfolio/attribution', filters as Record<string, string | number | undefined>),
  attributionTimeseries: (filters: PeriodFilters = {}, bucket: 'day' | 'month' | 'year' = 'month') =>
    get<AttributionTimeseriesRow[]>('/portfolio/attribution-timeseries', {
      ...(filters as Record<string, string | number | undefined>),
      bucket,
    }),
  /** Omit `riskFreeRatePct` to use the backend's per-year CHF table (default 0 %). */
  performanceRisk: (filters: PeriodFilters = {}, riskFreeRatePct?: number) =>
    get<RiskMetrics>('/performance/risk', {
      ...(filters as Record<string, string | number | undefined>),
      ...(riskFreeRatePct !== undefined ? { risk_free_rate_pct: riskFreeRatePct } : {}),
    }),
  performanceAlpha: (filters: PeriodFilters = {}, currency = 'CHF') =>
    get<AlphaBetaResponse>('/performance/alpha', {
      ...(filters as Record<string, string | number | undefined>),
      currency,
    }),
  performanceVsBenchmark: (filters: PeriodFilters = {}, currency = 'CHF') =>
    get<VsBenchmarkResponse>('/performance/vs-benchmark', {
      ...(filters as Record<string, string | number | undefined>),
      currency,
    }),
  benchmark: (filters: PeriodFilters = {}, currency = 'CHF') =>
    get<BenchmarkResponse>('/performance/benchmark', {
      ...(filters as Record<string, string | number | undefined>),
      symbol: 'sp500',
      currency,
    }),
  margin: () => get<MarginSummaryResponse>('/portfolio/margin'),
  allTimeReturn: () => get<AllTimeReturnResponse>('/portfolio/all-time-return'),
  marginInterest: (filters: PeriodFilters = {}) =>
    get<MarginInterestResponse>('/portfolio/margin/interest', filters as Record<string, string | number | undefined>),
  marginLeverageSeries: (filters: PeriodFilters = {}) =>
    get<LeverageSeriesRow[]>('/portfolio/margin/leverage-series', filters as Record<string, string | number | undefined>),
  commissionsBreakdown: (filters: PeriodFilters = {}) =>
    get<CommissionsBreakdown>('/commissions/breakdown', filters as Record<string, string | number | undefined>),
  symbolAttribution: (
    filters: PeriodFilters & { asset_class?: string; top_n?: number; held_only?: boolean } = {},
  ) =>
    get<SymbolAttributionResponse>('/portfolio/symbol-attribution', filters as Record<string, string | number | undefined>),
  executions: (
    filters: PeriodFilters & {
      symbol?: string;
      asset_class?: string;
      order_id?: string;
      multi_fill_only?: boolean;
      limit?: number;
      offset?: number;
    } = {},
  ) =>
    get<ExecutionsResponse>('/trades/executions', filters as Record<string, string | number | boolean | undefined>),
  optionsSummary: (filters: PeriodFilters = {}) =>
    get<OptionsSummaryResponse>('/options/summary', filters as Record<string, string | number | undefined>),
  optionsEae: (filters: PeriodFilters & { underlying?: string } = {}) =>
    get<OptionsEaeResponse>('/options/eae', filters as Record<string, string | number | undefined>),
  optionsByUnderlying: (filters: PeriodFilters = {}) =>
    get<OptionsByUnderlyingResponse>('/options/by-underlying', filters as Record<string, string | number | undefined>),
  optionsOpenPositions: (filters: Pick<PeriodFilters, 'account_id'> = {}) =>
    get<OptionsOpenPositionsResponse>('/options/open-positions', filters as Record<string, string | number | undefined>),
  optionsPnlAttribution: (filters: PeriodFilters = {}) =>
    get<OptionsPnlAttributionResponse>('/options/pnl-attribution', filters as Record<string, string | number | undefined>),
};

// ---- Options ----

export interface OptionsTradesSummary {
  total: number;
  contracts_traded: number;
  net_premium_base: number;
  commissions_base: number;
  realized_pnl_base: number;
  win: number;
  loss: number;
  be: number;
  win_rate: number | null;
  avg_winner_base?: number | null;
  avg_loser_base?: number | null;
  profit_factor?: number | null;
  expectancy_base?: number | null;
  win_sum_base?: number | null;
  loss_sum_base?: number | null;
}

export interface OptionsRiskSummary {
  shorts: number;
  itm: number;
  expiring_within_7d: number;
  intrinsic_at_risk_base: number;
}

export interface OptionsDteSummary {
  shorts_open: number;
  manage_count: number;
  expired_skipped: number;
}

export interface OptionsDteBucketPosition {
  symbol: string;
  underlying?: string | null;
  right?: 'C' | 'P' | null;
  strike?: number | null;
  expiry?: string | null;
  dte: number;
  quantity?: number;
}

export interface OptionsDteBucket {
  label: 'safe' | 'watch' | 'manage' | 'critical';
  lo: number | null;
  hi: number | null;
  count: number;
  positions: OptionsDteBucketPosition[];
}

export interface OptionsDteBucketsResponse {
  snapshot_date: string | null;
  buckets: OptionsDteBucket[];
  summary: OptionsDteSummary;
}

export interface OptionsConcentrationRow {
  underlying: string;
  positions: number;
  contracts: number;
  net_position_value_base: number;
  abs_exposure_base: number;
  exposure_share: number | null;
}

export interface OptionsConcentrationResponse {
  snapshot_date: string | null;
  rows: OptionsConcentrationRow[];
  summary: {
    positions: number;
    underlyings: number;
    total_abs_exposure_base: number;
    net_position_value_base: number;
    hhi: number | null;
    effective_names: number | null;
    top_underlying: string | null;
    top_share: number | null;
  };
}

export interface OptionsPnlAttributionByUnderlying {
  underlying: string;
  opt_pnl_base: number;
  stk_pnl_base: number;
  dividends_gross_base: number;
  withholding_base: number;
  dividends_net_base: number;
  total_base: number;
}

export interface OptionsPnlAttributionTotals {
  short_premium_base: number;
  long_premium_base?: number;
  wheel_stock_base: number;
  wheel_dividends_base: number;
  wheel_withholding_base: number;
  non_options_stock_base: number;
  non_options_dividends_base: number;
  interest_base: number;
  grand_total_base: number;
}

export interface OptionsPnlAttributionResponse {
  period?: { start?: string; end?: string; label: string };
  by_underlying: OptionsPnlAttributionByUnderlying[];
  totals: OptionsPnlAttributionTotals;
}

export interface OptionsAssignmentRateRow {
  underlying: string;
  exercise: number;
  assignment: number;
  expiration: number;
  bought_to_close: number;
  sold_to_close: number;
  total_disposals: number;
  stock_transfer_rate: number | null;
  expiration_rate: number | null;
  manual_close_rate: number | null;
}

export interface OptionsAssignmentRateTotals {
  exercise: number;
  assignment: number;
  expiration: number;
  bought_to_close: number;
  sold_to_close: number;
  total_disposals: number;
  stock_transfer_rate: number | null;
  expiration_rate: number | null;
  manual_close_rate: number | null;
}

export interface OptionsAssignmentRateResponse {
  period?: { start?: string; end?: string; label: string };
  by_underlying: OptionsAssignmentRateRow[];
  totals: OptionsAssignmentRateTotals;
}

export interface OptionsSummaryResponse {
  period?: { start?: string; end?: string; label: string };
  snapshot_date?: string;
  trades: OptionsTradesSummary;
  open_risk: OptionsRiskSummary;
  dte?: OptionsDteSummary;
}

export interface OptionTradeRow {
  trade_id: string;
  ib_order_id?: string;
  symbol: string;
  description?: string;
  underlying?: string;
  right?: 'C' | 'P';
  strike?: number;
  expiry?: string;
  dte_today?: number;
  trade_date?: string;
  trade_datetime?: string;
  buy_sell?: string;
  open_close?: string;
  action: string;
  quantity?: number;
  multiplier?: number;
  trade_price?: number;
  proceeds?: number;
  proceeds_base?: number;
  ib_commission?: number;
  commission_base?: number;
  fifo_pnl_realized?: number;
  fifo_pnl_realized_base?: number;
  currency: string;
  strategy?: string | null;
  strategy_legs?: number;
  is_multi_leg?: boolean;
  capital_at_risk_base?: number | null;
  roc_pct?: number | null;
  capture_pct?: number | null;
  is_open: boolean;
  is_closed: boolean;
}

export interface OptionsTradesResponse {
  rows: OptionTradeRow[];
  limit: number;
  offset: number;
  summary: OptionsTradesSummary;
  period?: { label: string };
}

export interface OptionEaeRow {
  eae_id: string;
  event_date?: string;
  transaction_type?: string;
  symbol?: string;
  underlying?: string;
  right?: string;
  strike?: number;
  expiry?: string;
  quantity?: number;
  multiplier?: number;
  trade_price?: number;
  proceeds?: number;
  commission?: number;
  realized_pnl?: number;
  currency?: string;
  description?: string;
}

export interface OptionsEaeResponse {
  rows: OptionEaeRow[];
  summary: {
    total: number;
    counts: Record<string, number>;
    cash_impact_total: number;
  };
}

export interface OptionsPremiumSeriesRow {
  bucket: string;
  premium_in_base: number;
  premium_out_base: number;
  net_premium_base: number;
  cumulative_net_base: number;
  trades: number;
}

export interface OptionsPremiumSeriesResponse {
  bucket: string;
  series: OptionsPremiumSeriesRow[];
  summary: { total_in: number; total_out: number; total_net: number; trades: number };
}

export interface OptionsByUnderlyingRow {
  underlying: string;
  trades: number;
  contracts: number;
  net_premium_base: number;
  realized_pnl_base: number;
  commissions_base: number;
  win: number;
  loss: number;
  be: number;
  win_rate: number | null;
}

export interface OptionsByUnderlyingResponse {
  rows: OptionsByUnderlyingRow[];
}

export interface OptionsOpenPositionRow {
  symbol: string;
  description?: string | null;
  underlying?: string | null;
  right?: 'C' | 'P' | null;
  strike?: number | null;
  expiry?: string | null;
  dte?: number | null;
  side: 'long' | 'short';
  quantity: number;
  contracts: number;
  multiplier?: number | null;
  mark_price?: number | null;
  position_value_base: number;
  cost_basis_money_base?: number | null;
  fifo_pnl_unrealized_base?: number | null;
  unrealized_pct?: number | null;
  pct_of_nav?: number | null;
  last_underlying_close?: number | null;
  moneyness_pct?: number | null;
  currency?: string | null;
  // OptionsLab management signal joined by underlying/right/expiry/strike; null when none applies.
  optionslab?: OptionsLabSignal | null;
}

export type OptionsLabAction = 'hold' | 'take_profit' | 'stop' | 'warning' | 'review' | 'unknown';

export interface OptionsLabSignal {
  action: OptionsLabAction;
  status_text: string | null;
  capture_pct: number | null; // percent units (45.2 = 45.2 %)
  short_delta: number | null;
  dte: number | null;
  leg: 'short' | 'long';
  generated_at: string; // UTC ISO 8601, Z suffix
  stale: boolean; // generated_at older than 24 h
}

export interface OptionsOpenPositionsResponse {
  snapshot_date: string | null;
  rows: OptionsOpenPositionRow[];
  summary: {
    positions: number;
    long_positions: number;
    short_positions: number;
    long_contracts: number;
    short_contracts: number;
    total_abs_exposure_base: number;
    net_position_value_base: number;
    unrealized_pnl_base: number;
    expiring_within_30d: number;
    expired: number;
  };
}

export interface OptionsAssignmentRow {
  symbol: string;
  underlying?: string;
  right?: 'C' | 'P';
  strike?: number;
  expiry?: string;
  dte?: number;
  quantity?: number;
  multiplier?: number;
  mark_price?: number;
  position_value_base?: number;
  last_underlying_close?: number;
  itm?: boolean | null;
  intrinsic_per_contract?: number;
  intrinsic_total_base?: number;
  currency?: string;
}

export interface OptionsAssignmentRiskResponse {
  snapshot_date?: string;
  rows: OptionsAssignmentRow[];
  summary: OptionsRiskSummary;
}

export interface RealizedTimeseriesRow {
  bucket: string;
  realized: number;
  trades: number;
}

export interface TradeRow {
  trade_id: string;
  ib_order_id?: string;
  symbol: string;
  description?: string;
  asset_class?: string;
  currency: string;
  trade_date?: string;
  trade_datetime?: string;
  settle_date?: string;
  buy_sell?: string;
  open_close?: string;
  quantity?: number;
  trade_price?: number;
  proceeds?: number;
  proceeds_base?: number;
  ib_commission?: number;
  commission_base?: number;
  fifo_pnl_realized?: number;
  fifo_pnl_realized_base?: number;
  dividends_during_hold_base?: number;
  total_return_base?: number;
  total_return_pct?: number;
  hold_start?: string;
  multiplier?: number;
  strategy?: string;
  strategy_legs?: number;
  is_multi_leg?: boolean;
  is_open?: boolean;
  is_closed?: boolean;
  is_winner?: boolean;
  is_loser?: boolean;
}

export interface TradesResponse {
  rows: TradeRow[];
  limit: number;
  offset: number;
  /** strategy labels present under the current filters (before the strategy filter) */
  strategies?: string[];
  summary: {
    total: number;
    /** opening fills under the current filters */
    open_count: number;
    /** closed orders (fills of one order summed); win + loss + be */
    closed_count: number;
    win: number;
    loss: number;
    be: number;
    realized_total_base?: number;
    win_rate?: number;
    profit_factor?: number;
    avg_winner_base?: number;
    avg_loser_base?: number;
    win_sum_base?: number;
    loss_sum_base?: number;
  };
}

export interface AttributionTimeseriesRow {
  bucket: string;
  mtm?: number;
  dividends?: number;
  withholding_tax?: number;
  change_in_dividend_accruals?: number;
  interest?: number;
  change_in_interest_accruals?: number;
  commissions?: number;
  broker_fees?: number;
  other_fees?: number;
  transaction_tax?: number;
  sales_tax?: number;
  fx_translation?: number;
  deposits_withdrawals?: number;
  asset_transfers?: number;
  /** Sum of the non-flow components: what the monthly Total Return matrix shows. */
  total_return_base?: number;
  starting_value?: number;
  ending_value?: number;
  /** Day bucket only: ending - starting - sum(components), ~0. */
  residual?: number;
  twr?: number;
}

export interface RiskMetrics {
  n_days: number;
  is_reliable: boolean;
  reason?: string;
  from_date?: string;
  to_date?: string;
  requested_from?: string;
  requested_to?: string;
  is_truncated?: boolean;
  truncation_reason?: string;
  risk_free_rate_pct: number;
  risk_free_source?: string;
  /** Risk metrics are computed on the CHF daily returns. */
  currency?: string;
  window_days?: number;
  /** False under 365 days: annualised return and Calmar are null. */
  annualised?: boolean;
  sharpe_method?: 'geometric' | 'arithmetic';
  annualised_return_pct?: number | null;
  annualised_volatility_pct?: number;
  annualised_downside_volatility_pct?: number;
  sharpe_ratio?: number;
  sortino_ratio?: number;
  max_drawdown_pct?: number;
  max_drawdown_peak_date?: string;
  max_drawdown_trough_date?: string;
  calmar_ratio?: number | null;
  negative_days?: number;
  positive_days?: number;
}

export interface AlphaBetaResponse {
  is_reliable: boolean;
  reason?: string;
  aligned_days?: number;
  unpaired_days?: number;
  portfolio_days?: number;
  from_date?: string;
  to_date?: string;
  requested_from?: string;
  requested_to?: string;
  is_truncated?: boolean;
  truncation_reason?: string;
  benchmark?: string;
  benchmark_type?: string;
  benchmark_currency?: string;
  fx_source?: string;
  benchmark_fx_source?: string | null;
  portfolio_total_return_pct?: number;
  benchmark_total_return_pct?: number;
  simple_alpha_pct?: number;
  beta?: number;
  jensen_alpha_pct?: number;
}

export interface VsBenchmarkResponse {
  is_reliable: boolean;
  reason?: string;
  // Everything below is omitted by the backend on the is_reliable=false path,
  // so it must be optional — guard on is_reliable before accessing.
  from?: string;
  to?: string;
  /** NAV day before the window; both lines are 100 here. */
  base_date?: string;
  display_currency?: string;
  benchmark?: string;
  benchmark_type?: string;
  benchmark_currency?: string;
  fx_source?: string;
  fx_degraded?: boolean;
  benchmark_fx_source?: string | null;
  resolution?: {
    points: number;
    max_gap_days: number | null;
    daily_segment_from?: string;
    coverage_ratio?: number | null;
    is_sparse?: boolean;
    note: string;
  };
  level_comparison?: {
    portfolio_return_pct: number;
    benchmark_return_pct: number;
    excess_return_pct: number;
  };
  risk_comparison?: {
    is_reliable?: boolean;
    aligned_days?: number;
    portfolio_total_return_pct?: number;
    benchmark_total_return_pct?: number;
    simple_alpha_pct?: number;
    beta?: number | null;
    jensen_alpha_pct?: number | null;
    tracking_error_pct?: number | null;
    information_ratio?: number | null;
    te_days?: number;
  };
  twr_quality?: unknown;
  points?: Array<{ date: string; portfolio: number; benchmark: number }>;
}

export interface AttributionResponse {
  period: { first_date?: string; last_date?: string; days?: number };
  starting_value?: number;
  ending_value?: number;
  attribution_base: {
    mtm?: number;
    dividends?: number;
    withholding_tax?: number;
    change_in_dividend_accruals?: number;
    interest?: number;
    change_in_interest_accruals?: number;
    commissions?: number;
    broker_fees?: number;
    other_fees?: number;
    transaction_tax?: number;
    sales_tax?: number;
    fx_translation?: number;
    deposits_withdrawals?: number;
    asset_transfers?: number;
  };
  performance_total_base?: number;
  /** ending - starting - sum(components): ~0 (residual-free waterfall). */
  residual_base?: number;
  residual_pct_of_ending?: number | null;
  twr_compound_pct?: number;
}

export interface BenchmarkRow {
  date: string;
  close_native: number;
  fx: number;
  close_base: number;
  index: number;
  return_pct: number;
}

export interface BenchmarkResponse {
  symbol: string;
  currency: string;
  from: string;
  to: string;
  baseline_index: number;
  rows: BenchmarkRow[];
}

export interface MarginCashRow {
  currency: string;
  ending_cash: number;
  /** ending_cash in base at the Flex fx rate; null when the rate is missing */
  ending_cash_base?: number | null;
  pct_of_nav?: number | null;
  is_debt: boolean;
}

export interface MarginPositionRow {
  symbol: string;
  asset_class?: string;
  quantity?: number;
  position_value_base?: number;
  pct_of_nav?: number;
}

export interface MarginUnavailableResponse {
  snapshot_date: null;
  warning: string;
}

export interface MarginReadyResponse {
  snapshot_date?: string;
  base_currency?: string;
  nav_total?: number;
  nav_breakdown?: { cash?: number; stock?: number; options?: number; bonds?: number };
  cash_by_currency: MarginCashRow[];
  cash_debt_native_sum?: number;
  /** NAV cash (net of per-currency debt) / NLV, in % */
  cash_pct_nlv?: number | null;
  exposure: {
    longs_base: number;
    shorts_base: number;
    gross_base: number;
    net_base: number;
    gross_pct_nlv?: number;
    net_pct_nlv?: number;
    options_value_base: number;
    options_pct_nlv?: number;
  };
  leverage_x?: number;
  reg_t_estimate: {
    source: string;
    init_margin_estimate: number;
    maint_margin_estimate: number;
    excess_liq_estimate: number;
    buying_power_estimate: number;
    cushion_pct_estimate?: number;
  };
  top_positions: MarginPositionRow[];
  warnings: string[];
}

export type MarginSummaryResponse = MarginReadyResponse | MarginUnavailableResponse;

export interface MarginInterestResponse {
  period: { from?: string; to?: string };
  paid_total_base: number;
  received_total_base: number;
  net_base: number;
  paid_by_currency: Array<{ currency: string; amount_native: number; amount_base?: number; avg_debt_base?: number | null; apr_pct?: number | null }>;
  /** Every currency that earned interest or held positive cash (idle balances show 0 %). */
  received_by_currency: Array<{
    currency: string;
    amount_native: number;
    amount_base?: number;
    cash_interest_native?: number;
    cash_interest_base?: number;
    syep_native?: number;
    syep_base?: number;
    avg_cash_base?: number;
    yield_pct?: number | null;
  }>;
  /** average debt over the period's snapshot days, all currencies converted to base */
  avg_debt_base: number;
  days_with_debt: number;
  span_days: number;
  effective_apr_pct?: number | null;
  /** credit interest on cash (base), SYEP lending income excluded */
  cash_interest_base?: number;
  syep_base?: number;
  /** average positive cash over the period's snapshot days, in base */
  avg_cash_base?: number;
  days_with_cash?: number;
  effective_cash_yield_pct?: number | null;
}

export interface LeverageSeriesRow {
  date: string;
  nav: number;
  stock: number;
  options: number;
  bonds: number;
  cash: number;
  gross_position_value: number;
  leverage_x?: number;
  cash_pct_nlv?: number;
}

export interface AllTimeReturnResponse {
  is_reliable: boolean;
  reason?: string;
  inception_date?: string;
  as_of?: string;
  current_nav_base?: number;
  cumulative_net_deposits_base?: number;
  absolute_profit_base?: number;
  absolute_return_pct?: number;
  years_elapsed?: number;
  /** All-time money-weighted annual return (replaces the old cagr_pct). */
  xirr_pct?: number | null;
  note?: string;
}

export interface CommissionsBreakdown {
  by_asset_class: Array<{ asset_class: string; amount_base?: number; trades: number }>;
  by_type: Array<{ type: string; amount_base?: number }>;
  total_base: number;
}

export interface SymbolAttributionRow {
  conid?: number;
  symbol: string;
  description?: string;
  asset_class?: string;
  country?: string | null;
  realized_pnl_base?: number;
  realized_st_base?: number;
  realized_lt_base?: number;
  transferred_pnl_base?: number;
  unrealized_pnl_base?: number;
  unrealized_st_base?: number;
  unrealized_lt_base?: number;
  mtm_total_base?: number;
  mtm_total_with_accruals_base?: number;
  mtm_transaction_base?: number;
  mtm_other_base?: number;
  commissions_base?: number;
  total_pnl_base?: number;
  snapshot_date?: string;
}

export interface SymbolAttributionResponse {
  period: { start?: string; end?: string; label?: string };
  rows: SymbolAttributionRow[];
}

export interface ExecutionRow {
  exec_id: string;
  trade_id?: string;
  order_id?: string;
  block_id?: string;
  ext_exec_id?: string;
  conid?: number;
  symbol?: string;
  description?: string;
  asset_class?: string;
  currency?: string;
  exchange?: string;
  order_type?: string;
  transaction_type?: string;
  execution_time?: string;
  order_time?: string;
  trade_date?: string;
  settle_date?: string;
  order_to_fill_lag_seconds?: number | null;
  buy_sell?: string;
  open_close?: string;
  quantity?: number;
  price?: number;
  proceeds?: number;
  proceeds_base?: number | null;
  net_cash?: number;
  commission?: number;
  commission_currency?: string;
  commission_base?: number | null;
  broker_execution_commission?: number;
  broker_clearing_commission?: number;
  third_party_execution_commission?: number;
  third_party_clearing_commission?: number;
  third_party_regulatory_commission?: number;
  other_commission?: number;
  multiplier?: number;
  is_multi_fill?: boolean;
  fills_in_order?: number;
}

export interface ExecutionsLagBucket {
  bucket: string;
  count: number;
}

export interface ExecutionsSummary {
  total_fills: number;
  distinct_orders: number;
  multi_fill_orders: number;
  multi_fill_order_ids: string[];
  max_lag_seconds: number | null;
  min_lag_seconds: number | null;
  avg_lag_seconds: number | null;
  lag_sample_size: number;
  total_commissions_base: number;
  total_proceeds_base: number;
  base_currency: string;
  lag_distribution: ExecutionsLagBucket[];
}

export interface ExecutionsResponse {
  rows: ExecutionRow[];
  limit: number;
  offset: number;
  summary: ExecutionsSummary;
}

export interface OpenShortRow {
  symbol: string;
  description?: string;
  asset_class?: string;
  currency: string;
  quantity?: number;
  mark_price?: number;
  position_value?: number;
  position_value_base?: number;
  cost_basis_money_base?: number;
  fifo_pnl_unrealized_base?: number;
  snapshot_date?: string;
}
