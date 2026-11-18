import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { api, type OptionsLabAction, type OptionsLabSignal, type PeriodFilters } from '../lib/api';
import { AttributionStrip } from './AttributionStrip';
import { ChartTooltip } from './ChartTooltip';
import { KPICard } from './KPICard';
import { QueryError } from './QueryError';
import { axisAmountFormatter, currentLocale, formatCurrency, formatNumber, isPrivacyModeEnabled } from '../lib/format';
import { useI18n } from '../lib/i18n';
import { chartTheme, seriesAnimation, useReducedMotion } from '../lib/motion';

// Opciones is a CONTROL view: what is open, what it earned, what could be
// assigned. Payoff calculators and DTE/roll/assignment-management tooling
// belong to the separate OptionsLab project.
type View = 'open' | 'underlying' | 'eae';

interface Props {
  filters: PeriodFilters;
  currency: string;
}

// Module-level helpers: locale and privacy come from the shared format flags.
function fmtCurrency(v: number | undefined | null, ccy: string): string {
  if (v === undefined || v === null || Number.isNaN(v)) return '—';
  return formatCurrency(v, ccy, currentLocale());
}

function fmtNumber(v: number | undefined | null, digits = 2): string {
  if (v === undefined || v === null || Number.isNaN(v)) return '—';
  return formatNumber(v, digits, currentLocale());
}

function fmtPercent(v: number | undefined | null): string {
  if (v === undefined || v === null || Number.isNaN(v)) return '—';
  if (isPrivacyModeEnabled()) return '--%';
  return `${formatNumber(v * 100, 1, currentLocale())}%`;
}

function fmtDate(iso: string | undefined | null): string {
  return iso ? iso.slice(0, 10) : '—';
}

function signClass(v: number | undefined | null): string {
  if (v === undefined || v === null) return '';
  return v > 0 ? 'pnl-pos' : v < 0 ? 'pnl-neg' : '';
}

function dteClass(dte: number | undefined | null): string {
  if (dte === undefined || dte === null) return '';
  if (dte <= 7) return 'pnl-neg';
  if (dte <= 30) return 'pnl-warn';
  return '';
}

