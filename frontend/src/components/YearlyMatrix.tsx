import { useMemo, type CSSProperties } from 'react';
import type { RealizedTimeseriesRow } from '../lib/api';
import { formatCurrency, formatSignedCompactCurrency } from '../lib/format';
import { useI18n } from '../lib/i18n';
import { heatCap } from './calendarHeat';
import '../styles/calendar.css';

interface Props {
  data: RealizedTimeseriesRow[];
  currency: string;
}

export function YearlyMatrix({ data, currency }: Props) {
  const { locale, t } = useI18n();
  const monthsShort = useMemo(() => (
    Array.from({ length: 12 }, (_, month) => new Intl.DateTimeFormat(locale, { month: 'short' })
      .format(new Date(2024, month, 1))
      .replace('.', ''))
  ), [locale]);

  const { years, byYearMonth, cap } = useMemo(() => {
    const yearsSet = new Set<number>();
    const map = new Map<string, number>();
    data.forEach((row) => {
      if (row.bucket.length === 7) {
        const [yStr, mStr] = row.bucket.split('-');
        const y = parseInt(yStr, 10);
        const m = parseInt(mStr, 10);
        yearsSet.add(y);
        map.set(`${y}-${m}`, row.realized);
      }
    });
    return {
      years: Array.from(yearsSet).sort((a, b) => a - b),
      byYearMonth: map,
      cap: heatCap(Array.from(map.values())),
    };
  }, [data]);

  if (years.length === 0) {
    return <div className="yearly-matrix-empty">{t('common.noData')}</div>;
  }

  const getCell = (y: number, m: number): number | undefined => byYearMonth.get(`${y}-${m}`);

  const getYearTotal = (y: number): number => {
    let total = 0;
    for (let m = 1; m <= 12; m += 1) {
      total += byYearMonth.get(`${y}-${m}`) ?? 0;
    }
    return total;
  };

  // sqrt like the calendar, so small months stay visible next to outliers.
  const intensity = (value: number | undefined): number => {
    if (value === undefined || value === 0 || cap === 0) return 0;
    return Math.sqrt(Math.min(1, Math.abs(value) / cap));
  };

  return (
    <div className="yearly-matrix">
      <div className="yearly-matrix-row yearly-matrix-header">
        <div className="yearly-matrix-cell yearly-matrix-label">{t('common.year')}</div>
        {monthsShort.map((m) => (
          <div key={m} className="yearly-matrix-cell yearly-matrix-month">{m}</div>
        ))}
        <div className="yearly-matrix-cell yearly-matrix-total">{t('common.total')}</div>
      </div>
      {years.map((y) => {
        const yearTotal = getYearTotal(y);
        const totalTone = yearTotal > 0 ? 'positive-strong' : yearTotal < 0 ? 'negative-strong' : 'neutral-strong';
        return (
          <div key={y} className="yearly-matrix-row">
            <div className="yearly-matrix-cell yearly-matrix-label">{y}</div>
            {monthsShort.map((_, idx) => {
              const value = getCell(y, idx + 1);
              const positive = value !== undefined && value > 0;
              const negative = value !== undefined && value < 0;
              return (
                <div
                  key={idx}
                  className={`yearly-matrix-cell ${positive ? 'positive' : negative ? 'negative' : 'empty'}`}
                  style={{ '--cell-alpha': intensity(value) } as CSSProperties}
                  title={value !== undefined ? `${y}-${String(idx + 1).padStart(2, '0')} · ${formatCurrency(value, currency, locale)}` : undefined}
                >
                  {value !== undefined && value !== 0 ? formatSignedCompactCurrency(value, '', locale) : ''}
                </div>
              );
            })}
            <div className={`yearly-matrix-cell yearly-matrix-total ${totalTone}`}>
              {formatSignedCompactCurrency(yearTotal, currency, locale)}
            </div>
          </div>
        );
      })}
    </div>
  );
}
