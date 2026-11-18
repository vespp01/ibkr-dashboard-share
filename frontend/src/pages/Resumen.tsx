import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { UseQueryResult } from '@tanstack/react-query';
import { api, type PeriodFilters, type RealizedTimeseriesRow } from '../lib/api';
import { computeTtm } from '../components/dividends/income';
import { KPICard } from '../components/KPICard';
import { QueryError } from '../components/QueryError';
import type { PageKey } from '../components/Sidebar';
import { TradeCalendar, latestTradeMonth, startOfMonth } from '../components/TradeCalendar';
import { YearlyMatrix } from '../components/YearlyMatrix';
import { useDashboardLayout } from '../lib/dashboardLayout';
import { formatCompactCurrency, formatCurrency, formatNumber, formatPercent, formatSignedCompactCurrency } from '../lib/format';
import { useI18n } from '../lib/i18n';

type FilterMode = 'period' | 'year';

interface Props {
  filters: PeriodFilters;
  currency: string;
  nav: number | undefined;
  realizedAsOf?: string;
  consistency?: { coverage_ratio?: number };
  /** Shared queries — passed in so React Query dedupes across pages. */
  overview: UseQueryResult<any>;
  marginQ: UseQueryResult<any>;
  /** Used by the "Cambiar a YTD" warning button. */
  onFilterModeChange: (mode: FilterMode) => void;
  onPeriodChange: (period: PeriodFilters['period']) => void;
  /** Used by the risk strip link to Margen. */
  onNavigate: (page: PageKey) => void;
}

