import type { DividendsCountryRow, DividendsTimeseriesRow } from '../../lib/api';

/** Calendar month as a single integer (year * 12 + month0). */
export function monthIndex(bucket: string): number | null {
  const m = /^(\d{4})-(\d{2})/.exec(bucket);
  if (!m) return null;
  return Number(m[1]) * 12 + Number(m[2]) - 1;
}

export function monthKey(index: number): string {
  const year = Math.floor(index / 12);
  return `${year}-${String((index % 12) + 1).padStart(2, '0')}`;
}

function netByMonth(rows: DividendsTimeseriesRow[]): Map<number, number> {
  const out = new Map<number, number>();
  rows.forEach((row) => {
    const idx = monthIndex(row.bucket);
    if (idx !== null) out.set(idx, (out.get(idx) ?? 0) + (row.net ?? 0));
  });
  return out;
}

function windowSum(byMonth: Map<number, number>, endIdx: number, months = 12): number {
  let sum = 0;
  for (let i = endIdx - months + 1; i <= endIdx; i += 1) sum += byMonth.get(i) ?? 0;
  return sum;
}

export interface TtmResult {
  /** Net income of the last 12 complete calendar months. */
  net: number;
  /** Net income of the 12 complete months before those. */
  prior: number;
  /** YoY change in %, null when the prior window is not fully covered or is zero. */
  yoyPct: number | null;
  /** Last complete month, "YYYY-MM". */
  endBucket: string;
}

/** TTM = sum of the last 12 COMPLETE calendar months (current partial month excluded). */
export function computeTtm(rows: DividendsTimeseriesRow[], now: Date = new Date()): TtmResult | null {
  const byMonth = netByMonth(rows);
  if (byMonth.size === 0) return null;
  const endIdx = now.getFullYear() * 12 + now.getMonth() - 1;
  const firstIdx = Math.min(...byMonth.keys());
  const net = windowSum(byMonth, endIdx);
  const prior = windowSum(byMonth, endIdx - 12);
  const priorCovered = firstIdx <= endIdx - 23;
  return {
    net,
    prior,
    yoyPct: priorCovered && prior > 0 ? ((net - prior) / prior) * 100 : null,
    endBucket: monthKey(endIdx),
  };
}

/** Rolling 12-month net per bucket; null until a full 12-month window of history exists. */
export function rollingTtm(rows: DividendsTimeseriesRow[]): Map<string, number | null> {
  const byMonth = netByMonth(rows);
  const out = new Map<string, number | null>();
  if (byMonth.size === 0) return out;
  const firstIdx = Math.min(...byMonth.keys());
  byMonth.forEach((_, idx) => {
    out.set(monthKey(idx), idx - 11 >= firstIdx ? windowSum(byMonth, idx) : null);
  });
  return out;
}

export interface HeatmapModel {
  years: number[];
  /** `${year}-${month1}` → net */
  cells: Map<string, number>;
  yearTotals: Map<number, number>;
  max: number;
}

export function buildHeatmap(rows: DividendsTimeseriesRow[]): HeatmapModel {
  const cells = new Map<string, number>();
  const yearTotals = new Map<number, number>();
  let max = 0;
  rows.forEach((row) => {
    const idx = monthIndex(row.bucket);
    if (idx === null) return;
    const year = Math.floor(idx / 12);
    const month = (idx % 12) + 1;
    const value = (cells.get(`${year}-${month}`) ?? 0) + (row.net ?? 0);
    cells.set(`${year}-${month}`, value);
  });
  cells.forEach((value, key) => {
    const year = Number(key.split('-')[0]);
    yearTotals.set(year, (yearTotals.get(year) ?? 0) + value);
    if (value > max) max = value;
  });
  const years = Array.from(yearTotals.keys()).sort((a, b) => a - b);
  return { years, cells, yearTotals, max };
}

/** Heatmap intensity 0..1 with sqrt scaling so small months stay visible. */
export function heatIntensity(value: number | undefined, max: number): number {
  if (value === undefined || value <= 0 || max <= 0) return 0;
  return Math.sqrt(Math.min(1, value / max));
}

export const NO_COUNTRY = 'NO_COUNTRY';

export interface CountryRow {
  country: string;
  gross: number;
  withholding: number;
  net: number;
}

/** Merge the 'XX' and 'UNKNOWN' buckets into one "no country" row. */
export function mergeCountries(rows: DividendsCountryRow[]): CountryRow[] {
  const map = new Map<string, CountryRow>();
  rows.forEach((row) => {
    const code = row.country === 'XX' || row.country === 'UNKNOWN' || !row.country ? NO_COUNTRY : row.country;
    const acc = map.get(code) ?? { country: code, gross: 0, withholding: 0, net: 0 };
    acc.gross += row.gross ?? 0;
    acc.withholding += row.withholding ?? 0;
    acc.net += row.net ?? 0;
    map.set(code, acc);
  });
  return Array.from(map.values()).sort((a, b) => b.gross - a.gross);
}

/** Same-period-last-year gross+net from day buckets: Jan 1 .. today's month/day of year-1. */
export function lastYearToDate(
  dayRows: DividendsTimeseriesRow[],
  now: Date = new Date(),
): { year: number; gross: number; net: number } {
  const year = now.getFullYear() - 1;
  const cutoff = `${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
  let gross = 0;
  let net = 0;
  dayRows.forEach((row) => {
    if (row.bucket.slice(0, 4) !== String(year)) return;
    if (row.bucket.slice(5) > cutoff) return;
    gross += row.gross ?? 0;
    net += row.net ?? 0;
  });
  return { year, gross, net };
}
