import { useMemo } from 'react';
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import type { DividendsTimeseriesRow } from '../../lib/api';
import { axisAmountFormatter, chartLabel, tooltipCurrencyFormatter } from '../../lib/format';
import { chartTheme, seriesAnimation, useReducedMotion } from '../../lib/motion';
import { useI18n } from '../../lib/i18n';
import { ChartTooltip } from '../ChartTooltip';
import type { DividendsCopy } from './copy';

interface Props {
  copy: DividendsCopy;
  currency: string;
  rows: DividendsTimeseriesRow[] | undefined;
  loading: boolean;
  error: boolean;
}

export function CumulativeChart({ copy, currency, rows, loading, error }: Props) {
  const { locale } = useI18n();
  const reduced = useReducedMotion();
  const theme = chartTheme();

  const data = useMemo(() => {
    const out: Array<{ label: string; gross: number; net: number }> = [];
    (rows ?? []).forEach((row) => {
      const prev = out[out.length - 1];
      out.push({
        label: chartLabel(row.bucket, locale),
        gross: (prev?.gross ?? 0) + (row.gross ?? 0),
        net: (prev?.net ?? 0) + (row.net ?? 0),
      });
    });
    return out;
  }, [rows, locale]);

  let body;
  if (loading) {
    body = <div className="skeleton dv-chart-skeleton" aria-busy="true" />;
  } else if (error) {
    body = <div className="trades-empty error-state">{copy.accumulatedError}</div>;
  } else if (data.length === 0) {
    body = <div className="trades-empty">{copy.noDividends}</div>;
  } else {
    body = (
      <ResponsiveContainer width="100%" height={320}>
        <LineChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={theme.grid} vertical={false} />
          <XAxis dataKey="label" tick={{ fill: theme.axis, fontSize: 11 }} minTickGap={18} interval="preserveStartEnd" />
          <YAxis tick={{ fill: theme.axis, fontSize: 11 }} tickFormatter={axisAmountFormatter(locale)} width={44} />
          <Tooltip content={<ChartTooltip formatter={tooltipCurrencyFormatter(currency, locale)} />} cursor={{ stroke: theme.primary, strokeOpacity: 0.4, strokeWidth: 1 }} isAnimationActive={false} />
          <Line type="stepAfter" dataKey="gross" name={copy.grossAccumulated} stroke={theme.primary} strokeWidth={2} dot={false} {...seriesAnimation('line', reduced, 0)} />
          <Line type="stepAfter" dataKey="net" name={copy.netAccumulated} stroke={theme.positive} strokeWidth={2} dot={false} {...seriesAnimation('line', reduced, 100)} />
        </LineChart>
      </ResponsiveContainer>
    );
  }

  return (
    <div className="chart-panel dv-panel">
      <div className="panel-title">{copy.accumulated}</div>
      {body}
      {!loading && !error && data.length > 0 ? (
        <ul className="dv-legend">
          <li><i className="line" style={{ background: 'var(--color-primary)' }} />{copy.grossAccumulated}</li>
          <li><i className="line" style={{ background: 'var(--color-positive)' }} />{copy.netAccumulated}</li>
        </ul>
      ) : null}
    </div>
  );
}