const COPY = {
  es: {
    noDataTitle: 'Sin datos todavía.',
    noDataBody: 'Ejecuta el bootstrap o un pull del pipeline para poblar la base de datos',
    snapshotFlex: 'snapshot Flex',
    navCardTitle: 'Net Liquidation Value = NAV: acciones + opciones + cash + bonos + devengos, valorado al cierre EOD de Flex. Es el mismo número que «NAV» en el resto de pestañas.',
    periodTwr: 'TWR del periodo',
    twrTitle: 'Rentabilidad ponderada por tiempo: aísla el efecto de aportes y retiradas.',
    annualized: 'anualizado',
    hiddenTwr: 'oculto (NAV diaria insuficiente)',
    vsBenchmark: 'S&P 500',
    excess: 'exceso',
    allTimeReturn: 'Retorno all-time',
    allTimeTitle: 'Beneficio absoluto sobre aportes netos acumulados desde el inicio. No es un TWR.',
    ttmIncome: 'Ingresos netos 12 m',
    ttmTitle: 'Dividendos netos de los últimos 12 meses completos.',
    ttmYoy: 'vs 12 m previos',
    nextPayment: 'Próximo cobro',
    noUpcoming: 'sin cobros pendientes',
    riskTitle: 'Riesgo',
    riskNote: 'estimación Reg-T, no PM real',
    cushion: 'Cushion',
    excessLiq: 'Excess liq.',
    leverage: 'Leverage',
    liquidity: 'Liquidez',
    liquidityTitle: 'Cash neto del NAV (deudas por divisa ya restadas) / NLV, al cierre Flex EOD. No es una estimación Reg-T.',
    regTMaintTitle: 'Estimación Reg-T conservadora (mantenimiento 25 % acciones, prima completa en opciones). El margen real de IBKR Portfolio Margin suele ser MAYOR. No es una lectura en vivo: se deriva de las posiciones EOD.',
    regTTitle: 'Estimación Reg-T conservadora. El margen real de IBKR Portfolio Margin suele ser MAYOR.',
    viewMargin: 'Ver Margen',
    tradeCalendar: 'Calendario de operaciones',
    realizedMonthly: 'P&L realizado mensual por año',
    realizedMonthlyNote: 'Solo P&L FIFO realizado de trades cerrados. No incluye dividendos, intereses, comisiones ni MTM.',
    totalReturnMonthly: 'Retorno total mensual por año',
    totalReturnNote: 'MTM + realizado + dividendos + retención + interés + comisiones + impuesto de transacción + FX. Solo periodos con NAV diario denso.',
    tradedTickers: 'Tickers operados',
    noTradesMonth: 'No hay operaciones este mes',
    noShortsLine: 'Sin posiciones cortas abiertas',
    openCreditTitle: 'Crédito abierto · posiciones cortas',
    marketValueShort: 'mkt',
    tradeSingular: 'op',
    tradePlural: 'ops',
    profitFactor: 'Profit factor',
    tradingQuality: 'Calidad de trading',
    tradingQualityHint: 'Estadísticas de cierres del periodo y riesgo sobre NAV',
    closedTrades: 'cierres',
    profitFactorTitle: 'Suma de cierres ganadores / valor absoluto de cierres perdedores en el periodo activo.',
    winRate: 'Tasa de acierto',
    winRateTitle: 'Ganadores / cierres decididos. El break-even queda fuera del denominador.',
    expectancy: 'Expectativa',
    expectancyTitle: 'P&L medio por cierre del periodo activo.',
    payoff: 'Payoff',
    payoffTitle: 'Ganancia media / valor absoluto de pérdida media.',
    avgWinner: 'media G',
    avgLoser: 'media P',
    sharpeNav: 'Sharpe (NAV)',
    sharpeNavTitle: 'Ratio de Sharpe anualizado sobre la serie diaria de NAV (rf 0), el mismo de Rentabilidad. No usa P&L realizado.',
    maxDdNav: 'Drawdown máx. (NAV)',
    maxDdNavTitle: 'Mayor caída pico-valle del NAV diario en el periodo, el mismo de Rentabilidad.',
    riskUnavailable: 'muestra insuficiente',
    cumulativeRealized: 'P&L realizado acumulado',
    cumulativeRealizedTitle: 'Suma del P&L FIFO realizado de cierres en el periodo. No incluye MTM, dividendos, intereses, comisiones ni impuestos.',
    days: 'días',
    daysWithPnl: 'días con P&L',
    up: 'al alza',
    down: 'a la baja',
    greenDays: 'días verdes',
    greenDaysTitle: 'Días ganadores sobre días con P&L realizado distinto de cero.',
    twrHiddenTitle: 'TWR ocultado para este periodo.',
    switchToYtd: 'Cambiar a YTD',
    allHistoryAlternative: 'o, para todo el histórico,',
    goToAllTimePanel: 'ir al panel «Retorno total»',
    positionSnapshotWarning: 'Snapshot de posiciones inconsistente con NAV. Cobertura actual {coverage}%.',
    allTimePanel: 'Retorno total all-time',
    currentNav: 'NAV actual',
    netDeposits: 'Aportes netos acumulados',
    absoluteProfit: 'Beneficio absoluto',
    absoluteReturn: 'Retorno absoluto',
    since: 'Desde',
    years: 'años',
    asOf: 'a',
    depositsWithdrawals: 'depósitos - retiradas (FX del día de cada aporte -> {currency})',
    navMinusDeposits: 'NAV - aportes',
    allTimeNote: 'No es un TWR: los aportes recientes pesan igual que los antiguos. Incluye realizado, no realizado, dividendos, intereses, comisiones, impuestos y FX, por eso supera a la matriz de P&L realizado.',
  },
  en: {
    noDataTitle: 'No data yet.',
    noDataBody: 'Run bootstrap or a pipeline pull to populate the database',
    snapshotFlex: 'Flex snapshot',
    navCardTitle: 'Net Liquidation Value = NAV: stock + options + cash + bonds + accruals, valued at Flex EOD. The same number shown as NAV on the other tabs.',
    periodTwr: 'Period TWR',
    twrTitle: 'Time-weighted return: isolates the effect of deposits and withdrawals.',
    annualized: 'annualized',
    hiddenTwr: 'hidden (insufficient daily NAV)',
    vsBenchmark: 'S&P 500',
    excess: 'excess',
    allTimeReturn: 'All-time return',
    allTimeTitle: 'Absolute profit over cumulative net deposits since inception. Not a TWR.',
    ttmIncome: 'Net income 12 m',
    ttmTitle: 'Net dividends of the last 12 complete months.',
    ttmYoy: 'vs prior 12 m',
    nextPayment: 'Next payment',
    noUpcoming: 'no pending payments',
    riskTitle: 'Risk',
    riskNote: 'Reg-T estimate, not real PM',
    cushion: 'Cushion',
    excessLiq: 'Excess liq.',
    leverage: 'Leverage',
    liquidity: 'Liquidity',
    liquidityTitle: 'NAV net cash (per-currency debt already subtracted) / NLV, at Flex EOD. Not a Reg-T estimate.',
    regTMaintTitle: 'Conservative Reg-T estimate (25% stock maintenance, full option premium). Real IBKR Portfolio Margin is usually HIGHER. Not a live reading: derived from EOD positions.',
    regTTitle: 'Conservative Reg-T estimate. Real IBKR Portfolio Margin is usually HIGHER.',
    viewMargin: 'See Margin',
    tradeCalendar: 'Trade calendar',
    realizedMonthly: 'Monthly realized P&L by year',
    realizedMonthlyNote: 'FIFO realized P&L of closed trades only. Does not include dividends, interest, commissions or MTM.',
    totalReturnMonthly: 'Monthly total return by year',
    totalReturnNote: 'MTM + realized + dividends + withholding + interest + commissions + transaction tax + FX. Only periods with dense daily NAV.',
    tradedTickers: 'Traded tickers',
    noTradesMonth: 'No trades this month',
    noShortsLine: 'No open short positions',
    openCreditTitle: 'Open credit · short positions',
    marketValueShort: 'mkt',
    tradeSingular: 'trade',
    tradePlural: 'trades',
    profitFactor: 'Profit factor',
    tradingQuality: 'Trading quality',
    tradingQualityHint: 'Closed-trade statistics for the period and NAV-based risk',
    closedTrades: 'closed trades',
    profitFactorTitle: 'Sum of winning closes divided by absolute losing closes in the active period.',
    winRate: 'Win rate',
    winRateTitle: 'Winners divided by decided closes. Break-even is excluded from the denominator.',
    expectancy: 'Expectancy',
    expectancyTitle: 'Average P&L per close in the active period.',
    payoff: 'Payoff',
    payoffTitle: 'Average winner divided by absolute average loser.',
    avgWinner: 'avg W',
    avgLoser: 'avg L',
    sharpeNav: 'Sharpe (NAV)',
    sharpeNavTitle: 'Annualised Sharpe ratio on the daily NAV series (rf 0), the same figure as Performance. Not based on realized P&L.',
    maxDdNav: 'Max drawdown (NAV)',
    maxDdNavTitle: 'Largest peak-to-trough fall of the daily NAV in the period, the same figure as Performance.',
    riskUnavailable: 'sample too short',
    cumulativeRealized: 'Cumulative realized P&L',
    cumulativeRealizedTitle: 'Sum of FIFO realized P&L of closes in the period. Does not include MTM, dividends, interest, commissions or taxes.',
    days: 'days',
    daysWithPnl: 'days with P&L',
    up: 'up',
    down: 'down',
    greenDays: 'green days',
    greenDaysTitle: 'Winning days over days with non-zero realized P&L.',
    twrHiddenTitle: 'TWR hidden for this period.',
    switchToYtd: 'Switch to YTD',
    allHistoryAlternative: 'or, for the full history,',
    goToAllTimePanel: 'go to the "All-time return" panel',
    positionSnapshotWarning: 'Position snapshot is inconsistent with NAV. Current coverage {coverage}%.',
    allTimePanel: 'All-time return',
    currentNav: 'Current NAV',
    netDeposits: 'Cumulative net deposits',
    absoluteProfit: 'Absolute profit',
    absoluteReturn: 'Absolute return',
    since: 'Since',
    years: 'years',
    asOf: 'as of',
    depositsWithdrawals: 'deposits - withdrawals (FX on each contribution date -> {currency})',
    navMinusDeposits: 'NAV - deposits',
    allTimeNote: 'Not a TWR: recent deposits carry the same weight as old ones. Includes realized, unrealized, dividends, interest, commissions, taxes and FX, so it is larger than the realized P&L matrix.',
  },
  de: {
    noDataTitle: 'Noch keine Daten.',
    noDataBody: 'Fuehre Bootstrap oder einen Pipeline-Pull aus, um die Datenbank zu fuellen',
    snapshotFlex: 'Flex Snapshot',
    navCardTitle: 'Net Liquidation Value = NAV: Aktien + Optionen + Cash + Bonds + Abgrenzungen, zum Flex-EOD bewertet. Derselbe Wert wie NAV in den anderen Tabs.',
    periodTwr: 'TWR Zeitraum',
    twrTitle: 'Zeitgewichtete Rendite: blendet den Effekt von Ein- und Auszahlungen aus.',
    annualized: 'annualisiert',
    hiddenTwr: 'ausgeblendet (zu wenig taegliche NAV)',
    vsBenchmark: 'S&P 500',
    excess: 'Mehrrendite',
    allTimeReturn: 'Rendite seit Start',
    allTimeTitle: 'Absoluter Gewinn auf die kumulierten Nettoeinzahlungen seit Start. Kein TWR.',
    ttmIncome: 'Nettoertrag 12 M',
    ttmTitle: 'Netto-Dividenden der letzten 12 vollen Monate.',
    ttmYoy: 'vs. vorherige 12 M',
    nextPayment: 'Naechste Zahlung',
    noUpcoming: 'keine offenen Zahlungen',
    riskTitle: 'Risiko',
    riskNote: 'Reg-T-Schaetzung, kein echtes PM',
    cushion: 'Cushion',
    excessLiq: 'Excess Liq.',
    leverage: 'Leverage',
    liquidity: 'Liquiditaet',
    liquidityTitle: 'Netto-Cash im NAV (Schulden pro Waehrung bereits abgezogen) / NLV, zum Flex-EOD. Keine Reg-T-Schaetzung.',
    regTMaintTitle: 'Konservative Reg-T-Schaetzung (25% Maintenance fuer Aktien, volle Optionspraemie). Der reale IBKR Portfolio Margin ist ueblicherweise HOEHER. Kein Live-Wert: aus EOD-Positionen abgeleitet.',
    regTTitle: 'Konservative Reg-T-Schaetzung. Der reale IBKR Portfolio Margin ist ueblicherweise HOEHER.',
    viewMargin: 'Zu Margin',
    tradeCalendar: 'Trade-Kalender',
    realizedMonthly: 'Monatlicher realisierter P&L nach Jahr',
    realizedMonthlyNote: 'Nur realisierter FIFO-P&L geschlossener Trades. Ohne Dividenden, Zinsen, Kommissionen oder MTM.',
    totalReturnMonthly: 'Monatlicher Total Return nach Jahr',
    totalReturnNote: 'MTM + realisiert + Dividenden + Quellensteuer + Zinsen + Kommissionen + Transaktionssteuer + FX. Nur Zeitraeume mit dichter taeglicher NAV.',
    tradedTickers: 'Gehandelte Ticker',
    noTradesMonth: 'Keine Trades in diesem Monat',
    noShortsLine: 'Keine offenen Short-Positionen',
    openCreditTitle: 'Offener Credit · Short-Positionen',
    marketValueShort: 'Mkt',
    tradeSingular: 'Trade',
    tradePlural: 'Trades',
    profitFactor: 'Profit Factor',
    tradingQuality: 'Trading-Qualitaet',
    tradingQualityHint: 'Statistik geschlossener Trades im Zeitraum und NAV-basiertes Risiko',
    closedTrades: 'geschlossene Trades',
    profitFactorTitle: 'Summe gewinnender Closes geteilt durch den absoluten Betrag verlierender Closes im aktiven Zeitraum.',
    winRate: 'Trefferquote',
    winRateTitle: 'Gewinner geteilt durch entschiedene Closes. Break-even ist vom Nenner ausgeschlossen.',
    expectancy: 'Erwartungswert',
    expectancyTitle: 'Durchschnittlicher P&L je Close im aktiven Zeitraum.',
    payoff: 'Payoff',
    payoffTitle: 'Durchschnittlicher Gewinner geteilt durch absoluten durchschnittlichen Verlierer.',
    avgWinner: 'avg G',
    avgLoser: 'avg V',
    sharpeNav: 'Sharpe (NAV)',
    sharpeNavTitle: 'Annualisierte Sharpe Ratio auf der taeglichen NAV-Serie (rf 0), derselbe Wert wie in Performance. Nicht auf realisiertem P&L basiert.',
    maxDdNav: 'Max. Drawdown (NAV)',
    maxDdNavTitle: 'Groesster Peak-to-Trough-Rueckgang der taeglichen NAV im Zeitraum, derselbe Wert wie in Performance.',
    riskUnavailable: 'Stichprobe zu kurz',
    cumulativeRealized: 'Kumulierter realisierter P&L',
    cumulativeRealizedTitle: 'Summe des realisierten FIFO-P&L der Closes im Zeitraum. Ohne MTM, Dividenden, Zinsen, Kommissionen oder Steuern.',
    days: 'Tage',
    daysWithPnl: 'Tage mit P&L',
    up: 'im Plus',
    down: 'im Minus',
    greenDays: 'gruene Tage',
    greenDaysTitle: 'Gewinntage geteilt durch Tage mit realisiertem P&L ungleich null.',
    twrHiddenTitle: 'TWR fuer diesen Zeitraum ausgeblendet.',
    switchToYtd: 'Zu YTD wechseln',
    allHistoryAlternative: 'oder fuer die gesamte Historie',
    goToAllTimePanel: 'zum Panel "Rendite seit Start" gehen',
    positionSnapshotWarning: 'Positions-Snapshot ist inkonsistent mit NAV. Aktuelle Abdeckung {coverage}%.',
    allTimePanel: 'Rendite seit Start',
    currentNav: 'Aktueller NAV',
    netDeposits: 'Kumulierte Nettoeinzahlungen',
    absoluteProfit: 'Absoluter Gewinn',
    absoluteReturn: 'Absolute Rendite',
    since: 'Seit',
    years: 'Jahre',
    asOf: 'Stand',
    depositsWithdrawals: 'Einzahlungen - Auszahlungen (FX am Tag jeder Einzahlung -> {currency})',
    navMinusDeposits: 'NAV - Einzahlungen',
    allTimeNote: 'Kein TWR: neue Einzahlungen zaehlen gleich stark wie alte. Enthaelt realisiert, unrealisiert, Dividenden, Zinsen, Kommissionen, Steuern und FX und ist deshalb groesser als die Matrix des realisierten P&L.',
  },
} as const;

