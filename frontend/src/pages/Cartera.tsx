import { useState } from 'react';
import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { Bar, BarChart, CartesianGrid, Cell, LabelList, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { api, type PeriodFilters } from '../lib/api';
import { ChartTooltip } from '../components/ChartTooltip';
import { MissingData } from '../components/MissingData';
import { formatCompactCurrency, formatCurrency, formatPercent } from '../lib/format';
import { useI18n } from '../lib/i18n';

const PIE_COLORS = [
  '#a855f7', '#ec4899', '#22d3ee', '#10b981', '#f59e0b', '#f87171',
  '#8b5cf6', '#c084fc', '#fb7185', '#60a5fa', '#facc15', '#34d399',
];

type SortDir = 'asc' | 'desc';
type PositionSortKey = 'symbol' | 'cost' | 'value' | 'pnl' | 'pnl_pct' | 'pct_nav';
type AttributionSortKey = 'symbol' | 'realized_st' | 'realized_lt' | 'unrealized' | 'mtm_total' | 'commissions' | 'total';

const COPY = {
  es: {
    unavailableTitle: 'Cartera no disponible',
    unavailableBody: 'El backend devolvió un error al cargar las posiciones.',
    valueVsCapital: 'Valor actual sobre capital puesto',
    capitalInvested: 'Capital puesto',
    valueInGain: 'Valor en ganancia',
    valueInLoss: 'Valor en pérdida',
    loadingPositions: 'Cargando posiciones...',
    noPositions: 'Sin posiciones todavía: ejecuta el bootstrap o un pull del pipeline.',
    top12Shown: 'Top 12 mostrado',
    percentOfTotal: '% del total',
    restPortfolio: 'Resto cartera',
    currentValue: 'Valor actual',
    distribution: 'Distribución de todas las posiciones',
    currentPositions: 'Posiciones actuales',
    symbol: 'Símbolo',
    topMovers: 'Top movers',
    symbols: 'símbolos',
    heldOnly: 'Solo actuales',
    showAll: 'Todo',
    loadingAttribution: 'Cargando atribución...',
    attributionError: 'No se pudo cargar la atribución por símbolo. Reintenta o cambia de periodo.',
    attributionEmpty: 'Sin datos FIFO/MTM para este periodo. Activity Flex repobla estas filas a diario; revisa /data/coverage.',
    commissions: 'Comisiones',
  },
  en: {
    unavailableTitle: 'Portfolio unavailable',
    unavailableBody: 'The backend returned an error while loading positions.',
    valueVsCapital: 'Current value vs invested capital',
    capitalInvested: 'Invested capital',
    valueInGain: 'Value with gains',
    valueInLoss: 'Value with losses',
    loadingPositions: 'Loading positions...',
    noPositions: 'No positions yet: run bootstrap or a pipeline pull.',
    top12Shown: 'Top 12 shown',
    percentOfTotal: '% of total',
    restPortfolio: 'Rest of portfolio',
    currentValue: 'Current value',
    distribution: 'All positions distribution',
    currentPositions: 'Current positions',
    symbol: 'Symbol',
    topMovers: 'Top movers',
    symbols: 'symbols',
    heldOnly: 'Current only',
    showAll: 'All',
    loadingAttribution: 'Loading attribution...',
    attributionError: 'Could not load symbol attribution. Retry or change the period.',
    attributionEmpty: 'No FIFO/MTM data for this period. Activity Flex refreshes these rows daily; check /data/coverage.',
    commissions: 'Fees',
  },
  de: {
    unavailableTitle: 'Portfolio nicht verfuegbar',
    unavailableBody: 'Das Backend hat beim Laden der Positionen einen Fehler geliefert.',
    valueVsCapital: 'Aktueller Wert vs investiertes Kapital',
    capitalInvested: 'Investiertes Kapital',
    valueInGain: 'Wert mit Gewinn',
    valueInLoss: 'Wert mit Verlust',
    loadingPositions: 'Positionen werden geladen...',
    noPositions: 'Noch keine Positionen: Bootstrap oder Pipeline-Pull ausfuehren.',
    top12Shown: 'Top 12 angezeigt',
    percentOfTotal: '% vom Total',
    restPortfolio: 'Rest des Portfolios',
    currentValue: 'Aktueller Wert',
    distribution: 'Verteilung aller Positionen',
    currentPositions: 'Aktuelle Positionen',
    symbol: 'Symbol',
    topMovers: 'Top movers',
    symbols: 'Symbole',
    heldOnly: 'Nur aktuelle',
    showAll: 'Alle',
    loadingAttribution: 'Attribution wird geladen...',
    attributionError: 'Symbol-Attribution konnte nicht geladen werden. Wiederhole den Versuch oder aendere den Zeitraum.',
    attributionEmpty: 'Keine FIFO/MTM-Daten fuer diesen Zeitraum. Activity Flex aktualisiert diese Zeilen taeglich; pruefe /data/coverage.',
    commissions: 'Gebuehren',
  },
} as const;

interface Props {
  filters: PeriodFilters;
  currency: string;
  positions: UseQueryResult<any[] | undefined>;
}

export function CarteraPage({ filters, currency, positions }: Props) {
  const { language, locale } = useI18n();
  const copy = COPY[language];

  const [positionSort, setPositionSort] = useState<{ key: PositionSortKey; dir: SortDir }>({ key: 'value', dir: 'desc' });
  const togglePositionSort = (key: PositionSortKey) => {
    setPositionSort((prev) => {
      if (prev.key !== key) return { key, dir: key === 'symbol' ? 'asc' : 'desc' };
      return { key, dir: prev.dir === 'desc' ? 'asc' : 'desc' };
    });
  };

  const [attributionSort, setAttributionSort] = useState<{ key: AttributionSortKey; dir: SortDir }>({ key: 'total', dir: 'desc' });

  const [heldOnly, setHeldOnly] = useState(true);

  const symbolAttributionQ = useQuery({
    queryKey: ['portfolio.symbolAttribution', filters, heldOnly],
    queryFn: () => api.symbolAttribution({ ...filters, top_n: 30, held_only: heldOnly }),
  });
  const toggleAttributionSort = (key: AttributionSortKey) => {
    setAttributionSort((prev) => {
      if (prev.key !== key) return { key, dir: key === 'symbol' ? 'asc' : 'desc' };
      return { key, dir: prev.dir === 'desc' ? 'asc' : 'desc' };
    });
  };

  if (positions.isError) {
    return <MissingData title={copy.unavailableTitle}>{copy.unavailableBody}</MissingData>;
  }

  const positionRows = (positions.data ?? [])
    .slice()
    .sort((a: any, b: any) => (b.position_value_base ?? 0) - (a.position_value_base ?? 0));

  const sortedPositionRows = (() => {
    const rows = positionRows.slice();
    const dir = positionSort.dir === 'desc' ? -1 : 1;
    const numericKey = (row: any): number => {
      const cost = Math.abs(row.cost_basis_money_base ?? 0);
      const value = row.position_value_base ?? 0;
      const pnl = row.fifo_pnl_unrealized_base ?? 0;
      switch (positionSort.key) {
        case 'cost': return cost;
        case 'value': return value;
        case 'pnl': return pnl;
        case 'pnl_pct': return cost > 0 ? (pnl / cost) * 100 : 0;
        case 'pct_nav': return row.pct_of_nav ?? 0;
        default: return 0;
      }
    };
    if (positionSort.key === 'symbol') {
      rows.sort((a: any, b: any) => dir * (a.symbol ?? '').localeCompare(b.symbol ?? ''));
    } else {
      rows.sort((a: any, b: any) => dir * (numericKey(a) - numericKey(b)));
    }
    return rows;
  })();

  const sortedAttributionRows = (() => {
    const rows = (symbolAttributionQ.data?.rows ?? []).slice();
    const dir = attributionSort.dir === 'desc' ? -1 : 1;
    const numericKey = (row: any): number => {
      switch (attributionSort.key) {
        case 'realized_st': return row.realized_st_base ?? 0;
        case 'realized_lt': return row.realized_lt_base ?? 0;
        case 'unrealized': return row.unrealized_pnl_base ?? 0;
        case 'mtm_total': return row.mtm_total_base ?? 0;
        case 'commissions': return row.commissions_base ?? 0;
        case 'total': return row.total_pnl_base ?? 0;
        default: return 0;
      }
    };
    if (attributionSort.key === 'symbol') {
      rows.sort((a: any, b: any) => dir * (a.symbol ?? '').localeCompare(b.symbol ?? ''));
    } else {
      rows.sort((a: any, b: any) => dir * (numericKey(a) - numericKey(b)));
    }
    return rows;
  })();

  const portfolioChartData = positionRows.slice(0, 12).map((row: any) => ({
    symbol: row.symbol,
    capitalPuesto: Number(Math.abs(row.cost_basis_money_base ?? 0).toFixed(2)),
    valorActual: Number(Math.abs(row.position_value_base ?? 0).toFixed(2)),
    pnl: row.fifo_pnl_unrealized_base ?? ((row.position_value_base ?? 0) - Math.abs(row.cost_basis_money_base ?? 0)),
  }));
  const portfolioPieData = positionRows
    .filter((row: any) => Math.abs(row.position_value_base ?? 0) > 0)
    .map((row: any) => ({
      symbol: row.symbol,
      value: Number(Math.abs(row.position_value_base ?? 0).toFixed(2)),
      signedValue: row.position_value_base ?? 0,
    }));
  const totalPortfolioExposure = portfolioPieData.reduce((sum: number, row: any) => sum + row.value, 0);
  const top12Exposure = portfolioChartData.reduce((sum: number, row: any) => sum + row.valorActual, 0);
  const top12Share = totalPortfolioExposure > 0 ? (top12Exposure / totalPortfolioExposure) * 100 : undefined;
  const remainingExposure = Math.max(totalPortfolioExposure - top12Exposure, 0);
  const pieLegendRows = portfolioPieData.slice(0, 16).map((row: any, index: number) => ({
    ...row,
    color: PIE_COLORS[index % PIE_COLORS.length],
    share: totalPortfolioExposure > 0 ? (row.value / totalPortfolioExposure) * 100 : 0,
  }));
  const renderPieLabel = ({ name, percent }: { name?: string; percent?: number }) => {
    if (!name || percent === undefined || percent < 0.018) return '';
    return `${name} ${(percent * 100).toFixed(1)}%`;
  };

  const currencyTooltipFormatter = (v: number | string) => {
    if (typeof v === 'number') return `${currency} ${v.toLocaleString(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    return String(v);
  };

  return (
    <div className="subsection-grid">
      <div className="chart-panel">
        <div className="panel-header">
          <div className="panel-title">{copy.valueVsCapital}</div>
          <div className="chart-legend">
            <span><i className="legend-dot capital" />{copy.capitalInvested}</span>
            <span><i className="legend-dot gain" />{copy.valueInGain}</span>
            <span><i className="legend-dot loss" />{copy.valueInLoss}</span>
          </div>
        </div>
        {positions.isLoading ? (
          <div className="trades-empty">{copy.loadingPositions}</div>
        ) : positionRows.length === 0 ? (
          <div className="trades-empty">{copy.noPositions}</div>
        ) : (
        <>
        <div className="coverage-strip">
          <div><span>{copy.top12Shown}</span><strong>{formatCompactCurrency(top12Exposure, currency, locale)}</strong></div>
          <div><span>{copy.percentOfTotal}</span><strong>{formatPercent(top12Share, false, locale)}</strong></div>
          <div><span>{copy.restPortfolio}</span><strong>{formatCompactCurrency(remainingExposure, currency, locale)}</strong></div>
        </div>
        <ResponsiveContainer width="100%" height={320}>
          <BarChart data={portfolioChartData} barCategoryGap="24%" barGap={-34}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
            <XAxis dataKey="symbol" tick={{ fill: '#8b88a6', fontSize: 11 }} />
            <YAxis tick={{ fill: '#8b88a6', fontSize: 11 }} />
            <Tooltip content={<ChartTooltip formatter={currencyTooltipFormatter} />} cursor={{ fill: 'rgba(168,85,247,0.06)' }} />
            <Bar dataKey="capitalPuesto" name={copy.capitalInvested} fill="rgba(139,136,166,0.35)" radius={[4, 4, 0, 0]} barSize={34} />
            <Bar dataKey="valorActual" name={copy.currentValue} radius={[4, 4, 0, 0]} barSize={20}>
              {portfolioChartData.map((row: any) => (
                <Cell key={row.symbol} fill={row.pnl >= 0 ? '#10b981' : '#ef4444'} />
              ))}
              <LabelList dataKey="valorActual" position="top" formatter={(value: number) => formatCompactCurrency(value, currency, locale)} />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
        <div className="portfolio-pie">
          <div className="panel-title">{copy.distribution}</div>
          <ResponsiveContainer width="100%" height={360}>
            <PieChart>
              <Tooltip content={<ChartTooltip formatter={(value) => formatCurrency(Number(value), currency, locale)} />} />
              <Pie data={portfolioPieData} dataKey="value" nameKey="symbol" innerRadius="50%" outerRadius="82%" paddingAngle={1} label={renderPieLabel} labelLine={false}>
                {portfolioPieData.map((row: any, index: number) => (
                  <Cell key={row.symbol} fill={PIE_COLORS[index % PIE_COLORS.length]} stroke="rgba(19,17,28,0.8)" strokeWidth={1.5} />
                ))}
              </Pie>
            </PieChart>
          </ResponsiveContainer>
          <div className="pie-legend-grid">
            {pieLegendRows.map((row: any) => (
              <div key={row.symbol} className="pie-legend-row">
                <span className="legend-dot" style={{ background: row.color }} />
                <span className="pie-symbol">{row.symbol}</span>
                <span>{formatPercent(row.share, false, locale)}</span>
                <strong>{formatCompactCurrency(row.value, currency, locale)}</strong>
              </div>
            ))}
          </div>
        </div>
        </>
        )}
      </div>

      <div className="table-panel portfolio-table">
        <div className="panel-title">{copy.currentPositions} · {positionRows.length}</div>
        {positions.isLoading ? (
          <div className="trades-empty">{copy.loadingPositions}</div>
        ) : positionRows.length === 0 ? (
          <div className="trades-empty">{copy.noPositions}</div>
        ) : (
        <div className="table-scroll">
          <table className="data-table sortable-table">
            <thead>
              <tr>
                {([
                  { key: 'symbol' as const, label: copy.symbol, align: 'left' as const },
                  { key: 'cost' as const, label: copy.capitalInvested, align: 'right' as const },
                  { key: 'value' as const, label: copy.currentValue, align: 'right' as const },
                  { key: 'pnl' as const, label: 'P&L', align: 'right' as const },
                  { key: 'pnl_pct' as const, label: 'P&L %', align: 'right' as const },
                  { key: 'pct_nav' as const, label: '% NAV', align: 'right' as const },
                ]).map((col) => {
                  const active = positionSort.key === col.key;
                  const arrow = active ? (positionSort.dir === 'desc' ? ' ▼' : ' ▲') : '';
                  return (
                    <th
                      key={col.key}
                      onClick={() => togglePositionSort(col.key)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault();
                          togglePositionSort(col.key);
                        }
                      }}
                      role="button"
                      tabIndex={0}
                      style={{ cursor: 'pointer', textAlign: col.align, userSelect: 'none' }}
                      aria-sort={active ? (positionSort.dir === 'desc' ? 'descending' : 'ascending') : 'none'}
                    >
                      {col.label}{arrow}
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {sortedPositionRows.map((row: any) => {
                const pnl = row.fifo_pnl_unrealized_base ?? 0;
                const cost = Math.abs(row.cost_basis_money_base ?? 0);
                const pnlPct = cost > 0 ? (pnl / cost) * 100 : null;
                return (
                  <tr key={`${row.asset_class}-${row.symbol}`}>
                    <td><span className="position-symbol">{row.symbol}</span><small>{row.country ?? row.currency ?? ''}</small></td>
                    <td>{formatCurrency(cost, currency, locale)}</td>
                    <td>{formatCurrency(row.position_value_base, currency, locale)}</td>
                    <td className={pnl >= 0 ? 'pnl-pos' : 'pnl-neg'}>{formatCurrency(pnl, currency, locale)}</td>
                    <td className={pnl >= 0 ? 'pnl-pos' : 'pnl-neg'}>
                      {pnlPct === null ? '—' : `${pnlPct >= 0 ? '+' : ''}${pnlPct.toLocaleString(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%`}
                    </td>
                    <td>{formatPercent(row.pct_of_nav, false, locale)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        )}
      </div>

      <div className="table-panel portfolio-table">
        <div className="panel-title">
          {copy.topMovers} · {symbolAttributionQ.data?.period?.label?.toUpperCase() ?? ''} · {sortedAttributionRows.length} {copy.symbols}
        </div>
        <div className="twr-switcher" style={{ marginBottom: 10 }}>
          <button className={heldOnly ? 'active' : ''} onClick={() => setHeldOnly(true)}>{copy.heldOnly}</button>
          <button className={!heldOnly ? 'active' : ''} onClick={() => setHeldOnly(false)}>{copy.showAll}</button>
        </div>
        {symbolAttributionQ.isLoading ? (
          <div style={{ padding: '12px 4px', color: '#8b88a6' }}>{copy.loadingAttribution}</div>
        ) : symbolAttributionQ.isError ? (
          <div style={{ padding: '12px 4px', color: '#ef4444' }}>
            {copy.attributionError}
          </div>
        ) : sortedAttributionRows.length === 0 ? (
          <div style={{ padding: '12px 4px', color: '#8b88a6' }}>
            {copy.attributionEmpty}
          </div>
        ) : (
          <div className="table-scroll">
            <table className="data-table sortable-table">
              <thead>
                <tr>
                  {([
                    { key: 'symbol' as const,       label: copy.symbol,    align: 'left'  as const },
                    { key: 'realized_st' as const,  label: 'Realized ST',  align: 'right' as const },
                    { key: 'realized_lt' as const,  label: 'Realized LT',  align: 'right' as const },
                    { key: 'unrealized' as const,   label: 'Unrealized',   align: 'right' as const },
                    { key: 'mtm_total' as const,    label: 'MTM total',    align: 'right' as const },
                    { key: 'commissions' as const,  label: copy.commissions, align: 'right' as const },
                    { key: 'total' as const,        label: 'Total P&L',    align: 'right' as const },
                  ]).map((col) => {
                    const active = attributionSort.key === col.key;
                    const arrow = active ? (attributionSort.dir === 'desc' ? ' ▼' : ' ▲') : '';
                    return (
                      <th
                        key={col.key}
                        onClick={() => toggleAttributionSort(col.key)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' || e.key === ' ') {
                            e.preventDefault();
                            toggleAttributionSort(col.key);
                          }
                        }}
                        role="button"
                        tabIndex={0}
                        style={{ cursor: 'pointer', textAlign: col.align, userSelect: 'none' }}
                        aria-sort={active ? (attributionSort.dir === 'desc' ? 'descending' : 'ascending') : 'none'}
                      >
                        {col.label}{arrow}
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody>
                {sortedAttributionRows.slice(0, 24).map((row: any) => {
                  const total = row.total_pnl_base ?? 0;
                  const realizedSt = row.realized_st_base ?? 0;
                  const realizedLt = row.realized_lt_base ?? 0;
                  const unreal = row.unrealized_pnl_base ?? 0;
                  const mtm = row.mtm_total_base ?? 0;
                  const fees = row.commissions_base ?? 0;
                  const cls = (v: number) => (v >= 0 ? 'pnl-pos' : 'pnl-neg');
                  return (
                    <tr key={`${row.asset_class ?? ''}-${row.conid ?? row.symbol}`}>
                      <td>
                        <span className="position-symbol">{row.symbol}</span>
                        <small>{row.country ?? row.asset_class ?? ''}</small>
                      </td>
                      <td className={cls(realizedSt)}>{formatCurrency(realizedSt, currency, locale)}</td>
                      <td className={cls(realizedLt)}>{formatCurrency(realizedLt, currency, locale)}</td>
                      <td className={cls(unreal)}>{formatCurrency(unreal, currency, locale)}</td>
                      <td className={cls(mtm)}>{formatCurrency(mtm, currency, locale)}</td>
                      <td className={cls(fees)}>{formatCurrency(fees, currency, locale)}</td>
                      <td className={cls(total)}><strong>{formatCurrency(total, currency, locale)}</strong></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
