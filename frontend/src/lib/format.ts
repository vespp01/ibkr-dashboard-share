import type { PeriodFilters } from './api';

const DEFAULT_LOCALE = 'en-US';
export const PRIVACY_MODE_STORAGE_KEY = 'ibkr-dashboard-privacy-mode';

function readStoredPrivacyMode(): boolean {
  if (typeof window === 'undefined') return false;
  try {
    return window.localStorage.getItem(PRIVACY_MODE_STORAGE_KEY) === 'true';
  } catch {
    return false;
  }
}

// Module-level flag so every formatter sees a toggle in the same render.
// I18nProvider calls setPrivacyModeFlag() while it computes its context value,
// before any child renders; localStorage is only the persistence layer.
let privacyModeFlag = readStoredPrivacyMode();

export function setPrivacyModeFlag(enabled: boolean): void {
  privacyModeFlag = enabled;
}

export function isPrivacyModeEnabled(): boolean {
  return privacyModeFlag;
}

// Same pattern for the UI locale, for module-level helpers that have no hook.
let localeFlag = DEFAULT_LOCALE;

export function setFormatLocale(locale: string): void {
  localeFlag = locale;
}

export function currentLocale(): string {
  return localeFlag;
}

const MASK = '••••';

// es-ES does not group 4-digit numbers by default (5755,40 vs 12.093,01);
// 'always' keeps every column consistent. Typed as boolean in the ES2022 lib.
const ALWAYS_GROUP = 'always' as unknown as boolean;

const numberFormatCache = new Map<string, Intl.NumberFormat>();

function numberFormat(locale: string, minDigits: number, maxDigits: number): Intl.NumberFormat {
  const key = `${locale}|${minDigits}|${maxDigits}`;
  let fmt = numberFormatCache.get(key);
  if (!fmt) {
    fmt = new Intl.NumberFormat(locale, {
      minimumFractionDigits: minDigits,
      maximumFractionDigits: maxDigits,
      useGrouping: ALWAYS_GROUP,
    });
    numberFormatCache.set(key, fmt);
  }
  return fmt;
}

function isMissing(value: number | null | undefined): value is null | undefined {
  return value === undefined || value === null || Number.isNaN(value);
}

/** Plain grouped number. Not masked: use it for counts, not for amounts. */
export function formatNumber(value: number | null | undefined, decimals = 0, locale = DEFAULT_LOCALE): string {
  if (isMissing(value)) return '--';
  return numberFormat(locale, decimals, decimals).format(value);
}

export function formatCurrency(value: number | null | undefined, currency = 'CHF', locale = DEFAULT_LOCALE): string {
  if (isMissing(value)) return '--';
  if (isPrivacyModeEnabled()) return `${currency} --`;
  return `${currency} ${numberFormat(locale, 2, 2).format(value)}`;
}

export function formatPercent(value: number | null | undefined, signed = false, locale = DEFAULT_LOCALE): string {
  if (isMissing(value)) return '--';
  if (isPrivacyModeEnabled()) return '--%';
  const sign = signed && value > 0 ? '+' : '';
  return `${sign}${numberFormat(locale, 2, 2).format(value)}%`;
}

function compactBody(value: number, locale: string): string {
  const amount = Math.abs(value);
  if (amount >= 1_000_000) return `${numberFormat(locale, 1, 1).format(value / 1_000_000)}M`;
  if (amount >= 1_000) return `${numberFormat(locale, 1, 1).format(value / 1_000)}k`;
  return numberFormat(locale, 0, 0).format(value);
}

export function formatCompactCurrency(value: number | null | undefined, currency = 'CHF', locale = DEFAULT_LOCALE): string {
  if (isMissing(value)) return '--';
  const prefix = currency ? `${currency} ` : '';
  if (isPrivacyModeEnabled()) return `${prefix}--`;
  return `${prefix}${compactBody(value, locale)}`;
}

/** Compact amount with an explicit sign: "+CHF 1,4k", "−CHF 268", "CHF 0". */
export function formatSignedCompactCurrency(value: number | null | undefined, currency = 'CHF', locale = DEFAULT_LOCALE): string {
  if (isMissing(value)) return '--';
  const prefix = currency ? `${currency} ` : '';
  if (isPrivacyModeEnabled()) return `${prefix}--`;
  if (value === 0) return `${prefix}0`;
  const sign = value > 0 ? '+' : '−';
  return `${sign}${prefix}${compactBody(Math.abs(value), locale)}`;
}

/** Recharts YAxis tickFormatter for amounts: compact, no currency, masked in privacy mode. */
export function axisAmountFormatter(locale = DEFAULT_LOCALE): (value: number) => string {
  return (value: number) => {
    if (isMissing(value)) return '';
    if (isPrivacyModeEnabled()) return MASK;
    return compactBody(value, locale);
  };
}

/** Recharts YAxis tickFormatter for percentages, masked in privacy mode. */
export function axisPercentFormatter(locale = DEFAULT_LOCALE, decimals = 0): (value: number) => string {
  return (value: number) => {
    if (isMissing(value)) return '';
    if (isPrivacyModeEnabled()) return MASK;
    return `${numberFormat(locale, decimals, decimals).format(value)}%`;
  };
}

/** ChartTooltip formatter for amounts. */
export function tooltipCurrencyFormatter(currency: string, locale = DEFAULT_LOCALE): (value: number | string) => string {
  return (value: number | string) => (typeof value === 'number' ? formatCurrency(value, currency, locale) : String(value));
}

/**
 * Parse a backend timestamp. Pipeline routes emit naive ISO strings that are
 * UTC (models._utcnow strips tzinfo); JS would read them as local time.
 */
export function parseBackendTimestamp(value: string | null | undefined): Date | undefined {
  if (!value) return undefined;
  const hasZone = /([zZ]|[+-]\d{2}:?\d{2})$/.test(value);
  const isDateOnly = /^\d{4}-\d{2}-\d{2}$/.test(value);
  const date = new Date(hasZone || isDateOnly ? value : `${value}Z`);
  return Number.isNaN(date.getTime()) ? undefined : date;
}

/** Parse "YYYY-MM-DD" as a LOCAL calendar date (new Date(str) would be UTC midnight). */
export function parseLocalDate(value: string): Date {
  const [y, m, d] = value.split('-').map(Number);
  return new Date(y, (m ?? 1) - 1, d ?? 1);
}

export function formatAsOf(
  dateStr: string | undefined,
  daysStale: number | undefined,
  labels: { asOf: string; asOfOneDay: string; asOfDays: string } = {
    asOf: 'as of {date}',
    asOfOneDay: 'as of {date} · 1 day',
    asOfDays: 'as of {date} · {days} days',
  },
): string | undefined {
  if (!dateStr) return undefined;
  if (daysStale === undefined || daysStale === null || daysStale <= 0) return labels.asOf.replace('{date}', dateStr);
  if (daysStale === 1) return labels.asOfOneDay.replace('{date}', dateStr);
  return labels.asOfDays.replace('{date}', dateStr).replace('{days}', String(daysStale));
}

export function chartLabel(bucket: string, locale = DEFAULT_LOCALE): string {
  const [year, month] = bucket.split('-');
  if (!month) return bucket;
  const date = new Date(Number(year), Number(month) - 1, 1);
  const monthLabel = new Intl.DateTimeFormat(locale, { month: 'short' })
    .format(date)
    .replace('.', '');
  return `${monthLabel} ${year.slice(2)}`;
}

export function periodLabel(filters: PeriodFilters): string {
  if (filters.year !== undefined) return String(filters.year);
  return (filters.period ?? 'all').toUpperCase();
}
