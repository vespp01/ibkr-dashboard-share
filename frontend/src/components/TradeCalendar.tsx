import { useEffect, useMemo, useRef, useState, useSyncExternalStore, type CSSProperties, type KeyboardEvent } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api, type RealizedTimeseriesRow, type TradeRow } from '../lib/api';
import { formatCompactCurrency, formatCurrency, formatNumber, formatSignedCompactCurrency, parseLocalDate } from '../lib/format';
import { useI18n } from '../lib/i18n';
import { useCountUp } from '../lib/motion';
import { heatAlpha, heatCap } from './calendarHeat';
import '../styles/calendar.css';

interface Props {
  currency: string;
  /** First day of the visible month (controlled by the page so the tiles below follow it). */
  month: Date;
  onMonthChange: (month: Date) => void;
  /** True while the page-filtered series is still loading (keeps the skeleton up). */
  pending?: boolean;
}

type View = 'month' | 'year';
type Dir = 'none' | 'next' | 'prev' | 'fade';
type Entry = { realized: number; trades: number };

const COPY = {
  es: {
    ops: 'ops', op: 'op', operations: 'operaciones', operation: 'operación',
    month: 'Mes', year: 'Año', thisMonth: 'Este mes', thisYear: 'Este año',
    realized: 'realizado', noRealized: 'sin resultado realizado', noTrades: 'Sin operaciones este mes',
    greenDays: 'verdes', redDays: 'rojos', daysWord: 'días', best: 'Mejor', worst: 'Peor',
    legendLoss: 'pérdida', legendGain: 'ganancia', outlier: 'fuera de escala', scaleCap: 'escala hasta',
    realizedTotal: 'P&L realizado', closing: 'Cierres', opening: 'Aperturas', buy: 'Compra', sell: 'Venta',
    openWord: 'apertura', closeWord: 'cierre', loadingTrades: 'Cargando operaciones', tradesError: 'No se pudieron cargar las operaciones.',
    calError: 'No se pudo cargar el calendario.', retry: 'Reintentar', close: 'Cerrar', shown: 'Mostrando', of: 'de', noRows: 'Sin operaciones ese día.',
    weekdayHeader: 'Calendario de operaciones', yearOf: 'Año', openMonth: 'Abrir',
  },
  en: {
    ops: 'trades', op: 'trade', operations: 'trades', operation: 'trade',
    month: 'Month', year: 'Year', thisMonth: 'This month', thisYear: 'This year',
    realized: 'realized', noRealized: 'no realized result', noTrades: 'No trades this month',
    greenDays: 'green', redDays: 'red', daysWord: 'days', best: 'Best', worst: 'Worst',
    legendLoss: 'loss', legendGain: 'gain', outlier: 'off scale', scaleCap: 'scale up to',
    realizedTotal: 'Realized P&L', closing: 'Closing', opening: 'Opening', buy: 'Buy', sell: 'Sell',
    openWord: 'open', closeWord: 'close', loadingTrades: 'Loading trades', tradesError: 'Could not load the trades.',
    calError: 'Could not load the calendar.', retry: 'Retry', close: 'Close', shown: 'Showing', of: 'of', noRows: 'No trades that day.',
    weekdayHeader: 'Trade calendar', yearOf: 'Year', openMonth: 'Open',
  },
  de: {
    ops: 'Trades', op: 'Trade', operations: 'Trades', operation: 'Trade',
    month: 'Monat', year: 'Jahr', thisMonth: 'Dieser Monat', thisYear: 'Dieses Jahr',
    realized: 'realisiert', noRealized: 'kein realisiertes Ergebnis', noTrades: 'Keine Trades in diesem Monat',
    greenDays: 'grün', redDays: 'rot', daysWord: 'Tage', best: 'Bester', worst: 'Schlechtester',
    legendLoss: 'Verlust', legendGain: 'Gewinn', outlier: 'ausserhalb der Skala', scaleCap: 'Skala bis',
    realizedTotal: 'Realisierter P&L', closing: 'Schliessungen', opening: 'Eröffnungen', buy: 'Kauf', sell: 'Verkauf',
    openWord: 'Eröffnung', closeWord: 'Schliessung', loadingTrades: 'Trades werden geladen', tradesError: 'Trades konnten nicht geladen werden.',
    calError: 'Kalender konnte nicht geladen werden.', retry: 'Erneut versuchen', close: 'Schliessen', shown: 'Zeige', of: 'von', noRows: 'Keine Trades an diesem Tag.',
    weekdayHeader: 'Trade-Kalender', yearOf: 'Jahr', openMonth: 'Öffnen',
  },
} as const;