const COPY = {
  es: {
    openPositions: 'Opciones abiertas',
    optionsLabel: 'Opciones',
    unrealized: 'No realizado',
    marketValue: 'Valor mercado',
    long: 'largas',
    short: 'cortas',
    trades: 'Operaciones',
    contractsDelta: 'contratos (cada apertura o cierre cuenta 1)',
    netPremium: 'Prima neta',
    netOfFees: 'neto de comisiones',
    netPremiumTitle: 'Cobros - pagos de prima (net_cash, ya neto de comisiones).',
    realizedPnl: 'P&L realizado',
    commissionsIncl: 'comisiones incl.',
    winRate: 'Tasa de acierto',
    closesOnly: 'solo cierres',
    assignmentNotional: 'Nocional de asignación',
    assignmentNotionalTitle: 'Strike x multiplicador x contratos de los puts cortos abiertos: lo que tendrías que comprar si te asignan todos.',
    noShortPuts: 'sin puts cortos abiertos',
    shortPutsDelta: '{n} puts cortos',
    intrinsicRisk: 'Intrínseco en riesgo',
    intrinsicRiskTitle: 'Valor intrínseco de los cortos in-the-money al último cierre.',
    itmShorts: '{n} cortos ITM',
    labNote: 'Calculadora de payoff, DTE, rolls y gestión de asignación viven en OptionsLab.',
    attributionTitle: 'P&L por subyacente',
    attributionSubtitle: 'Opciones, acciones y dividendos por subyacente (base {currency}).',
    underlying: 'Subyacente',
    optPnlTitle: 'Suma de P&L realizado FIFO de opciones cerradas.',
    stkPnlTitle: 'Suma de P&L realizado FIFO de acciones cerradas.',
    divsNetTitle: 'Dividendos netos (bruto + retención).',
    tabOpen: 'Abiertas',
    tabUnderlying: 'Por subyacente',
    tabEae: 'Ejercicio / asignación / expiración',
    openSubtitle: 'Último snapshot de opciones: valor de mercado, P&L no realizado y DTE por contrato.',
    openEmpty: 'No hay opciones abiertas en el último snapshot.',
    side: 'Lado',
    type: 'Tipo',
    expires: 'Vence',
    contracts: 'Contratos',
    pnlPct: 'P&L %',
    moneyness: 'Moneyness',
    optionsLabTitle: 'Señal de gestión de OptionsLab (GTC, stop por delta o por prima). Solo lectura.',
    labActions: { take_profit: 'Tomar beneficio', stop: 'Stop', warning: 'Aviso', hold: 'Mantener', review: 'Revisar', unknown: 'Desconocido' },
    labStale: 'obsoleta',
    labCapture: 'Captura',
    labShortDelta: 'Delta corto',
    labGenerated: 'Generada',
    underlyingSubtitle: 'Ordenado por |P&L realizado|. Tasa de acierto = ganadoras / (ganadoras + perdedoras) de cierres.',
    underlyingEmpty: 'Sin datos en este periodo.',
    commissions: 'Comisiones',
    eaeEmpty: 'Sin eventos de ejercicio, asignación o expiración en este periodo.',
    exercise: 'Ejercicio',
    assignment: 'Asignación',
    expiration: 'Expiración',
    cashNet: 'Efectivo neto',
    date: 'Fecha',
    contract: 'Contrato',
    price: 'Precio',
    cash: 'Efectivo',
    call: 'CALL',
    put: 'PUT',
    snapshot: 'Snapshot EOD',
  },
  en: {
    openPositions: 'Open options',
    optionsLabel: 'Options',
    unrealized: 'Unrealized',
    marketValue: 'Market value',
    long: 'long',
    short: 'short',
    trades: 'Trades',
    contractsDelta: 'contracts (each open or close counts as 1)',
    netPremium: 'Net premium',
    netOfFees: 'net of commissions',
    netPremiumTitle: 'Premium received minus premium paid (net_cash, already net of commissions).',
    realizedPnl: 'Realized P&L',
    commissionsIncl: 'incl. commissions',
    winRate: 'Win rate',
    closesOnly: 'closes only',
    assignmentNotional: 'Assignment notional',
    assignmentNotionalTitle: 'Strike x multiplier x contracts of the open short puts: what you would have to buy if every one is assigned.',
    noShortPuts: 'no open short puts',
    shortPutsDelta: '{n} short puts',
    intrinsicRisk: 'Intrinsic at risk',
    intrinsicRiskTitle: 'Intrinsic value of in-the-money shorts at the last close.',
    itmShorts: '{n} ITM shorts',
    labNote: 'The payoff calculator, DTE, rolls and assignment management live in OptionsLab.',
    attributionTitle: 'P&L by underlying',
    attributionSubtitle: 'Options, stock and dividends per underlying ({currency}).',
    underlying: 'Underlying',
    optPnlTitle: 'Sum of FIFO realized P&L on closed options.',
    stkPnlTitle: 'Sum of FIFO realized P&L on closed stock.',
    divsNetTitle: 'Net dividends (gross + withholding).',
    tabOpen: 'Open',
    tabUnderlying: 'By underlying',
    tabEae: 'Exercise / assignment / expiration',
    openSubtitle: 'Latest options snapshot: market value, unrealized P&L and DTE per contract.',
    openEmpty: 'No open option positions at the latest snapshot.',
    side: 'Side',
    type: 'Type',
    expires: 'Expires',
    contracts: 'Contracts',
    pnlPct: 'P&L %',
    moneyness: 'Moneyness',
    optionsLabTitle: 'OptionsLab management signal (GTC, delta or premium stop). Read-only.',
    labActions: { take_profit: 'Take profit', stop: 'Stop', warning: 'Warning', hold: 'Hold', review: 'Review', unknown: 'Unknown' },
    labStale: 'stale',
    labCapture: 'Capture',
    labShortDelta: 'Short delta',
    labGenerated: 'Generated',
    underlyingSubtitle: 'Sorted by |realized P&L|. Win rate = winners / (winners + losers) of closes.',
    underlyingEmpty: 'No data in this period.',
    commissions: 'Commissions',
    eaeEmpty: 'No exercise, assignment or expiration events in this period.',
    exercise: 'Exercise',
    assignment: 'Assignment',
    expiration: 'Expiration',
    cashNet: 'Net cash',
    date: 'Date',
    contract: 'Contract',
    price: 'Price',
    cash: 'Cash',
    call: 'CALL',
    put: 'PUT',
    snapshot: 'EOD snapshot',
  },
  de: {
    openPositions: 'Offene Optionen',
    optionsLabel: 'Optionen',
    unrealized: 'Unrealisiert',
    marketValue: 'Marktwert',
    long: 'long',
    short: 'short',
    trades: 'Trades',
    contractsDelta: 'Kontrakte (jedes Open oder Close zaehlt als 1)',
    netPremium: 'Netto-Praemie',
    netOfFees: 'netto nach Kommissionen',
    netPremiumTitle: 'Erhaltene minus gezahlte Praemie (net_cash, bereits netto nach Kommissionen).',
    realizedPnl: 'Realisierter P&L',
    commissionsIncl: 'inkl. Kommissionen',
    winRate: 'Trefferquote',
    closesOnly: 'nur Closes',
    assignmentNotional: 'Zuteilungs-Nominal',
    assignmentNotionalTitle: 'Strike x Multiplikator x Kontrakte der offenen Short Puts: was du kaufen muesstest, wenn alle zugeteilt werden.',
    noShortPuts: 'keine offenen Short Puts',
    shortPutsDelta: '{n} Short Puts',
    intrinsicRisk: 'Innerer Wert im Risiko',
    intrinsicRiskTitle: 'Innerer Wert der In-the-money-Shorts zum letzten Schluss.',
    itmShorts: '{n} ITM-Shorts',
    labNote: 'Payoff-Rechner, DTE, Rolls und Zuteilungs-Management liegen in OptionsLab.',
    attributionTitle: 'P&L je Underlying',
    attributionSubtitle: 'Optionen, Aktien und Dividenden je Underlying ({currency}).',
    underlying: 'Underlying',
    optPnlTitle: 'Summe des realisierten FIFO-P&L geschlossener Optionen.',
    stkPnlTitle: 'Summe des realisierten FIFO-P&L geschlossener Aktien.',
    divsNetTitle: 'Netto-Dividenden (brutto + Quellensteuer).',
    tabOpen: 'Offen',
    tabUnderlying: 'Je Underlying',
    tabEae: 'Ausuebung / Zuteilung / Verfall',
    openSubtitle: 'Letzter Options-Snapshot: Marktwert, unrealisierter P&L und DTE je Kontrakt.',
    openEmpty: 'Keine offenen Optionspositionen im letzten Snapshot.',
    side: 'Seite',
    type: 'Typ',
    expires: 'Verfall',
    contracts: 'Kontrakte',
    pnlPct: 'P&L %',
    moneyness: 'Moneyness',
    optionsLabTitle: 'OptionsLab-Management-Signal (GTC, Delta- oder Praemien-Stop). Nur lesend.',
    labActions: { take_profit: 'Gewinn mitnehmen', stop: 'Stop', warning: 'Warnung', hold: 'Halten', review: 'Pruefen', unknown: 'Unbekannt' },
    labStale: 'veraltet',
    labCapture: 'Erfasst',
    labShortDelta: 'Short-Delta',
    labGenerated: 'Erstellt',
    underlyingSubtitle: 'Sortiert nach |realisiertem P&L|. Trefferquote = Gewinner / (Gewinner + Verlierer) der Closes.',
    underlyingEmpty: 'Keine Daten in diesem Zeitraum.',
    commissions: 'Kommissionen',
    eaeEmpty: 'Keine Ausuebungs-, Zuteilungs- oder Verfallsereignisse in diesem Zeitraum.',
    exercise: 'Ausuebung',
    assignment: 'Zuteilung',
    expiration: 'Verfall',
    cashNet: 'Netto-Cash',
    date: 'Datum',
    contract: 'Kontrakt',
    price: 'Preis',
    cash: 'Cash',
    call: 'CALL',
    put: 'PUT',
    snapshot: 'EOD-Snapshot',
  },
} as const;

