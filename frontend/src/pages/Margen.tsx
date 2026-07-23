import { useQuery } from '@tanstack/react-query';
import type { UseQueryResult } from '@tanstack/react-query';
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { api, type PeriodFilters } from '../lib/api';
import { ChartTooltip } from '../components/ChartTooltip';
import { KPICard } from '../components/KPICard';
import { MissingData } from '../components/MissingData';
import { formatCompactCurrency, formatCurrency } from '../lib/format';
import { useI18n } from '../lib/i18n';

interface Props {
  filters: PeriodFilters;
  currency: string;
  /** Shared with Resumen — passed in so React Query dedupes. */
  marginQ: UseQueryResult<any>;
}

const COPY = {
  es: {
    unavailableTitle: 'Margen no disponible',
    unavailableBody: 'El backend devolvió un error al cargar el snapshot.',
    loadingSnapshot: 'Cargando snapshot de margen...',
    warnings: 'Avisos:',
    snapshotNote: 'Las estimaciones marcadas (Reg-T) son conservadoras (50 % inicial / 25 % mantenimiento para stocks, 100 % premium para opciones, 15 % para futuros). Cuando se conecte IB Gateway estos cuatro KPI se reemplazarán por los números reales de IBKR.',
    cushion: 'Cushion estimado (Reg-T)',
    buyingPower: 'Buying Power estimado (Reg-T)',
    maintMargin: 'Maint Margin estimado (Reg-T)',
    optionsPct: '% en opciones',
    cashByCurrency: 'Cash por divisa',
    currency: 'Divisa',
    endingCash: 'Ending cash',
    status: 'Estado',
    debt: 'debt',
    topConcentration: 'Top concentración (>= 1 % NLV)',
    symbol: 'Símbolo',
    class: 'Clase',
    value: 'Valor',
    debtInterest: 'Intereses sobre la deuda',
    interestPaid: 'Intereses pagados (periodo)',
    daysWithDebt: 'días con debt',
    interestReceived: 'Intereses recibidos',
    netFinancingCost: 'Net coste de financiación',
    effectiveApr: 'APR efectivo aprox',
    paidNative: 'Pagado (native)',
    paidBase: 'Pagado',
    loadingInterest: 'Cargando intereses...',
    leverageHistory: 'Leverage histórico (Gross / NAV)',
  },
  en: {
    unavailableTitle: 'Margin unavailable',
    unavailableBody: 'The backend returned an error while loading the snapshot.',
    loadingSnapshot: 'Loading margin snapshot...',
    warnings: 'Warnings:',
    snapshotNote: 'Reg-T estimates are conservative (50% initial / 25% maintenance for stocks, 100% option premium, 15% futures). Once IB Gateway is connected, these KPIs can be replaced by real IBKR figures.',
    cushion: 'Estimated cushion (Reg-T)',
    buyingPower: 'Estimated buying power (Reg-T)',
    maintMargin: 'Estimated maint margin (Reg-T)',
    optionsPct: '% in options',
    cashByCurrency: 'Cash by currency',
    currency: 'Currency',
    endingCash: 'Ending cash',
    status: 'Status',
    debt: 'debt',
    topConcentration: 'Top concentration (>= 1% NLV)',
    symbol: 'Symbol',
    class: 'Class',
    value: 'Value',
    debtInterest: 'Interest on debt',
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
    loadingSnapshot: 'Margin-Snapshot wird geladen...',
    warnings: 'Warnungen:',
    snapshotNote: 'Reg-T-Schaetzungen sind konservativ (50% initial / 25% maintenance fuer Aktien, 100% Optionspraemie, 15% Futures). Sobald IB Gateway verbunden ist, koennen diese KPIs durch echte IBKR-Werte ersetzt werden.',
    cushion: 'Geschaetzter Cushion (Reg-T)',
    buyingPower: 'Geschaetzte Buying Power (Reg-T)',
    maintMargin: 'Geschaetzte Maint Margin (Reg-T)',
    optionsPct: '% in Optionen',
    cashByCurrency: 'Cash nach Waehrung',
    currency: 'Waehrung',
    endingCash: 'Ending cash',
    status: 'Status',
    debt: 'Debt',
    topConcentration: 'Top-Konzentration (>= 1% NLV)',
    symbol: 'Symbol',
    class: 'Klasse',
    value: 'Wert',
    debtInterest: 'Zinsen auf Schuld',
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
          value={m.leverage_x !== undefined ? `${m.leverage_x.toLocaleString(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}×` : '—'}
          delta={ex ? `Gross ${ex.gross_pct_nlv?.toLocaleString(locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%` : undefined}
          deltaKind={(m.leverage_x ?? 0) > 1.30 ? 'warn' : 'pos'}
          loading={marginQ.isLoading} />
        <KPICard label={copy.cushion}
          value={rt.cushion_pct_estimate !== undefined ? `${rt.cushion_pct_estimate.toLocaleString(locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%` : '—'}
          delta={`ExcessLiq ${formatCompactCurrency(rt.excess_liq_estimate, currency, locale)}`}
          deltaKind={(rt.cushion_pct_estimate ?? 100) < 30 ? 'warn' : 'pos'}
          loading={marginQ.isLoading} />
        <KPICard label={copy.buyingPower}
          value={formatCompactCurrency(rt.buying_power_estimate, currency, locale)}
          delta={`Init ${formatCompactCurrency(rt.init_margin_estimate, currency, locale)}`}
          deltaKind="neutral" loading={marginQ.isLoading} />
        <KPICard label={copy.maintMargin}
          value={formatCompactCurrency(rt.maint_margin_estimate, currency, locale)}
          delta={`${((rt.maint_margin_estimate / (m.nav_total ?? 1)) * 100).toLocaleString(locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 })}% NLV`}
          deltaKind="neutral" loading={marginQ.isLoading} />
        <KPICard label={copy.optionsPct}
          value={ex?.options_pct_nlv !== undefined ? `${ex.options_pct_nlv.toLocaleString(locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%` : '—'}
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
                  <td className={r.is_debt ? 'pnl-neg' : 'pnl-pos'}>{r.ending_cash.toLocaleString(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
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
                  <td>{(p.pct_of_nav ?? 0).toLocaleString(locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%</td>
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
              <KPICard label={copy.interestReceived}
                value={formatCurrency(mi.received_total_base, currency, locale)}
                deltaKind="pos" />
              <KPICard label={copy.netFinancingCost}
                value={formatCurrency(mi.net_base, currency, locale)}
                deltaKind={mi.net_base < 0 ? 'neg' : 'pos'} />
              <KPICard label={copy.effectiveApr}
                value={mi.effective_apr_pct !== undefined && mi.effective_apr_pct !== null
                  ? `${mi.effective_apr_pct.toLocaleString(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%` : '—'}
                delta={`avg debt ${formatCompactCurrency(mi.avg_debt_native_sum, '', locale)}`}
                deltaKind="neutral" />
            </div>
            {(mi.paid_by_currency?.length ?? 0) > 0 && (
              <table className="data-table" style={{ marginTop: 14 }}>
                <thead><tr><th>{copy.currency}</th><th>{copy.paidNative}</th><th>{copy.paidBase} ({currency})</th></tr></thead>
                <tbody>
                  {mi.paid_by_currency.map((r: any) => (
                    <tr key={r.currency}>
                      <td>{r.currency}</td>
                      <td className="pnl-neg">{r.amount_native.toLocaleString(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                      <td className="pnl-neg">{r.amount_base !== undefined ? formatCurrency(r.amount_base, currency, locale) : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
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
