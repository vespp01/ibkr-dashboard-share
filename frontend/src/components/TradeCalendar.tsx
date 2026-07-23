import { useMemo, useState } from 'react';
import type { RealizedTimeseriesRow } from '../lib/api';
import { useI18n } from '../lib/i18n';

interface Props {
  data: RealizedTimeseriesRow[];
  currency: string;
  initialMonth?: Date;
}

const COPY = {
  es: { tradeSingular: 'op', tradePlural: 'ops' },
  en: { tradeSingular: 'trade', tradePlural: 'trades' },
  de: { tradeSingular: 'Trade', tradePlural: 'Trades' },
} as const;

function startOfMonth(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), 1);
}

function addMonths(d: Date, n: number): Date {
  return new Date(d.getFullYear(), d.getMonth() + n, 1);
}

function isoDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function getISOWeek(d: Date): number {
  const target = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  const dayNum = target.getUTCDay() || 7;
  target.setUTCDate(target.getUTCDate() + 4 - dayNum);
  const yearStart = new Date(Date.UTC(target.getUTCFullYear(), 0, 1));
  return Math.ceil((((target.getTime() - yearStart.getTime()) / 86400000) + 1) / 7);
}

function formatCompact(value: number, currency: string, locale: string): string {
  if (value === 0) return `${currency} 0`;
  const abs = Math.abs(value);
  const sign = value > 0 ? '+' : '-';
  if (abs >= 1000) return `${sign}${currency} ${(abs / 1000).toLocaleString(locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 })}k`;
  return `${sign}${currency} ${abs.toLocaleString(locale, { maximumFractionDigits: 0 })}`;
}

