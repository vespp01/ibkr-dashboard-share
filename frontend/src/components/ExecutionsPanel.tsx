import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import {
  Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { api, type PeriodFilters } from '../lib/api';
import { ChartTooltip } from './ChartTooltip';
import { formatCurrency } from '../lib/format';
import { useI18n } from '../lib/i18n';

interface Props {
  filters: PeriodFilters;
  currency: string;
}

const COPY = {
  es: {
    title: 'Ejecuciones (Trade Confirmation Flex)',
    all: 'Todas',
    multiFillOnly: 'Multi-fill only',
    removeDrilldown: 'Quitar el drilldown a esta orden',
    order: 'Orden',
    orders: 'órdenes',
    commissions: 'comisiones',
    multiFillDetected: 'Multi-fill orders detectadas:',
    clickOrder: 'pulsa una orden para ver sus slices.',
    lagTitle: 'Lag order -> fill',
    loading: 'Cargando...',
    error: 'Error cargando ejecuciones: revisa el backend.',
    empty: 'No hay ejecuciones para este filtro. TC tiene un ceiling de 45 días; la primera ejecución del cron es lunes 08:15 local.',
    execTime: 'Exec time',
    orderTime: 'Order time',
    symbol: 'Símbolo',
    side: 'Lado',
    price: 'Precio',
    commission: 'Comisión',
    orderId: 'Order ID',
    fillsTitle: 'Cuántos fills tiene esta orden en el filtro actual',
    commissionTitle: 'Comisión total (incluye broker + third party + regulatory)',
    drilldownTitle: 'Drilldown: ver todos los fills de esta orden',
  },
  en: {
    title: 'Executions (Trade Confirmation Flex)',
    all: 'All',
    multiFillOnly: 'Multi-fill only',
    removeDrilldown: 'Remove drilldown for this order',
    order: 'Order',
    orders: 'orders',
    commissions: 'commissions',
    multiFillDetected: 'Multi-fill orders detected:',
    clickOrder: 'click an order to inspect its slices.',
    lagTitle: 'Lag order -> fill',
    loading: 'Loading...',
    error: 'Error loading executions: check the backend.',
    empty: 'No executions for this filter. TC has a 45-day ceiling; the first cron run is Monday 08:15 local time.',
    execTime: 'Exec time',
    orderTime: 'Order time',
    symbol: 'Symbol',
    side: 'Side',
    price: 'Price',
    commission: 'Commission',
    orderId: 'Order ID',
    fillsTitle: 'How many fills this order has in the current filter',
    commissionTitle: 'Total commission including broker, third-party, and regulatory fees',
    drilldownTitle: 'Drilldown: see every fill for this order',
  },
  de: {
    title: 'Ausfuehrungen (Trade Confirmation Flex)',
    all: 'Alle',
    multiFillOnly: 'Nur Multi-fill',
    removeDrilldown: 'Drilldown fuer diese Order entfernen',
    order: 'Order',
    orders: 'Orders',
    commissions: 'Kommissionen',
    multiFillDetected: 'Multi-fill Orders erkannt:',
    clickOrder: 'Order anklicken, um Slices zu sehen.',
    lagTitle: 'Lag order -> fill',
    loading: 'Laden...',
    error: 'Fehler beim Laden der Ausfuehrungen: Backend pruefen.',
    empty: 'Keine Ausfuehrungen fuer diesen Filter. TC hat ein 45-Tage-Limit; der erste Cron-Lauf ist Montag 08:15 lokal.',
    execTime: 'Exec time',
    orderTime: 'Order time',
    symbol: 'Symbol',
    side: 'Seite',
    price: 'Preis',
    commission: 'Kommission',
    orderId: 'Order ID',
    fillsTitle: 'Anzahl Fills dieser Order im aktuellen Filter',
    commissionTitle: 'Gesamtkommission inkl. Broker, Drittpartei und Regulatory Fees',
    drilldownTitle: 'Drilldown: alle Fills dieser Order sehen',
  },
} as const;

function fmtCurrency(v: number | null | undefined, ccy: string, locale: string): string {
  if (v === undefined || v === null || Number.isNaN(v)) return '—';
  return formatCurrency(v, ccy, locale);
}

function fmtDateTime(iso: string | undefined): { date: string; time: string } {
  if (!iso || iso.length < 10) return { date: '—', time: '' };
  return { date: iso.slice(0, 10), time: iso.length >= 16 ? iso.slice(11, 16) : '' };
}

function fmtLag(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return '—';
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ${m % 60}m`;
  const d = Math.floor(h / 24);
  return d < 7 ? `${d}d ${h % 24}h` : `${d}d`;
}

export function ExecutionsPanel({ filters, currency }: Props) {
  const { language, locale } = useI18n();
  const copy = COPY[language];
  const [multiFillOnly, setMultiFillOnly] = useState(false);
  const [orderDrilldown, setOrderDrilldown] = useState<string | null>(null);

  const query = useQuery({
    queryKey: ['trades.executions', filters, multiFillOnly, orderDrilldown],
    queryFn: () => api.executions({
      ...filters,
      multi_fill_only: multiFillOnly,
      ...(orderDrilldown ? { order_id: orderDrilldown } : {}),
      limit: 200,
    }),
  });

  const rows = query.data?.rows ?? [];
  const summary = query.data?.summary;
  const baseCcy = summary?.base_currency ?? currency;

  const lagChartData = (summary?.lag_distribution ?? []).map((b) => ({
    bucket: b.bucket,
    count: b.count,
  }));
  const lagBarColor = (label: string) =>
    label === '<1m' || label === '1-5m' ? '#10b981'
    : label === '5-30m' || label === '30m-2h' ? '#a855f7'
    : label === '2-24h' ? '#f59e0b'
    : '#ef4444';

  return (
    <div className="trades-panel">
      <div className="panel-header">
        <div className="panel-title">{copy.title}</div>
        <div className="trades-filters">
          <div className="filter-chip-grid">
            <button className={multiFillOnly ? '' : 'active'} onClick={() => { setMultiFillOnly(false); setOrderDrilldown(null); }}>{copy.all}</button>
            <button className={multiFillOnly ? 'active' : ''} onClick={() => { setMultiFillOnly(true); setOrderDrilldown(null); }}>
              {copy.multiFillOnly}
            </button>
          </div>
          {orderDrilldown && (
            <button
              className="active"
              style={{ background: 'rgba(168,85,247,0.18)' }}
              onClick={() => setOrderDrilldown(null)}
              title={copy.removeDrilldown}
            >
              {copy.order} #{orderDrilldown} ×
            </button>
          )}
        </div>
      </div>

      {summary ? (
        <div className="trades-summary-line">
          <strong>{summary.total_fills}</strong> fills
          <span className="dot">·</span>
          <strong>{summary.distinct_orders}</strong> {copy.orders}
          <span className="dot">·</span>
          <span className={summary.multi_fill_orders > 0 ? 'pos' : 'muted'}>
            <strong>{summary.multi_fill_orders}</strong> multi-fill
          </span>
          <span className="dot">·</span>
          <span className="muted">lag avg <strong>{fmtLag(summary.avg_lag_seconds)}</strong></span>
          <span className="dot">·</span>
          <span className="muted">lag max <strong>{fmtLag(summary.max_lag_seconds)}</strong></span>
          <span className="dot">·</span>
          <span className="neg">
            <strong>{fmtCurrency(summary.total_commissions_base, baseCcy, locale)}</strong> {copy.commissions}
          </span>
        </div>
      ) : null}

      {summary && summary.multi_fill_order_ids.length > 0 && !orderDrilldown ? (
        <div className="info-box" style={{ marginBottom: 12 }}>
          <strong>{copy.multiFillDetected}</strong>{' '}
          {summary.multi_fill_order_ids.map((oid, i) => (
            <span key={oid}>
              {i > 0 ? ' · ' : ''}
              <button
                className="warn-cta"
                style={{ background: 'transparent', textDecoration: 'underline', padding: 0 }}
                onClick={() => setOrderDrilldown(oid)}
              >
                #{oid}
              </button>
            </span>
          ))}
          <span className="muted"> — {copy.clickOrder}</span>
        </div>
      ) : null}

      {summary && summary.lag_sample_size > 0 ? (
        <div className="chart-panel" style={{ marginBottom: 12 }}>
          <div className="panel-title">{copy.lagTitle} ({summary.lag_sample_size} fills)</div>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={lagChartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
              <XAxis dataKey="bucket" tick={{ fill: '#8b88a6', fontSize: 11 }} />
              <YAxis allowDecimals={false} tick={{ fill: '#8b88a6', fontSize: 11 }} />
              <Tooltip content={<ChartTooltip formatter={(v) => `${v} fills`} />} cursor={{ fill: 'rgba(168,85,247,0.06)' }} />
              <Bar dataKey="count" name="Fills" radius={[4, 4, 0, 0]}>
                {lagChartData.map((row) => (
                  <Cell key={row.bucket} fill={lagBarColor(row.bucket)} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      ) : null}

      {query.isLoading ? (
        <div className="trades-empty">{copy.loading}</div>
      ) : query.isError ? (
        <div className="trades-empty">{copy.error}</div>
      ) : rows.length === 0 ? (
        <div className="trades-empty">
          {copy.empty}
        </div>
      ) : (
        <div className="table-scroll">
          <table className="data-table trades-table">
            <thead>
              <tr>
                <th>{copy.execTime}</th>
                <th>{copy.orderTime}</th>
                <th>Lag</th>
                <th>{copy.symbol}</th>
                <th>{copy.side}</th>
                <th>Qty</th>
                <th>{copy.price}</th>
                <th>Proceeds</th>
                <th title={copy.commissionTitle}>{copy.commission}</th>
                <th>{copy.orderId}</th>
                <th title={copy.fillsTitle}>Fills</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const ex = fmtDateTime(r.execution_time);
                const od = fmtDateTime(r.order_time);
                const lag = r.order_to_fill_lag_seconds;
                const lagClass = lag === undefined || lag === null ? ''
                  : lag < 300 ? 'pnl-pos'
                  : lag < 7200 ? ''
                  : 'pnl-neg';
                return (
                  <tr key={r.exec_id}>
                    <td>
                      <span className="position-symbol">{ex.date}</span>
                      <small>{ex.time}</small>
                    </td>
                    <td>
                      <span className="position-symbol">{od.date}</span>
                      <small>{od.time}</small>
                    </td>
                    <td className={lagClass}>{fmtLag(lag)}</td>
                    <td>
                      <span className="position-symbol">{r.symbol}</span>
                      <small>{r.currency} · {r.asset_class}</small>
                    </td>
                    <td className={r.buy_sell === 'BUY' ? 'pnl-pos' : r.buy_sell === 'SELL' ? 'pnl-neg' : ''}>
                      {r.buy_sell}
                    </td>
                    <td>{r.quantity?.toLocaleString(locale)}</td>
                    <td>{r.price?.toLocaleString(locale, { minimumFractionDigits: 2 })}</td>
                    <td>{fmtCurrency(r.proceeds_base, baseCcy, locale)}</td>
                    <td className="pnl-neg">{fmtCurrency(r.commission_base, baseCcy, locale)}</td>
                    <td>
                      {r.is_multi_fill && r.order_id && orderDrilldown !== r.order_id ? (
                        <button
                          className="warn-cta"
                          style={{ background: 'transparent', textDecoration: 'underline', padding: 0, cursor: 'pointer' }}
                          onClick={() => setOrderDrilldown(r.order_id!)}
                          title={copy.drilldownTitle}
                        >
                          #{r.order_id}
                        </button>
                      ) : (
                        <small className="muted">{r.order_id ?? '—'}</small>
                      )}
                    </td>
                    <td className={r.is_multi_fill ? 'pnl-pos' : ''}>{r.fills_in_order ?? 1}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
