import { useQuery } from '@tanstack/react-query';
import type { UseQueryResult } from '@tanstack/react-query';
import { api, type PeriodFilters, type RealizedTimeseriesRow } from '../lib/api';
import { AttributionStrip } from '../components/AttributionStrip';
import { KPICard } from '../components/KPICard';
import { TradeCalendar } from '../components/TradeCalendar';
import { YearlyMatrix } from '../components/YearlyMatrix';
import { useDashboardLayout } from '../lib/dashboardLayout';
import { formatCompactCurrency, formatCurrency, formatPercent } from '../lib/format';
import { useI18n } from '../lib/i18n';

type FilterMode = 'period' | 'year';

interface Props {
  filters: PeriodFilters;
  currency: string;
  nav: number | undefined;
  realizedAsOf?: string;
  unrealizedAsOf?: string;
  unrealized?: number;
  dividendsNet?: number;
  commissionsTotal?: number;
  incomeProjection?: {
    as_of?: string; padi_base?: number; monthly_base?: number;
    yield_pct?: number; yield_on_nav_pct?: number;
    positions_total?: number; positions_covered?: number;
  };
  consistency?: { coverage_ratio?: number };
  /** Shared queries — passed in so React Query dedupes across pages. */
  overview: UseQueryResult<any>;
  positions: UseQueryResult<any[] | undefined>;
  dividends: UseQueryResult<any>;
  commissions: UseQueryResult<any>;
  marginQ: UseQueryResult<any>;
  /** Used by the "Cambiar a YTD" warning button. */
  onFilterModeChange: (mode: FilterMode) => void;
  onPeriodChange: (period: PeriodFilters['period']) => void;
}

