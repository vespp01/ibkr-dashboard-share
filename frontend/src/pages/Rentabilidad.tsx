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

interface Props {
  filters: PeriodFilters;
  currency: string;
}

const COPY = {
  es: {
    intro: 'TWR (time-weighted) encadena el TWR diario de IBKR (ChangeInNAV) y neutraliza los aportes. XIRR (money-weighted) responde a qué tasa anual genera tu dinero y pondera por tamaño y fecha de aportes. Nada se anualiza por debajo de un año.',
    currencyLabel: 'Divisa TWR:',
    unavailableTitle: 'Rentabilidad no disponible',
    unavailableBody: 'Necesita datos de NAV para calcular TWR.',
    noNavTitle: 'Sin datos de NAV en este periodo',
    noNavBody: 'No hay filas de NAV para el periodo seleccionado. Si la base de datos está recién creada, ejecuta el bootstrap o un pull del pipeline.',
    hiddenTitle: 'TWR y el chart Cartera vs S&P 500 están ocultos para este periodo.',
    underOneYear: 'periodo < 1 año: no se anualiza',
    fxFallback: 'Tipo de cambio de respaldo (Frankfurter): sin serie fx_rates de IBKR para esta divisa.',
    fxMissing: 'Sin tipo de cambio para esta divisa: cifras convertidas no disponibles.',
    spPeriodDelta: '{excess} exceso vs S&P 500',
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
    loadingComparison: 'Cargando comparativa...',
    beatsIndex: 'cartera bate al índice',
    belowIndex: 'cartera por debajo',
    riskNeeds: 'Necesita >= 10 días de TWR diario (sección Flex ChangeInNAV); tienes {days}.',
    riskMetrics: 'Métricas de riesgo',
    riskSubtitle: '{days} días desde {from} hasta {to} · {ccy} · risk-free {rate}% ({rfSource}) · fuente: Flex ChangeInNAV.twr',
    riskShortWindow: 'Periodo < 1 año: retorno anualizado y Calmar no se muestran; Sharpe y Sortino usan la media aritmética diaria.',
    truncatedPrefix: 'Truncado:',
    riskTruncated: 'tu filtro pide {requestedFrom} -> {requestedTo}, pero el TWR diario solo existe desde {from}. Las métricas abajo cubren solo {days} días.',
    dailyStd: 'std diaria x sqrt(obs/año)',
    sharpeDelta: '(ret - rf) / vol · > 1 bueno',
    sortinoDelta: 'penaliza solo downside',
    annualReturnDaily: 'Annual return (de daily)',
    alphaUnreliable: 'Alpha/Beta no fiable:',
    alignedSubtitle: '{days} días alineados ({from} -> {to}) · S&P 500 (índice de precio) en {currency}',
    alphaTruncated: 'tu filtro pide {requestedFrom} -> {requestedTo}, pero el comparativo solo usa {days} días.',
    marketSensitivity: 'sensibilidad al mercado',
    adjustedByBeta: 'ajustado por beta',
    trackingDays: '{days} días',
    trackingDelta: 'desviación de retornos vs índice',
    informationDelta: 'exceso de retorno / tracking error',
    benchmarkAlignedNote: 'Cartera y S&P 500 (índice de precio, sin dividendos) se comparan en las mismas fechas y se rebasan a 100 el día de NAV anterior al periodo. Los días sin cotización de uno de los dos se componen en la siguiente fecha común.',
  },
  en: {
    intro: 'TWR (time-weighted) chains IBKR\'s own daily ChangeInNAV TWR and neutralizes deposits and withdrawals. XIRR (money-weighted) answers what annual rate your money is generating and weights cash flows by size and date. Nothing is annualised below one year.',
    currencyLabel: 'TWR currency:',
    unavailableTitle: 'Performance unavailable',
    unavailableBody: 'NAV data is required to calculate TWR.',
    noNavTitle: 'No NAV data in this period',
    noNavBody: 'There are no NAV rows for the selected period. If the database is new, run bootstrap or a pipeline pull.',
    hiddenTitle: 'TWR and the Portfolio vs S&P 500 chart are hidden for this period.',
    underOneYear: 'period < 1 year: not annualised',
    fxFallback: 'Fallback exchange rate (Frankfurter): no IBKR fx_rates series for this currency.',
    fxMissing: 'No exchange rate for this currency: converted figures are unavailable.',
    spPeriodDelta: '{excess} excess vs S&P 500',
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
    loadingComparison: 'Loading comparison...',
    beatsIndex: 'portfolio beats the index',
    belowIndex: 'portfolio below the index',
    riskNeeds: 'Needs >= 10 days of daily TWR (Flex section ChangeInNAV); you have {days}.',
    riskMetrics: 'Risk metrics',
    riskSubtitle: '{days} days from {from} to {to} · {ccy} · risk-free {rate}% ({rfSource}) · source: Flex ChangeInNAV.twr',
    riskShortWindow: 'Period < 1 year: annualised return and Calmar are not shown; Sharpe and Sortino use the arithmetic daily mean.',
    truncatedPrefix: 'Truncated:',
    riskTruncated: 'your filter asks for {requestedFrom} -> {requestedTo}, but daily TWR only exists from {from}. The metrics below cover only {days} days.',
    dailyStd: 'daily std x sqrt(obs/year)',
    sharpeDelta: '(ret - rf) / vol · > 1 good',
    sortinoDelta: 'penalizes downside only',
    annualReturnDaily: 'Annual return (from daily)',
    alphaUnreliable: 'Alpha/Beta unreliable:',
    alignedSubtitle: '{days} aligned days ({from} -> {to}) · S&P 500 (price index) in {currency}',
    alphaTruncated: 'your filter asks for {requestedFrom} -> {requestedTo}, but the comparison only uses {days} days.',
    marketSensitivity: 'market sensitivity',
    adjustedByBeta: 'adjusted for beta',
    trackingDays: '{days} days',
    trackingDelta: 'return deviation vs index',
    informationDelta: 'excess return / tracking error',
    benchmarkAlignedNote: 'Portfolio and S&P 500 (price index, no dividends) are compared on the same dates and rebased to 100 on the NAV day before the period. Days when one of them has no quote are compounded into the next common date.',
  },
  de: {
    intro: 'TWR (time-weighted) verkettet den taeglichen ChangeInNAV-TWR von IBKR und bereinigt Ein- und Auszahlungen. XIRR (money-weighted) beantwortet, welche Jahresrendite dein Geld erzielt, gewichtet nach Groesse und Datum der Cashflows. Unter einem Jahr wird nichts annualisiert.',
    currencyLabel: 'TWR-Waehrung:',
    unavailableTitle: 'Performance nicht verfuegbar',
    unavailableBody: 'NAV-Daten sind erforderlich, um TWR zu berechnen.',
    noNavTitle: 'Keine NAV-Daten in diesem Zeitraum',
    noNavBody: 'Fuer den ausgewaehlten Zeitraum gibt es keine NAV-Zeilen. Bei neuer Datenbank Bootstrap oder Pipeline-Pull ausfuehren.',
    hiddenTitle: 'TWR und Portfolio vs S&P 500 sind fuer diesen Zeitraum ausgeblendet.',
    underOneYear: 'Zeitraum < 1 Jahr: nicht annualisiert',
    fxFallback: 'Ersatz-Wechselkurs (Frankfurter): keine IBKR-fx_rates-Reihe fuer diese Waehrung.',
    fxMissing: 'Kein Wechselkurs fuer diese Waehrung: umgerechnete Zahlen nicht verfuegbar.',
    spPeriodDelta: '{excess} Excess vs S&P 500',
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
    loadingComparison: 'Vergleich wird geladen...',
    beatsIndex: 'Portfolio schlaegt den Index',
    belowIndex: 'Portfolio unter dem Index',
    riskNeeds: 'Braucht >= 10 Tage taeglichen TWR (Flex-Sektion ChangeInNAV); vorhanden: {days}.',
    riskMetrics: 'Risikometriken',
    riskSubtitle: '{days} Tage von {from} bis {to} · {ccy} · risk-free {rate}% ({rfSource}) · Quelle: Flex ChangeInNAV.twr',
    riskShortWindow: 'Zeitraum < 1 Jahr: annualisierte Rendite und Calmar werden nicht angezeigt; Sharpe und Sortino nutzen den arithmetischen Tagesmittelwert.',
    truncatedPrefix: 'Trunkiert:',
    riskTruncated: 'dein Filter verlangt {requestedFrom} -> {requestedTo}, aber taeglicher TWR existiert erst ab {from}. Die Metriken unten decken nur {days} Tage ab.',
    dailyStd: 'taegliche Std x sqrt(Beob./Jahr)',
    sharpeDelta: '(ret - rf) / vol · > 1 gut',
    sortinoDelta: 'bestraft nur Downside',
    annualReturnDaily: 'Annual return (aus daily)',
    alphaUnreliable: 'Alpha/Beta unzuverlaessig:',
    alignedSubtitle: '{days} ausgerichtete Tage ({from} -> {to}) · S&P 500 (Preisindex) in {currency}',
    alphaTruncated: 'dein Filter verlangt {requestedFrom} -> {requestedTo}, aber der Vergleich nutzt nur {days} Tage.',
    marketSensitivity: 'Marktsensitivitaet',
    adjustedByBeta: 'beta-adjustiert',
    trackingDays: '{days} Tage',
    trackingDelta: 'Return-Abweichung vs Index',
    informationDelta: 'Excess Return / Tracking Error',
    benchmarkAlignedNote: 'Portfolio und S&P 500 (Preisindex, ohne Dividenden) werden an denselben Daten verglichen und am NAV-Tag vor dem Zeitraum auf 100 normiert. Tage ohne Kurs eines der beiden werden ins naechste gemeinsame Datum aufgezinst.',
  },
} as const;

