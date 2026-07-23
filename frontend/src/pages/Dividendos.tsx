import { useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { UseQueryResult } from '@tanstack/react-query';
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { api, type PeriodFilters } from '../lib/api';
import { ChartTooltip } from '../components/ChartTooltip';
import { KPICard } from '../components/KPICard';
import { MissingData } from '../components/MissingData';
import { chartLabel, formatCurrency, formatPercent, periodLabel } from '../lib/format';
import { useI18n } from '../lib/i18n';

interface DividendsSummary {
  gross_base?: number;
  withholding_base?: number;
  net_base?: number;
}

interface IncomeProjection {
  padi_base?: number;
  monthly_base?: number;
  yield_pct?: number;
  yield_on_nav_pct?: number;
  as_of?: string;
  positions_total?: number;
  positions_covered?: number;
}

interface Props {
  filters: PeriodFilters;
  filterMode: 'period' | 'year';
  period: PeriodFilters['period'];
  year: number;
  currency: string;
  /** Shared with Resumen. */
  dividends: UseQueryResult<DividendsSummary>;
  /** Derived from overview; shared with Resumen. */
  incomeProjection?: IncomeProjection;
  overviewIsLoading: boolean;
}

const COPY = {
  es: {
    unavailableTitle: 'Dividendos no disponibles',
    unavailableBody: 'El backend devolvió un error. Revisa la base de datos o vuelve a cargar la importación Flex.',
    activePeriod: 'Periodo activo:',
    rangeSuffix: 'Todos los KPIs, tabla de dividendos por país y series temporales abajo respetan este rango.',
    allHistory: 'Todo el histórico',
    year: 'Año',
    ytd: 'Year to Date',
    mtd: 'Month to Date',
    qtd: 'Quarter to Date',
    lastMonth: 'Último mes',
    last3Months: 'Últimos 3 meses',
    last6Months: 'Últimos 6 meses',
    lastYear: 'Último año',
    last3Years: 'Últimos 3 años',
    last5Years: 'Últimos 5 años',
    gross: 'Bruto',
    withholding: 'Retención',
    net: 'Neto',
    franceWithholding: 'Retención Francia',
    loadingWithholdingError: 'error cargando retenciones: revisa el backend',
    entries: 'apuntes',
    padi: 'PADI estimado',
    yieldEquity: 'Yield sobre equity',
    positions: 'posiciones',
    monthlyDividends: 'Dividendos por mes',
    loading: 'Cargando...',
    monthlyError: 'Error cargando la serie mensual de dividendos: revisa el backend.',
    noDividends: 'Sin dividendos en este periodo. Si la base de datos está recién creada, ejecuta el bootstrap o un pull del pipeline.',
    yearlyDividends: 'Dividendos por año',
    yearlyError: 'Error cargando dividendos por año: revisa el backend.',
    accumulated: 'Dividendos acumulados',
    accumulatedError: 'Error cargando dividendos acumulados: revisa el backend.',
    byCountry: 'Dividendos por país',
    byCountryError: 'Error cargando dividendos por país: revisa el backend.',
    country: 'País',
    percentGross: '% bruto',
    grossAccumulated: 'Bruto acumulado',
    netAccumulated: 'Neto acumulado',
  },
  en: {
    unavailableTitle: 'Dividends unavailable',
    unavailableBody: 'The backend returned an error. Check the database or reload the Flex import.',
    activePeriod: 'Active period:',
    rangeSuffix: 'All KPIs, country dividend tables, and time series below respect this range.',
    allHistory: 'All history',
    year: 'Year',
    ytd: 'Year to Date',
    mtd: 'Month to Date',
    qtd: 'Quarter to Date',
    lastMonth: 'Last month',
    last3Months: 'Last 3 months',
    last6Months: 'Last 6 months',
    lastYear: 'Last year',
    last3Years: 'Last 3 years',
    last5Years: 'Last 5 years',
    gross: 'Gross',
    withholding: 'Withholding',
    net: 'Net',
    franceWithholding: 'France withholding',
    loadingWithholdingError: 'error loading withholding: check the backend',
    entries: 'entries',
    padi: 'Estimated PADI',
    yieldEquity: 'Yield on equity',
    positions: 'positions',
    monthlyDividends: 'Dividends by month',
    loading: 'Loading...',
    monthlyError: 'Error loading monthly dividend series: check the backend.',
    noDividends: 'No dividends in this period. If the database is new, run bootstrap or a pipeline pull.',
    yearlyDividends: 'Dividends by year',
    yearlyError: 'Error loading yearly dividends: check the backend.',
    accumulated: 'Accumulated dividends',
    accumulatedError: 'Error loading accumulated dividends: check the backend.',
    byCountry: 'Dividends by country',
    byCountryError: 'Error loading dividends by country: check the backend.',
    country: 'Country',
    percentGross: '% gross',
    grossAccumulated: 'Gross accumulated',
    netAccumulated: 'Net accumulated',
  },
  de: {
    unavailableTitle: 'Dividenden nicht verfuegbar',
    unavailableBody: 'Das Backend hat einen Fehler geliefert. Pruefe die Datenbank oder lade den Flex-Import neu.',
    activePeriod: 'Aktiver Zeitraum:',
    rangeSuffix: 'Alle KPIs, Laendertabellen und Zeitreihen unten verwenden diesen Zeitraum.',
    allHistory: 'Gesamte Historie',
    year: 'Jahr',
    ytd: 'Year to Date',
    mtd: 'Month to Date',
    qtd: 'Quarter to Date',
    lastMonth: 'Letzter Monat',
    last3Months: 'Letzte 3 Monate',
    last6Months: 'Letzte 6 Monate',
    lastYear: 'Letztes Jahr',
    last3Years: 'Letzte 3 Jahre',
    last5Years: 'Letzte 5 Jahre',
    gross: 'Brutto',
    withholding: 'Quellensteuer',
    net: 'Netto',
    franceWithholding: 'Quellensteuer Frankreich',
    loadingWithholdingError: 'Fehler beim Laden der Quellensteuer: Backend pruefen',
    entries: 'Eintraege',
    padi: 'Geschaetzter PADI',
    yieldEquity: 'Rendite auf Equity',
    positions: 'Positionen',
    monthlyDividends: 'Dividenden nach Monat',
    loading: 'Laden...',
    monthlyError: 'Fehler beim Laden der monatlichen Dividendenserie: Backend pruefen.',
    noDividends: 'Keine Dividenden in diesem Zeitraum. Bei neuer Datenbank Bootstrap oder Pipeline-Pull ausfuehren.',
    yearlyDividends: 'Dividenden nach Jahr',
    yearlyError: 'Fehler beim Laden der Dividenden nach Jahr: Backend pruefen.',
    accumulated: 'Kumulierte Dividenden',
    accumulatedError: 'Fehler beim Laden kumulierter Dividenden: Backend pruefen.',
    byCountry: 'Dividenden nach Land',
    byCountryError: 'Fehler beim Laden der Dividenden nach Land: Backend pruefen.',
    country: 'Land',
    percentGross: '% brutto',
    grossAccumulated: 'Brutto kumuliert',
    netAccumulated: 'Netto kumuliert',
  },
} as const;

export function DividendosPage({
  filters, filterMode, period, year, currency,
  dividends, incomeProjection, overviewIsLoading,
}: Props) {
  const { language, locale } = useI18n();
  const copy = COPY[language];
  const dividendsMonthly = useQuery({
    queryKey: ['dividends.timeseries', filters, 'month'],
    queryFn: () => api.dividendsTimeseries(filters, 'month'),
  });
  const dividendsYearly = useQuery({
    queryKey: ['dividends.timeseries', filters, 'year'],
    queryFn: () => api.dividendsTimeseries(filters, 'year'),
  });
  const withholdingByCountry = useQuery({
    queryKey: ['dividends.withholdingByCountry', filters],
    queryFn: () => api.withholdingByCountry(filters),
  });
  const dividendsByCountry = useQuery({
    queryKey: ['dividends.byCountry', filters],
    queryFn: () => api.dividendsByCountry(filters),
  });

  const chartPanelRef = useRef<HTMLDivElement | null>(null);

  const dividendsNet = dividends.data?.net_base as number | undefined;
  const frenchWithholding = withholdingByCountry.data?.find((row) => row.country === 'FR');
  const totalGrossByCountry = (dividendsByCountry.data ?? []).reduce(
    (sum, row) => sum + (row.gross ?? 0), 0
  );

  const monthlyChartData = (dividendsMonthly.data ?? []).map((row) => ({
    label: chartLabel(row.bucket ?? '', locale),
    gross: row.gross ?? 0,
    net: row.net ?? 0,
    withholding: row.withholding ?? 0,
    withholdingAbs: Math.abs(row.withholding ?? 0),
  }));

  const cumulativeDividendData = (dividendsMonthly.data ?? []).map((row, idx, arr) => {
    const cumGross = arr.slice(0, idx + 1).reduce((sum, r) => sum + (r.gross ?? 0), 0);
    const cumNet = arr.slice(0, idx + 1).reduce((sum, r) => sum + (r.net ?? 0), 0);
    return { label: chartLabel(row.bucket ?? '', locale), gross: cumGross, net: cumNet };
  });

  const yearlyRows = dividendsYearly.data ?? [];

  const currencyTooltipFormatter = (v: number | string) => {
    if (typeof v === 'number') return `${currency} ${v.toLocaleString(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    return String(v);
  };

  const exportMonthlyChartSvg = () => {
    const svg = chartPanelRef.current?.querySelector('svg');
    if (!svg) return;
    const blob = new Blob([new XMLSerializer().serializeToString(svg)], { type: 'image/svg+xml;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `dividendos-${periodLabel(filters)}.svg`;
    link.click();
    URL.revokeObjectURL(url);
  };

  const exportMonthlyChartCsv = () => {
    const rows = [
      ['bucket', 'gross', 'net', 'withholding'],
      ...(dividendsMonthly.data ?? []).map((row) => [row.bucket, String(row.gross ?? 0), String(row.net ?? 0), String(row.withholding ?? 0)]),
    ];
    const blob = new Blob([rows.map((r) => r.join(',')).join('\n')], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `dividendos-${periodLabel(filters)}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  };

  if (dividends.isError) {
    return <MissingData title={copy.unavailableTitle}>{copy.unavailableBody}</MissingData>;
  }

  const fromDate = filters.from;
  const toDate = filters.to;
  let rangeLabel: string = copy.allHistory;
  if (filterMode === 'year') {
    rangeLabel = `${copy.year} ${year}`;
  } else if (period === 'all') {
    rangeLabel = copy.allHistory;
  } else if (fromDate && toDate) {
    rangeLabel = `${fromDate} → ${toDate}`;
  } else if (period) {
    const periodLabels: Record<string, string> = {
      ytd: copy.ytd, mtd: copy.mtd, qtd: copy.qtd,
      '1m': copy.lastMonth, '3m': copy.last3Months, '6m': copy.last6Months,
      '1y': copy.lastYear, '3y': copy.last3Years, '5y': copy.last5Years,
    };
    rangeLabel = periodLabels[period] ?? period;
  }

  return (
    <>
      <div className="info-box" style={{ marginBottom: 12 }}>
        {copy.activePeriod} <strong>{rangeLabel}</strong>. {copy.rangeSuffix}
      </div>
      <div className="kpi-grid">
        <KPICard label={copy.gross} value={formatCurrency(dividends.data?.gross_base as number, currency, locale)} loading={dividends.isLoading} />
        <KPICard label={copy.withholding} value={formatCurrency(dividends.data?.withholding_base as number, currency, locale)} deltaKind="warn" loading={dividends.isLoading} />
        <KPICard label={copy.net} value={formatCurrency(dividendsNet, currency, locale)} deltaKind="pos" loading={dividends.isLoading} />
        <KPICard label={copy.franceWithholding} value={formatCurrency(frenchWithholding?.withholding as number | undefined, currency, locale)}
          delta={withholdingByCountry.isError ? copy.loadingWithholdingError : frenchWithholding ? `${frenchWithholding.rows ?? 0} ${copy.entries}` : undefined}
          deltaKind="warn" loading={dividends.isLoading} />
        <KPICard label={copy.padi} value={formatCurrency(incomeProjection?.padi_base, currency, locale)}
          delta={incomeProjection?.as_of ? `${incomeProjection.as_of}` : undefined} deltaKind="pos" loading={overviewIsLoading} />
        <KPICard label={copy.yieldEquity} value={formatPercent(incomeProjection?.yield_pct, false, locale)}
          delta={incomeProjection?.positions_covered !== undefined && incomeProjection?.positions_total !== undefined ? `${incomeProjection.positions_covered}/${incomeProjection.positions_total} ${copy.positions}` : undefined}
          deltaKind="pos" loading={overviewIsLoading} />
      </div>

      <div className="subsection-grid">
        <div className="chart-panel" ref={chartPanelRef}>
          <div className="panel-header">
            <div className="panel-title">{copy.monthlyDividends}</div>
            <div className="panel-actions">
              <button onClick={exportMonthlyChartSvg}>SVG</button>
              <button onClick={exportMonthlyChartCsv}>CSV</button>
            </div>
          </div>
          {dividendsMonthly.isLoading ? (
            <div className="trades-empty">{copy.loading}</div>
          ) : dividendsMonthly.isError ? (
            <div className="trades-empty error-state">{copy.monthlyError}</div>
          ) : monthlyChartData.length === 0 ? (
            <div className="trades-empty">{copy.noDividends}</div>
          ) : (
          <ResponsiveContainer width="100%" height={320}>
            <BarChart data={monthlyChartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
              <XAxis dataKey="label" tick={{ fill: '#8b88a6', fontSize: 11 }} />
              <YAxis tick={{ fill: '#8b88a6', fontSize: 11 }} />
              <Tooltip content={<ChartTooltip formatter={currencyTooltipFormatter} />} cursor={{ fill: 'rgba(168,85,247,0.06)' }} />
              <Bar dataKey="gross" name={copy.gross} fill="#a855f7" radius={[4, 4, 0, 0]} />
              <Bar dataKey="net" name={copy.net} fill="#10b981" radius={[4, 4, 0, 0]} />
              <Bar dataKey="withholdingAbs" name={copy.withholding} fill="#f59e0b" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
          )}
        </div>

        <div className="table-panel">
          <div className="panel-title">{copy.yearlyDividends}</div>
          {dividendsYearly.isLoading ? (
            <div className="trades-empty">{copy.loading}</div>
          ) : dividendsYearly.isError ? (
            <div className="trades-empty error-state">{copy.yearlyError}</div>
          ) : yearlyRows.length === 0 ? (
            <div className="trades-empty">{copy.noDividends}</div>
          ) : (
          <table className="data-table">
            <thead><tr><th>{copy.year}</th><th>{copy.gross}</th><th>{copy.withholding}</th><th>{copy.net}</th></tr></thead>
            <tbody>
              {yearlyRows.map((row) => (
                <tr key={row.bucket}>
                  <td>{row.bucket}</td>
                  <td>{formatCurrency(row.gross, currency, locale)}</td>
                  <td className="pnl-neg">{formatCurrency(row.withholding, currency, locale)}</td>
                  <td className="pnl-pos">{formatCurrency(row.net, currency, locale)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          )}
        </div>
      </div>

      <div className="chart-panel">
        <div className="panel-title">{copy.accumulated}</div>
        {dividendsMonthly.isLoading ? (
          <div className="trades-empty">{copy.loading}</div>
        ) : dividendsMonthly.isError ? (
          <div className="trades-empty error-state">{copy.accumulatedError}</div>
        ) : cumulativeDividendData.length === 0 ? (
          <div className="trades-empty">{copy.noDividends}</div>
        ) : (
        <ResponsiveContainer width="100%" height={320}>
          <LineChart data={cumulativeDividendData}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
            <XAxis dataKey="label" tick={{ fill: '#8b88a6', fontSize: 11 }} />
            <YAxis tick={{ fill: '#8b88a6', fontSize: 11 }} />
            <Tooltip content={<ChartTooltip formatter={currencyTooltipFormatter} />} cursor={{ stroke: 'rgba(168,85,247,0.4)', strokeWidth: 1 }} />
            <Line type="monotone" dataKey="gross" name={copy.grossAccumulated} stroke="#a855f7" strokeWidth={2} dot={false} />
            <Line type="monotone" dataKey="net" name={copy.netAccumulated} stroke="#10b981" strokeWidth={2} dot={false} />
          </LineChart>
        </ResponsiveContainer>
        )}
      </div>

      <div className="table-panel">
        <div className="panel-title">{copy.byCountry}</div>
        {dividendsByCountry.isLoading ? (
          <div className="trades-empty">{copy.loading}</div>
        ) : dividendsByCountry.isError ? (
          <div className="trades-empty error-state">{copy.byCountryError}</div>
        ) : (dividendsByCountry.data ?? []).length === 0 ? (
          <div className="trades-empty">{copy.noDividends}</div>
        ) : (
        <table className="data-table">
          <thead><tr><th>{copy.country}</th><th>{copy.gross}</th><th>{copy.withholding}</th><th>{copy.net}</th><th>{copy.percentGross}</th></tr></thead>
          <tbody>
            {(dividendsByCountry.data ?? []).map((row) => {
              const share = totalGrossByCountry > 0 ? ((row.gross ?? 0) / totalGrossByCountry) * 100 : undefined;
              return (
                <tr key={row.country}>
                  <td>{row.country}</td>
                  <td>{formatCurrency(row.gross, currency, locale)}</td>
                  <td className={(row.withholding ?? 0) !== 0 ? 'pnl-neg' : ''}>{formatCurrency(row.withholding, currency, locale)}</td>
                  <td className={(row.net ?? 0) < 0 ? 'pnl-neg' : 'pnl-pos'}>{formatCurrency(row.net, currency, locale)}</td>
                  <td>{formatPercent(share, false, locale)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        )}
      </div>
    </>
  );
}