const COPY = {
  es: {
    noDataTitle: 'Sin datos todavía.',
    noDataBody: 'Ejecuta el bootstrap o un pull del pipeline para poblar la base de datos',
    snapshotFlex: 'snapshot Flex',
    regTEstimate: 'estimación, no PM real',
    realizedMonthly: 'P&L Realizado Mensual por Año',
    realizedMonthlyNote: 'Sólo fifo_pnl_realized de trades cerrados. NO incluye dividendos, intereses, comisiones ni MTM.',
    totalReturnMonthly: 'Total Return Mensual por Año',
    totalReturnNote: 'MTM + realized + dividendos + withholding + interés + comisiones + tx tax + FX. Sólo periodos con NAV diario denso.',
    tradeCalendar: 'Calendario de operaciones',
    tradedTickers: 'Tickers operados',
    noTradesMonth: 'No hay operaciones este mes',
    openCreditEmpty: 'No hay posiciones cortas abiertas',
    periodTwr: 'TWR período',
    annualized: 'anualizado',
    hiddenTwr: 'oculto (NAV diaria insuficiente)',
    netDividends: 'Dividendos netos',
    estimatedPadi: 'PADI estimado',
    yieldMarket: 'Yield (s/ mercado)',
    unrealizedPnl: 'P&L No Realizado',
    commissions: 'Comisiones',
    openPositions: 'Posiciones abiertas',
    withYield: 'con yield',
    tradedDays: 'Días operados',
    daysInPeriod: 'días en periodo',
    profitableSystem: 'sistema rentable',
    losingSystem: 'pierdes más de lo que ganas',
    allTimeReturn: 'Retorno total all-time',
    currentNav: 'NAV actual',
    netDeposits: 'Aportes netos acumulados',
    absoluteProfit: 'Beneficio absoluto',
    absoluteReturn: 'Retorno absoluto',
    since: 'Desde',
    years: 'años',
    month: 'mes',
    navCoverage: 'cobertura',
    navCardTitle: 'Net Liquidation Value = NAV. Mismo número que ves como «NAV» en otros tabs. = stock + opciones + cash + bonos + accrued, valorado al EOD de Flex.',
    regTMaintTitle: 'Estimación Reg-T conservadora (mantenimiento 25 % stocks, prima completa opciones). El número real en IBKR Portfolio Margin será habitualmente MAYOR. No es lectura en vivo del API — derivado de las posiciones EOD.',
    regTInitTitle: 'Estimación Reg-T conservadora (inicial 50 % stocks). El número real en IBKR Portfolio Margin será habitualmente MAYOR.',
    regTTitle: 'Estimación Reg-T conservadora. El número real en IBKR Portfolio Margin será habitualmente MAYOR.',
    profitFactorTitle: 'Suma de trades ganadores / valor absoluto de trades perdedores en el periodo activo.',
    closedTrades: 'closed trades',
    winRateTitle: 'Trades ganadores / trades decididos. Excluye break-even del denominador.',
    expectancyTitle: 'P&L medio por trade cerrado del periodo activo.',
    payoffTitle: 'Ganancia media / valor absoluto de pérdida media.',
    avgWinner: 'avg W',
    avgLoser: 'avg L',
    maxDrawdownTitle: 'Peak-to-trough del P&L realizado diario en el periodo activo.',
    sharpeTitle: 'Media diaria / desviación diaria, anualizado con raíz de 252. Usa solo P&L realizado diario.',
    tradingDays: 'trading days',
    dailyPf: 'daily PF',
    cumulativeRealizedTitle: 'Suma de fifo_pnl_realized de trades cerrados en el periodo. No incluye MTM, dividendos, intereses, comisiones ni transaction tax.',
    days: 'days',
    up: 'up',
    down: 'down',
    flat: 'flat',
    greenDays: 'green days',
    twrHiddenTitle: 'TWR ocultado para este periodo.',
    twrDenseNav: 'TWR necesita una serie diaria densa de NAV (>= 50 %); el histórico previo a 2026 es disperso.',
    switchToYtd: 'Cambiar a YTD',
    allHistoryAlternative: 'o, para todo el histórico,',
    goToAllTimePanel: 'ir al panel «Retorno total»',
    noDailyNavNeeded: '(no necesita NAV diaria).',
    positionSnapshotWarning: 'Snapshot de posiciones inconsistente con NAV. Cobertura actual {coverage}%.',
    tradeSingular: 'op',
    tradePlural: 'ops',
    openCreditTitle: 'Open Credit · short positions',
    marketValueShort: 'mkt',
    subestimates: 'subestima',
    withoutYield: 'sin yield',
    yieldOnNav: 's/ NAV',
    allTimeNote: 'NO es TWR — los aportes recientes pesan igual que los antiguos.',
    allTimeIncludes: 'Incluye realized + unrealized + dividendos + intereses - comisiones - tx tax - FX (por eso es mayor que la suma de la matriz «P&L Realizado» arriba, que sólo es FIFO realizado).',
    asOf: 'a',
    depositsWithdrawals: 'depósitos - retiradas (FX del día de cada aporte -> {currency})',
    navMinusDeposits: 'NAV - aportes',
  },
  en: {
    noDataTitle: 'No data yet.',
    noDataBody: 'Run bootstrap or a pipeline pull to populate the database',
    snapshotFlex: 'Flex snapshot',
    regTEstimate: 'estimate, not real PM',
    realizedMonthly: 'Monthly Realized P&L by Year',
    realizedMonthlyNote: 'Only fifo_pnl_realized from closed trades. Does not include dividends, interest, commissions, or MTM.',
    totalReturnMonthly: 'Monthly Total Return by Year',
    totalReturnNote: 'MTM + realized + dividends + withholding + interest + commissions + transaction tax + FX. Only dense daily NAV periods.',
    tradeCalendar: 'Trade calendar',
    tradedTickers: 'Traded tickers',
    noTradesMonth: 'No trades this month',
    openCreditEmpty: 'No open short positions',
    periodTwr: 'Period TWR',
    annualized: 'annualized',
    hiddenTwr: 'hidden (insufficient daily NAV)',
    netDividends: 'Net dividends',
    estimatedPadi: 'Estimated PADI',
    yieldMarket: 'Yield on market',
    unrealizedPnl: 'Unrealized P&L',
    commissions: 'Fees',
    openPositions: 'Open positions',
    withYield: 'with yield',
    tradedDays: 'Traded days',
    daysInPeriod: 'days in period',
    profitableSystem: 'profitable system',
    losingSystem: 'losing more than winning',
    allTimeReturn: 'All-time total return',
    currentNav: 'Current NAV',
    netDeposits: 'Cumulative net deposits',
    absoluteProfit: 'Absolute profit',
    absoluteReturn: 'Absolute return',
    since: 'Since',
    years: 'years',
    month: 'month',
    navCoverage: 'coverage',
    navCardTitle: 'Net Liquidation Value = NAV. Same number shown as NAV in other tabs: stock + options + cash + bonds + accrued, valued at Flex EOD.',
    regTMaintTitle: 'Conservative Reg-T estimate (25% stock maintenance, full option premium). Real IBKR Portfolio Margin is usually higher. Not a live API reading; derived from EOD positions.',
    regTInitTitle: 'Conservative Reg-T estimate (50% initial stock margin). Real IBKR Portfolio Margin is usually higher.',
    regTTitle: 'Conservative Reg-T estimate. Real IBKR Portfolio Margin is usually higher.',
    profitFactorTitle: 'Sum of winning trades divided by absolute losing trades in the active period.',
    closedTrades: 'closed trades',
    winRateTitle: 'Winning trades divided by decided trades. Break-even is excluded from the denominator.',
    expectancyTitle: 'Average P&L per closed trade in the active period.',
    payoffTitle: 'Average winner divided by absolute average loser.',
    avgWinner: 'avg W',
    avgLoser: 'avg L',
    maxDrawdownTitle: 'Peak-to-trough realized daily P&L in the active period.',
    sharpeTitle: 'Daily mean divided by daily standard deviation, annualized with square root of 252. Uses realized daily P&L only.',
    tradingDays: 'trading days',
    dailyPf: 'daily PF',
    cumulativeRealizedTitle: 'Sum of fifo_pnl_realized from closed trades in the period. Does not include MTM, dividends, interest, commissions, or transaction tax.',
    days: 'days',
    up: 'up',
    down: 'down',
    flat: 'flat',
    greenDays: 'green days',
    twrHiddenTitle: 'TWR hidden for this period.',
    twrDenseNav: 'TWR needs a dense daily NAV series (>= 50%); history before 2026 is sparse.',
    switchToYtd: 'Switch to YTD',
    allHistoryAlternative: 'or, for the full history,',
    goToAllTimePanel: 'go to the "Total return" panel',
    noDailyNavNeeded: '(does not require daily NAV).',
    positionSnapshotWarning: 'Position snapshot is inconsistent with NAV. Current coverage {coverage}%.',
    tradeSingular: 'trade',
    tradePlural: 'trades',
    openCreditTitle: 'Open credit · short positions',
    marketValueShort: 'mkt',
    subestimates: 'understates',
    withoutYield: 'without yield',
    yieldOnNav: 'on NAV',
    allTimeNote: 'This is not TWR: recent deposits carry the same weight as old ones.',
    allTimeIncludes: 'Includes realized + unrealized + dividends + interest - commissions - transaction tax - FX, so it is larger than the "Monthly Realized P&L" matrix above, which is FIFO realized only.',
    asOf: 'as of',
    depositsWithdrawals: 'deposits - withdrawals (FX on each contribution date -> {currency})',
    navMinusDeposits: 'NAV - deposits',
  },
  de: {
    noDataTitle: 'Noch keine Daten.',
    noDataBody: 'Fuehre Bootstrap oder einen Pipeline-Pull aus, um die Datenbank zu fuellen',
    snapshotFlex: 'Flex Snapshot',
    regTEstimate: 'Schaetzung, kein echtes PM',
    realizedMonthly: 'Monatlicher realisierter P&L nach Jahr',
    realizedMonthlyNote: 'Nur fifo_pnl_realized aus geschlossenen Trades. Ohne Dividenden, Zinsen, Kommissionen oder MTM.',
    totalReturnMonthly: 'Monatlicher Total Return nach Jahr',
    totalReturnNote: 'MTM + realized + Dividenden + Withholding + Zinsen + Kommissionen + Transaction Tax + FX. Nur Zeitraeume mit dichter taeglicher NAV.',
    tradeCalendar: 'Trade-Kalender',
    tradedTickers: 'Gehandelte Ticker',
    noTradesMonth: 'Keine Trades in diesem Monat',
    openCreditEmpty: 'Keine offenen Short-Positionen',
    periodTwr: 'TWR Zeitraum',
    annualized: 'annualisiert',
    hiddenTwr: 'ausgeblendet (zu wenig taegliche NAV)',
    netDividends: 'Netto-Dividenden',
    estimatedPadi: 'Geschaetzter PADI',
    yieldMarket: 'Yield auf Marktwert',
    unrealizedPnl: 'Unrealisierter P&L',
    commissions: 'Gebuehren',
    openPositions: 'Offene Positionen',
    withYield: 'mit Yield',
    tradedDays: 'Handelstage',
    daysInPeriod: 'Tage im Zeitraum',
    profitableSystem: 'profitables System',
    losingSystem: 'mehr Verlust als Gewinn',
    allTimeReturn: 'Total Return seit Start',
    currentNav: 'Aktueller NAV',
    netDeposits: 'Kumulierte Nettoeinzahlungen',
    absoluteProfit: 'Absoluter Gewinn',
    absoluteReturn: 'Absolute Rendite',
    since: 'Seit',
    years: 'Jahre',
    month: 'Monat',
    navCoverage: 'Abdeckung',
    navCardTitle: 'Net Liquidation Value = NAV. Derselbe Wert wie NAV in anderen Tabs: Aktien + Optionen + Cash + Bonds + accrued, zum Flex-EOD bewertet.',
    regTMaintTitle: 'Konservative Reg-T-Schaetzung (25% Maintenance fuer Aktien, volle Optionspraemie). Realer IBKR Portfolio Margin ist ueblicherweise hoeher. Kein Live-API-Wert; aus EOD-Positionen abgeleitet.',
    regTInitTitle: 'Konservative Reg-T-Schaetzung (50% Initial Margin fuer Aktien). Realer IBKR Portfolio Margin ist ueblicherweise hoeher.',
    regTTitle: 'Konservative Reg-T-Schaetzung. Realer IBKR Portfolio Margin ist ueblicherweise hoeher.',
    profitFactorTitle: 'Summe gewinnender Trades geteilt durch den absoluten Betrag verlierender Trades im aktiven Zeitraum.',
    closedTrades: 'geschlossene Trades',
    winRateTitle: 'Gewinner geteilt durch entschiedene Trades. Break-even ist vom Nenner ausgeschlossen.',
    expectancyTitle: 'Durchschnittlicher P&L je geschlossenem Trade im aktiven Zeitraum.',
    payoffTitle: 'Durchschnittlicher Gewinner geteilt durch absoluten durchschnittlichen Verlierer.',
    avgWinner: 'avg W',
    avgLoser: 'avg L',
    maxDrawdownTitle: 'Peak-to-trough des realisierten taeglichen P&L im aktiven Zeitraum.',
    sharpeTitle: 'Taeglicher Mittelwert / taegliche Standardabweichung, annualisiert mit Wurzel 252. Nutzt nur realisierten taeglichen P&L.',
    tradingDays: 'Handelstage',
    dailyPf: 'daily PF',
    cumulativeRealizedTitle: 'Summe von fifo_pnl_realized aus geschlossenen Trades im Zeitraum. Ohne MTM, Dividenden, Zinsen, Kommissionen oder Transaction Tax.',
    days: 'Tage',
    up: 'up',
    down: 'down',
    flat: 'flat',
    greenDays: 'gruene Tage',
    twrHiddenTitle: 'TWR fuer diesen Zeitraum ausgeblendet.',
    twrDenseNav: 'TWR benoetigt eine dichte taegliche NAV-Serie (>= 50%); die Historie vor 2026 ist duenn.',
    switchToYtd: 'Zu YTD wechseln',
    allHistoryAlternative: 'oder fuer die gesamte Historie',
    goToAllTimePanel: 'zum Panel "Total Return" gehen',
    noDailyNavNeeded: '(benoetigt keine taegliche NAV).',
    positionSnapshotWarning: 'Positions-Snapshot ist inkonsistent mit NAV. Aktuelle Abdeckung {coverage}%.',
    tradeSingular: 'Trade',
    tradePlural: 'Trades',
    openCreditTitle: 'Open Credit · Short-Positionen',
    marketValueShort: 'Mkt',
    subestimates: 'unterschaetzt',
    withoutYield: 'ohne Yield',
    yieldOnNav: 'auf NAV',
    allTimeNote: 'Das ist kein TWR: neue Einzahlungen zaehlen gleich stark wie alte.',
    allTimeIncludes: 'Enthaelt realized + unrealized + Dividenden + Zinsen - Kommissionen - tx tax - FX; deshalb ist es groesser als die Matrix "Monthly Realized P&L" oben, die nur FIFO-realized enthaelt.',
    asOf: 'Stand',
    depositsWithdrawals: 'Einzahlungen - Auszahlungen (FX am Tag jeder Einzahlung -> {currency})',
    navMinusDeposits: 'NAV - Einzahlungen',
  },
} as const;

