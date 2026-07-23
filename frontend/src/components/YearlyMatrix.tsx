import { useMemo } from 'react';
import type { RealizedTimeseriesRow } from '../lib/api';
import { useI18n } from '../lib/i18n';

interface Props {
  data: RealizedTimeseriesRow[];
  currency: string;
}

function formatCompact(value: number, currency: string, locale: string): string {
  if (value === 0) return `${currency} 0`;
  const abs = Math.abs(value);
  const sign = value > 0 ? '+' : '-';
  if (abs >= 1_000_000) return `${sign}${currency} ${(abs / 1_000_000).toLocaleString(locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 })}M`;
  if (abs >= 1000) return `${sign}${currency} ${(abs / 1000).toLocaleString(locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 })}k`;
  return `${sign}${currency} ${abs.toLocaleString(locale, { maximumFractionDigits: 0 })}`;
}

export function YearlyMatrix({ data, currency }: Props) {
  const { locale, t } = useI18n();
  const monthsShort = useMemo(() => (
    Array.from({ length: 12 }, (_, month) => new Intl.DateTimeFormat(locale, { month: 'short' })
      .format(new Date(2024, month, 1))
      .replace('.', ''))
  ), [locale]);

  const { years, byYearMonth, maxAbs } = useMemo(() => {
    const yearsSet = new Set<number>();
    const map = new Map<string, number>();
    let maxAbs = 0;
    data.forEach((row) => {
      if (row.bucket.length === 7) {
        const [yStr, mStr] = row.bucket.split('-');
        const y = parseInt(yStr, 10);
        const m = parseInt(mStr, 10);
        yearsSet.add(y);
        map.set(`${y}-${m}`, row.realized);
        const abs = Math.abs(row.realized);
        if (abs > maxAbs) maxAbs = abs;
      }
    });
    return {
      years: Array.from(yearsSet).sort((a, b) => a - b),
      byYearMonth: map,
      maxAbs,
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

  const intensity = (value: number | undefined): number => {
    if (value === undefined || maxAbs === 0) return 0;
    return Math.min(1, Math.abs(value) / maxAbs);
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
        return (
          <div key={y} className="yearly-matrix-row">
            <div className="yearly-matrix-cell yearly-matrix-label">{y}</div>
            {monthsShort.map((_, idx) => {
              const value = getCell(y, idx + 1);
              const alpha = intensity(value);
              const positive = value !== undefined && value > 0;
              const negative = value !== undefined && value < 0;
              return (
                <div
                  key={idx}
                  className={`yearly-matrix-cell ${positive ? 'positive' : negative ? 'negative' : 'empty'}`}
                  style={{ '--cell-alpha': alpha } as React.CSSProperties}
                  title={value !== undefined ? `${y}-${String(idx + 1).padStart(2, '0')} · ${value.toFixed(2)} ${currency}` : ''}
                >
                  {value !== undefined && value !== 0 ? formatCompact(value, '', locale) : ''}
                </div>
              );
            })}
            <div className={`yearly-matrix-cell yearly-matrix-total ${yearTotal >= 0 ? 'positive-strong' : 'negative-strong'}`}>
              {formatCompact(yearTotal, currency, locale)}
            </div>
          </div>
        );
      })}
    </div>
  );
}
