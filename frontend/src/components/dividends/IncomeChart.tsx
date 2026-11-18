import { useMemo, useRef, useState } from 'react';
import {
  Bar, CartesianGrid, Cell, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import type { DividendsTimeseriesRow } from '../../lib/api';
import { axisAmountFormatter, chartLabel, formatCurrency } from '../../lib/format';
import { chartTheme, seriesAnimation, useReducedMotion } from '../../lib/motion';
import { useI18n } from '../../lib/i18n';
import { fill, type DividendsCopy } from './copy';
import { lastYearToDate, rollingTtm } from './income';

export type IncomeMode = 'month' | 'year';

interface Props {
  copy: DividendsCopy;
  currency: string;
  mode: IncomeMode;
  onModeChange: (mode: IncomeMode) => void;
  monthly: DividendsTimeseriesRow[] | undefined;
  yearly: DividendsTimeseriesRow[] | undefined;
  /** All-history monthly series: feeds the rolling TTM line. */
  allMonthly: DividendsTimeseriesRow[] | undefined;
  /** All-history daily series: feeds the same-period-last-year ghost bar. */
  allDaily: DividendsTimeseriesRow[] | undefined;
  loading: boolean;
  error: boolean;
  exportName: string;
}

interface Point {
  label: string;
  net: number;
  wht: number;
  gross: number;
  ttm?: number | null;
  ghost?: number;
}

function download(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

export function IncomeChart({
  copy, currency, mode, onModeChange, monthly, yearly, allMonthly, allDaily, loading, error, exportName,
}: Props) {
  const { locale } = useI18n();
  const reduced = useReducedMotion();
  const theme = chartTheme();
  const panelRef = useRef<HTMLDivElement | null>(null);
  const [active, setActive] = useState<number | null>(null);

  const rolling = useMemo(() => rollingTtm(allMonthly ?? []), [allMonthly]);
  const ghostYear = useMemo(() => lastYearToDate(allDaily ?? []), [allDaily]);
  const currentYear = String(new Date().getFullYear());

  const data: Point[] = useMemo(() => {
    const rows = mode === 'month' ? monthly : yearly;
    return (rows ?? []).map((row) => {
      const net = row.net ?? 0;
      const wht = Math.abs(row.withholding ?? 0);
      const point: Point = {
        label: mode === 'month' ? chartLabel(row.bucket, locale) : row.bucket,
        net,
        wht,
        gross: row.gross ?? net + wht,
      };
      if (mode === 'month') point.ttm = rolling.get(row.bucket) ?? null;
      if (mode === 'year' && row.bucket === currentYear && allDaily) point.ghost = ghostYear.gross;
      return point;
    });
  }, [mode, monthly, yearly, rolling, locale, allDaily, ghostYear, currentYear]);

  const hasTtm = mode === 'month' && data.some((d) => typeof d.ttm === 'number');
  const hasGhost = mode === 'year' && data.some((d) => d.ghost !== undefined);
  const rows = mode === 'month' ? monthly : yearly;

  const exportSvg = () => {
    const svg = panelRef.current?.querySelector('svg.recharts-surface');
    if (!svg) return;
    download(new Blob([new XMLSerializer().serializeToString(svg)], { type: 'image/svg+xml;charset=utf-8' }), `${exportName}.svg`);
  };
  const exportCsv = () => {
    const lines = [
      ['bucket', 'gross', 'net', 'withholding'],
      ...(rows ?? []).map((row) => [row.bucket, String(row.gross ?? 0), String(row.net ?? 0), String(row.withholding ?? 0)]),
    ];
    download(new Blob([lines.map((r) => r.join(',')).join('\n')], { type: 'text/csv;charset=utf-8' }), `${exportName}.csv`);
  };

  const renderTooltip = ({ active: isActive, payload }: { active?: boolean; payload?: Array<{ payload: Point }> }) => {
    if (!isActive || !payload || payload.length === 0) return null;
    const p = payload[0].payload;
    return (
      <div className="chart-tooltip">
        <div className="chart-tooltip-label">{p.label}</div>
        <div className="chart-tooltip-rows">
          <TooltipRow color={theme.positive} name={copy.net} value={formatCurrency(p.net, currency, locale)} />
          <TooltipRow color={theme.warning} name={copy.withholding} value={formatCurrency(p.wht, currency, locale)} />
          <TooltipRow name={copy.gross} value={formatCurrency(p.gross, currency, locale)} strong />
          {typeof p.ttm === 'number' ? (
            <TooltipRow color={theme.primary} name={copy.ttmLine.replace(/ \(.*\)/, '')} value={formatCurrency(p.ttm, currency, locale)} />
          ) : null}
          {p.ghost !== undefined ? (
            <TooltipRow name={fill(copy.samePeriod, { year: ghostYear.year })} value={formatCurrency(p.ghost, currency, locale)} />
          ) : null}
        </div>
      </div>
    );
  };

  let body;
  if (loading) {
    body = <div className="skeleton dv-chart-skeleton" aria-busy="true" />;
  } else if (error) {
    body = <div className="trades-empty error-state">{copy.chartError}</div>;
  } else if (data.length === 0) {
    body = <div className="trades-empty">{copy.noDividends}</div>;
  } else {
    body = (
      <div className="dv-income-chart">
        <ResponsiveContainer width="100%" height={320}>
          <ComposedChart
            data={data}
            margin={{ top: 8, right: hasTtm ? 4 : 8, left: 0, bottom: 0 }}
            onMouseMove={(state) => {
              const idx = typeof state?.activeTooltipIndex === 'number' ? state.activeTooltipIndex : null;
              setActive((prev) => (prev === idx ? prev : idx));
            }}
            onMouseLeave={() => setActive(null)}
          >
            <CartesianGrid strokeDasharray="3 3" stroke={theme.grid} vertical={false} />
            <XAxis dataKey="label" xAxisId="main" tick={{ fill: theme.axis, fontSize: 11 }} minTickGap={mode === 'month' ? 18 : 8} interval="preserveStartEnd" />
            <XAxis dataKey="label" xAxisId="ghost" hide />
            <YAxis yAxisId="amount" tick={{ fill: theme.axis, fontSize: 11 }} tickFormatter={axisAmountFormatter(locale)} width={44} />
            {hasTtm ? (
              <YAxis yAxisId="ttm" orientation="right" tick={{ fill: theme.primary, fontSize: 11 }} tickFormatter={axisAmountFormatter(locale)} width={44} />
            ) : null}
            <Tooltip content={renderTooltip as never} cursor={{ fill: theme.cursorFill }} isAnimationActive={false} />
            <Bar xAxisId="main" yAxisId="amount" stackId="income" dataKey="net" name={copy.net} fill={theme.positive} {...seriesAnimation('bar', reduced, 0)}>
              {data.map((d, i) => (
                <Cell key={`n-${d.label}`} fillOpacity={active === null || active === i ? 1 : 0.35} />
              ))}
            </Bar>
            <Bar xAxisId="main" yAxisId="amount" stackId="income" dataKey="wht" name={copy.withholding} fill={theme.warning} radius={[3, 3, 0, 0]} {...seriesAnimation('bar', reduced, 80)}>
              {data.map((d, i) => (
                <Cell key={`w-${d.label}`} fillOpacity={active === null || active === i ? 1 : 0.35} />
              ))}
            </Bar>
            {hasGhost ? (
              <Bar xAxisId="ghost" yAxisId="amount" dataKey="ghost" name={fill(copy.samePeriod, { year: ghostYear.year })} fill="none" stroke={theme.muted} strokeDasharray="4 3" strokeWidth={1.5} {...seriesAnimation('bar', reduced, 160)} />
            ) : null}
            {hasTtm ? (
              <Line xAxisId="main" yAxisId="ttm" type="monotone" dataKey="ttm" name={copy.ttmLine} stroke={theme.primary} strokeWidth={2} dot={false} connectNulls={false} {...seriesAnimation('line', reduced, 200)} />
            ) : null}
          </ComposedChart>
        </ResponsiveContainer>
        <ul className="dv-legend" aria-label={copy.incomeTitle}>
          <li><i style={{ background: 'var(--color-positive)' }} />{copy.net}</li>
          <li><i style={{ background: 'var(--color-warning)' }} />{copy.withholding}</li>
          {hasTtm ? <li><i className="line" style={{ background: 'var(--color-primary)' }} />{copy.ttmLine}</li> : null}
          {hasGhost ? <li><i className="ghost" />{fill(copy.samePeriod, { year: ghostYear.year })}</li> : null}
        </ul>
      </div>
    );
  }

  return (
    <div className="chart-panel dv-panel" ref={panelRef}>
      <div className="panel-header">
        <div className="panel-title">{copy.incomeTitle}</div>
        <div className="dv-header-actions">
          <div className="segmented-control compact" role="group" aria-label={copy.granularity}>
            <button type="button" className={mode === 'month' ? 'active' : ''} aria-pressed={mode === 'month'} onClick={() => onModeChange('month')}>{copy.month}</button>
            <button type="button" className={mode === 'year' ? 'active' : ''} aria-pressed={mode === 'year'} onClick={() => onModeChange('year')}>{copy.year}</button>
          </div>
          <div className="panel-actions">
            <button type="button" onClick={exportSvg}>SVG</button>
            <button type="button" onClick={exportCsv}>CSV</button>
          </div>
        </div>
      </div>
      {body}
      <p className="dv-note">{copy.incomeNote}</p>
    </div>
  );
}

function TooltipRow({ color, name, value, strong }: { color?: string; name: string; value: string; strong?: boolean }) {
  return (
    <div className="chart-tooltip-row" style={strong ? { borderTop: '1px solid var(--border-card)', paddingTop: 4 } : undefined}>
      <span className="chart-tooltip-dot" style={{ background: color ?? 'transparent', border: color ? undefined : '1px dashed var(--fg-muted)' }} />
      <span className="chart-tooltip-name">{name}</span>
      <span className="chart-tooltip-value">{value}</span>
    </div>
  );
}