type Copy = (typeof COPY)[keyof typeof COPY];

export function startOfMonth(d: Date): Date {
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

function monthKey(d: Date): string {
  return isoDate(d).slice(0, 7);
}

function getISOWeek(d: Date): number {
  const target = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  const dayNum = target.getUTCDay() || 7;
  target.setUTCDate(target.getUTCDate() + 4 - dayNum);
  const yearStart = new Date(Date.UTC(target.getUTCFullYear(), 0, 1));
  return Math.ceil((((target.getTime() - yearStart.getTime()) / 86400000) + 1) / 7);
}

/** Latest month that has trades in `rows` (day buckets), or null. */
export function latestTradeMonth(rows: RealizedTimeseriesRow[]): Date | null {
  let latest = '';
  rows.forEach((row) => {
    if (row.bucket.length === 10 && row.trades > 0 && row.bucket > latest) latest = row.bucket;
  });
  return latest ? startOfMonth(parseLocalDate(latest)) : null;
}

function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (onChange) => {
      const mql = window.matchMedia(query);
      mql.addEventListener('change', onChange);
      return () => mql.removeEventListener('change', onChange);
    },
    () => window.matchMedia(query).matches,
    () => false,
  );
}

interface Cell { date: Date; inMonth: boolean; iso: string; row: number; col: number }

function buildCells(y: number, m: number): Cell[] {
  const first = new Date(y, m, 1);
  const gridStart = new Date(first);
  gridStart.setDate(first.getDate() - ((first.getDay() + 6) % 7));
  const out: Cell[] = [];
  for (let i = 0; i < 42; i += 1) {
    const d = new Date(gridStart);
    d.setDate(gridStart.getDate() + i);
    out.push({ date: d, inMonth: d.getMonth() === first.getMonth(), iso: isoDate(d), row: Math.floor(i / 7), col: i % 7 });
  }
  return out;
}

interface Range { total: number; trades: number; green: number; red: number; best?: { iso: string; v: number }; worst?: { iso: string; v: number } }

function rangeStats(byDate: Map<string, Entry>, prefix: string): Range {
  const out: Range = { total: 0, trades: 0, green: 0, red: 0 };
  byDate.forEach((entry, iso) => {
    if (!iso.startsWith(prefix)) return;
    out.total += entry.realized;
    out.trades += entry.trades;
    if (entry.realized > 0) {
      out.green += 1;
      if (!out.best || entry.realized > out.best.v) out.best = { iso, v: entry.realized };
    } else if (entry.realized < 0) {
      out.red += 1;
      if (!out.worst || entry.realized < out.worst.v) out.worst = { iso, v: entry.realized };
    }
  });
  return out;
}

function signClass(v: number): '' | 'pos' | 'neg' {
  return v > 0 ? 'pos' : v < 0 ? 'neg' : '';
}

function opsLabel(n: number, copy: Copy): string {
  return `${n} ${n === 1 ? copy.op : copy.ops}`;
}

