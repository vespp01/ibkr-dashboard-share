import { useMemo } from 'react';
import type { DividendsTimeseriesRow } from '../../lib/api';
import { axisAmountFormatter, formatCompactCurrency, formatCurrency } from '../../lib/format';
import { useI18n } from '../../lib/i18n';
import type { DividendsCopy } from './copy';
import { buildHeatmap, heatIntensity } from './income';

interface Props {
  copy: DividendsCopy;
  currency: string;
  rows: DividendsTimeseriesRow[] | undefined;
  loading: boolean;
  error: boolean;
}

export function IncomeHeatmap({ copy, currency, rows, loading, error }: Props) {
  const { locale, privacyMode } = useI18n();
  const model = useMemo(() => buildHeatmap(rows ?? []), [rows]);
  const months = useMemo(
    () => Array.from({ length: 12 }, (_, m) => {
      const d = new Date(2024, m, 1);
      return {
        short: new Intl.DateTimeFormat(locale, { month: 'short' }).format(d).replace('.', ''),
        long: new Intl.DateTimeFormat(locale, { month: 'long' }).format(d),
      };
    }),
    [locale],
  );
  const now = new Date();
  const compact = axisAmountFormatter(locale);

  let body;
  if (loading) {
    body = <div className="skeleton dv-chart-skeleton short" aria-busy="true" />;
  } else if (error) {
    body = <div className="trades-empty error-state">{copy.chartError}</div>;
  } else if (model.years.length === 0) {
    body = <div className="trades-empty">{copy.heatmapEmpty}</div>;
  } else {
    body = (
      <div className="dv-heatmap-scroll">
        <table className="dv-heatmap">
          <thead>
            <tr>
              <th scope="col">{copy.year}</th>
              {months.map((m) => <th key={m.short} scope="col">{m.short}</th>)}
              <th scope="col" className="total">{copy.total}</th>
            </tr>
          </thead>
          <tbody>
            {model.years.map((year, r) => (
              <tr key={year}>
                <th scope="row">{year}</th>
                {months.map((m, c) => {
                  const value = model.cells.get(`${year}-${c + 1}`);
                  const future = year > now.getFullYear() || (year === now.getFullYear() && c > now.getMonth());
                  const partial = year === now.getFullYear() && c === now.getMonth();
                  const label = `${m.long} ${year}: ${value === undefined ? '' : formatCurrency(value, currency, locale)}${partial ? ` (${copy.currentMonth})` : ''}`;
                  return (
                    <td
                      key={c}
                      className={`${value === undefined || future ? 'empty' : ''}${partial ? ' partial' : ''}`}
                      style={{
                        '--i': heatIntensity(value, model.max),
                        '--c': c,
                        '--r': r,
                      } as React.CSSProperties}
                      title={value === undefined ? undefined : label}
                      aria-label={value === undefined ? undefined : label}
                    >
                      {value !== undefined && value > 0 && !privacyMode ? compact(value) : ''}
                    </td>
                  );
                })}
                <td className="total num">{formatCompactCurrency(model.yearTotals.get(year), currency, locale)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <div className="table-panel dv-panel">
      <div className="panel-title">{copy.heatmapTitle}</div>
      {body}
    </div>
  );
}
