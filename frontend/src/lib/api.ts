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

// ---- Endpoint signatures (subset) - extend as the dashboard grows. ----

export interface HealthResponse {
  status: string;
}

export interface PortfolioOverview {
  as_of?: string;
  nav_base?: number;
  nav_date?: string;
  base_currency?: string;
  realized_pnl_base?: number;
  deposits_withdrawals_base?: number;
  total_pnl_base?: number;
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
    cost_base?: number;
    unrealized_pct?: number;
    positions?: number;
  };
  dividends?: { gross_base?: number; withholding_base?: number; net_base?: number };
  commissions?: { total_base?: number; trade_count?: number };
  interest?: { net_base?: number };
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
    equity_market_value_base?: number;
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
  pct_of_nav?: number;
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

export interface CommissionsSummary {
  total_base?: number;
  trade_count?: number;
  avg_per_trade_base?: number;
  [key: string]: unknown;
}

export interface PerformanceTwr {
  twr_pct?: number;
  annualised_pct?: number;
  raw_twr_pct?: number;
  raw_annualised_pct?: number;
  n_days?: number;
  series?: Array<{ date: string; nav: number; cashflow: number; cumulative_pct: number }>;
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
  xirr_pct?: number | null;
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
    latest_success_at?: string | null;
    age_hours?: number | null;
    is_stale?: boolean;
    threshold_hours?: number;
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

export const api = {
  health: () => get<HealthResponse>('/health'),
  dataCoverage: () => get<DataCoverage>('/data/coverage'),
  pipelineStatus: () => get<PipelineStatus>('/pipeline/status'),
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
  commissionsSummary: (filters: PeriodFilters = {}) =>
    get<CommissionsSummary>('/commissions/summary', filters as Record<string, string | number | undefined>),
  performanceTwr: (filters: PeriodFilters = {}, currency?: string) =>
    get<PerformanceTwr>('/performance/twr', {
      ...(filters as Record<string, string | number | undefined>),
      ...(currency ? { currency } : {}),
    }),
  performanceXirr: (filters: PeriodFilters = {}) =>
    get<XirrResponse>('/performance/xirr', filters as Record<string, string | number | undefined>),
  realizedTimeseries: (filters: PeriodFilters = {}, bucket: 'year' | 'month' | 'day' = 'day') =>
    get<RealizedTimeseriesRow[]>('/portfolio/realized-timeseries', {
      ...(filters as Record<string, string | number | undefined>),
      bucket,
    }),
  trades: (filters: PeriodFilters & { symbol?: string; asset_class?: string; status?: 'all' | 'open' | 'closed'; limit?: number; offset?: number } = {}) =>
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
  performanceRisk: (filters: PeriodFilters = {}, riskFreeRatePct = 0) =>
    get<RiskMetrics>('/performance/risk', {
      ...(filters as Record<string, string | number | undefined>),
      risk_free_rate_pct: riskFreeRatePct,
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
  optionsTrades: (
    filters: PeriodFilters & {
      underlying?: string;
      right?: 'C' | 'P';
      status?: 'all' | 'open' | 'closed';
      limit?: number;
      offset?: number;
    } = {},
  ) =>
    get<OptionsTradesResponse>('/options/trades', filters as Record<string, string | number | undefined>),
  optionsEae: (filters: PeriodFilters & { underlying?: string } = {}) =>
    get<OptionsEaeResponse>('/options/eae', filters as Record<string, string | number | undefined>),
  optionsPremiumSeries: (filters: PeriodFilters & { bucket?: 'month' | 'year' } = {}) =>
    get<OptionsPremiumSeriesResponse>('/options/premium-timeseries', filters as Record<string, string | number | undefined>),
  optionsByUnderlying: (filters: PeriodFilters = {}) =>
    get<OptionsByUnderlyingResponse>('/options/by-underlying', filters as Record<string, string | number | undefined>),
  optionsOpenPositions: (filters: Pick<PeriodFilters, 'account_id'> = {}) =>
    get<OptionsOpenPositionsResponse>('/options/open-positions', filters as Record<string, string | number | undefined>),
  optionsAssignmentRisk: () =>
    get<OptionsAssignmentRiskResponse>('/options/assignment-risk'),
  optionsConcentration: (filters: Pick<PeriodFilters, 'account_id'> = {}) =>
    get<OptionsConcentrationResponse>('/options/concentration', filters as Record<string, string | number | undefined>),
  optionsDteBuckets: () =>
    get<OptionsDteBucketsResponse>('/options/dte-buckets'),
  optionsPnlAttribution: (filters: PeriodFilters = {}) =>
    get<OptionsPnlAttributionResponse>('/options/pnl-attribution', filters as Record<string, string | number | undefined>),
  optionsAssignmentRate: (filters: PeriodFilters = {}) =>
    get<OptionsAssignmentRateResponse>('/options/assignment-rate', filters as Record<string, string | number | undefined>),
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
  account_id: string;
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
  summary: {
    total: number;
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
  realized?: number;
  dividends?: number;
  withholding_tax?: number;
  interest?: number;
  commissions?: number;
  transaction_tax?: number;
  fx_translation?: number;
  starting_value?: number;
  ending_value?: number;
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
  annualised_return_pct?: number;
  annualised_volatility_pct?: number;
  annualised_downside_volatility_pct?: number;
  sharpe_ratio?: number;
  sortino_ratio?: number;
  max_drawdown_pct?: number;
  max_drawdown_peak_date?: string;
  max_drawdown_trough_date?: string;
  calmar_ratio?: number;
  negative_days?: number;
  positive_days?: number;
}

export interface AlphaBetaResponse {
  is_reliable: boolean;
  reason?: string;
  aligned_days?: number;
  from_date?: string;
  to_date?: string;
  requested_from?: string;
  requested_to?: string;
  is_truncated?: boolean;
  truncation_reason?: string;
  benchmark?: string;
  benchmark_currency?: string;
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
  display_currency?: string;
  benchmark?: string;
  benchmark_currency?: string;
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
    realized?: number;
    change_in_unrealized?: number;
    dividends?: number;
    withholding_tax?: number;
    interest?: number;
    commissions?: number;
    broker_fees?: number;
    other_fees?: number;
    transaction_tax?: number;
    deposits_withdrawals?: number;
    fx_translation?: number;
  };
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
  is_debt: boolean;
}

export interface MarginPositionRow {
  symbol: string;
  asset_class?: string;
  quantity?: number;
  position_value_base?: number;
  pct_of_nav?: number;
}

export interface MarginSummaryResponse {
  snapshot_date?: string;
  base_currency?: string;
  nav_total?: number;
  nav_breakdown?: { cash?: number; stock?: number; options?: number; bonds?: number };
  cash_by_currency: MarginCashRow[];
  cash_debt_native_sum?: number;
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

export interface MarginInterestResponse {
  period: { from?: string; to?: string };
  paid_total_base: number;
  received_total_base: number;
  net_base: number;
  paid_by_currency: Array<{ currency: string; amount_native: number; amount_base?: number }>;
  received_by_currency: Array<{ currency: string; amount_native: number; amount_base?: number }>;
  avg_debt_native_sum: number;
  days_with_debt: number;
  span_days: number;
  effective_apr_pct?: number;
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
  cagr_pct?: number;
  note?: string;
}

export interface CommissionsBreakdown {
  by_asset_class: Array<{ asset_class: string; amount_base?: number; trades: number }>;
  by_type: Array<{ type: string; amount_base?: number }>;
  total_base: number;
}

export interface SymbolAttributionRow {
  account_id?: string;
  conid?: number;
  symbol: string;
  description?: string;
  asset_class?: string;
  sector?: string | null;
  industry?: string | null;
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
  account_id?: string;
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
  open_dt?: string;
  snapshot_date?: string;
}