export function ResumenPage({
  filters, currency, nav, realizedAsOf, unrealizedAsOf, unrealized,
  dividendsNet, commissionsTotal, incomeProjection, consistency,
  overview, positions, dividends, commissions, marginQ,
  onFilterModeChange, onPeriodChange,
}: Props) {
  const { language, locale } = useI18n();
  const { isVisible } = useDashboardLayout();
  const copy = COPY[language];
  // Resumen-only queries.
  const realizedDaily = useQuery({
    queryKey: ['portfolio.realized', filters, 'day'],
    queryFn: () => api.realizedTimeseries(filters, 'day'),
  });
  const realizedMonthlyAll = useQuery({
    queryKey: ['portfolio.realized', 'all', 'month'],
    queryFn: () => api.realizedTimeseries({ period: 'all' }, 'month'),
  });
  const totalReturnMonthlyAll = useQuery({
    queryKey: ['portfolio.attribution.monthly', 'all'],
    queryFn: () => api.attributionTimeseries({ period: 'all' }, 'month'),
  });
  const openShortsQ = useQuery({
    queryKey: ['portfolio.openShorts'],
    queryFn: () => api.openShorts(),
  });
  const monthTradesQ = useQuery({
    queryKey: ['trades.month', new Date().getFullYear(), new Date().getMonth() + 1],
    queryFn: () => api.trades({ year: new Date().getFullYear(), month: new Date().getMonth() + 1, limit: 500 }),
  });
  const periodClosedTrades = useQuery({
    queryKey: ['trades.period.closed', filters],
    queryFn: () => api.trades({ ...filters, status: 'closed', limit: 1 }),
  });
  const allTimeReturnQ = useQuery({
    queryKey: ['portfolio.all-time-return'],
    queryFn: () => api.allTimeReturn(),
  });
  const pnlAttribution = useQuery({
    queryKey: ['options.attribution', filters],
    queryFn: () => api.optionsPnlAttribution(filters),
  });
  const twr = useQuery({
    queryKey: ['performance.twr', filters, 'CHF'],
    queryFn: () => api.performanceTwr(filters),
  });

  const twrPct = twr.data?.twr_pct as number | undefined;
  const annualisedPct = twr.data?.annualised_pct as number | undefined;
  const twrQuality = twr.data?.quality as
    | { is_reliable?: boolean; reason?: string; coverage_ratio?: number; max_gap_days?: number }
    | undefined;
  const twrCoverage = twrQuality?.coverage_ratio !== undefined ? `${(twrQuality.coverage_ratio * 100).toFixed(0)}% ${copy.navCoverage}` : undefined;
  const showTwrWarning = twrQuality?.is_reliable === false;
  const showPositionsWarning = consistency?.coverage_ratio !== undefined && consistency.coverage_ratio < 0.9;

  // Position count (sorted only for stable iteration — Cartera does the real sort).
  const positionRows = (positions.data ?? [])
    .slice()
    .sort((a: any, b: any) => (b.position_value_base ?? 0) - (a.position_value_base ?? 0));

  // Trading stats computed from daily realized P&L (period-filtered)
  const days: RealizedTimeseriesRow[] = realizedDaily.data ?? [];
  let winDays = 0;
  let lossDays = 0;
  let beDays = 0;
  let cumPnl = 0;
  let positiveSum = 0;
  let negativeSum = 0;
  let firstDate: string | undefined;
  let lastDate: string | undefined;
  days.forEach((d) => {
    cumPnl += d.realized;
    if (d.realized > 0) { winDays += 1; positiveSum += d.realized; }
    else if (d.realized < 0) { lossDays += 1; negativeSum += d.realized; }
    else if (d.trades > 0) beDays += 1;
    if (!firstDate || d.bucket < firstDate) firstDate = d.bucket;
    if (!lastDate || d.bucket > lastDate) lastDate = d.bucket;
  });
  const tradingDays = winDays + lossDays + beDays;
  const winRate = tradingDays > 0 ? (winDays / tradingDays) * 100 : 0;
  const profitFactor = negativeSum < 0 ? positiveSum / Math.abs(negativeSum) : positiveSum > 0 ? 99 : 0;
  const dailyPnls = days.map((d) => Number(d.realized ?? 0)).filter((value) => Number.isFinite(value));
  let runningPnl = 0;
  let peakPnl = 0;
  let maxDrawdown = 0;
  dailyPnls.forEach((value) => {
    runningPnl += value;
    peakPnl = Math.max(peakPnl, runningPnl);
    maxDrawdown = Math.max(maxDrawdown, peakPnl - runningPnl);
  });
  const dailyMean = dailyPnls.length > 0 ? dailyPnls.reduce((sum, value) => sum + value, 0) / dailyPnls.length : 0;
  const dailyVariance = dailyPnls.length > 1
    ? dailyPnls.reduce((sum, value) => sum + (value - dailyMean) ** 2, 0) / (dailyPnls.length - 1)
    : 0;
  const realizedSharpe = dailyVariance > 0 ? (dailyMean / Math.sqrt(dailyVariance)) * Math.sqrt(252) : 0;
  const marginRegT = marginQ.data?.reg_t_estimate;
  const marginLev = marginQ.data?.leverage_x;
  const marginCushion = marginRegT?.cushion_pct_estimate;
  const marginBP = marginRegT?.buying_power_estimate;
  const marginExcessLiq = marginRegT?.excess_liq_estimate;

  return (
    <>
      {/* ─── First-boot empty state: DB without NAV nor positions ─── */}
      {overview.isSuccess && nav == null && positions.isSuccess && (positions.data ?? []).length === 0 ? (
        <div className="info-box" style={{ marginBottom: 12 }}>
          <strong>{copy.noDataTitle}</strong> {copy.noDataBody}
          (<code>docker exec ibkr-backend python -m ibkr_backend.cli pull --period LastBusinessDay</code>).
        </div>
      ) : null}

      {/* ─── Account Summary ─── */}
      {isVisible('accountSummary') ? (
      <div className="account-summary">
        <div className="account-card" title={copy.navCardTitle}>
          <span className="label">NLV (= NAV)</span>
          <span className="value">{formatCurrency(nav, currency, locale)}</span>
          <span className="sub">{realizedAsOf ?? copy.snapshotFlex}</span>
        </div>
        <div className="account-card" title={copy.regTMaintTitle}>
          <span className="label">Cushion (Reg-T aprox)</span>
          <span className="value">{marginCushion !== undefined && marginCushion !== null ? `${marginCushion.toFixed(1)}%` : '—'}</span>
          <span className="sub">Reg-T 25 % maint · {copy.regTEstimate}</span>
        </div>
        <div className="account-card" title={copy.regTInitTitle}>
          <span className="label">Buying Power (Reg-T aprox)</span>
          <span className="value">{formatCompactCurrency(marginBP, currency, locale)}</span>
          <span className="sub">Reg-T 50 % init · {copy.regTEstimate}</span>
        </div>
        <div className="account-card" title={copy.regTTitle}>
          <span className="label">Excess Liq. (Reg-T aprox)</span>
          <span className="value">{formatCompactCurrency(marginExcessLiq, currency, locale)}</span>
          <span className="sub">{marginLev !== undefined && marginLev !== null ? `leverage ${marginLev.toLocaleString(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}× · ${copy.regTEstimate}` : copy.regTEstimate}</span>
        </div>
      </div>
      ) : null}

      {/* ─── Atribución de P&L (Sinclair "decompose before any skill claim") ─── */}
      {isVisible('pnlAttribution') && pnlAttribution.data && pnlAttribution.data.totals.grand_total_base !== 0 ? (
        <div style={{ marginTop: 16 }}>
          <AttributionStrip
            totals={pnlAttribution.data.totals}
            currency={currency}
            showEdgeRatio
          />
        </div>
      ) : null}

      {/* ─── Trading Quality ─── */}
      {isVisible('tradingQuality') ? (() => {
        const tradeSum = periodClosedTrades.data?.summary;
        const ptPf = tradeSum?.profit_factor;
        const ptWinRatePct = tradeSum?.win_rate !== undefined && tradeSum?.win_rate !== null
          ? tradeSum.win_rate * 100 : undefined;
        const ptWin = tradeSum?.win ?? 0;
        const ptLoss = tradeSum?.loss ?? 0;
        const ptBe = tradeSum?.be ?? 0;
        const ptWinSum = tradeSum?.win_sum_base ?? 0;
        const ptLossSum = tradeSum?.loss_sum_base ?? 0;
        const ptAvgW = tradeSum?.avg_winner_base ?? 0;
        const ptAvgL = tradeSum?.avg_loser_base ?? 0;
        const ptTotal = tradeSum?.total ?? 0;
        const expectancy = ptTotal > 0 ? (tradeSum?.realized_total_base ?? 0) / ptTotal : undefined;
        const payoffRatio = ptAvgL < 0 ? ptAvgW / Math.abs(ptAvgL) : ptAvgW > 0 ? 99 : undefined;

        return (
          <div className="dashboard-card-section">
            <div className="panel-title">Trading Quality</div>
            <div className="stats-grid trading-quality-grid">
              <div className="stat-card" title={copy.profitFactorTitle}>
                <span className="label">Profit Factor</span>
                <div className="value-row">
                  <span className="value">{ptPf !== undefined && ptPf !== null ? ptPf.toFixed(2) : '--'}</span>
                  <span className="value-suffix">{copy.closedTrades}</span>
                </div>
                <div className="breakdown">
                  <span className="pos">+{formatCompactCurrency(ptWinSum, currency, locale)}</span>
                  <span className="neg">{formatCompactCurrency(ptLossSum, currency, locale)}</span>
                </div>
              </div>

              <div className="stat-card" title={copy.winRateTitle}>
                <span className="label">Win Rate</span>
                <div className="value-row">
                  <span className="value">{ptWinRatePct !== undefined ? `${ptWinRatePct.toFixed(1)}%` : '--'}</span>
                </div>
                <div className="gauge-bar">
                  <div className="gauge-fill" style={{ width: `${ptWinRatePct ?? 0}%` }} />
                </div>
                <div className="breakdown">
                  <span className="pos">{ptWin}W</span>
                  <span className="neg">{ptLoss}L</span>
                  {ptBe > 0 && <span>{ptBe} BE</span>}
                </div>
              </div>

              <div className="stat-card" title={copy.expectancyTitle}>
                <span className="label">Expectancy</span>
                <div className="value-row">
                  <span className={`value ${(expectancy ?? 0) >= 0 ? 'pos' : 'neg'}`}>
                    {formatCurrency(expectancy, currency, locale)}
                  </span>
                </div>
                <div className="breakdown">
                  <span className="sub">{ptTotal} {copy.closedTrades}</span>
                </div>
              </div>

              <div className="stat-card" title={copy.payoffTitle}>
                <span className="label">Payoff Ratio</span>
                <div className="value-row">
                  <span className="value">{payoffRatio !== undefined ? payoffRatio.toFixed(2) : '--'}</span>
                </div>
                <div className="breakdown">
                  <span className="pos">{copy.avgWinner} {formatCompactCurrency(ptAvgW, currency, locale)}</span>
                  <span className="neg">{copy.avgLoser} {formatCompactCurrency(ptAvgL, currency, locale)}</span>
                </div>
              </div>

              <div className="stat-card" title={copy.maxDrawdownTitle}>
                <span className="label">Max Drawdown</span>
                <div className="value-row">
                  <span className="value neg">{formatCurrency(maxDrawdown > 0 ? -maxDrawdown : 0, currency, locale)}</span>
                </div>
                <div className="breakdown">
                  <span className="sub">{firstDate ?? '--'} {'->'} {lastDate ?? '--'}</span>
                </div>
              </div>

              <div className="stat-card" title={copy.sharpeTitle}>
                <span className="label">Sharpe Realized</span>
                <div className="value-row">
                  <span className={`value ${realizedSharpe >= 0 ? 'pos' : 'neg'}`}>
                    {Number.isFinite(realizedSharpe) ? realizedSharpe.toFixed(2) : '--'}
                  </span>
                </div>
                <div className="breakdown">
                  <span className="sub">{tradingDays} {copy.tradingDays} · {copy.dailyPf} {profitFactor.toLocaleString(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
                </div>
              </div>

              <div className="stat-card wide" title={copy.cumulativeRealizedTitle}>
                <span className="label">Cumulative realized P&L</span>
                <div className="value-row">
                  <span className={`value ${cumPnl >= 0 ? 'pos' : 'neg'}`}>
                    {formatCurrency(cumPnl, currency, locale)}
                  </span>
                </div>
                <div className="breakdown">
                  <span className="sub">
                    {tradingDays} {copy.days} · <span className="pos">{winDays} {copy.up}</span> · <span className="neg">{lossDays} {copy.down}</span> · {beDays} {copy.flat} · {copy.greenDays} {winRate.toLocaleString(locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%
                  </span>
                </div>
              </div>
            </div>
          </div>
        );
      })() : null}

      {showTwrWarning ? (
        <div className="warn-box">
          <strong>{copy.twrHiddenTitle}</strong>{' '}
          {twrQuality?.reason}{twrCoverage ? ` (${twrCoverage}).` : '.'}
          {' '}{copy.twrDenseNav}{' '}
          <button
            type="button"
            className="warn-cta"
            onClick={() => { onFilterModeChange('period'); onPeriodChange('ytd'); }}
          >
            {copy.switchToYtd}
          </button>
          {' '}{copy.allHistoryAlternative}{' '}
          <button
            type="button"
            className="warn-cta"
            onClick={() => {
              document.getElementById('all-time-return-panel')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }}
          >
            {copy.goToAllTimePanel}
          </button>{' '}{copy.noDailyNavNeeded}
        </div>
      ) : null}
      {showPositionsWarning ? (
        <div className="warn-box">
          {copy.positionSnapshotWarning.replace('{coverage}', (consistency!.coverage_ratio! * 100).toFixed(1))}
        </div>
      ) : null}

      {/* ─── Yearly P&L matrix (Year × Month heatmap) ─── */}
      {isVisible('realizedMonthly') ? (
        <>
          <div className="panel-title" style={{ marginTop: 20 }}>
            {copy.realizedMonthly}
            <small style={{ display: 'block', fontWeight: 400, color: 'var(--fg-muted, #8b88a6)', fontSize: 12 }}>
              {copy.realizedMonthlyNote}
            </small>
          </div>
          <YearlyMatrix data={realizedMonthlyAll.data ?? []} currency={currency} />
        </>
      ) : null}

      {(() => {
        const rows = totalReturnMonthlyAll.data ?? [];
        if (!isVisible('totalReturnMonthly') || rows.length === 0) return null;
        const totalRows = rows.map((r: any) => ({
          bucket: r.bucket,
          realized: (r.mtm ?? 0) + (r.realized ?? 0)
            + (r.dividends ?? 0) + (r.withholding_tax ?? 0)
            + (r.interest ?? 0) + (r.commissions ?? 0)
            + (r.transaction_tax ?? 0) + (r.fx_translation ?? 0),
          trades: 0,
        }));
        return (
          <>
            <div className="panel-title" style={{ marginTop: 20 }}>
              {copy.totalReturnMonthly}
              <small style={{ display: 'block', fontWeight: 400, color: 'var(--fg-muted, #8b88a6)', fontSize: 12 }}>
                {copy.totalReturnNote}
              </small>
            </div>
            <YearlyMatrix data={totalRows} currency={currency} />
          </>
        );
      })()}

      {/* ─── Calendar (monthly view with daily P&L) ─── */}
      {isVisible('tradeCalendar') ? (
        <>
          <div className="panel-title" style={{ marginTop: 20 }}>{copy.tradeCalendar}</div>
          <TradeCalendar data={days} currency={currency} />
        </>
      ) : null}

      {/* ─── Bottom row: Traded Tickers · Open Credit · Monthly Profit Factor ─── */}
      {isVisible('activityTiles') ? (() => {
        const monthRows = monthTradesQ.data?.rows ?? [];
        const monthSummary = monthTradesQ.data?.summary;
        const tickerMap = new Map<string, { trades: number; pnl: number; currency?: string }>();
        monthRows.forEach((r: any) => {
          const cur = tickerMap.get(r.symbol) ?? { trades: 0, pnl: 0, currency: r.currency };
          cur.trades += 1;
          cur.pnl += r.fifo_pnl_realized_base ?? 0;
          tickerMap.set(r.symbol, cur);
        });
        const tickers = Array.from(tickerMap.entries())
          .sort((a, b) => b[1].trades - a[1].trades)
          .slice(0, 10);
        // Server-side sums cover the full filtered set; monthRows is capped by `limit`.
        const monthGross = monthSummary?.win_sum_base ?? 0;
        const monthNeg = monthSummary?.loss_sum_base ?? 0;
        const monthPf = monthSummary?.profit_factor ?? (monthGross > 0 ? 99 : 0);
        const monthLabel = new Date().toLocaleDateString(locale, { month: 'long' });
        return (
          <div className="bottom-row">
            <div className="tile">
              <div className="tile-title">{copy.tradedTickers} · {monthLabel}</div>
              {tickers.length === 0 ? (
                <div className="tile-empty">{copy.noTradesMonth}</div>
              ) : (
                <div className="tile-list">
                  {tickers.map(([sym, info]) => (
                    <div key={sym} className="tile-row">
                      <div>
                        <span className="sym">{sym}</span>
                        <span className="sub">{info.currency}</span>
                      </div>
                      <div className="val">
                        <span className={info.pnl > 0 ? 'pnl-pos' : info.pnl < 0 ? 'pnl-neg' : ''}>
                          {info.pnl !== 0
                            ? `${info.pnl > 0 ? '+' : ''}${formatCompactCurrency(info.pnl, currency, locale)}`
                            : '—'}
                        </span>
                        <span className="sub">{info.trades} {info.trades === 1 ? copy.tradeSingular : copy.tradePlural}</span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="tile">
              <div className="tile-title">{copy.openCreditTitle}</div>
              {(openShortsQ.data ?? []).length === 0 ? (
                <div className="tile-empty">{copy.openCreditEmpty}</div>
              ) : (
                <div className="tile-list">
                  {(openShortsQ.data ?? []).map((s: any) => (
                    <div key={s.symbol} className="tile-row" title={s.description}>
                      <div>
                        <span className="sym">{s.symbol.length > 18 ? s.description ?? s.symbol : s.symbol}</span>
                        <span className="sub">{s.quantity?.toFixed(0)} × {s.asset_class}</span>
                      </div>
                      <div className="val">
                        <span className={(s.fifo_pnl_unrealized_base ?? 0) >= 0 ? 'pnl-pos' : 'pnl-neg'}>
                          {(s.fifo_pnl_unrealized_base ?? 0) >= 0 ? '+' : ''}{formatCompactCurrency(s.fifo_pnl_unrealized_base, currency, locale)}
                        </span>
                        <span className="sub">{formatCompactCurrency(s.position_value_base, currency, locale)} {copy.marketValueShort}</span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="tile pf">
              <div className="tile-title">Profit Factor · {monthLabel}</div>
              <div>
                <span className="value">{monthPf.toFixed(2)}</span>
                <span className="value-suffix">profit factor</span>
              </div>
              <div className="breakdown">
                <span className="pos">+{monthGross.toFixed(0)} {currency}</span>
                <span className="neg">{monthNeg.toFixed(0)} {currency}</span>
              </div>
              <div className="breakdown" style={{ marginTop: 'auto' }}>
                <span>{monthSummary?.total ?? 0} ops · {monthSummary?.win ?? 0}W · {monthSummary?.loss ?? 0}L</span>
              </div>
            </div>
          </div>
        );
      })() : null}

      {/* ─── Additional KPIs for context ─── */}
      {isVisible('coreKpis') ? (
      <div className="kpi-grid" style={{ marginTop: 18 }}>
        <KPICard label={copy.periodTwr} value={formatPercent(twrPct, true, locale)}
          delta={annualisedPct != null ? `${formatPercent(annualisedPct, true, locale)} ${copy.annualized}` : (twrPct == null ? copy.hiddenTwr : undefined)}
          deltaKind={twrPct != null ? (twrPct >= 0 ? 'pos' : 'neg') : 'neutral'} loading={twr.isLoading} />
        <KPICard label={copy.netDividends} value={formatCurrency(dividendsNet, currency, locale)} deltaKind="pos" loading={dividends.isLoading} />
        <KPICard label={copy.estimatedPadi} value={formatCurrency(incomeProjection?.padi_base, currency, locale)}
          delta={(() => {
            const monthly = incomeProjection?.monthly_base !== undefined ? `${formatCompactCurrency(incomeProjection.monthly_base, currency, locale)}/${copy.month}` : '';
            const covTot = incomeProjection?.positions_total ?? 0;
            const covOk = incomeProjection?.positions_covered ?? 0;
            const gap = covTot - covOk;
            return gap > 0 ? `${monthly} · ${copy.subestimates} (${gap} ${copy.withoutYield})` : monthly || undefined;
          })()}
          deltaKind="pos" loading={overview.isLoading} />
        <KPICard label={copy.yieldMarket} value={formatPercent(incomeProjection?.yield_pct, false, locale)}
          delta={incomeProjection?.yield_on_nav_pct !== undefined ? `${formatPercent(incomeProjection.yield_on_nav_pct, false, locale)} ${copy.yieldOnNav}` : undefined}
          deltaKind="pos" loading={overview.isLoading} />
        <KPICard label={copy.unrealizedPnl} value={formatCurrency(unrealized, currency, locale)}
          delta={unrealizedAsOf} deltaKind={unrealized !== undefined ? (unrealized >= 0 ? 'pos' : 'neg') : 'neutral'} loading={overview.isLoading} />
        <KPICard label={copy.commissions} value={formatCurrency(commissionsTotal, currency, locale)} deltaKind="neg" loading={commissions.isLoading} />
        <KPICard label={copy.openPositions} value={String(positionRows.length)}
          delta={`${incomeProjection?.positions_covered ?? '—'}/${incomeProjection?.positions_total ?? '—'} ${copy.withYield}`}
          deltaKind="neutral" loading={positions.isLoading} />
        <KPICard label={copy.tradedDays} value={String(tradingDays)} delta={`${days.length} ${copy.daysInPeriod}`} deltaKind="neutral" loading={realizedDaily.isLoading} />
      </div>
      ) : null}

      {/* ─── All-time return on cash (honest absolute number, no daily TWR needed) ─── */}
      {(() => {
        const atr = allTimeReturnQ.data;
        if (!isVisible('allTimeReturn') || !atr || !atr.is_reliable) return null;
        const cagrLabel = atr.cagr_pct !== undefined && atr.cagr_pct !== null
          ? `CAGR ${atr.cagr_pct >= 0 ? '+' : ''}${atr.cagr_pct.toFixed(2)}%` : null;
        return (
          <>
            <div className="panel-title" id="all-time-return-panel" style={{ marginTop: 22, scrollMarginTop: 80 }}>
              {copy.allTimeReturn}
              <small style={{ display: 'block', fontWeight: 400, color: '#8b88a6', fontSize: 12 }}>
                {copy.currentNav} - {copy.netDeposits}. {atr.inception_date && `${copy.since} ${atr.inception_date}`} {atr.years_elapsed && `(${atr.years_elapsed.toLocaleString(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${copy.years})`}.
                {copy.allTimeNote}
                {copy.allTimeIncludes}
              </small>
            </div>
            <div className="kpi-grid">
              <KPICard label={copy.currentNav}
                value={formatCurrency(atr.current_nav_base, currency, locale)}
                delta={atr.as_of ? `${copy.asOf} ${atr.as_of}` : undefined} deltaKind="neutral" />
              <KPICard label={copy.netDeposits}
                value={formatCurrency(atr.cumulative_net_deposits_base, currency, locale)}
                delta={copy.depositsWithdrawals.replace('{currency}', currency)} deltaKind="neutral" />
              <KPICard label={copy.absoluteProfit}
                value={formatCurrency(atr.absolute_profit_base, currency, locale)}
                delta={copy.navMinusDeposits} deltaKind={(atr.absolute_profit_base ?? 0) >= 0 ? 'pos' : 'neg'} />
              <KPICard label={copy.absoluteReturn}
                value={atr.absolute_return_pct !== undefined && atr.absolute_return_pct !== null
                  ? `${atr.absolute_return_pct >= 0 ? '+' : ''}${atr.absolute_return_pct.toLocaleString(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%` : '—'}
                delta={cagrLabel ?? undefined}
                deltaKind={(atr.absolute_return_pct ?? 0) >= 0 ? 'pos' : 'neg'} />
            </div>
          </>
        );
      })()}
    </>
  );
}