export function TradeCalendar({ data, currency, initialMonth }: Props) {
  const { language, locale, t } = useI18n();
  const copy = COPY[language];
  const [viewMonth, setViewMonth] = useState<Date>(() => startOfMonth(initialMonth ?? new Date()));
  const weekdays = useMemo(() => {
    const monday = new Date(2024, 0, 1);
    return Array.from({ length: 7 }, (_, index) => new Intl.DateTimeFormat(locale, { weekday: 'short' })
      .format(new Date(2024, 0, monday.getDate() + index))
      .replace('.', ''));
  }, [locale]);

  // Respect the active period/year filter: when the (already filtered) data
  // changes, jump to the latest month that has trades instead of staying on
  // the current month (e.g. filter Año=2024 used to open on an empty month).
  const latestDataMonth = useMemo(() => {
    let latest = '';
    data.forEach((row) => {
      if (row.bucket.length === 10 && row.trades > 0 && row.bucket > latest) latest = row.bucket;
    });
    return latest ? latest.slice(0, 7) : '';
  }, [data]);

  // Adjust-during-render (React docs pattern) instead of an effect: when the
  // filtered data's latest month changes, jump the view in the same pass.
  const [seenDataMonth, setSeenDataMonth] = useState('');
  if (latestDataMonth && latestDataMonth !== seenDataMonth) {
    setSeenDataMonth(latestDataMonth);
    const [y, m] = latestDataMonth.split('-').map(Number);
    setViewMonth(new Date(y, m - 1, 1));
  }

  const byDate = useMemo(() => {
    const map = new Map<string, { realized: number; trades: number }>();
    data.forEach((row) => {
      if (row.bucket.length === 10) {
        map.set(row.bucket, { realized: row.realized, trades: row.trades });
      }
    });
    return map;
  }, [data]);

  const cells = useMemo(() => {
    const firstOfMonth = startOfMonth(viewMonth);
    const firstWeekday = (firstOfMonth.getDay() + 6) % 7;
    const gridStart = new Date(firstOfMonth);
    gridStart.setDate(firstOfMonth.getDate() - firstWeekday);

    const result: Array<{ date: Date; inMonth: boolean; iso: string }> = [];
    for (let i = 0; i < 42; i += 1) {
      const d = new Date(gridStart);
      d.setDate(gridStart.getDate() + i);
      result.push({ date: d, inMonth: d.getMonth() === viewMonth.getMonth(), iso: isoDate(d) });
    }
    return result;
  }, [viewMonth]);

  const monthTotal = useMemo(() => {
    let total = 0;
    let trades = 0;
    cells.forEach((cell) => {
      if (!cell.inMonth) return;
      const entry = byDate.get(cell.iso);
      if (entry) {
        total += entry.realized;
        trades += entry.trades;
      }
    });
    return { total, trades };
  }, [cells, byDate]);

  const weeks = useMemo(() => {
    const rows: Array<{ days: typeof cells; total: number; trades: number; iso: number }> = [];
    for (let i = 0; i < 6; i += 1) {
      const days = cells.slice(i * 7, i * 7 + 7);
      let total = 0;
      let trades = 0;
      days.forEach((day) => {
        if (!day.inMonth) return;
        const entry = byDate.get(day.iso);
        if (entry) {
          total += entry.realized;
          trades += entry.trades;
        }
      });
      rows.push({ days, total, trades, iso: getISOWeek(days[0].date) });
    }
    return rows;
  }, [cells, byDate]);

  const monthLabel = new Intl.DateTimeFormat(locale, { month: 'long', year: 'numeric' }).format(viewMonth);

  return (
    <div className="trade-calendar">
      <div className="calendar-header">
        <div className="calendar-nav">
          <button type="button" onClick={() => setViewMonth(addMonths(viewMonth, -1))} aria-label={t('common.previous')}>‹</button>
          <span className="calendar-month-label">{monthLabel}</span>
          <button type="button" onClick={() => setViewMonth(addMonths(viewMonth, 1))} aria-label={t('common.next')}>›</button>
          <button type="button" className="calendar-today" onClick={() => setViewMonth(startOfMonth(new Date()))}>
            {t('common.today')}
          </button>
        </div>
        <div className="calendar-month-total">
          <span className="calendar-month-total-label">{t('common.total')} {t('common.month')}</span>
          <strong className={monthTotal.total >= 0 ? 'pnl-pos' : 'pnl-neg'}>
            {monthTotal.total >= 0 ? '+' : ''}
            {monthTotal.total.toLocaleString(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} {currency}
          </strong>
          <span className="calendar-month-total-sub">
            {monthTotal.trades} {monthTotal.trades === 1 ? copy.tradeSingular : copy.tradePlural}
          </span>
        </div>
      </div>

      <div className="calendar-grid">
        <div className="calendar-weekday-row">
          {weekdays.map((wd) => (
            <div key={wd} className="calendar-weekday">{wd}</div>
          ))}
          <div className="calendar-weekday calendar-weekday-wk">{t('common.week')}</div>
        </div>

        {weeks.map((week, weekIdx) => (
          <div key={weekIdx} className="calendar-week-row">
            {week.days.map((cell) => {
              const entry = byDate.get(cell.iso);
              const hasData = entry !== undefined && entry.trades > 0;
              const positive = entry !== undefined && entry.realized > 0;
              const negative = entry !== undefined && entry.realized < 0;
              return (
                <div
                  key={cell.iso}
                  className={[
                    'calendar-day',
                    cell.inMonth ? '' : 'outside',
                    hasData ? 'has-data' : '',
                    positive ? 'positive' : '',
                    negative ? 'negative' : '',
                  ].filter(Boolean).join(' ')}
                  title={hasData ? `${cell.iso} · ${entry!.trades} ${entry!.trades === 1 ? copy.tradeSingular : copy.tradePlural} · ${entry!.realized.toLocaleString(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${currency}` : cell.iso}
                >
                  <div className="day-number">{cell.date.getDate()}</div>
                  {hasData && cell.inMonth ? (
                    <div className="day-pnl">{formatCompact(entry!.realized, currency, locale)}</div>
                  ) : null}
                  {hasData && cell.inMonth && entry!.trades > 0 ? (
                    <div className="day-trades">
                      {entry!.trades} {entry!.trades === 1 ? copy.tradeSingular : copy.tradePlural}
                    </div>
                  ) : null}
                </div>
              );
            })}
            <div className="calendar-week-total">
              <div className="wk-label">{t('common.week')}{week.iso}</div>
              {week.trades > 0 ? (
                <div className={`wk-pnl ${week.total >= 0 ? 'pnl-pos' : 'pnl-neg'}`}>
                  {formatCompact(week.total, currency, locale)}
                </div>
              ) : (
                <div className="wk-pnl wk-pnl-empty">—</div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
