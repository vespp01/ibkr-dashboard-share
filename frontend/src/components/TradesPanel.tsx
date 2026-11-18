import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { api, type PeriodFilters } from '../lib/api';
import { ExecutionsPanel } from './ExecutionsPanel';
import { formatCurrency } from '../lib/format';
import { useI18n } from '../lib/i18n';

type Status = 'all' | 'open' | 'closed';
type View = 'trades' | 'executions';

interface Props {
  filters: PeriodFilters;
  currency: string;
}

const COPY = {
  es: {
    trades: 'Trades',
    executions: 'Ejecuciones',
    panelTitle: 'Operaciones',
    all: 'All',
    open: 'Open',
    closed: 'Closed',
    symbolTitle: 'Filtro exacto en servidor (todas las páginas). Las sugerencias salen de la página cargada; puedes escribir cualquier símbolo.',
    strategyTitle: 'Filtro de estrategia en el servidor (todas las páginas)',
    win: 'win',
    loss: 'loss',
    loading: 'Cargando...',
    error: 'Error cargando operaciones: revisa el backend.',
    empty: 'No hay operaciones en este periodo / filtro. Si la base de datos está recién creada, ejecuta el bootstrap o un pull del pipeline.',
    date: 'Fecha',
    symbol: 'Símbolo',
    strategy: 'Estrategia',
    side: 'Lado',
    price: 'Precio',
    commission: 'Comisión',
    retainedDividends: 'Div. retenidos',
    retainedTitle: 'Solo en operaciones de cierre: dividendos cobrados desde el cierre anterior del símbolo (o su primer BUY) hasta este cierre. Aprox.; la atribución por lote requiere FIFO parsing.',
    totalReturnTitle: 'P&L + dividendos cobrados durante el holding',
    status: 'Estado',
    openBadge: 'Abierta',
    closedBadge: 'Cerrada',
    from: 'Desde',
    previous: 'Anterior',
    next: 'Siguiente',
    of: 'de',
    operations: 'operaciones',
    openTrades: 'abiertas',
    closedTrades: 'cerradas',
  },
  en: {
    trades: 'Trades',
    executions: 'Executions',
    panelTitle: 'Trades',
    all: 'All',
    open: 'Open',
    closed: 'Closed',
    symbolTitle: 'Exact server-side filter across all pages. Suggestions come from the loaded page; you can type any symbol.',
    strategyTitle: 'Server-side strategy filter (all pages)',
    win: 'win',
    loss: 'loss',
    loading: 'Loading...',
    error: 'Error loading trades: check the backend.',
    empty: 'No trades in this period/filter. If the database is new, run bootstrap or a pipeline pull.',
    date: 'Date',
    symbol: 'Symbol',
    strategy: 'Strategy',
    side: 'Side',
    price: 'Price',
    commission: 'Commission',
    retainedDividends: 'Held divs',
    retainedTitle: 'Closing trades only: dividends collected since the symbol\'s previous close (or its first BUY) up to this close. Approximate; lot attribution requires FIFO parsing.',
    totalReturnTitle: 'P&L + dividends collected during the holding period',
    status: 'Status',
    openBadge: 'Open',
    closedBadge: 'Closed',
    from: 'From',
    previous: 'Previous',
    next: 'Next',
    of: 'of',
    operations: 'trades',
    openTrades: 'open',
    closedTrades: 'closed',
  },
  de: {
    trades: 'Trades',
    executions: 'Ausfuehrungen',
    panelTitle: 'Trades',
    all: 'Alle',
    open: 'Offen',
    closed: 'Geschlossen',
    symbolTitle: 'Exakter serverseitiger Filter ueber alle Seiten. Vorschlaege stammen aus der geladenen Seite; jedes Symbol kann eingegeben werden.',
    strategyTitle: 'Serverseitiger Strategie-Filter (alle Seiten)',
    win: 'Gewinn',
    loss: 'Verlust',
    loading: 'Laden...',
    error: 'Fehler beim Laden der Trades: Backend pruefen.',
    empty: 'Keine Trades in diesem Zeitraum/Filter. Bei neuer Datenbank Bootstrap oder Pipeline-Pull ausfuehren.',
    date: 'Datum',
    symbol: 'Symbol',
    strategy: 'Strategie',
    side: 'Seite',
    price: 'Preis',
    commission: 'Kommission',
    retainedDividends: 'Gehaltene Divs',
    retainedTitle: 'Nur bei Schluss-Trades: Dividenden seit dem vorherigen Schluss des Symbols (oder seinem ersten BUY) bis zu diesem Schluss. Approximiert; Lot-Attribution benoetigt FIFO Parsing.',
    totalReturnTitle: 'P&L + Dividenden waehrend der Haltedauer',
    status: 'Status',
    openBadge: 'Offen',
    closedBadge: 'Geschlossen',
    from: 'Seit',
    previous: 'Zurueck',
    next: 'Weiter',
    of: 'von',
    operations: 'Trades',
    openTrades: 'offen',
    closedTrades: 'geschlossen',
  },
} as const;