type Copy = (typeof COPY)[keyof typeof COPY];

// OptionsLab action -> existing badge tone. Stale signals always render muted.
const LAB_BADGE: Record<OptionsLabAction, string> = {
  take_profit: 'pos',
  stop: 'neg',
  warning: 'warn',
  review: 'open',
  hold: 'closed',
  unknown: 'closed',
};

function labTooltip(sig: OptionsLabSignal, copy: Copy): string {
  return [
    `${copy.labActions[sig.action]}${sig.status_text ? `: ${sig.status_text}` : ''}`,
    `${copy.labCapture} ${fmtPercent(sig.capture_pct === null ? null : sig.capture_pct / 100)}`,
    `${copy.labShortDelta} ${fmtNumber(sig.short_delta)}`,
    `DTE ${sig.dte ?? '—'}`,
    `${copy.labGenerated} ${sig.generated_at.slice(0, 16).replace('T', ' ')} UTC${sig.stale ? ` (${copy.labStale})` : ''}`,
  ].join('\n');
}

export function OptionsPanel({ filters, currency }: Props) {
  const { language } = useI18n();
  const copy = COPY[language];
  const reduced = useReducedMotion();
  const theme = chartTheme();
  const [view, setView] = useState<View>('open');

  // Always-on queries (3): summary, open positions, P&L attribution.
  const summary = useQuery({
    queryKey: ['options.summary', filters],
    queryFn: () => api.optionsSummary(filters),
  });
  const openPositions = useQuery({
    queryKey: ['options.openPositions', filters.account_id],
    queryFn: () => api.optionsOpenPositions({ account_id: filters.account_id }),
  });
  const attribution = useQuery({
    queryKey: ['options.attribution', filters],
    queryFn: () => api.optionsPnlAttribution(filters),
  });
  // One more per sub-tab, fetched only while that tab is open.
  const byUnderlying = useQuery({
    queryKey: ['options.byUnderlying', filters],
    queryFn: () => api.optionsByUnderlying(filters),
    enabled: view === 'underlying',
  });
  const eae = useQuery({
    queryKey: ['options.eae', filters],
    queryFn: () => api.optionsEae(filters),
    enabled: view === 'eae',
  });

  const s = summary.data;
  const open = openPositions.data;
  // Assignment notional: every open short put assigned at its strike.
  const shortPuts = (open?.rows ?? []).filter((r) => r.side === 'short' && r.right === 'P');
  const assignmentNotional = shortPuts.reduce(
    (sum, r) => sum + (r.strike ?? 0) * (r.multiplier ?? 100) * Math.abs(r.contracts ?? 0),
    0,
  );
  const tabs: Array<{ key: View; label: string }> = [
    { key: 'open', label: copy.tabOpen },
    { key: 'underlying', label: copy.tabUnderlying },
    { key: 'eae', label: copy.tabEae },
  ];

  return (
    <div className="trades-panel">
      <div className="kpi-grid" style={{ marginBottom: 12 }}>
        <KPICard
          label={copy.openPositions}
          value={open ? `${open.summary.positions}` : '—'}
          delta={open ? `${open.summary.long_positions} ${copy.long} · ${open.summary.short_positions} ${copy.short}` : undefined}
          loading={openPositions.isLoading}
        />
        <KPICard
          label={copy.unrealized}
          value={open ? fmtCurrency(open.summary.unrealized_pnl_base, currency) : '—'}
          delta={open ? `${copy.marketValue} ${fmtCurrency(open.summary.net_position_value_base, currency)}` : undefined}
          deltaKind={open && open.summary.unrealized_pnl_base !== 0 ? (open.summary.unrealized_pnl_base > 0 ? 'pos' : 'neg') : 'neutral'}
          loading={openPositions.isLoading}
        />
        <KPICard
          label={copy.trades}
          value={s ? `${s.trades.total}` : '—'}
          delta={s ? `${s.trades.contracts_traded} ${copy.contractsDelta}` : undefined}
          loading={summary.isLoading}
        />
        <KPICard
          label={copy.netPremium}
          value={s ? fmtCurrency(s.trades.net_premium_base, currency) : '—'}
          title={copy.netPremiumTitle}
          delta={s ? copy.netOfFees : undefined}
          deltaKind={s ? (s.trades.net_premium_base >= 0 ? 'pos' : 'neg') : 'neutral'}
          loading={summary.isLoading}
        />
        <KPICard
          label={copy.realizedPnl}
          value={s ? fmtCurrency(s.trades.realized_pnl_base, currency) : '—'}
          delta={s ? `${copy.commissionsIncl} ${fmtCurrency(s.trades.commissions_base, currency)}` : undefined}
          deltaKind={s ? (s.trades.realized_pnl_base >= 0 ? 'pos' : 'neg') : 'neutral'}
          loading={summary.isLoading}
        />
        <KPICard
          label={copy.winRate}
          value={s ? fmtPercent(s.trades.win_rate) : '—'}
          delta={s ? `${s.trades.win}W · ${s.trades.loss}L · ${s.trades.be}BE · n=${s.trades.win + s.trades.loss + s.trades.be} ${copy.closesOnly}` : undefined}
          loading={summary.isLoading}
        />
        <KPICard
          label={copy.assignmentNotional}
          title={copy.assignmentNotionalTitle}
          value={open ? fmtCurrency(assignmentNotional, currency) : '—'}
          delta={shortPuts.length > 0 ? copy.shortPutsDelta.replace('{n}', String(shortPuts.length)) : copy.noShortPuts}
          deltaKind={shortPuts.length > 0 ? 'warn' : 'neutral'}
          loading={openPositions.isLoading}
        />
        <KPICard
          label={copy.intrinsicRisk}
          title={copy.intrinsicRiskTitle}
          value={s ? fmtCurrency(s.open_risk.intrinsic_at_risk_base, currency) : '—'}
          delta={s ? copy.itmShorts.replace('{n}', String(s.open_risk.itm)) : undefined}
          deltaKind={s && s.open_risk.itm > 0 ? 'neg' : 'neutral'}
          loading={summary.isLoading}
        />
      </div>
      {summary.isError ? <QueryError endpoint="/options/summary" onRetry={() => summary.refetch()} /> : null}
      <p className="muted" style={{ margin: '0 0 16px', fontSize: 12 }}>{copy.labNote}</p>

      {attribution.isError ? (
        <QueryError endpoint="/options/pnl-attribution" onRetry={() => attribution.refetch()} />
      ) : attribution.data && (attribution.data.totals.grand_total_base !== 0 || attribution.data.by_underlying.length > 0) ? (
        <div className="chart-panel" style={{ marginBottom: 16 }}>
          <div className="panel-header">
            <div className="panel-title">{copy.attributionTitle}</div>
            <small className="muted">
              Total <strong>{fmtCurrency(attribution.data.totals.grand_total_base, currency)}</strong> ·{' '}
              {copy.attributionSubtitle.replace('{currency}', currency)}
            </small>
          </div>
          <div style={{ marginBottom: 12 }}>
            <AttributionStrip totals={attribution.data.totals} currency={currency} />
          </div>
          {attribution.data.by_underlying.length > 0 ? (
            <div className="table-scroll">
              <table className="data-table trades-table">
                <thead>
                  <tr>
                    <th>{copy.underlying}</th>
                    <th title={copy.optPnlTitle}>{copy.optionsLabel} P&L</th>
                    <th title={copy.stkPnlTitle}>STK P&L</th>
                    <th title={copy.divsNetTitle}>Divs net</th>
                    <th>Total</th>
                  </tr>
                </thead>
                <tbody>
                  {attribution.data.by_underlying.map((r) => (
                    <tr key={r.underlying}>
                      <td><span className="position-symbol">{r.underlying}</span></td>
                      <td className={signClass(r.opt_pnl_base)}>{fmtCurrency(r.opt_pnl_base, currency)}</td>
                      <td className={signClass(r.stk_pnl_base)}>{fmtCurrency(r.stk_pnl_base, currency)}</td>
                      <td title={`gross ${fmtCurrency(r.dividends_gross_base, currency)} · WHT ${fmtCurrency(r.withholding_base, currency)}`}>
                        {fmtCurrency(r.dividends_net_base, currency)}
                      </td>
                      <td className={signClass(r.total_base)}><strong>{fmtCurrency(r.total_base, currency)}</strong></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </div>
      ) : null}

      <div className="filter-chip-grid" style={{ marginBottom: 12 }} role="group">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            type="button"
            data-testid={`options-subtab-${tab.key}`}
            aria-pressed={view === tab.key}
            className={view === tab.key ? 'active' : ''}
            onClick={() => setView(tab.key)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {view === 'open' && (
        <div>
          <div className="panel-header">
            <div className="panel-title">{copy.openPositions}</div>
            <small className="muted">{copy.openSubtitle} {copy.snapshot} {open?.snapshot_date ?? '—'}.</small>
          </div>
          {openPositions.isLoading ? (
            <div className="trades-empty">…</div>
          ) : openPositions.isError ? (
            <QueryError endpoint="/options/open-positions" onRetry={() => openPositions.refetch()} />
          ) : !open?.rows.length ? (
            <div className="trades-empty">{copy.openEmpty}</div>
          ) : (
            <div className="table-scroll">
              <table className="data-table trades-table">
                <thead>
                  <tr>
                    <th>{copy.underlying}</th>
                    <th>{copy.side}</th>
                    <th>{copy.type}</th>
                    <th>Strike</th>
                    <th>{copy.expires}</th>
                    <th>DTE</th>
                    <th>{copy.contracts}</th>
                    <th>Mark</th>
                    <th>{copy.marketValue}</th>
                    <th>{copy.unrealized}</th>
                    <th>{copy.pnlPct}</th>
                    <th>{copy.moneyness}</th>
                    <th title={copy.optionsLabTitle}>OptionsLab</th>
                  </tr>
                </thead>
                <tbody>
                  {open.rows.map((r) => (
                    <tr key={`${r.symbol}|${r.quantity}`}>
                      <td>
                        <span className="position-symbol">{r.underlying ?? r.symbol}</span>
                        <small>{r.symbol}</small>
                      </td>
                      <td>
                        <span className={`trade-badge ${r.side === 'long' ? 'open' : 'closed'}`}>{r.side === 'long' ? copy.long : copy.short}</span>
                      </td>
                      <td>
                        <span className={`trade-badge ${r.right === 'C' ? 'open' : 'closed'}`}>
                          {r.right === 'C' ? copy.call : r.right === 'P' ? copy.put : '—'}
                        </span>
                      </td>
                      <td className="num">{fmtNumber(r.strike)}</td>
                      <td>{fmtDate(r.expiry)}</td>
                      <td className={`num ${dteClass(r.dte)}`}>{r.dte ?? '—'}</td>
                      <td className="num">{fmtNumber(r.contracts, 0)}</td>
                      <td className="num">{fmtNumber(r.mark_price, 4)}</td>
                      <td className={`num ${signClass(r.position_value_base)}`}>{fmtCurrency(r.position_value_base, currency)}</td>
                      <td className={`num ${signClass(r.fifo_pnl_unrealized_base)}`}>{fmtCurrency(r.fifo_pnl_unrealized_base, currency)}</td>
                      <td className={`num ${signClass(r.unrealized_pct)}`}>{fmtPercent(r.unrealized_pct)}</td>
                      <td className={`num ${signClass(r.moneyness_pct)}`}>{fmtPercent(r.moneyness_pct)}</td>
                      <td>
                        {r.optionslab ? (
                          <span
                            className={`trade-badge ${r.optionslab.stale ? 'closed' : LAB_BADGE[r.optionslab.action]}`}
                            title={labTooltip(r.optionslab, copy)}
                          >
                            {r.optionslab.stale ? copy.labStale : copy.labActions[r.optionslab.action]}
                          </span>
                        ) : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {view === 'underlying' && (
        <div>
          <div className="panel-header">
            <div className="panel-title">{copy.tabUnderlying}</div>
            <small className="muted">{copy.underlyingSubtitle}</small>
          </div>
          {byUnderlying.isLoading ? (
            <div className="trades-empty">…</div>
          ) : byUnderlying.isError ? (
            <QueryError endpoint="/options/by-underlying" onRetry={() => byUnderlying.refetch()} />
          ) : !byUnderlying.data?.rows.length ? (
            <div className="trades-empty">{copy.underlyingEmpty}</div>
          ) : (
            <>
              <ResponsiveContainer width="100%" height={Math.max(180, 30 * byUnderlying.data.rows.length + 40)}>
                <BarChart data={byUnderlying.data.rows} layout="vertical" margin={{ left: 20, right: 20 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={theme.grid} />
                  <XAxis type="number" stroke={theme.axis} fontSize={11} tickFormatter={axisAmountFormatter(currentLocale())} />
                  <YAxis type="category" dataKey="underlying" stroke={theme.axis} fontSize={11} width={70} />
                  <Tooltip
                    cursor={{ fill: theme.cursorFill }}
                    content={<ChartTooltip formatter={(v, n) => `${n}: ${typeof v === 'number' ? fmtCurrency(v, currency) : v}`} />}
                  />
                  <Bar dataKey="realized_pnl_base" name={copy.realizedPnl} {...seriesAnimation('bar', reduced)}>
                    {byUnderlying.data.rows.map((r) => (
                      <Cell key={r.underlying} fill={r.realized_pnl_base >= 0 ? theme.positive : theme.negative} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
              <div className="table-scroll" style={{ marginTop: 12 }}>
                <table className="data-table trades-table">
                  <thead>
                    <tr>
                      <th>{copy.underlying}</th>
                      <th>{copy.trades}</th>
                      <th>{copy.contracts}</th>
                      <th>{copy.netPremium}</th>
                      <th>{copy.realizedPnl}</th>
                      <th>{copy.commissions}</th>
                      <th>{copy.winRate}</th>
                      <th>W · L · BE</th>
                    </tr>
                  </thead>
                  <tbody>
                    {byUnderlying.data.rows.map((r) => (
                      <tr key={r.underlying}>
                        <td><span className="position-symbol">{r.underlying}</span></td>
                        <td className="num">{r.trades}</td>
                        <td className="num">{r.contracts}</td>
                        <td className={`num ${r.net_premium_base >= 0 ? 'pnl-pos' : 'pnl-neg'}`}>{fmtCurrency(r.net_premium_base, currency)}</td>
                        <td className={`num ${signClass(r.realized_pnl_base)}`}>{fmtCurrency(r.realized_pnl_base, currency)}</td>
                        <td className="num pnl-neg">{fmtCurrency(r.commissions_base, currency)}</td>
                        <td className="num">{fmtPercent(r.win_rate)}</td>
                        <td><small>{r.win} · {r.loss} · {r.be}</small></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      )}

      {view === 'eae' && (
        <div>
          <div className="panel-header">
            <div className="panel-title">{copy.tabEae}</div>
            <small className="muted">
              {eae.data ? (
                <>
                  {copy.exercise}: <strong>{eae.data.summary.counts.exercise ?? 0}</strong> ·{' '}
                  {copy.assignment}: <strong>{eae.data.summary.counts.assignment ?? 0}</strong> ·{' '}
                  {copy.expiration}: <strong>{eae.data.summary.counts.expiration ?? 0}</strong> ·{' '}
                  {copy.cashNet} {fmtCurrency(eae.data.summary.cash_impact_total, currency)}
                </>
              ) : null}
            </small>
          </div>
          {eae.isLoading ? (
            <div className="trades-empty">…</div>
          ) : eae.isError ? (
            <QueryError endpoint="/options/eae" onRetry={() => eae.refetch()} />
          ) : !eae.data?.rows.length ? (
            <div className="trades-empty">{copy.eaeEmpty}</div>
          ) : (
            <div className="table-scroll">
              <table className="data-table trades-table">
                <thead>
                  <tr>
                    <th>{copy.date}</th>
                    <th>{copy.type}</th>
                    <th>{copy.underlying}</th>
                    <th>{copy.contract}</th>
                    <th>Right</th>
                    <th>Strike</th>
                    <th>{copy.expires}</th>
                    <th>Qty</th>
                    <th>{copy.price}</th>
                    <th>{copy.cash}</th>
                    <th>P&L</th>
                  </tr>
                </thead>
                <tbody>
                  {eae.data.rows.map((r) => (
                    <tr key={r.eae_id}>
                      <td>{fmtDate(r.event_date)}</td>
                      <td>{r.transaction_type ?? '—'}</td>
                      <td><span className="position-symbol">{r.underlying ?? '—'}</span></td>
                      <td><small>{r.symbol}</small></td>
                      <td>{r.right ?? '—'}</td>
                      <td className="num">{fmtNumber(r.strike)}</td>
                      <td>{fmtDate(r.expiry)}</td>
                      <td className="num">{r.quantity}</td>
                      <td className="num">{fmtNumber(r.trade_price)}</td>
                      <td className={`num ${(r.proceeds ?? 0) >= 0 ? 'pnl-pos' : 'pnl-neg'}`}>{fmtNumber(r.proceeds)}</td>
                      <td className={`num ${signClass(r.realized_pnl)}`}>
                        {r.realized_pnl !== undefined && r.realized_pnl !== null ? fmtNumber(r.realized_pnl) : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
