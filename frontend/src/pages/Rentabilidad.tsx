import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api, type PeriodFilters } from '../lib/api';
import { EquityChart } from '../components/EquityChart';
import { KPICard } from '../components/KPICard';
import { MissingData } from '../components/MissingData';
import { formatPercent } from '../lib/format';
import { useI18n } from '../lib/i18n';

type TwrCurrency = 'CHF' | 'EUR' | 'USD';
const TWR_CURRENCIES: TwrCurrency[] = ['CHF', 'EUR', 'USD'];
const YEAR_RETURN_FILTER: PeriodFilters = { period: '1y' };

interface Props {
  filters: PeriodFilters;
  currency: string;
}

const COPY = {
  es: {
    intro: 'TWR (time-weighted) mide la habilidad de la cartera neutralizando el efecto de aportes. XIRR (money-weighted) responde a qué tasa anual está generando tu dinero y pondera por tamaño y timing de aportes.',
    currencyLabel: 'Divisa TWR:',
    unavailableTitle: 'Rentabilidad no disponible',
    unavailableBody: 'Necesita datos de NAV para calcular TWR.',
    noNavTitle: 'Sin datos de NAV en este periodo',
    noNavBody: 'No hay filas de NAV para el periodo seleccionado. Si la base de datos está recién creada, ejecuta el bootstrap o un pull del pipeline.',
    hiddenTitle: 'TWR y el chart Cartera vs S&P 500 están ocultos para este periodo.',
    action: 'Acción:',
    actionBody: 'cambia el filtro de período arriba a YTD o 1Y para usar una zona con NAV diaria densa.',
    loading: 'cargando...',
    hidden: 'oculto (no fiable)',
    isolatesFlows: 'time-weighted · aísla aportes',
    insufficient: 'sin datos suficientes',
    portfolioVsSp: 'Cartera vs S&P 500',
    portfolio: 'Cartera',
    unavailableVs: 'Cartera vs S&P 500 no disponible',
    riskUnavailable: 'Métricas de riesgo no disponibles para este periodo',
    navCoverage: 'Cobertura NAV {pct}% (necesita >= 50 %).',
    maxGap: 'Gap máximo entre snapshots: {days} días.',
    moneyWeightedFlows: 'money-weighted · {count} flujos',
    interpolatedSnapshots: 'interpolado (snapshots)',
    loadingComparison: 'Cargando comparativa...',
    portfolioPeriod: 'Cartera ({currency}) periodo',
    spPeriod: 'S&P 500 ({currency}) periodo',
    excessVsSp: 'Cartera vs S&P 500 (exceso)',
    beatsIndex: 'cartera bate al índice',
    belowIndex: 'cartera por debajo',
    riskNeeds: 'Necesita >= 10 días de TWR diario (sección Flex ChangeInNAV); tienes {days}.',
    riskMetrics: 'Métricas de riesgo',
    riskSubtitle: '{days} días desde {from} hasta {to} · risk-free {rate}% · fuente: Flex ChangeInNAV.twr',
    truncatedPrefix: 'Truncado:',
    riskTruncated: 'tu filtro pide {requestedFrom} -> {requestedTo}, pero el TWR diario solo existe desde {from}. Las métricas abajo cubren solo {days} días.',
    dailyStd: 'std diaria x sqrt(252)',
    sharpeDelta: '(ret - rf) / vol · > 1 bueno',
    sortinoDelta: 'penaliza solo downside',
    annualReturnDaily: 'Annual return (de daily)',
    alphaUnreliable: 'Alpha/Beta no fiable:',
    alignedSubtitle: '{days} días alineados ({from} -> {to}) · S&P 500 en {currency}',
    alphaTruncated: 'tu filtro pide {requestedFrom} -> {requestedTo}, pero el comparativo solo usa {days} días.',
    portfolioReturnPeriod: 'Retorno cartera (periodo)',
    spReturn: 'Retorno S&P 500',
    marketSensitivity: 'sensibilidad al mercado',
    adjustedByBeta: 'ajustado por beta',
    trackingDays: '{days} días',
    trackingDelta: 'desviación de retornos vs índice',
    informationDelta: 'exceso de retorno / tracking error',
    benchmarkAlignedNote: 'Cartera y S&P comparten exactamente las mismas fechas: ningún tramo dibuja una curva suave contra otra dentada. Donde el NAV es disperso (pre-2023) ambos son rectas entre snapshots; el segmento diario fiable empieza el 2023-11-28.',
  },
  en: {
    intro: 'TWR (time-weighted) measures portfolio skill while neutralizing deposits and withdrawals. XIRR (money-weighted) answers what annual rate your money is generating and weights cash flows by size and timing.',
    currencyLabel: 'TWR currency:',
    unavailableTitle: 'Performance unavailable',
    unavailableBody: 'NAV data is required to calculate TWR.',
    noNavTitle: 'No NAV data in this period',
    noNavBody: 'There are no NAV rows for the selected period. If the database is new, run bootstrap or a pipeline pull.',
    hiddenTitle: 'TWR and the Portfolio vs S&P 500 chart are hidden for this period.',
    action: 'Action:',
    actionBody: 'change the period filter above to YTD or 1Y to use a dense daily NAV zone.',
    loading: 'loading...',
    hidden: 'hidden (unreliable)',
    isolatesFlows: 'time-weighted · isolates cash flows',
    insufficient: 'not enough data',
    portfolioVsSp: 'Portfolio vs S&P 500',
    portfolio: 'Portfolio',
    unavailableVs: 'Portfolio vs S&P 500 unavailable',
    riskUnavailable: 'Risk metrics unavailable for this period',
    navCoverage: 'NAV coverage {pct}% (needs >= 50%).',
    maxGap: 'Maximum gap between snapshots: {days} days.',
    moneyWeightedFlows: 'money-weighted · {count} flows',
    interpolatedSnapshots: 'interpolated (snapshots)',
    loadingComparison: 'Loading comparison...',
    portfolioPeriod: 'Portfolio ({currency}) period',
    spPeriod: 'S&P 500 ({currency}) period',
    excessVsSp: 'Portfolio vs S&P 500 (excess)',
    beatsIndex: 'portfolio beats the index',
    belowIndex: 'portfolio below the index',
    riskNeeds: 'Needs >= 10 days of daily TWR (Flex section ChangeInNAV); you have {days}.',
    riskMetrics: 'Risk metrics',
    riskSubtitle: '{days} days from {from} to {to} · risk-free {rate}% · source: Flex ChangeInNAV.twr',
    truncatedPrefix: 'Truncated:',
    riskTruncated: 'your filter asks for {requestedFrom} -> {requestedTo}, but daily TWR only exists from {from}. The metrics below cover only {days} days.',
    dailyStd: 'daily std x sqrt(252)',
    sharpeDelta: '(ret - rf) / vol · > 1 good',
    sortinoDelta: 'penalizes downside only',
    annualReturnDaily: 'Annual return (from daily)',
    alphaUnreliable: 'Alpha/Beta unreliable:',
    alignedSubtitle: '{days} aligned days ({from} -> {to}) · S&P 500 in {currency}',
    alphaTruncated: 'your filter asks for {requestedFrom} -> {requestedTo}, but the comparison only uses {days} days.',
    portfolioReturnPeriod: 'Portfolio return (period)',
    spReturn: 'S&P 500 return',
    marketSensitivity: 'market sensitivity',
    adjustedByBeta: 'adjusted for beta',
    trackingDays: '{days} days',
    trackingDelta: 'return deviation vs index',
    informationDelta: 'excess return / tracking error',
    benchmarkAlignedNote: 'Portfolio and S&P use exactly the same dates: no segment draws a smooth curve against a jagged one. Where NAV is sparse (pre-2023), both are straight lines between snapshots; the reliable daily segment starts on 2023-11-28.',
  },
  de: {
    intro: 'TWR (time-weighted) misst Portfolio-Skill bereinigt um Ein- und Auszahlungen. XIRR (money-weighted) beantwortet, welche Jahresrendite dein Geld erzielt, gewichtet nach Groesse und Timing der Cashflows.',
    currencyLabel: 'TWR-Waehrung:',
    unavailableTitle: 'Performance nicht verfuegbar',
    unavailableBody: 'NAV-Daten sind erforderlich, um TWR zu berechnen.',
    noNavTitle: 'Keine NAV-Daten in diesem Zeitraum',
    noNavBody: 'Fuer den ausgewaehlten Zeitraum gibt es keine NAV-Zeilen. Bei neuer Datenbank Bootstrap oder Pipeline-Pull ausfuehren.',
    hiddenTitle: 'TWR und Portfolio vs S&P 500 sind fuer diesen Zeitraum ausgeblendet.',
    action: 'Aktion:',
    actionBody: 'stelle den Zeitraumfilter oben auf YTD oder 1Y, um dichte taegliche NAV-Daten zu verwenden.',
    loading: 'laden...',
    hidden: 'ausgeblendet (unzuverlaessig)',
    isolatesFlows: 'time-weighted · isoliert Cashflows',
    insufficient: 'zu wenig Daten',
    portfolioVsSp: 'Portfolio vs S&P 500',
    portfolio: 'Portfolio',
    unavailableVs: 'Portfolio vs S&P 500 nicht verfuegbar',
    riskUnavailable: 'Risikometriken fuer diesen Zeitraum nicht verfuegbar',
    navCoverage: 'NAV-Abdeckung {pct}% (braucht >= 50%).',
    maxGap: 'Maximaler Gap zwischen Snapshots: {days} Tage.',
    moneyWeightedFlows: 'money-weighted · {count} Cashflows',
    interpolatedSnapshots: 'interpoliert (Snapshots)',
    loadingComparison: 'Vergleich wird geladen...',
    portfolioPeriod: 'Portfolio ({currency}) Zeitraum',
    spPeriod: 'S&P 500 ({currency}) Zeitraum',
    excessVsSp: 'Portfolio vs S&P 500 (Excess)',
    beatsIndex: 'Portfolio schlaegt den Index',
    belowIndex: 'Portfolio unter dem Index',
    riskNeeds: 'Braucht >= 10 Tage taeglichen TWR (Flex-Sektion ChangeInNAV); vorhanden: {days}.',
    riskMetrics: 'Risikometriken',
    riskSubtitle: '{days} Tage von {from} bis {to} · risk-free {rate}% · Quelle: Flex ChangeInNAV.twr',
    truncatedPrefix: 'Trunkiert:',
    riskTruncated: 'dein Filter verlangt {requestedFrom} -> {requestedTo}, aber taeglicher TWR existiert erst ab {from}. Die Metriken unten decken nur {days} Tage ab.',
    dailyStd: 'taegliche Std x sqrt(252)',
    sharpeDelta: '(ret - rf) / vol · > 1 gut',
    sortinoDelta: 'bestraft nur Downside',
    annualReturnDaily: 'Annual return (aus daily)',
    alphaUnreliable: 'Alpha/Beta unzuverlaessig:',
    alignedSubtitle: '{days} ausgerichtete Tage ({from} -> {to}) · S&P 500 in {currency}',
    alphaTruncated: 'dein Filter verlangt {requestedFrom} -> {requestedTo}, aber der Vergleich nutzt nur {days} Tage.',
    portfolioReturnPeriod: 'Portfolio-Return (Zeitraum)',
    spReturn: 'S&P 500 Return',
    marketSensitivity: 'Marktsensitivitaet',
    adjustedByBeta: 'beta-adjustiert',
    trackingDays: '{days} Tage',
    trackingDelta: 'Return-Abweichung vs Index',
    informationDelta: 'Excess Return / Tracking Error',
    benchmarkAlignedNote: 'Portfolio und S&P verwenden exakt dieselben Daten: kein Abschnitt zeichnet eine glatte Kurve gegen eine gezackte. Wo der NAV duenn ist (vor 2023), sind beide Linien zwischen Snapshots gerade; das verlaessliche taegliche Segment beginnt am 2023-11-28.',
  },
} as const;