export function ResumenPage({
  filters, currency, nav, realizedAsOf, consistency,
  overview, marginQ,
  onFilterModeChange, onPeriodChange, onNavigate,
}: Props) {
  const { language, locale } = useI18n();
  const { isVisible } = useDashboardLayout();
  const copy = COPY[language];
  // The calendar's visible month also drives the activity tiles below it.
  const [calMonth, setCalMonth] = useState<Date>(() => startOfMonth(new Date()));
  // Trading quality is collapsed by default; its queries only run once opened.
  const [tqOpen, setTqOpen] = useState(false);
  const accountId = filters.account_id;

  // Resumen-only queries.
  // With the default "all" filter this is the very request the calendar makes: share its cache entry.
  const isAllPeriod = filters.period === 'all' && filters.year === undefined;
  const realizedDaily = useQuery({
    queryKey: isAllPeriod ? ['portfolio.realized', 'all', 'day', 'calendar'] : ['portfolio.realized', filters, 'day'],
    queryFn: () => api.realizedTimeseries(filters, 'day'),
  });
  // Sections a user hides in Settings do not fetch their data.
  const realizedMonthlyAll = useQuery({
    queryKey: ['portfolio.realized', 'all', 'month'],
    queryFn: () => api.realizedTimeseries({ period: 'all' }, 'month'),
    enabled: isVisible('realizedMonthly'),
  });
  const totalReturnMonthlyAll = useQuery({
    queryKey: ['portfolio.attribution.monthly', 'all'],
    queryFn: () => api.attributionTimeseries({ period: 'all' }, 'month'),
    enabled: isVisible('totalReturnMonthly'),
  });
  const openShortsQ = useQuery({
    queryKey: ['portfolio.openShorts'],
    queryFn: () => api.openShorts(),
    enabled: isVisible('activityTiles'),
  });
  const monthTradesQ = useQuery({
    queryKey: ['trades.month', calMonth.getFullYear(), calMonth.getMonth() + 1],
    queryFn: () => api.trades({ year: calMonth.getFullYear(), month: calMonth.getMonth() + 1, limit: 500 }),
    // The tiles are labelled with the month: never show last month's trades under it.
    placeholderData: () => undefined,
    // Wait for the daily series so the calendar has jumped to its latest month first.
    enabled: isVisible('activityTiles') && !realizedDaily.isLoading,
  });
  const allTimeReturnQ = useQuery({
    queryKey: ['portfolio.all-time-return'],
    queryFn: () => api.allTimeReturn(),
  });
  const twr = useQuery({
    queryKey: ['performance.twr', filters, 'CHF', 'summary'],
    queryFn: () => api.performanceTwr(filters, undefined, true),
  });
  const vsBenchmark = useQuery({
    queryKey: ['performance.vsBenchmark', filters, 'CHF'],
    queryFn: () => api.performanceVsBenchmark(filters, 'CHF'),
  });
  // Same keys as the Dividendos page: one fetch serves both.
  const dividendsAllMonthly = useQuery({
    queryKey: ['dividends.timeseries', { period: 'all', account_id: accountId }, 'month'],
    queryFn: () => api.dividendsTimeseries({ period: 'all', account_id: accountId }, 'month'),
  });
  const upcoming = useQuery({
    queryKey: ['dividends.upcoming', accountId],
    queryFn: () => api.dividendsUpcoming({ account_id: accountId }),
  });
  // Trading quality (only while the section is open).
  const periodClosedTrades = useQuery({
    queryKey: ['trades.period.closed', filters],
    queryFn: () => api.trades({ ...filters, status: 'closed', limit: 1 }),
    enabled: tqOpen,
  });
  const navRisk = useQuery({
    queryKey: ['performance.risk', filters],
    queryFn: () => api.performanceRisk(filters),
    enabled: tqOpen,
  });

  const twrPct = twr.data?.twr_pct as number | undefined;
  const annualisedPct = twr.data?.annualised_pct as number | undefined;
  const twrQuality = twr.data?.quality as
    | { is_reliable?: boolean; reason?: string; coverage_ratio?: number; max_gap_days?: number }
    | undefined;
  const showTwrWarning = twrQuality?.is_reliable === false;
  const showPositionsWarning = consistency?.coverage_ratio !== undefined && consistency.coverage_ratio < 0.9;
  const vsb = vsBenchmark.data?.is_reliable ? vsBenchmark.data.level_comparison : undefined;
  const ttm = useMemo(() => computeTtm(dividendsAllMonthly.data ?? []), [dividendsAllMonthly.data]);
  const nextPayment = [...(upcoming.data?.events ?? [])].sort((a, b) => a.pay_date.localeCompare(b.pay_date))[0];

  const atr = allTimeReturnQ.data;
  const cagrLabel = atr?.xirr_pct != null
    ? `XIRR ${formatPercent(atr.xirr_pct, true, locale)}` : null;

  // Daily realized P&L of the period: feeds the calendar and the cumulative card.
  const days: RealizedTimeseriesRow[] = realizedDaily.data ?? [];
  let winDays = 0;
  let lossDays = 0;
  let cumPnl = 0;
  days.forEach((d) => {
    cumPnl += d.realized;
    if (d.realized > 0) winDays += 1;
    else if (d.realized < 0) lossDays += 1;
  });
  // Green days over days with realized P&L != 0 (opening-only days are not decided days).
  const decidedDays = winDays + lossDays;
  const greenDayRate = decidedDays > 0 ? (winDays / decidedDays) * 100 : undefined;

  // Adjust-during-render: when the page filter changes, open the latest month with trades inside it.
  const filteredLatestMonth = latestTradeMonth(days);
  const filteredLatestKey = filteredLatestMonth ? filteredLatestMonth.getTime() : 0;
  const [seenLatestKey, setSeenLatestKey] = useState(0);
  if (filteredLatestMonth && filteredLatestKey !== seenLatestKey) {
    setSeenLatestKey(filteredLatestKey);
    setCalMonth(filteredLatestMonth);
  }
  const marginRegT = marginQ.data?.reg_t_estimate;
  const marginLev = marginQ.data?.leverage_x;
  const marginCushion = marginRegT?.cushion_pct_estimate;
  const marginExcessLiq = marginRegT?.excess_liq_estimate;
  const marginCashPct = marginQ.data?.cash_pct_nlv;

  return (
    <>
      {/* ─── First-boot empty state: DB without NAV nor positions ─── */}
      {overview.isSuccess && nav == null && overview.data?.unrealized?.positions === 0 ? (
        <div className="info-box" style={{ marginBottom: 12 }}>
          <strong>{copy.noDataTitle}</strong> {copy.noDataBody}
          (<code>docker exec ibkr-backend python -m ibkr_backend.cli pull</code>).
        </div>
      ) : null}

      {overview.isError ? <QueryError endpoint="/portfolio/overview" onRetry={() => overview.refetch()} /> : null}
      {twr.isError ? <QueryError endpoint="/performance/twr" onRetry={() => twr.refetch()} /> : null}

      {showTwrWarning ? (
        <div className="warn-box">
          <strong>{copy.twrHiddenTitle}</strong>{' '}
          {twrQuality?.reason}{' '}
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
              const panel = document.getElementById('all-time-return-panel');
              if (panel instanceof HTMLDetailsElement) panel.open = true;
              panel?.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }}
          >
            {copy.goToAllTimePanel}
          </button>.
        </div>
      ) : null}
      {showPositionsWarning ? (
        <div className="warn-box">
          {copy.positionSnapshotWarning.replace('{coverage}', (consistency!.coverage_ratio! * 100).toFixed(1))}
        </div>
      ) : null}

      {/* ─── Hero: what the account is worth and what it earned ─── */}
      {isVisible('accountSummary') ? (
      <div className="account-summary">
        <div className="account-card" title={copy.navCardTitle}>
          <span className="label">NAV</span>
          <span className="value">{formatCurrency(nav, currency, locale)}</span>
          <span className="sub">{realizedAsOf ?? copy.snapshotFlex}</span>
        </div>
        <div className="account-card" title={copy.twrTitle}>
          <span className="label">{copy.periodTwr}</span>
          <span className="value">{twr.isLoading ? '…' : formatPercent(twrPct, true, locale)}</span>
          <span className="sub">
            {vsb
              ? `${copy.vsBenchmark} ${formatPercent(vsb.benchmark_return_pct, true, locale)} · ${copy.excess} ${formatPercent(vsb.excess_return_pct, true, locale)}`
              : annualisedPct != null
                ? `${formatPercent(annualisedPct, true, locale)} ${copy.annualized}`
                : twrPct == null && !twr.isLoading ? copy.hiddenTwr : ' '}
          </span>
        </div>
        <div className="account-card" title={copy.allTimeTitle}>
          <span className="label">{copy.allTimeReturn}</span>
          <span className="value">
            {atr?.is_reliable && atr.absolute_return_pct != null ? formatPercent(atr.absolute_return_pct, true, locale) : allTimeReturnQ.isLoading ? '…' : '—'}
          </span>
          <span className="sub">{(atr?.is_reliable && cagrLabel) || ' '}</span>
        </div>
        <div className="account-card" title={copy.ttmTitle}>
          <span className="label">{copy.ttmIncome}</span>
          <span className="value">{dividendsAllMonthly.isLoading ? '…' : ttm ? formatCurrency(ttm.net, currency, locale) : '—'}</span>
          <span className="sub">
            {nextPayment
              ? `${copy.nextPayment}: ${nextPayment.symbol ?? '—'} · ${nextPayment.pay_date} · ${formatCompactCurrency(nextPayment.net_base, currency, locale)}`
              : upcoming.isSuccess ? copy.noUpcoming : ' '}
            {ttm?.yoyPct != null ? ` · ${formatPercent(ttm.yoyPct, true, locale)} ${copy.ttmYoy}` : ''}
          </span>
        </div>
      </div>
      ) : null}

      {/* ─── Risk strip: four numbers and a link; the detail lives in Margen ─── */}
      {isVisible('riskStrip') ? (
      <div className="risk-strip" title={copy.regTMaintTitle}>
        <span className="risk-strip-title">{copy.riskTitle} <small>{copy.riskNote}</small></span>
        {marginQ.isError ? (
          <QueryError endpoint="/portfolio/margin" onRetry={() => marginQ.refetch()} />
        ) : (
          <>
            <span className="risk-chip" title={copy.regTMaintTitle}>
              <small>{copy.cushion}</small>
              <b>{marginCushion != null ? formatPercent(marginCushion, false, locale) : '—'}</b>
            </span>
            <span className="risk-chip" title={copy.regTTitle}>
              <small>{copy.excessLiq}</small>
              <b>{formatCompactCurrency(marginExcessLiq, currency, locale)}</b>
            </span>
            <span className="risk-chip" title={copy.regTTitle}>
              <small>{copy.leverage}</small>
              <b>{marginLev != null ? `${formatNumber(marginLev, 2, locale)}×` : '—'}</b>
            </span>
            <span className="risk-chip" title={copy.liquidityTitle}>
              <small>{copy.liquidity}</small>
              <b>{marginCashPct != null ? formatPercent(marginCashPct, false, locale) : '—'}</b>
            </span>
          </>
        )}
        <button type="button" className="risk-strip-link" onClick={() => onNavigate('margen')}>{copy.viewMargin} →</button>
      </div>
      ) : null}

      {/* ─── Calendar (monthly view with daily P&L) ─── */}
      {isVisible('tradeCalendar') ? (
        <>
          <div className="panel-title" style={{ marginTop: 20 }}>{copy.tradeCalendar}</div>
          {realizedDaily.isError ? (
            <QueryError endpoint="/portfolio/realized-timeseries" onRetry={() => realizedDaily.refetch()} />
          ) : (
            <TradeCalendar currency={currency} month={calMonth} onMonthChange={setCalMonth} pending={realizedDaily.isLoading} />
          )}
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
        // '—' = nothing to measure, '∞' = gains without a single loss (never a numeric sentinel).
        const monthPfText = monthNeg < 0
          ? formatNumber(monthGross / Math.abs(monthNeg), 2, locale)
          : monthGross > 0 ? '∞' : '—';
        const monthLabel = calMonth.toLocaleDateString(locale, { month: 'long', year: 'numeric' });
        const shorts = openShortsQ.data ?? [];
        return (
          <>
          <div className={`bottom-row${shorts.length === 0 ? ' two-up' : ''}`}>
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
                          {info.pnl !== 0 ? formatSignedCompactCurrency(info.pnl, currency, locale) : '—'}
                        </span>
                        <span className="sub">{info.trades} {info.trades === 1 ? copy.tradeSingular : copy.tradePlural}</span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {shorts.length > 0 ? (
            <div className="tile">
              <div className="tile-title">{copy.openCreditTitle}</div>
              <div className="tile-list">
                  {shorts.map((s: any) => (
                    <div key={s.symbol} className="tile-row" title={s.description}>
                      <div>
                        <span className="sym">{s.symbol.length > 18 ? s.description ?? s.symbol : s.symbol}</span>
                        <span className="sub">{s.quantity?.toFixed(0)} × {s.asset_class}</span>
                      </div>
                      <div className="val">
                        <span className={(s.fifo_pnl_unrealized_base ?? 0) >= 0 ? 'pnl-pos' : 'pnl-neg'}>
                          {formatSignedCompactCurrency(s.fifo_pnl_unrealized_base ?? 0, currency, locale)}
                        </span>
                        <span className="sub">{formatCompactCurrency(s.position_value_base, currency, locale)} {copy.marketValueShort}</span>
                      </div>
                    </div>
                  ))}
              </div>
            </div>
            ) : null}

            <div className="tile pf">
              <div className="tile-title">{copy.profitFactor} · {monthLabel}</div>
              <div>
                <span className="value">{monthPfText}</span>
              </div>
              <div className="breakdown">
                <span className="pos">{formatSignedCompactCurrency(monthGross, currency, locale)}</span>
                <span className="neg">{formatSignedCompactCurrency(monthNeg, currency, locale)}</span>
              </div>
              <div className="breakdown" style={{ marginTop: 'auto' }}>
                <span>{monthSummary?.total ?? 0} {copy.tradePlural} · {monthSummary?.win ?? 0}W · {monthSummary?.loss ?? 0}L</span>
              </div>
            </div>
          </div>
          {shorts.length === 0 && openShortsQ.isSuccess ? (
            <div className="tile-note"><b>{copy.openCreditTitle}</b>{copy.noShortsLine}</div>
          ) : null}
          </>
        );
      })() : null}

      {/* ─── Yearly P&L matrix (Year × Month heatmap) ─── */}
      {isVisible('realizedMonthly') ? (
        <>
          <div className="panel-title" style={{ marginTop: 20 }}>{copy.realizedMonthly}</div>
          <p className="panel-subtitle">{copy.realizedMonthlyNote}</p>
          <YearlyMatrix data={realizedMonthlyAll.data ?? []} currency={currency} />
        </>
      ) : null}

      {(() => {
        const rows = totalReturnMonthlyAll.data ?? [];
        if (!isVisible('totalReturnMonthly') || rows.length === 0) return null;
        const totalRows = rows.map((r: any) => ({
          bucket: r.bucket,
          realized: r.total_return_base ?? 0,
          trades: 0,
        }));
        return (
          <>
            <div className="panel-title" style={{ marginTop: 20 }}>{copy.totalReturnMonthly}</div>
            <p className="panel-subtitle">{copy.totalReturnNote}</p>
            <YearlyMatrix data={totalRows} currency={currency} />
          </>
        );
      })()}

      {/* ─── Trading Quality: collapsed, loads on open ─── */}
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
        // null = no losers (infinite payoff), undefined = no data.
        const payoffRatio = ptAvgL < 0 ? ptAvgW / Math.abs(ptAvgL) : ptAvgW > 0 ? null : undefined;
        const risk = navRisk.data?.is_reliable ? navRisk.data : undefined;

        return (
          <details
            className="collapsible"
            open={tqOpen}
            onToggle={(event) => setTqOpen(event.currentTarget.open)}
          >
            <summary>
              <span className="panel-title">{copy.tradingQuality}</span>
              <span className="panel-subtitle">{copy.tradingQualityHint}</span>
            </summary>
            {periodClosedTrades.isError ? (
              <QueryError endpoint="/trades?status=closed" onRetry={() => periodClosedTrades.refetch()} />
            ) : null}
            <div className="stats-grid trading-quality-grid">
              <div className="stat-card" title={copy.profitFactorTitle}>
                <span className="label">{copy.profitFactor}</span>
                <div className="value-row">
                  <span className="value">{ptPf !== undefined && ptPf !== null ? formatNumber(ptPf, 2, locale) : '—'}</span>
                  <span className="value-suffix">n={ptTotal} {copy.closedTrades}</span>
                </div>
                <div className="breakdown">
                  <span className="pos">{formatSignedCompactCurrency(ptWinSum, currency, locale)}</span>
                  <span className="neg">{formatSignedCompactCurrency(ptLossSum, currency, locale)}</span>
                </div>
              </div>

              <div className="stat-card" title={copy.winRateTitle}>
                <span className="label">{copy.winRate}</span>
                <div className="value-row">
                  <span className="value">{ptWinRatePct !== undefined ? formatPercent(ptWinRatePct, false, locale) : '—'}</span>
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
                <span className="label">{copy.expectancy}</span>
                <div className="value-row">
                  <span className={`value ${(expectancy ?? 0) >= 0 ? 'pos' : 'neg'}`}>
                    {formatCurrency(expectancy, currency, locale)}
                  </span>
                </div>
                <div className="breakdown">
                  <span className="sub">n={ptTotal}</span>
                </div>
              </div>

              <div className="stat-card" title={copy.payoffTitle}>
                <span className="label">{copy.payoff}</span>
                <div className="value-row">
                  <span className="value">{payoffRatio === null ? '∞' : payoffRatio !== undefined ? formatNumber(payoffRatio, 2, locale) : '—'}</span>
                </div>
                <div className="breakdown">
                  <span className="pos">{copy.avgWinner} {formatCompactCurrency(ptAvgW, currency, locale)}</span>
                  <span className="neg">{copy.avgLoser} {formatCompactCurrency(ptAvgL, currency, locale)}</span>
                </div>
              </div>

              <div className="stat-card" title={copy.maxDdNavTitle}>
                <span className="label">{copy.maxDdNav}</span>
                <div className="value-row">
                  <span className="value neg">{risk?.max_drawdown_pct != null ? formatPercent(-Math.abs(risk.max_drawdown_pct), false, locale) : '—'}</span>
                </div>
                <div className="breakdown">
                  <span className="sub">
                    {risk ? `${risk.max_drawdown_peak_date ?? '—'} -> ${risk.max_drawdown_trough_date ?? '—'}` : navRisk.isSuccess ? copy.riskUnavailable : ' '}
                  </span>
                </div>
              </div>

              <div className="stat-card" title={copy.sharpeNavTitle}>
                <span className="label">{copy.sharpeNav}</span>
                <div className="value-row">
                  <span className="value">{risk?.sharpe_ratio != null ? formatNumber(risk.sharpe_ratio, 2, locale) : '—'}</span>
                </div>
                <div className="breakdown">
                  <span className="sub">{risk ? `n=${risk.n_days} ${copy.days}` : navRisk.isSuccess ? copy.riskUnavailable : ' '}</span>
                </div>
              </div>

              <div className="stat-card wide" title={copy.cumulativeRealizedTitle}>
                <span className="label">{copy.cumulativeRealized}</span>
                <div className="value-row">
                  <span className={`value ${cumPnl >= 0 ? 'pos' : 'neg'}`}>
                    {formatCurrency(cumPnl, currency, locale)}
                  </span>
                </div>
                <div className="breakdown">
                  <span className="sub" title={copy.greenDaysTitle}>
                    {decidedDays} {copy.daysWithPnl} · <span className="pos">{winDays} {copy.up}</span> · <span className="neg">{lossDays} {copy.down}</span> · {copy.greenDays} {greenDayRate !== undefined ? formatPercent(greenDayRate, false, locale) : '—'}
                  </span>
                </div>
              </div>
            </div>
          </details>
        );
      })() : null}

      {/* ─── All-time return on cash (honest absolute number, no daily TWR needed) ─── */}
      {(() => {
        if (!isVisible('allTimeReturn') || !atr || !atr.is_reliable) return null;
        return (
          <details className="collapsible" id="all-time-return-panel" style={{ scrollMarginTop: 80 }}>
            <summary>
              <span className="panel-title">{copy.allTimePanel}</span>
              <span className="panel-subtitle">
                {copy.currentNav} - {copy.netDeposits}. {atr.inception_date && `${copy.since} ${atr.inception_date}`} {atr.years_elapsed && `(${atr.years_elapsed.toLocaleString(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${copy.years})`}. {copy.allTimeNote}
              </span>
            </summary>
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
                  ? formatPercent(atr.absolute_return_pct, true, locale) : '—'}
                delta={cagrLabel ?? undefined}
                deltaKind={(atr.absolute_return_pct ?? 0) >= 0 ? 'pos' : 'neg'} />
            </div>
          </details>
        );
      })()}
    </>
  );
}