function fmtCurrency(v: number | undefined, ccy: string, locale: string): string {
  if (v === undefined || v === null || Number.isNaN(v)) return '—';
  return formatCurrency(v, ccy, locale);
}

function fmtDate(iso: string | undefined): string {
  if (!iso) return '—';
  return iso.slice(0, 10);
}

function fmtTime(iso: string | undefined): string {
  if (!iso || iso.length < 19) return '';
  return iso.slice(11, 16);
}

export function TradesPanel({ filters, currency }: Props) {
  const { language, locale } = useI18n();
  const copy = COPY[language];
  const [view, setView] = useState<View>('trades');
  const [status, setStatus] = useState<Status>('all');
  const [symbol, setSymbol] = useState<string>('');
  // The ticker box refetches on every keystroke otherwise: debounce 300 ms.
  const [debouncedSymbol, setDebouncedSymbol] = useState<string>('');
  useEffect(() => {
    const id = window.setTimeout(() => setDebouncedSymbol(symbol), 300);
    return () => window.clearTimeout(id);
  }, [symbol]);
  const [strategy, setStrategy] = useState<string>('');
  const [offset, setOffset] = useState<number>(0);
  const limit = 50;

  // The filters prop is a fresh object each App render — key the reset on its
  // serialized value so paging returns to page 1 when the period/year changes.
  // Adjust-during-render (React docs pattern) rather than an effect.
  const filtersKey = JSON.stringify(filters);
  const [seenFiltersKey, setSeenFiltersKey] = useState(filtersKey);
  if (filtersKey !== seenFiltersKey) {
    setSeenFiltersKey(filtersKey);
    setOffset(0);
  }

  const query = useQuery({
    queryKey: ['trades', filtersKey, status, debouncedSymbol, strategy, limit, offset],
    queryFn: () => api.trades({
      ...filters,
      status,
      ...(debouncedSymbol ? { symbol: debouncedSymbol } : {}),
      ...(strategy ? { strategy } : {}),
      limit,
      offset,
    }),
    placeholderData: keepPreviousData,
  });

  const rows = useMemo(() => query.data?.rows ?? [], [query.data?.rows]);
  const summary = query.data?.summary;

  // Symbol suggestions + strategy options come from the loaded page only —
  // there is no backend endpoint exposing the full symbol universe, so the
  // ticker filter is a free-text input (exact server-side match) with
  // datalist suggestions instead of a closed dropdown.
  const symbols = useMemo(() => {
    const set = new Set<string>();
    rows.forEach((r) => set.add(r.symbol));
    return Array.from(set).sort();
  }, [rows]);

  // Selectable strategies come from the server (computed before the strategy
  // filter, so the list does not collapse once one is picked).
  const strategies = query.data?.strategies ?? [];
  const filteredRows = rows;

  if (view === 'executions') {
    return (
      <>
        <div className="filter-chip-grid" style={{ marginBottom: 8 }}>
          <button onClick={() => setView('trades')}>{copy.trades}</button>
          <button className="active" onClick={() => setView('executions')}>{copy.executions}</button>
        </div>
        <ExecutionsPanel filters={filters} currency={currency} />
      </>
    );
  }

  return (
    <div className="trades-panel">
      <div className="filter-chip-grid" style={{ marginBottom: 8 }}>
        <button className="active" onClick={() => setView('trades')}>{copy.trades}</button>
        <button onClick={() => setView('executions')}>{copy.executions}</button>
      </div>
      <div className="panel-header">
        <div className="panel-title">{copy.panelTitle}</div>
        <div className="trades-filters">
          <div className="filter-chip-grid">
            <button className={status === 'all' ? 'active' : ''} onClick={() => { setStatus('all'); setOffset(0); }}>{copy.all}</button>
            <button className={status === 'open' ? 'active' : ''} onClick={() => { setStatus('open'); setOffset(0); }}>{copy.open}</button>
            <button className={status === 'closed' ? 'active' : ''} onClick={() => { setStatus('closed'); setOffset(0); }}>{copy.closed}</button>
          </div>
          <input
            className="trades-select trades-symbol-input"
            list="trades-symbol-options"
            placeholder="Ticker"
            value={symbol}
            onChange={(e) => { setSymbol(e.target.value); setOffset(0); }}
            title={copy.symbolTitle}
          />
          <datalist id="trades-symbol-options">
            {symbols.map((s) => <option key={s} value={s} />)}
          </datalist>
          <select
            value={strategy}
            onChange={(e) => { setStrategy(e.target.value); setOffset(0); }}
            className="trades-select"
            title={copy.strategyTitle}
          >
            <option value="">Strategy ▾</option>
            {strategies.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
      </div>

      {summary ? (
        <div className="trades-summary-line">
          <strong>{summary.open_count}</strong> {copy.openTrades}
          <span className="dot">·</span>
          <strong>{summary.closed_count}</strong> {copy.closedTrades}
          <span className="dot">(</span>
          <span className="pos"><strong>{summary.win}</strong> {copy.win}</span>
          <span className="dot">·</span>
          <span className="neg"><strong>{summary.loss}</strong> {copy.loss}</span>
          <span className="dot">·</span>
          <span className="muted"><strong>{summary.be}</strong> BE</span>
          <span className="dot">)</span>
          <span className="dot">·</span>
          <span className={summary.realized_total_base !== undefined && summary.realized_total_base >= 0 ? 'pos' : 'neg'}>
            <strong>
              {summary.realized_total_base !== undefined && summary.realized_total_base >= 0 ? '+' : ''}
              {fmtCurrency(summary.realized_total_base, currency, locale)}
            </strong>
          </span>
        </div>
      ) : null}

      {query.isLoading ? (
        <div className="trades-empty">{copy.loading}</div>
      ) : query.isError ? (
        <div className="trades-empty error-state">{copy.error}</div>
      ) : filteredRows.length === 0 ? (
        <div className="trades-empty">{copy.empty}</div>
      ) : (
        <div className="table-scroll">
          <table className="data-table trades-table">
            <thead>
              <tr>
                <th>{copy.date}</th>
                <th>{copy.symbol}</th>
                <th>{copy.strategy}</th>
                <th>{copy.side}</th>
                <th>Qty</th>
                <th>{copy.price}</th>
                <th>Proceeds</th>
                <th>{copy.commission}</th>
                <th>P&L</th>
                <th title={copy.retainedTitle}>{copy.retainedDividends}</th>
                <th title={copy.totalReturnTitle}>Total Return</th>
                <th>{copy.status}</th>
              </tr>
            </thead>
            <tbody>
              {filteredRows.map((r) => {
                const pnl = r.fifo_pnl_realized_base;
                const divs = r.dividends_during_hold_base;
                const total = r.total_return_base;
                return (
                  <tr key={r.trade_id}>
                    <td>
                      <span className="position-symbol">{fmtDate(r.trade_date)}</span>
                      <small>{fmtTime(r.trade_datetime)}</small>
                    </td>
                    <td>
                      <span className="position-symbol">{r.symbol}</span>
                      <small>{r.currency} · {r.asset_class}</small>
                    </td>
                    <td title={r.is_multi_leg ? `${r.strategy_legs} legs sharing order_id ${r.ib_order_id ?? ''}` : undefined}>
                      {r.is_multi_leg ? (
                        <>
                          <span className="trade-badge open" style={{ marginRight: 6, fontSize: '0.7rem' }}>
                            {r.strategy_legs}L
                          </span>
                          {r.strategy}
                        </>
                      ) : (
                        r.strategy
                      )}
                    </td>
                    <td className={r.buy_sell === 'BUY' ? 'pnl-pos' : r.buy_sell === 'SELL' ? 'pnl-neg' : ''}>{r.buy_sell}</td>
                    <td>{r.quantity?.toLocaleString(locale)}</td>
                    <td>{r.trade_price?.toLocaleString(locale, { minimumFractionDigits: 2 })}</td>
                    <td>{fmtCurrency(r.proceeds_base, currency, locale)}</td>
                    <td className="pnl-neg">{r.commission_base !== undefined ? fmtCurrency(r.commission_base, currency, locale) : '—'}</td>
                    <td className={pnl !== undefined ? (pnl > 0 ? 'pnl-pos' : pnl < 0 ? 'pnl-neg' : '') : ''}>
                      {pnl !== undefined && pnl !== 0
                        ? `${pnl > 0 ? '+' : ''}${fmtCurrency(pnl, currency, locale)}`
                        : (r.open_close === 'O' ? '—' : fmtCurrency(0, currency, locale))}
                    </td>
                    <td className={divs && divs > 0 ? 'pnl-pos' : ''} title={r.hold_start ? `${copy.from} ${r.hold_start}` : undefined}>
                      {divs && divs > 0 ? `+${fmtCurrency(divs, currency, locale)}` : '—'}
                    </td>
                    <td className={total !== undefined && total !== null ? (total > 0 ? 'pnl-pos' : total < 0 ? 'pnl-neg' : '') : ''}>
                      {total !== undefined && total !== null && total !== 0
                        ? `${total > 0 ? '+' : ''}${fmtCurrency(total, currency, locale)}`
                        : '—'}
                    </td>
                    <td>
                      <span className={`trade-badge ${r.is_open ? 'open' : 'closed'}`}>
                        {r.is_open ? copy.openBadge : copy.closedBadge}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {summary && summary.total > limit ? (
        <div className="trades-pagination">
          <button
            type="button"
            disabled={offset === 0 || query.isFetching}
            onClick={() => setOffset(Math.max(0, offset - limit))}
          >
            ‹ {copy.previous}
          </button>
          <span className="trades-pagination-info">
            {rows.length > 0 ? `${offset + 1}–${offset + rows.length}` : '0'} {copy.of} {summary.total} {copy.operations}
          </span>
          <button
            type="button"
            disabled={offset + limit >= summary.total || query.isFetching}
            onClick={() => setOffset(offset + limit)}
          >
            {copy.next} ›
          </button>
        </div>
      ) : null}
    </div>
  );
}