export function RentabilidadPage({ filters, currency }: Props) {
  const { language, locale } = useI18n();
  const copy = COPY[language];
  const [twrCurrency, setTwrCurrency] = useState<TwrCurrency>('CHF');

  const twr = useQuery({
    queryKey: ['performance.twr', filters, twrCurrency],
    queryFn: () => api.performanceTwr(filters, twrCurrency === 'CHF' ? undefined : twrCurrency),
  });
  const yearTwrChf = useQuery({
    queryKey: ['performance.twr', YEAR_RETURN_FILTER, 'CHF'],
    queryFn: () => api.performanceTwr(YEAR_RETURN_FILTER),
  });
  const yearTwrEur = useQuery({
    queryKey: ['performance.twr', YEAR_RETURN_FILTER, 'EUR'],
    queryFn: () => api.performanceTwr(YEAR_RETURN_FILTER, 'EUR'),
  });
  const yearTwrUsd = useQuery({
    queryKey: ['performance.twr', YEAR_RETURN_FILTER, 'USD'],
    queryFn: () => api.performanceTwr(YEAR_RETURN_FILTER, 'USD'),
  });
  const benchmarkSp500 = useQuery({
    queryKey: ['performance.benchmark.sp500', filters, twrCurrency],
    queryFn: () => api.benchmark(filters, twrCurrency),
  });
  const riskQ = useQuery({
    queryKey: ['performance.risk', filters],
    queryFn: () => api.performanceRisk(filters),
  });
  const alphaQ = useQuery({
    queryKey: ['performance.alpha', filters, twrCurrency],
    queryFn: () => api.performanceAlpha(filters, twrCurrency),
  });
  const vsb = useQuery({
    queryKey: ['performance.vsBenchmark', filters, twrCurrency],
    queryFn: () => api.performanceVsBenchmark(filters, twrCurrency),
  });
  const xirrQ = useQuery({
    queryKey: ['performance.xirr', filters],
    queryFn: () => api.performanceXirr(filters),
  });

  const twrPct = twr.data?.twr_pct as number | undefined;
  const annualisedPct = twr.data?.annualised_pct as number | undefined;
  const localizeBenchmarkNote = (note: string | undefined | null): string => {
    if (!note) return '';
    if (note.includes('Cartera y S&P comparten exactamente las mismas fechas')) {
      return copy.benchmarkAlignedNote;
    }
    return note;
  };

  return (
    <>
      <div className="info-box">
        {copy.intro}
      </div>
      <div className="kpi-grid">
        <KPICard label="TWR 1Y (CHF)" value={formatPercent(yearTwrChf.data?.twr_pct, true, locale)}
          delta={formatPercent(yearTwrChf.data?.annualised_pct, true, locale)}
          deltaKind={(yearTwrChf.data?.twr_pct ?? 0) >= 0 ? 'pos' : 'neg'} loading={yearTwrChf.isLoading} />
        <KPICard label="TWR 1Y (EUR)" value={formatPercent(yearTwrEur.data?.twr_pct, true, locale)}
          delta={formatPercent(yearTwrEur.data?.annualised_pct, true, locale)}
          deltaKind={(yearTwrEur.data?.twr_pct ?? 0) >= 0 ? 'pos' : 'neg'} loading={yearTwrEur.isLoading} />
        <KPICard label="TWR 1Y (USD)" value={formatPercent(yearTwrUsd.data?.twr_pct, true, locale)}
          delta={formatPercent(yearTwrUsd.data?.annualised_pct, true, locale)}
          deltaKind={(yearTwrUsd.data?.twr_pct ?? 0) >= 0 ? 'pos' : 'neg'} loading={yearTwrUsd.isLoading} />
      </div>

      <div className="twr-switcher">
        <span className="twr-switcher-label">{copy.currencyLabel}</span>
        {TWR_CURRENCIES.map((c) => (
          <button key={c} className={twrCurrency === c ? 'active' : ''} onClick={() => setTwrCurrency(c)}>{c}</button>
        ))}
      </div>

      {twr.isError ? (
        <MissingData title={copy.unavailableTitle}>{copy.unavailableBody}</MissingData>
      ) : twr.data && (twr.data.series ?? []).length === 0 && twr.data.quality == null ? (
        // Backend OK pero sin filas NavDaily en el rango (p. ej. primer arranque con DB vacía).
        <MissingData title={copy.noNavTitle}>
          {copy.noNavBody}
        </MissingData>
      ) : (() => {
        const quality = twr.data?.quality as { is_reliable?: boolean; reason?: string; coverage_ratio?: number; max_gap_days?: number } | undefined;
        const warmupSkipped = (twr.data as { warmup_skipped?: number } | undefined)?.warmup_skipped ?? 0;
        const reliable = quality?.is_reliable !== false;
        return (
          <>
            {!reliable && (
              <div className="info-box" style={{ background: 'rgba(245,158,11,0.08)', borderColor: 'var(--border-warn)' }}>
                <strong>{copy.hiddenTitle}</strong>{' '}
                {quality?.reason ?? ''}
                {quality?.coverage_ratio !== undefined && ` ${copy.navCoverage.replace('{pct}', (quality.coverage_ratio * 100).toFixed(0))}`}
                {quality?.max_gap_days !== undefined && quality.max_gap_days > 10 && ` ${copy.maxGap.replace('{days}', String(quality.max_gap_days))}`}
                {warmupSkipped > 0 && ` (${warmupSkipped} filas warm-up descartadas por NAV inicial muy bajo.)`}
                <br />
                <strong>{copy.action}</strong> {copy.actionBody}
              </div>
            )}

            <div className="kpi-grid">
              <KPICard label={`TWR (${twrCurrency})`} value={formatPercent(twrPct, true, locale)}
                delta={twr.isFetching ? copy.loading : (twrPct == null ? copy.hidden : copy.isolatesFlows)}
                deltaKind={twrPct != null ? (twrPct >= 0 ? 'pos' : 'neg') : 'neutral'} loading={twr.isLoading} />
              <KPICard label={`TWR Annualized (${twrCurrency})`} value={formatPercent(annualisedPct, true, locale)}
                delta={annualisedPct == null ? copy.hidden : undefined}
                deltaKind={annualisedPct != null ? (annualisedPct >= 0 ? 'pos' : 'neg') : 'neutral'} loading={twr.isLoading} />
              {(() => {
                const xirrPct = xirrQ.data?.xirr_pct;
                const flowCount = xirrQ.data?.flow_count;
                const xirrNote = xirrQ.data?.note;
                return (
                  <KPICard
                    label={`XIRR (${currency})`}
                    value={xirrPct != null ? `${xirrPct >= 0 ? '+' : ''}${xirrPct.toFixed(2)}%` : '—'}
                    delta={xirrPct != null
                      ? `${copy.moneyWeightedFlows.replace('{count}', String(flowCount ?? 0))}${xirrNote ? ' · ⚠ ' + xirrNote : ''}`
                      : (xirrQ.data?.error ?? copy.insufficient)}
                    deltaKind={xirrPct != null ? (xirrPct >= 0 ? 'pos' : 'neg') : 'neutral'}
                    loading={xirrQ.isLoading}
                  />
                );
              })()}
              {(() => {
                const last = benchmarkSp500.data?.rows?.[benchmarkSp500.data.rows.length - 1];
                const delta = (last?.return_pct != null && twrPct != null)
                  ? `${formatPercent(twrPct - last.return_pct, true, locale)} vs SP500`
                  : undefined;
                return (
                  <KPICard
                    label={`SP500 (${twrCurrency})`}
                    value={formatPercent(last?.return_pct, true, locale)}
                    delta={delta}
                    deltaKind={last?.return_pct !== undefined ? (last.return_pct >= 0 ? 'pos' : 'neg') : 'neutral'}
                    loading={benchmarkSp500.isLoading}
                  />
                );
              })()}
            </div>

            {(() => {
              if (vsb.isError) {
                return (
                  <div className="info-box" style={{ marginTop: 18 }}>
                    {copy.unavailableVs}: error.
                  </div>
                );
              }
              if (!vsb.data) return null;
              if (!vsb.data.is_reliable) {
                return (
                  <div className="info-box" style={{ marginTop: 18, background: 'rgba(245,158,11,0.08)', borderColor: 'var(--border-warn)' }}>
                    <strong>{copy.unavailableVs}.</strong> {vsb.data.reason}
                  </div>
                );
              }
              const vsbPoints = vsb.data.points ?? [];
              if (vsbPoints.length === 0) return null;
              const res = vsb.data.resolution;
              return (
                <div className="chart-panel" style={{ marginTop: 18 }}>
                  <div className="panel-header">
                    <div className="panel-title">{copy.portfolioVsSp} ({twrCurrency})</div>
                    <div className="chart-legend">
                      <span><i className="legend-dot capital" style={{ background: '#a855f7' }} />{copy.portfolio}</span>
                      <span><i className="legend-dot capital" style={{ background: '#22d3ee' }} />S&P 500</span>
                    </div>
                  </div>
                  {res?.note && (
                    <div className="info-box" style={{ marginTop: 8, marginBottom: 8, fontSize: 12 }}>
                      {localizeBenchmarkNote(res.note)}
                      {res.is_sparse && (
                        <span style={{ marginLeft: 8, padding: '1px 6px', background: 'rgba(245,158,11,0.15)', borderRadius: 4, color: '#f59e0b', fontSize: 11, fontWeight: 600 }}>
                          {copy.interpolatedSnapshots}
                        </span>
                      )}
                    </div>
                  )}
                  <EquityChart
                    points={vsbPoints}
                    portfolioLabel={`${copy.portfolio} (${twrCurrency})`}
                    benchmarkLabel="S&P 500"
                    locale={locale}
                    height={340}
                  />
                  {vsb.isLoading && (<div className="info-box" style={{ marginTop: 8 }}>{copy.loadingComparison}</div>)}
                </div>
              );
            })()}

            {vsb.data?.is_reliable && vsb.data.level_comparison && (() => {
              const lc = vsb.data.level_comparison;
              return (
                <div className="kpi-grid" style={{ marginTop: 18 }}>
                  <KPICard
                    label={copy.portfolioPeriod.replace('{currency}', twrCurrency)}
                    value={`${lc.portfolio_return_pct >= 0 ? '+' : ''}${lc.portfolio_return_pct.toFixed(2)}%`}
                    deltaKind={lc.portfolio_return_pct >= 0 ? 'pos' : 'neg'}
                  />
                  <KPICard
                    label={copy.spPeriod.replace('{currency}', twrCurrency)}
                    value={`${lc.benchmark_return_pct >= 0 ? '+' : ''}${lc.benchmark_return_pct.toFixed(2)}%`}
                    deltaKind={lc.benchmark_return_pct >= 0 ? 'pos' : 'neg'}
                  />
                  <KPICard
                    label={copy.excessVsSp}
                    value={`${lc.excess_return_pct >= 0 ? '+' : ''}${lc.excess_return_pct.toFixed(2)}%`}
                    delta={lc.excess_return_pct >= 0 ? copy.beatsIndex : copy.belowIndex}
                    deltaKind={lc.excess_return_pct >= 0 ? 'pos' : 'neg'}
                  />
                </div>
              );
            })()}

            {(() => {
              const rm = riskQ.data;
              if (!rm) return null;
              if (!rm.is_reliable) {
                return (
                  <div className="info-box" style={{ marginTop: 18 }}>
                    {copy.riskUnavailable}: {rm.reason}
                    <br />
                    {copy.riskNeeds.replace('{days}', String(rm.n_days))}
                  </div>
                );
              }
              return (
                <>
                  <div className="panel-title" style={{ marginTop: 18 }}>
                    {copy.riskMetrics}
                    <small style={{ display: 'block', fontWeight: 400, color: '#8b88a6', fontSize: 12 }}>
                      {copy.riskSubtitle
                        .replace('{days}', String(rm.n_days))
                        .replace('{from}', String(rm.from_date ?? ''))
                        .replace('{to}', String(rm.to_date ?? ''))
                        .replace('{rate}', String(rm.risk_free_rate_pct))}
                    </small>
                  </div>
                  {rm.is_truncated && (
                    <div className="info-box" style={{ background: 'rgba(245,158,11,0.10)', borderColor: 'var(--border-warn)', marginTop: 8 }}>
                      <strong>⚠ {copy.truncatedPrefix}</strong>{' '}
                      {copy.riskTruncated
                        .replace('{requestedFrom}', String(rm.requested_from ?? ''))
                        .replace('{requestedTo}', String(rm.requested_to ?? ''))
                        .replace('{from}', String(rm.from_date ?? ''))
                        .replace('{days}', String(rm.n_days))}{' '}
                      {rm.truncation_reason}
                    </div>
                  )}
                  <div className="kpi-grid">
                    <KPICard label="Volatility (annualised)"
                      value={rm.annualised_volatility_pct !== undefined ? `${rm.annualised_volatility_pct.toFixed(2)}%` : '—'}
                      delta={copy.dailyStd} deltaKind="neutral" loading={riskQ.isLoading} />
                    <KPICard label="Sharpe Ratio"
                      value={rm.sharpe_ratio !== undefined && rm.sharpe_ratio !== null ? rm.sharpe_ratio.toFixed(2) : '—'}
                      delta={copy.sharpeDelta}
                      deltaKind={(rm.sharpe_ratio ?? 0) >= 1 ? 'pos' : (rm.sharpe_ratio ?? 0) >= 0 ? 'neutral' : 'neg'} loading={riskQ.isLoading} />
                    <KPICard label="Sortino Ratio"
                      value={rm.sortino_ratio !== undefined && rm.sortino_ratio !== null ? rm.sortino_ratio.toFixed(2) : '—'}
                      delta={copy.sortinoDelta}
                      deltaKind={(rm.sortino_ratio ?? 0) >= 1 ? 'pos' : (rm.sortino_ratio ?? 0) >= 0 ? 'neutral' : 'neg'} loading={riskQ.isLoading} />
                    <KPICard label="Max Drawdown"
                      value={rm.max_drawdown_pct !== undefined ? `${rm.max_drawdown_pct.toFixed(2)}%` : '—'}
                      delta={rm.max_drawdown_peak_date && rm.max_drawdown_trough_date
                        ? `${rm.max_drawdown_peak_date} → ${rm.max_drawdown_trough_date}`
                        : undefined}
                      deltaKind="neg" loading={riskQ.isLoading} />
                    <KPICard label="Calmar Ratio"
                      value={rm.calmar_ratio !== undefined && rm.calmar_ratio !== null ? rm.calmar_ratio.toFixed(2) : '—'}
                      delta="annual return / |max DD|"
                      deltaKind={(rm.calmar_ratio ?? 0) >= 1 ? 'pos' : 'neutral'} loading={riskQ.isLoading} />
                    <KPICard label={copy.annualReturnDaily}
                      value={rm.annualised_return_pct !== undefined ? `${rm.annualised_return_pct.toFixed(2)}%` : '—'}
                      delta={`${rm.positive_days}↑ ${rm.negative_days}↓`}
                      deltaKind={(rm.annualised_return_pct ?? 0) >= 0 ? 'pos' : 'neg'} loading={riskQ.isLoading} />
                  </div>

                  {alphaQ.data && (() => {
                    const a = alphaQ.data;
                    if (!a.is_reliable) {
                      return <div className="info-box" style={{ marginTop: 8 }}>{copy.alphaUnreliable} {a.reason}</div>;
                    }
                    return (
                      <>
                        <div className="panel-title" style={{ marginTop: 18 }}>
                          Alpha · Beta vs S&P 500
                          <small style={{ display: 'block', fontWeight: 400, color: '#8b88a6', fontSize: 12 }}>
                            {copy.alignedSubtitle
                              .replace('{days}', String(a.aligned_days))
                              .replace('{from}', String(a.from_date ?? ''))
                              .replace('{to}', String(a.to_date ?? ''))
                              .replace('{currency}', String(a.benchmark_currency ?? ''))}
                          </small>
                        </div>
                        {a.is_truncated && (
                          <div className="info-box" style={{ background: 'rgba(245,158,11,0.10)', borderColor: 'var(--border-warn)', marginTop: 8 }}>
                            <strong>⚠ {copy.truncatedPrefix}</strong>{' '}
                            {copy.alphaTruncated
                              .replace('{requestedFrom}', String(a.requested_from ?? ''))
                              .replace('{requestedTo}', String(a.requested_to ?? ''))
                              .replace('{days}', String(a.aligned_days))}{' '}
                            {a.truncation_reason}
                          </div>
                        )}
                        <div className="kpi-grid">
                          <KPICard label={copy.portfolioReturnPeriod}
                            value={a.portfolio_total_return_pct !== undefined ? `${a.portfolio_total_return_pct.toFixed(2)}%` : '—'}
                            deltaKind={(a.portfolio_total_return_pct ?? 0) >= 0 ? 'pos' : 'neg'} />
                          <KPICard label={copy.spReturn}
                            value={a.benchmark_total_return_pct !== undefined ? `${a.benchmark_total_return_pct.toFixed(2)}%` : '—'}
                            deltaKind={(a.benchmark_total_return_pct ?? 0) >= 0 ? 'pos' : 'neg'} />
                          <KPICard label="Alpha simple (diff)"
                            value={a.simple_alpha_pct !== undefined ? `${a.simple_alpha_pct >= 0 ? '+' : ''}${a.simple_alpha_pct.toFixed(2)}%` : '—'}
                            delta={a.simple_alpha_pct !== undefined && a.simple_alpha_pct >= 0 ? copy.beatsIndex : copy.belowIndex}
                            deltaKind={(a.simple_alpha_pct ?? 0) >= 0 ? 'pos' : 'neg'} />
                          <KPICard label="Beta"
                            value={a.beta !== undefined && a.beta !== null ? a.beta.toFixed(2) : '—'}
                            delta={copy.marketSensitivity}
                            deltaKind={a.beta !== undefined && a.beta !== null && a.beta > 1.2 ? 'warn' : 'neutral'} />
                          <KPICard label="Alpha Jensen"
                            value={a.jensen_alpha_pct !== undefined && a.jensen_alpha_pct !== null ? `${a.jensen_alpha_pct >= 0 ? '+' : ''}${a.jensen_alpha_pct.toFixed(2)}%` : '—'}
                            delta={copy.adjustedByBeta}
                            deltaKind={(a.jensen_alpha_pct ?? 0) >= 0 ? 'pos' : 'neg'} />
                          {vsb.data?.risk_comparison && (() => {
                            const rc = vsb.data!.risk_comparison;
                            return (
                              <>
                                <KPICard
                                  label="Tracking Error"
                                  value={rc.tracking_error_pct != null ? `${rc.tracking_error_pct.toFixed(2)}%` : '—'}
                                  delta={rc.te_days != null ? copy.trackingDays.replace('{days}', String(rc.te_days)) : copy.trackingDelta}
                                  deltaKind="neutral"
                                />
                                <KPICard
                                  label="Information Ratio"
                                  value={rc.information_ratio != null ? rc.information_ratio.toFixed(2) : '—'}
                                  delta={copy.informationDelta}
                                  deltaKind={rc.information_ratio != null ? (rc.information_ratio >= 0.5 ? 'pos' : rc.information_ratio >= 0 ? 'neutral' : 'neg') : 'neutral'}
                                />
                              </>
                            );
                          })()}
                        </div>
                      </>
                    );
                  })()}
                </>
              );
            })()}
          </>
        );
      })()}
    </>
  );
}