export function RentabilidadPage({ filters, currency }: Props) {
  const { language, locale } = useI18n();
  const copy = COPY[language];
  const [twrCurrency, setTwrCurrency] = useState<TwrCurrency>('CHF');

  // One summary call: every figure on the page (TWR, S&P, excess, alpha) derives
  // from the same ChangeInNAV series and the same dates.
  const twr = useQuery({
    queryKey: ['performance.twr', filters, twrCurrency, 'summary'],
    queryFn: () => api.performanceTwr(filters, twrCurrency === 'CHF' ? undefined : twrCurrency, true),
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

  const twrPct = (twr.data?.twr_pct ?? undefined) as number | undefined;
  const annualisedPct = twr.data?.annualised_pct as number | undefined;

  return (
    <>
      <div className="info-box">
        {copy.intro}
      </div>
      <div className="twr-switcher">
        <span className="twr-switcher-label">{copy.currencyLabel}</span>
        {TWR_CURRENCIES.map((c) => (
          <button key={c} className={twrCurrency === c ? 'active' : ''} onClick={() => setTwrCurrency(c)}>{c}</button>
        ))}
      </div>

      {twr.isError ? (
        <MissingData title={copy.unavailableTitle}>{copy.unavailableBody}</MissingData>
      ) : twr.data && (twr.data.n_days ?? 0) === 0 && twr.data.quality == null ? (
        // Backend OK pero sin filas NavDaily en el rango (p. ej. primer arranque con DB vacía).
        <MissingData title={copy.noNavTitle}>
          {copy.noNavBody}
        </MissingData>
      ) : (() => {
        const quality = twr.data?.quality as { is_reliable?: boolean; reason?: string; coverage_ratio?: number; max_gap_days?: number } | undefined;
        const reliable = quality?.is_reliable !== false;
        return (
          <>
            {!reliable && (
              <div className="info-box" style={{ background: 'rgba(245,158,11,0.08)', borderColor: 'var(--border-warn)' }}>
                <strong>{copy.hiddenTitle}</strong>{' '}
                {quality?.reason ?? ''}
                {quality?.coverage_ratio !== undefined && ` ${copy.navCoverage.replace('{pct}', (quality.coverage_ratio * 100).toFixed(0))}`}
                {quality?.max_gap_days !== undefined && quality.max_gap_days > 10 && ` ${copy.maxGap.replace('{days}', String(quality.max_gap_days))}`}
                <br />
                <strong>{copy.action}</strong> {copy.actionBody}
              </div>
            )}

            {twr.data?.fx_degraded && (
              <div className="info-box" style={{ background: 'rgba(245,158,11,0.08)', borderColor: 'var(--border-warn)' }}>
                {twr.data.twr_pct == null && twr.data.fx_source ? copy.fxMissing : copy.fxFallback}
              </div>
            )}

            <div className="kpi-grid">
              <KPICard label={`TWR (${twrCurrency})`} value={formatPercent(twrPct, true, locale)}
                delta={twr.isFetching ? copy.loading : (twrPct == null ? copy.hidden : copy.isolatesFlows)}
                deltaKind={twrPct != null ? (twrPct >= 0 ? 'pos' : 'neg') : 'neutral'} loading={twr.isLoading} />
              <KPICard label={`TWR Annualized (${twrCurrency})`} value={formatPercent(annualisedPct, true, locale)}
                delta={annualisedPct == null ? (twrPct == null ? copy.hidden : copy.underOneYear) : undefined}
                deltaKind={annualisedPct != null ? (annualisedPct >= 0 ? 'pos' : 'neg') : 'neutral'} loading={twr.isLoading} />
              {(() => {
                const xirrPct = xirrQ.data?.xirr_pct;
                const flowCount = xirrQ.data?.flow_count;
                const xirrNote = xirrQ.data?.note;
                return (
                  <KPICard
                    label={`XIRR (${currency})`}
                    value={xirrPct != null ? formatPercent(xirrPct, true, locale) : '—'}
                    delta={xirrPct != null
                      ? `${copy.moneyWeightedFlows.replace('{count}', String(flowCount ?? 0))}${xirrNote ? ' · ⚠ ' + xirrNote : ''}`
                      : (xirrQ.data?.error ?? (xirrQ.data?.annualised === false ? copy.underOneYear : copy.insufficient))}
                    deltaKind={xirrPct != null ? (xirrPct >= 0 ? 'pos' : 'neg') : 'neutral'}
                    loading={xirrQ.isLoading}
                  />
                );
              })()}
              {(() => {
                const lc = vsb.data?.is_reliable ? vsb.data.level_comparison : undefined;
                return (
                  <KPICard
                    label={`S&P 500 (${twrCurrency})`}
                    value={formatPercent(lc?.benchmark_return_pct, true, locale)}
                    delta={lc ? copy.spPeriodDelta.replace('{excess}', formatPercent(lc.excess_return_pct, true, locale)) : undefined}
                    deltaKind={lc ? (lc.excess_return_pct >= 0 ? 'pos' : 'neg') : 'neutral'}
                    loading={vsb.isLoading}
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
                      {copy.benchmarkAlignedNote}
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
                        .replace('{ccy}', String(rm.currency ?? 'CHF'))
                        .replace('{rate}', String(rm.risk_free_rate_pct))
                        .replace('{rfSource}', String(rm.risk_free_source ?? ''))}
                    </small>
                  </div>
                  {rm.annualised === false && (
                    <div className="info-box" style={{ marginTop: 8 }}>{copy.riskShortWindow}</div>
                  )}
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
                      value={rm.annualised_volatility_pct !== undefined ? formatPercent(rm.annualised_volatility_pct, false, locale) : '—'}
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
                      value={rm.max_drawdown_pct !== undefined ? formatPercent(rm.max_drawdown_pct, false, locale) : '—'}
                      delta={rm.max_drawdown_peak_date && rm.max_drawdown_trough_date
                        ? `${rm.max_drawdown_peak_date} → ${rm.max_drawdown_trough_date}`
                        : undefined}
                      deltaKind="neg" loading={riskQ.isLoading} />
                    <KPICard label="Calmar Ratio"
                      value={rm.calmar_ratio !== undefined && rm.calmar_ratio !== null ? rm.calmar_ratio.toFixed(2) : '—'}
                      delta="annual return / |max DD|"
                      deltaKind={(rm.calmar_ratio ?? 0) >= 1 ? 'pos' : 'neutral'} loading={riskQ.isLoading} />
                    <KPICard label={copy.annualReturnDaily}
                      value={rm.annualised_return_pct != null ? formatPercent(rm.annualised_return_pct, false, locale) : '—'}
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
                          <KPICard label="Beta"
                            value={a.beta !== undefined && a.beta !== null ? a.beta.toFixed(2) : '—'}
                            delta={copy.marketSensitivity}
                            deltaKind={a.beta !== undefined && a.beta !== null && a.beta > 1.2 ? 'warn' : 'neutral'} />
                          <KPICard label="Alpha Jensen"
                            value={a.jensen_alpha_pct !== undefined && a.jensen_alpha_pct !== null ? formatPercent(a.jensen_alpha_pct, true, locale) : '—'}
                            delta={copy.adjustedByBeta}
                            deltaKind={(a.jensen_alpha_pct ?? 0) >= 0 ? 'pos' : 'neg'} />
                          {vsb.data?.risk_comparison && (() => {
                            const rc = vsb.data!.risk_comparison;
                            return (
                              <>
                                <KPICard
                                  label="Tracking Error"
                                  value={rc.tracking_error_pct != null ? formatPercent(rc.tracking_error_pct, false, locale) : '—'}
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
