import { useQuery } from '@tanstack/react-query';
import type { UseQueryResult } from '@tanstack/react-query';
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { api, type MarginSummaryResponse, type PeriodFilters } from '../lib/api';
import { ChartTooltip } from '../components/ChartTooltip';
import { KPICard } from '../components/KPICard';
import { MissingData } from '../components/MissingData';
import { formatCompactCurrency, formatCurrency, formatNumber, formatPercent } from '../lib/format';
import { useI18n } from '../lib/i18n';

interface Props {
  filters: PeriodFilters;
  currency: string;
  /** Shared with Resumen — passed in so React Query dedupes. */
  marginQ: UseQueryResult<MarginSummaryResponse>;
}

const COPY = {
  es: {
    unavailableTitle: 'Margen no disponible',
    unavailableBody: 'El backend devolvió un error al cargar el snapshot.',
    noDataBody: 'Todavía no hay un snapshot NAV para calcular el margen.',
    loadingSnapshot: 'Cargando snapshot de margen...',
    warnings: 'Avisos:',
    snapshotNote: 'Las estimaciones marcadas (Reg-T) son conservadoras (50 % inicial / 25 % mantenimiento para stocks, 100 % premium para opciones, 15 % para futuros). Cuando se conecte IB Gateway estos cuatro KPI se reemplazarán por los números reales de IBKR.',
    cushion: 'Cushion estimado (Reg-T)',
    buyingPower: 'Buying Power estimado (Reg-T)',
    maintMargin: 'Maint Margin estimado (Reg-T)',
    optionsPct: '% en opciones',
    liquidity: 'Liquidez (cash / NLV)',
    liquidityTitle: 'Cash neto del NAV (deudas por divisa ya restadas) dividido por el NLV, al cierre Flex EOD.',
    cashByCurrency: 'Cash por divisa',
    currency: 'Divisa',
    endingCash: 'Ending cash',
    status: 'Estado',
    debt: 'debt',
    topConcentration: 'Top concentración (>= 1 % NLV)',
    symbol: 'Símbolo',
    class: 'Clase',
    value: 'Valor',
    debtInterest: 'Intereses: deuda y efectivo',
    debtTable: 'Pagados por divisa (deuda)',
    cashTable: 'Cobrados por divisa (efectivo)',
    cashInterest: 'Intereses cobrados por efectivo',
    cashYield: 'Rendimiento efectivo del efectivo',
    syepIncome: 'Préstamo de acciones (SYEP)',
    avgCash: 'Saldo medio',
    avgDebt: 'Deuda media',
    earnedNative: 'Cobrado (nativo)',
    earnedBase: 'Cobrado',
    yieldCol: 'Rendimiento',
    daysWithCash: 'días con saldo positivo',
    avgCashOf: 'sobre saldo medio',
    thresholdNote: 'IBKR no remunera el primer tramo de cada divisa (umbral por divisa, p. ej. USD 10.000 o HKD 78.000), así que el rendimiento sobre el saldo total queda por debajo del tipo publicado. Los intereses se abonan a mes vencido y se cuentan por fecha de abono, igual que los pagados. SYEP es préstamo de acciones, no interés del efectivo.',
    interestPaid: 'Intereses pagados (periodo)',
    daysWithDebt: 'días con debt',
    interestReceived: 'Intereses recibidos',
    netFinancingCost: 'Net coste de financiación',
    effectiveApr: 'APR efectivo aprox',
    paidNative: 'Pagado (nativo)',
    paidBase: 'Pagado',
    loadingInterest: 'Cargando intereses...',
    leverageHistory: 'Leverage histórico (Gross / NAV)',
  },
  en: {
    unavailableTitle: 'Margin unavailable',
    unavailableBody: 'The backend returned an error while loading the snapshot.',
    noDataBody: 'There is no NAV snapshot available to calculate margin yet.',
    loadingSnapshot: 'Loading margin snapshot...',
    warnings: 'Warnings:',
    snapshotNote: 'Reg-T estimates are conservative (50% initial / 25% maintenance for stocks, 100% option premium, 15% futures). Once IB Gateway is connected, these KPIs can be replaced by real IBKR figures.',
    cushion: 'Estimated cushion (Reg-T)',
    buyingPower: 'Estimated buying power (Reg-T)',
    maintMargin: 'Estimated maint margin (Reg-T)',
    optionsPct: '% in options',
    liquidity: 'Liquidity (cash / NLV)',
    liquidityTitle: 'NAV net cash (per-currency debt already subtracted) divided by NLV, at Flex EOD.',
    cashByCurrency: 'Cash by currency',
    currency: 'Currency',
    endingCash: 'Ending cash',
    status: 'Status',
    debt: 'debt',
    topConcentration: 'Top concentration (>= 1% NLV)',
    symbol: 'Symbol',
    class: 'Class',
    value: 'Value',
    debtInterest: 'Interest: debt and cash',
    debtTable: 'Paid by currency (debt)',
    cashTable: 'Earned by currency (cash)',
    cashInterest: 'Interest earned on cash',
    cashYield: 'Effective yield on cash',
    syepIncome: 'Stock lending (SYEP)',
    avgCash: 'Avg balance',
    avgDebt: 'Avg debt',
    earnedNative: 'Earned (native)',
    earnedBase: 'Earned',
    yieldCol: 'Yield',
    daysWithCash: 'days with positive cash',
    avgCashOf: 'on avg balance',
    thresholdNote: 'IBKR pays nothing on the first tranche of each currency (a per-currency threshold, e.g. USD 10,000 or HKD 78,000), so the yield on the whole balance stays below the published rate. Interest is credited a month in arrears and counted by credit date, like interest paid. SYEP is stock lending, not interest on cash.',
    interestPaid: 'Interest paid (period)',
    daysWithDebt: 'days with debt',
    interestReceived: 'Interest received',
    netFinancingCost: 'Net financing cost',
    effectiveApr: 'Approx effective APR',
    paidNative: 'Paid (native)',
    paidBase: 'Paid',
    loadingInterest: 'Loading interest...',
    leverageHistory: 'Historical leverage (Gross / NAV)',
  },
  de: {
    unavailableTitle: 'Margin nicht verfuegbar',
    unavailableBody: 'Das Backend hat beim Laden des Snapshots einen Fehler geliefert.',
    noDataBody: 'Es ist noch kein NAV-Snapshot fuer die Margin-Berechnung vorhanden.',
    loadingSnapshot: 'Margin-Snapshot wird geladen...',
    warnings: 'Warnungen:',
    snapshotNote: 'Reg-T-Schaetzungen sind konservativ (50% initial / 25% maintenance fuer Aktien, 100% Optionspraemie, 15% Futures). Sobald IB Gateway verbunden ist, koennen diese KPIs durch echte IBKR-Werte ersetzt werden.',
    cushion: 'Geschaetzter Cushion (Reg-T)',
    buyingPower: 'Geschaetzte Buying Power (Reg-T)',
    maintMargin: 'Geschaetzte Maint Margin (Reg-T)',
    optionsPct: '% in Optionen',
    liquidity: 'Liquiditaet (Cash / NLV)',
    liquidityTitle: 'Netto-Cash im NAV (Schulden pro Waehrung bereits abgezogen) geteilt durch NLV, zum Flex-EOD.',
    cashByCurrency: 'Cash nach Waehrung',
    currency: 'Waehrung',
    endingCash: 'Ending cash',
    status: 'Status',
    debt: 'Debt',
    topConcentration: 'Top-Konzentration (>= 1% NLV)',
    symbol: 'Symbol',
    class: 'Klasse',
    value: 'Wert',
    debtInterest: 'Zinsen: Schuld und Cash',
    debtTable: 'Gezahlt nach Waehrung (Schuld)',
    cashTable: 'Erhalten nach Waehrung (Cash)',
    cashInterest: 'Zinsertrag auf Cash',
    cashYield: 'Effektive Rendite auf Cash',
    syepIncome: 'Wertpapierleihe (SYEP)',
    avgCash: 'Durchschn. Saldo',
    avgDebt: 'Durchschn. Schuld',
    earnedNative: 'Erhalten (nativ)',
    earnedBase: 'Erhalten',
    yieldCol: 'Rendite',
    daysWithCash: 'Tage mit positivem Cash',
    avgCashOf: 'auf durchschn. Saldo',
    thresholdNote: 'IBKR verzinst die erste Tranche jeder Waehrung nicht (Schwelle pro Waehrung, z. B. USD 10.000 oder HKD 78.000), daher liegt die Rendite auf den Gesamtsaldo unter dem publizierten Satz. Zinsen werden einen Monat nachtraeglich gutgeschrieben und nach Gutschriftdatum gezaehlt, wie gezahlte Zinsen. SYEP ist Wertpapierleihe, kein Zins auf Cash.',
    interestPaid: 'Gezahlte Zinsen (Zeitraum)',
    daysWithDebt: 'Tage mit Debt',
    interestReceived: 'Erhaltene Zinsen',
    netFinancingCost: 'Netto-Finanzierungskosten',
    effectiveApr: 'Effektiver APR ca.',
    paidNative: 'Gezahlt (native)',
    paidBase: 'Gezahlt',
    loadingInterest: 'Zinsen werden geladen...',
    leverageHistory: 'Historisches Leverage (Gross / NAV)',
  },
} as const;