export function TradeCalendar({ currency, month, onMonthChange, pending = false }: Props) {
  const { language, locale, privacyMode, t } = useI18n();
  const copy = COPY[language];
  const isAgenda = useMediaQuery('(max-width: 599px)');
  const viewMonth = startOfMonth(month);
  const viewKey = monthKey(viewMonth);

  // The calendar browses every month, so it keeps its own unfiltered series
  // (the page-filtered one only steers which month opens first).
  const allDays = useQuery({
    queryKey: ['portfolio.realized', 'all', 'day', 'calendar'],
    queryFn: () => api.realizedTimeseries({ period: 'all' }, 'day'),
  });
  const loading = allDays.isLoading || pending;

  const [view, setView] = useState<View>('month');
  const [dir, setDir] = useState<Dir>('none');
  const [drawerDay, setDrawerDay] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [focusIso, setFocusIso] = useState<string | null>(null);
  const [pendingDay, setPendingDay] = useState<string | null>(null);
  const [seenKey, setSeenKey] = useState(viewKey);
  const [intro, setIntro] = useState(true);
  const stageRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (loading) return undefined;
    const id = window.setTimeout(() => setIntro(false), 700);
    return () => window.clearTimeout(id);
  }, [loading]);

  const weekdays = useMemo(() => Array.from({ length: 7 }, (_, index) => new Intl.DateTimeFormat(locale, { weekday: 'short' })
    .format(new Date(2024, 0, 1 + index))
    .replace('.', '')), [locale]);

  const byDate = useMemo(() => {
    const map = new Map<string, Entry>();
    (allDays.data ?? []).forEach((row) => {
      if (row.bucket.length === 10 && row.trades > 0) map.set(row.bucket, { realized: row.realized, trades: row.trades });
    });
    return map;
  }, [allDays.data]);

  const cap = useMemo(() => heatCap(Array.from(byDate.values()).map((e) => e.realized)), [byDate]);

  const viewYear = month.getFullYear();
  const viewMonthIdx = month.getMonth();
  const cells = useMemo(() => buildCells(viewYear, viewMonthIdx), [viewYear, viewMonthIdx]);

  const weeks = useMemo(() => {
    const rows: Array<{ days: Cell[]; total: number; trades: number; iso: number }> = [];
    for (let i = 0; i < 6; i += 1) {
      const days = cells.slice(i * 7, i * 7 + 7);
      if (!days.some((d) => d.inMonth)) continue;
      let total = 0;
      let trades = 0;
      days.forEach((d) => {
        const e = d.inMonth ? byDate.get(d.iso) : undefined;
        if (e) { total += e.realized; trades += e.trades; }
      });
      rows.push({ days, total, trades, iso: getISOWeek(days[0].date) });
    }
    return rows;
  }, [cells, byDate]);

  const tradeIsos = useMemo(
    () => cells.filter((c) => c.inMonth && byDate.has(c.iso)).map((c) => c.iso),
    [cells, byDate],
  );
  // Agenda lists the newest day first.
  const navList = useMemo(() => (isAgenda ? [...tradeIsos].reverse() : tradeIsos), [isAgenda, tradeIsos]);
  const activeIso = focusIso && tradeIsos.includes(focusIso) ? focusIso : tradeIsos[tradeIsos.length - 1];

  const scopePrefix = view === 'year' ? String(viewMonth.getFullYear()) : viewKey;
  const stats = useMemo(() => rangeStats(byDate, scopePrefix), [byDate, scopePrefix]);

  const countedTotal = useCountUp(stats.total, !privacyMode);
  const countedGreen = useCountUp(stats.green, true);
  const countedRed = useCountUp(stats.red, true);

  const dayLabel = (iso: string, long = false) => new Intl.DateTimeFormat(locale, long
    ? { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' }
    : { day: 'numeric', month: 'short', year: 'numeric' }).format(parseLocalDate(iso));

  const dayAria = (iso: string, entry: Entry) => {
    const parts = [dayLabel(iso), `${entry.trades} ${entry.trades === 1 ? copy.operation : copy.operations}`];
    parts.push(entry.realized === 0 ? copy.noRealized : `${copy.realized} ${formatSignedCompactCurrency(entry.realized, currency, locale)}`);
    return parts.join(' · ');
  };

  const focusDay = (iso: string) => {
    setFocusIso(iso);
    window.requestAnimationFrame(() => {
      stageRef.current?.querySelector<HTMLElement>(`[data-iso="${iso}"]`)?.focus();
    });
  };

  const toggleDay = (iso: string) => {
    setFocusIso(iso);
    if (drawerOpen && drawerDay === iso) {
      setDrawerOpen(false);
    } else {
      setDrawerDay(iso);
      setDrawerOpen(true);
    }
  };

  const jumpToDay = (iso: string) => {
    const target = startOfMonth(parseLocalDate(iso));
    setView('month');
    if (monthKey(target) === viewKey) {
      setDrawerDay(iso);
      setDrawerOpen(true);
      focusDay(iso);
    } else {
      setPendingDay(iso);
      onMonthChange(target);
    }
  };

  const changeView = (next: View) => {
    if (next === view) return;
    setDir('fade');
    setDrawerOpen(false);
    setView(next);
  };

  const step = (n: number) => onMonthChange(addMonths(viewMonth, view === 'year' ? n * 12 : n));

  const gotoToday = () => onMonthChange(startOfMonth(new Date()));

  const onDayKeyDown = (event: KeyboardEvent<HTMLElement>, iso: string) => {
    if (event.key === 'Escape' && drawerOpen) {
      event.preventDefault();
      setDrawerOpen(false);
      return;
    }
    const idx = navList.indexOf(iso);
    if (idx < 0) return;
    let next: string | undefined;
    if (event.key === 'Home') next = navList[0];
    else if (event.key === 'End') next = navList[navList.length - 1];
    else if (isAgenda) {
      if (event.key === 'ArrowDown') next = navList[Math.min(idx + 1, navList.length - 1)];
      if (event.key === 'ArrowUp') next = navList[Math.max(idx - 1, 0)];
    } else if (event.key === 'ArrowRight') next = navList[Math.min(idx + 1, navList.length - 1)];
    else if (event.key === 'ArrowLeft') next = navList[Math.max(idx - 1, 0)];
    else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      const from = cells.find((c) => c.iso === iso);
      if (from) {
        const stepRow = event.key === 'ArrowDown' ? 1 : -1;
        for (let r = from.row + stepRow; r >= 0 && r < 6 && !next; r += stepRow) {
          const candidates = cells.filter((c) => c.row === r && c.inMonth && byDate.has(c.iso));
          if (candidates.length) {
            next = candidates.reduce((best, c) => (Math.abs(c.col - from.col) < Math.abs(best.col - from.col) ? c : best)).iso;
          }
        }
      }
    }
    if (next) {
      event.preventDefault();
      focusDay(next);
    }
  };

  // Adjust-during-render: a month change closes the drawer and sets the slide
  // direction in the same pass (unless a "jump to day" is waiting for it).
  if (viewKey !== seenKey) {
    setSeenKey(viewKey);
    setDir(viewKey > seenKey ? 'next' : 'prev');
    if (pendingDay && pendingDay.startsWith(viewKey)) {
      setDrawerDay(pendingDay);
      setDrawerOpen(true);
      setFocusIso(pendingDay);
      setPendingDay(null);
    } else {
      setDrawerOpen(false);
      setFocusIso(null);
    }
  }

  const monthLabel = new Intl.DateTimeFormat(locale, { month: 'long', year: 'numeric' }).format(viewMonth);
  const headerLabel = view === 'year' ? String(viewMonth.getFullYear()) : monthLabel;
  const heatStyle = (value: number): CSSProperties => ({ '--heat': heatAlpha(value, cap) } as CSSProperties);

  const extremeButton = (kind: 'best' | 'worst', point?: { iso: string; v: number }) => (point ? (
    <button type="button" className="calendar-extreme" onClick={() => jumpToDay(point.iso)}>
      {kind === 'best' ? copy.best : copy.worst}{' '}
      <strong>{new Intl.DateTimeFormat(locale, { day: 'numeric', month: 'short' }).format(parseLocalDate(point.iso))}</strong>{' '}
      {formatSignedCompactCurrency(point.v, currency, locale)}
    </button>
  ) : null);

  const totalText = formatSignedCompactCurrency(typeof countedTotal === 'number' ? countedTotal : stats.total, currency, locale);

  const renderDrawer = (id: string, dayIso: string | null, open: boolean) => (
    <div className={`calendar-drawer${open ? ' open' : ''}`} id={id}>
      <div className="calendar-drawer-clip">
        {dayIso ? (
          <DayDrawer
            iso={dayIso}
            entry={byDate.get(dayIso)}
            currency={currency}
            copy={copy}
            label={dayLabel(dayIso, true)}
            onClose={() => { setDrawerOpen(false); focusDay(dayIso); }}
            active={open}
          />
        ) : null}
      </div>
    </div>
  );

  const stageClass = `calendar-stage dir-${dir}`;
  const stageKey = `${view}-${view === 'year' ? viewMonth.getFullYear() : viewKey}-${isAgenda ? 'a' : 'g'}`;

  return (
    <div className="trade-calendar">
      <div className="calendar-header">
        <div className="calendar-nav">
          <button type="button" onClick={() => step(-1)} aria-label={t('common.previous')}>‹</button>
          <span className="calendar-month-label" aria-live="polite">{headerLabel}</span>
          <button type="button" onClick={() => step(1)} aria-label={t('common.next')}>›</button>
          <button type="button" className="calendar-today" onClick={gotoToday}>
            {view === 'year' ? copy.thisYear : copy.thisMonth}
          </button>
        </div>
        <div className="calendar-toggle" role="group" aria-label={copy.weekdayHeader}>
          <button type="button" aria-pressed={view === 'month'} onClick={() => changeView('month')}>{copy.month}</button>
          <button type="button" aria-pressed={view === 'year'} onClick={() => changeView('year')}>{copy.year}</button>
        </div>
      </div>

      {loading ? (
        <div className="calendar-skeleton" role="status" aria-busy="true" aria-label={t('common.loading')} style={{ marginTop: 14 }}>
          {isAgenda
            ? Array.from({ length: 6 }, (_, i) => <div key={i} className="skeleton agenda" />)
            : Array.from({ length: 5 }, (_, r) => (
              <div key={r} className="row">
                {Array.from({ length: 8 }, (_, c) => <div key={c} className="skeleton cell" />)}
              </div>
            ))}
        </div>
      ) : allDays.isError ? (
        <div className="calendar-error" role="alert">
          <span>{copy.calError}</span>
          <button type="button" onClick={() => { void allDays.refetch(); }}>{copy.retry}</button>
        </div>
      ) : (
        <>
          <div className="calendar-stats">
            <div className="calendar-total">
              <span className="calendar-stat-label">{t('common.total')} {view === 'year' ? t('common.year') : t('common.month')}</span>
              <strong className={signClass(stats.total)}>{totalText}</strong>
              <span className="calendar-total-sub">{opsLabel(stats.trades, copy)}</span>
            </div>
            <div className="calendar-facts">
              <span>
                <span className="pos">{formatNumber(Math.round(Number(countedGreen ?? stats.green)))}</span> {copy.daysWord} {copy.greenDays}
                {' · '}
                <span className="neg">{formatNumber(Math.round(Number(countedRed ?? stats.red)))}</span> {copy.redDays}
              </span>
              {extremeButton('best', stats.best)}
              {extremeButton('worst', stats.worst)}
            </div>
          </div>

          <div className="calendar-legend" aria-hidden="true">
            <span>{copy.legendLoss}</span>
            <span className="calendar-legend-swatches">
              {[1, 0.5, 0.1].map((v) => (
                <span key={`n${v}`} className="calendar-legend-swatch" style={{ '--heat-color': 'var(--color-negative)', '--heat': heatAlpha(v, 1) } as CSSProperties} />
              ))}
              <span className="calendar-legend-swatch neutral" />
              {[0.1, 0.5, 1].map((v) => (
                <span key={`p${v}`} className="calendar-legend-swatch" style={{ '--heat-color': 'var(--color-positive)', '--heat': heatAlpha(v, 1) } as CSSProperties} />
              ))}
            </span>
            <span>{copy.legendGain}</span>
            <span>· {copy.scaleCap} {formatCompactCurrency(cap, currency, locale)}</span>
            <span className="outlier-dot" /> <span>{copy.outlier}</span>
          </div>

          <div
            key={stageKey}
            ref={stageRef}
            className={stageClass}
            onKeyDown={(e) => {
              if (e.key === 'Escape' && drawerOpen) { e.preventDefault(); setDrawerOpen(false); }
            }}
          >
            {view === 'year' ? (
              <YearView
                year={viewMonth.getFullYear()}
                byDate={byDate}
                cap={cap}
                currency={currency}
                locale={locale}
                copy={copy}
                onPick={(m) => { setDir('fade'); setView('month'); onMonthChange(m); }}
              />
            ) : isAgenda ? (
              <AgendaList
                isos={navList}
                byDate={byDate}
                cap={cap}
                currency={currency}
                locale={locale}
                copy={copy}
                activeIso={activeIso}
                drawerDay={drawerDay}
                drawerOpen={drawerOpen}
                dayAria={dayAria}
                onToggle={toggleDay}
                onKeyDown={onDayKeyDown}
                renderDrawer={renderDrawer}
              />
            ) : (
              <div className={`calendar-grid${intro ? ' intro' : ''}`}>
                <div className="calendar-weekday-row">
                  {weekdays.map((wd) => <div key={wd} className="calendar-weekday">{wd}</div>)}
                  <div className="calendar-weekday calendar-weekday-wk">{t('common.week')}</div>
                </div>
                {weeks.map((week) => {
                  const weekHasDrawer = drawerDay !== null && week.days.some((d) => d.iso === drawerDay && d.inMonth);
                  return (
                    <div key={week.iso} className="calendar-week-block">
                      <div className="calendar-week-row">
                        {week.days.map((cell) => {
                          const entry = cell.inMonth ? byDate.get(cell.iso) : undefined;
                          const style = { '--cell-delay': `${Math.min(200, (cell.row + cell.col) * 12)}ms` } as CSSProperties;
                          const weekend = cell.col >= 5;
                          if (!cell.inMonth) {
                            return <div key={cell.iso} className="calendar-day outside" style={style}><div className="day-number">{cell.date.getDate()}</div></div>;
                          }
                          if (!entry) {
                            return <div key={cell.iso} className={`calendar-day idle${weekend ? ' weekend' : ''}`} style={style}><div className="day-number">{cell.date.getDate()}</div></div>;
                          }
                          const tone = entry.realized > 0 ? 'positive' : entry.realized < 0 ? 'negative' : 'activity';
                          const expanded = drawerOpen && drawerDay === cell.iso;
                          return (
                            <button
                              key={cell.iso}
                              type="button"
                              data-iso={cell.iso}
                              className={`calendar-day ${tone}${weekend ? ' weekend' : ''}`}
                              style={{ ...style, ...heatStyle(entry.realized) }}
                              tabIndex={cell.iso === activeIso ? 0 : -1}
                              aria-label={dayAria(cell.iso, entry)}
                              aria-expanded={expanded}
                              aria-controls={`calendar-drawer-w${week.iso}`}
                              onClick={() => toggleDay(cell.iso)}
                              onKeyDown={(e) => onDayKeyDown(e, cell.iso)}
                              onFocus={() => setFocusIso(cell.iso)}
                            >
                              <span className="day-number">{cell.date.getDate()}</span>
                              {entry.realized !== 0 ? (
                                <span className="day-pnl">{formatSignedCompactCurrency(entry.realized, '', locale)}</span>
                              ) : null}
                              <span className="day-ops">{opsLabel(entry.trades, copy)}</span>
                              {Math.abs(entry.realized) > cap ? <span className="outlier-dot" aria-hidden="true" /> : null}
                            </button>
                          );
                        })}
                        <div className="calendar-week-total" style={{ '--cell-delay': `${Math.min(200, (weeks.indexOf(week) + 7) * 12)}ms` } as CSSProperties}>
                          <div className="wk-label">{t('common.week')}{week.iso}</div>
                          {week.trades > 0 ? (
                            <div className={`wk-pnl ${signClass(week.total)}`}>
                              {week.total === 0 ? '—' : formatSignedCompactCurrency(week.total, '', locale)}
                            </div>
                          ) : (
                            <div className="wk-pnl wk-pnl-empty">—</div>
                          )}
                        </div>
                      </div>
                      {renderDrawer(`calendar-drawer-w${week.iso}`, weekHasDrawer ? drawerDay : null, weekHasDrawer && drawerOpen)}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

/* ─────────────────────────── Agenda (mobile) ─────────────────────────── */

interface AgendaProps {
  isos: string[];
  byDate: Map<string, Entry>;
  cap: number;
  currency: string;
  locale: string;
  copy: Copy;
  activeIso?: string;
  drawerDay: string | null;
  drawerOpen: boolean;
  dayAria: (iso: string, entry: Entry) => string;
  onToggle: (iso: string) => void;
  onKeyDown: (event: KeyboardEvent<HTMLElement>, iso: string) => void;
  renderDrawer: (id: string, dayIso: string | null, open: boolean) => React.ReactNode;
}

function AgendaList({ isos, byDate, cap, currency, locale, copy, activeIso, drawerDay, drawerOpen, dayAria, onToggle, onKeyDown, renderDrawer }: AgendaProps) {
  if (isos.length === 0) return <div className="agenda-empty">{copy.noTrades}</div>;
  return (
    <ul className="calendar-agenda">
      {isos.map((iso) => {
        const entry = byDate.get(iso)!;
        const date = parseLocalDate(iso);
        const bar = Math.sqrt(Math.min(Math.abs(entry.realized), cap) / cap) * 50;
        const expanded = drawerOpen && drawerDay === iso;
        return (
          <li key={iso}>
            <button
              type="button"
              data-iso={iso}
              className="calendar-agenda-row"
              tabIndex={iso === activeIso ? 0 : -1}
              aria-label={dayAria(iso, entry)}
              aria-expanded={expanded}
              aria-controls={`calendar-drawer-${iso}`}
              onClick={() => onToggle(iso)}
              onKeyDown={(e) => onKeyDown(e, iso)}
            >
              <span className="agenda-date">
                <b>{date.getDate()}</b>
                <span>{new Intl.DateTimeFormat(locale, { weekday: 'short' }).format(date).replace('.', '')}</span>
              </span>
              <span className="agenda-track" aria-hidden="true">
                {entry.realized !== 0 ? (
                  <span
                    className={`agenda-bar ${entry.realized > 0 ? 'positive' : 'negative'}`}
                    style={{ '--bar': `${bar}%`, '--heat': heatAlpha(entry.realized, cap) } as CSSProperties}
                  />
                ) : null}
              </span>
              <span className="agenda-values">
                {entry.realized !== 0 ? <b>{formatSignedCompactCurrency(entry.realized, currency, locale)}</b> : null}
                <span>{opsLabel(entry.trades, copy)}</span>
              </span>
            </button>
            {renderDrawer(`calendar-drawer-${iso}`, drawerDay === iso ? drawerDay : null, expanded)}
          </li>
        );
      })}
    </ul>
  );
}

/* ─────────────────────────── Year view ─────────────────────────── */

interface YearProps {
  year: number;
  byDate: Map<string, Entry>;
  cap: number;
  currency: string;
  locale: string;
  copy: Copy;
  onPick: (month: Date) => void;
}

function YearView({ year, byDate, cap, currency, locale, copy, onPick }: YearProps) {
  return (
    <div className="calendar-year-grid">
      {Array.from({ length: 12 }, (_, m) => {
        const first = new Date(year, m, 1);
        const prefix = `${year}-${String(m + 1).padStart(2, '0')}`;
        const stats = rangeStats(byDate, prefix);
        const lead = (first.getDay() + 6) % 7;
        const daysInMonth = new Date(year, m + 1, 0).getDate();
        const name = new Intl.DateTimeFormat(locale, { month: 'long' }).format(first);
        const aria = `${name} ${year} · ${opsLabel(stats.trades, copy)} · ${stats.trades === 0 ? copy.noRealized : `${copy.realized} ${formatSignedCompactCurrency(stats.total, currency, locale)}`}`;
        return (
          <button key={m} type="button" className="mini-month" aria-label={aria} onClick={() => onPick(first)}>
            <span className="mini-month-head">
              <span className="mini-month-name">{name}</span>
              {stats.trades > 0 ? (
                <span className={`mini-month-total ${signClass(stats.total)}`}>{stats.total === 0 ? '—' : formatSignedCompactCurrency(stats.total, '', locale)}</span>
              ) : null}
            </span>
            <span className="mini-month-days" aria-hidden="true">
              {Array.from({ length: lead }, (_, i) => <span key={`b${i}`} className="mini-day blank" />)}
              {Array.from({ length: daysInMonth }, (_, d) => {
                const entry = byDate.get(`${prefix}-${String(d + 1).padStart(2, '0')}`);
                const tone = !entry ? '' : entry.realized > 0 ? 'positive' : entry.realized < 0 ? 'negative' : 'activity';
                return <span key={d} className={`mini-day ${tone}`} style={entry ? ({ '--heat': heatAlpha(entry.realized, cap) } as CSSProperties) : undefined} />;
              })}
              {Array.from({ length: 42 - lead - daysInMonth }, (_, i) => <span key={`t${i}`} className="mini-day blank" />)}
            </span>
            <span className="mini-month-foot">{stats.trades > 0 ? opsLabel(stats.trades, copy) : '—'}</span>
          </button>
        );
      })}
    </div>
  );
}

/* ─────────────────────────── Day drawer ─────────────────────────── */

interface DrawerProps {
  iso: string;
  entry?: Entry;
  currency: string;
  copy: Copy;
  label: string;
  active: boolean;
  onClose: () => void;
}

function isClosing(row: TradeRow): boolean {
  return (row.fifo_pnl_realized_base ?? 0) !== 0 || (row.open_close ?? '').toUpperCase().includes('C');
}

function DayDrawer({ iso, entry, currency, copy, label, active, onClose }: DrawerProps) {
  const { locale } = useI18n();
  const q = useQuery({
    queryKey: ['trades.day', iso],
    queryFn: () => api.trades({ from: iso, to: iso, limit: 200 }),
    staleTime: 5 * 60_000,
    // Another day's trades under this day's heading would be wrong data, not
    // a stale refresh: opt out of the keepPreviousData default.
    placeholderData: () => undefined,
    enabled: active,
  });
  const rows = q.data?.rows ?? [];
  const closing = rows.filter(isClosing).sort((a, b) => Math.abs(b.fifo_pnl_realized_base ?? 0) - Math.abs(a.fifo_pnl_realized_base ?? 0));
  const opening = rows.filter((r) => !isClosing(r)).sort((a, b) => a.symbol.localeCompare(b.symbol));

  const sideText = (row: TradeRow) => {
    const side = (row.buy_sell ?? '').toUpperCase().startsWith('B') ? copy.buy : (row.buy_sell ? copy.sell : '');
    const oc = (row.open_close ?? '').toUpperCase().includes('C') ? copy.closeWord : (row.open_close ? copy.openWord : '');
    return [side, oc].filter(Boolean).join(' · ');
  };

  const renderRow = (row: TradeRow, opening: boolean) => {
    const pnl = row.fifo_pnl_realized_base ?? 0;
    const qty = Math.abs(row.quantity ?? 0);
    return (
      <li key={row.trade_id} className={`day-drawer-row${opening ? ' opening' : ''}`}>
        <span className="day-drawer-sym" title={row.description ?? row.symbol}>{row.symbol}</span>
        <span className="day-drawer-side">{sideText(row)}</span>
        <span className="day-drawer-qty">{row.quantity != null ? formatNumber(qty, Number.isInteger(qty) ? 0 : 2, locale) : ''}</span>
        <span className={`day-drawer-pnl ${signClass(pnl)}`}>{pnl === 0 ? '—' : `${pnl > 0 ? '+' : '−'}${formatCurrency(Math.abs(pnl), currency, locale)}`}</span>
      </li>
    );
  };

  return (
    <div className="day-drawer" role="region" aria-label={label}>
      <div className="day-drawer-head">
        <span className="day-drawer-title">{label}</span>
        <span className="day-drawer-sum">
          {entry ? <>{opsLabel(entry.trades, copy)} · {entry.realized === 0 ? copy.noRealized : <>{copy.realizedTotal} <b>{formatSignedCompactCurrency(entry.realized, currency, locale)}</b></>}</> : null}
        </span>
        <button type="button" className="day-drawer-close" onClick={onClose}>{copy.close}</button>
      </div>
      {q.isLoading ? (
        <div role="status" aria-busy="true" aria-label={copy.loadingTrades}>
          {[0, 1, 2].map((i) => <div key={i} className="skeleton day-drawer-skeleton" />)}
        </div>
      ) : q.isError ? (
        <div className="day-drawer-state" role="alert">
          <span>{copy.tradesError}</span>
          <button type="button" onClick={() => { void q.refetch(); }}>{copy.retry}</button>
        </div>
      ) : rows.length === 0 ? (
        <div className="day-drawer-state">{copy.noRows}</div>
      ) : (
        <>
          {closing.length > 0 ? (
            <>
              <div className="day-drawer-group">{copy.closing} · {closing.length}</div>
              <ul className="day-drawer-list">{closing.map((r) => renderRow(r, false))}</ul>
            </>
          ) : null}
          {opening.length > 0 ? (
            <>
              <div className="day-drawer-group">{copy.opening} · {opening.length}</div>
              <ul className="day-drawer-list">{opening.map((r) => renderRow(r, true))}</ul>
            </>
          ) : null}
          {q.data && q.data.summary.total > rows.length ? (
            <div className="day-drawer-state">{copy.shown} {rows.length} {copy.of} {q.data.summary.total}</div>
          ) : null}
        </>
      )}
    </div>
  );
}