export function MargenPage({ filters, currency, marginQ }: Props) {
  const { language, locale } = useI18n();
  const copy = COPY[language];
  const marginInterestQ = useQuery({
    queryKey: ['portfolio.margin.interest', filters],
    queryFn: () => api.marginInterest(filters),
  });

  const leverageSeriesQ = useQuery({
    queryKey: ['portfolio.margin.leverage', filters],
    queryFn: () => api.marginLeverageSeries(filters),
  });

  const m = marginQ.data;
  const mi = marginInterestQ.data;
  const ls = leverageSeriesQ.data ?? [];

  if (marginQ.isError) {
    return <MissingData title={copy.unavailableTitle}>{copy.unavailableBody}</MissingData>;
  }
  if (!m) {
    return <div className="info-box">{copy.loadingSnapshot}</div>;
  }
  if (!('reg_t_estimate' in m)) {
    return <MissingData title={copy.unavailableTitle}>{copy.noDataBody}</MissingData>;
  }
  const ex = m.exposure;
  const rt = m.reg_t_estimate;

  return (
    <>
      {m.warnings && m.warnings.length > 0 && (
        <div className="info-box" style={{ background: 'rgba(245,158,11,0.08)', borderColor: 'var(--border-warn)' }}>
          <strong>{copy.warnings}</strong>
          <ul style={{ margin: '4px 0 0 16px', padding: 0 }}>
            {m.warnings.map((w: string) => <li key={w}>{w}</li>)}
          </ul>
        </div>
      )}

      <div className="info-box">
        Snapshot {m.snapshot_date}. {copy.snapshotNote}
      </div>

      <div className="kpi-grid">
        <KPICard label="NLV" value={formatCurrency(m.nav_total, currency, locale)} delta={m.snapshot_date} deltaKind="neutral" loading={marginQ.isLoading} />
        <KPICard label="Leverage (Gross/NLV)"
          value={m.leverage_x != null ? `${formatNumber(m.leverage_x, 2, locale)}×` : '—'}
          delta={ex?.gross_pct_nlv != null ? `Gross ${formatPercent(ex.gross_pct_nlv, false, locale)}` : undefined}
          deltaKind={(m.leverage_x ?? 0) > 1.30 ? 'warn' : 'pos'}
          loading={marginQ.isLoading} />
        <KPICard label={copy.cushion}
          value={rt.cushion_pct_estimate != null ? formatPercent(rt.cushion_pct_estimate, false, locale) : '—'}
          delta={`ExcessLiq ${formatCompactCurrency(rt.excess_liq_estimate, currency, locale)}`}
          deltaKind={(rt.cushion_pct_estimate ?? 100) < 30 ? 'warn' : 'pos'}
          loading={marginQ.isLoading} />
        <KPICard label={copy.buyingPower}
          value={formatCompactCurrency(rt.buying_power_estimate, currency, locale)}
          delta={`Init ${formatCompactCurrency(rt.init_margin_estimate, currency, locale)}`}
          deltaKind="neutral" loading={marginQ.isLoading} />
        <KPICard label={copy.maintMargin}
          value={formatCompactCurrency(rt.maint_margin_estimate, currency, locale)}
          delta={m.nav_total ? `${formatPercent((rt.maint_margin_estimate / m.nav_total) * 100, false, locale)} NLV` : undefined}
          deltaKind="neutral" loading={marginQ.isLoading} />
        <KPICard label={copy.liquidity} title={copy.liquidityTitle}
          value={m.cash_pct_nlv != null ? formatPercent(m.cash_pct_nlv, false, locale) : '—'}
          delta={formatCompactCurrency(m.nav_breakdown?.cash, currency, locale)}
          deltaKind={(m.cash_pct_nlv ?? 0) < 0 ? 'warn' : 'neutral'} loading={marginQ.isLoading} />
        <KPICard label={copy.optionsPct}
          value={ex?.options_pct_nlv != null ? formatPercent(ex.options_pct_nlv, false, locale) : '—'}
          delta={formatCompactCurrency(ex?.options_value_base, currency, locale)}
          deltaKind={(ex?.options_pct_nlv ?? 0) > 15 ? 'warn' : 'neutral'} loading={marginQ.isLoading} />
      </div>

      <div className="subsection-grid" style={{ marginTop: 18 }}>
        <div className="chart-panel">
          <div className="panel-header">
            <div className="panel-title">{copy.cashByCurrency}</div>
            <div className="chart-legend">
              <span><i className="legend-dot loss" />Margin debt</span>
            </div>
          </div>
          <table className="data-table">
            <thead><tr><th>{copy.currency}</th><th>{copy.endingCash}</th><th>{copy.status}</th></tr></thead>
            <tbody>
              {m.cash_by_currency.map((r: any) => (
                <tr key={r.currency}>
                  <td>{r.currency}</td>
                  <td className={r.is_debt ? 'pnl-neg num' : 'pnl-pos num'}>{formatCurrency(r.ending_cash, r.currency, locale)}</td>
                  <td>{r.is_debt ? `! ${copy.debt}` : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="chart-panel">
          <div className="panel-header">
            <div className="panel-title">{copy.topConcentration}</div>
          </div>
          <table className="data-table">
            <thead><tr><th>{copy.symbol}</th><th>{copy.class}</th><th>{copy.value}</th><th>% NLV</th></tr></thead>
            <tbody>
              {m.top_positions.filter((p: any) => Math.abs(p.pct_of_nav ?? 0) >= 1).map((p: any) => (
                <tr key={p.symbol}>
                  <td>{p.symbol}</td>
                  <td><small>{p.asset_class ?? '—'}</small></td>
                  <td>{formatCompactCurrency(p.position_value_base, currency, locale)}</td>
                  <td className="num">{formatPercent(p.pct_of_nav ?? 0, false, locale)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="chart-panel" style={{ marginTop: 18 }}>
        <div className="panel-header">
          <div className="panel-title">{copy.debtInterest}</div>
        </div>
        {mi ? (
          <>
            <div className="kpi-grid">
              <KPICard label={copy.interestPaid}
                value={formatCurrency(mi.paid_total_base, currency, locale)}
                delta={`${mi.days_with_debt} ${copy.daysWithDebt}`} deltaKind="neg" />
              <KPICard label={copy.effectiveApr}
                value={mi.effective_apr_pct != null ? formatPercent(mi.effective_apr_pct, false, locale) : '—'}
                delta={`${copy.avgDebt} ${formatCompactCurrency(mi.avg_debt_base, currency, locale)}`}
                deltaKind="neutral" />
              <KPICard label={copy.cashInterest}
                value={formatCurrency(mi.cash_interest_base ?? mi.received_total_base, currency, locale)}
                delta={mi.days_with_cash != null ? `${mi.days_with_cash} ${copy.daysWithCash}` : undefined}
                deltaKind="pos" />
              <KPICard label={copy.cashYield}
                value={mi.effective_cash_yield_pct != null ? formatPercent(mi.effective_cash_yield_pct, false, locale) : '—'}
                delta={mi.avg_cash_base != null ? `${copy.avgCashOf} ${formatCompactCurrency(mi.avg_cash_base, currency, locale)}` : undefined}
                deltaKind="neutral" />
              <KPICard label={copy.syepIncome}
                value={formatCurrency(mi.syep_base ?? 0, currency, locale)}
                deltaKind="pos" />
              <KPICard label={copy.netFinancingCost}
                value={formatCurrency(mi.net_base, currency, locale)}
                deltaKind={mi.net_base < 0 ? 'neg' : 'pos'} />
            </div>
            {(mi.paid_by_currency?.length ?? 0) > 0 && (
              <>
                <div className="panel-subtitle" style={{ marginTop: 14 }}>{copy.debtTable}</div>
                <div className="table-scroll">
                <table className="data-table">
                  <thead><tr><th>{copy.currency}</th><th className="num">{copy.avgDebt} ({currency})</th><th className="num">{copy.paidNative}</th><th className="num">{copy.paidBase} ({currency})</th><th className="num">{copy.effectiveApr}</th></tr></thead>
                  <tbody>
                    {mi.paid_by_currency.map((r) => (
                      <tr key={r.currency}>
                        <td>{r.currency}</td>
                        <td className="num">{r.avg_debt_base != null ? formatCurrency(r.avg_debt_base, currency, locale) : '—'}</td>
                        <td className="pnl-neg num">{formatCurrency(r.amount_native, r.currency, locale)}</td>
                        <td className="pnl-neg num">{r.amount_base !== undefined ? formatCurrency(r.amount_base, currency, locale) : '—'}</td>
                        <td className="num">{r.apr_pct != null ? formatPercent(r.apr_pct, false, locale) : '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                </div>
              </>
            )}
            {(mi.received_by_currency?.length ?? 0) > 0 && (
              <>
                <div className="panel-subtitle" style={{ marginTop: 14 }}>{copy.cashTable}</div>
                <div className="table-scroll">
                <table className="data-table">
                  <thead><tr><th>{copy.currency}</th><th className="num">{copy.avgCash} ({currency})</th><th className="num">{copy.earnedNative}</th><th className="num">{copy.earnedBase} ({currency})</th><th className="num">{copy.yieldCol}</th><th className="num">SYEP ({currency})</th></tr></thead>
                  <tbody>
                    {mi.received_by_currency.map((r) => (
                      <tr key={r.currency}>
                        <td>{r.currency}</td>
                        <td className="num">{r.avg_cash_base != null ? formatCurrency(r.avg_cash_base, currency, locale) : '—'}</td>
                        <td className={`num${(r.cash_interest_native ?? 0) > 0 ? ' pnl-pos' : ''}`}>{formatCurrency(r.cash_interest_native ?? r.amount_native, r.currency, locale)}</td>
                        <td className={`num${(r.cash_interest_base ?? 0) > 0 ? ' pnl-pos' : ''}`}>{formatCurrency(r.cash_interest_base ?? r.amount_base ?? 0, currency, locale)}</td>
                        <td className="num">{r.yield_pct != null ? formatPercent(r.yield_pct, false, locale) : '—'}</td>
                        <td className="num">{r.syep_base ? formatCurrency(r.syep_base, currency, locale) : '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                </div>
              </>
            )}
            <p className="panel-subtitle" style={{ marginTop: 10 }}>{copy.thresholdNote}</p>
          </>
        ) : (
          <div className="info-box">{copy.loadingInterest}</div>
        )}
      </div>

      {ls.length > 0 && (
        <div className="chart-panel" style={{ marginTop: 18 }}>
          <div className="panel-header">
            <div className="panel-title">{copy.leverageHistory}</div>
            <div className="chart-legend">
              <span><i className="legend-dot capital" style={{ background: '#a855f7' }} />Leverage ×</span>
            </div>
          </div>
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={ls}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
              <XAxis dataKey="date" tick={{ fill: '#8b88a6', fontSize: 11 }} minTickGap={32} />
              <YAxis tick={{ fill: '#8b88a6', fontSize: 11 }} tickFormatter={(v: number) => `${v.toLocaleString(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}×`} domain={['dataMin', 'dataMax']} />
              <Tooltip content={<ChartTooltip formatter={(v: unknown) => typeof v === 'number' ? `${v.toLocaleString(locale, { minimumFractionDigits: 3, maximumFractionDigits: 3 })}×` : '—'} />} cursor={{ stroke: 'rgba(168,85,247,0.3)' }} />
              <Line type="monotone" dataKey="leverage_x" name="Leverage" stroke="#a855f7" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </>
  );
}
