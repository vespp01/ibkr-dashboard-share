import type { PeriodFilters } from './api';

const DEFAULT_LOCALE = 'en-US';
export const PRIVACY_MODE_STORAGE_KEY = 'ibkr-dashboard-privacy-mode';

export function isPrivacyModeEnabled(): boolean {
  if (typeof window === 'undefined') return false;
  return window.localStorage.getItem(PRIVACY_MODE_STORAGE_KEY) === 'true';
}

export function formatCurrency(value: number | undefined, currency = 'CHF', locale = DEFAULT_LOCALE): string {
  if (value === undefined || value === null || Number.isNaN(value)) return '--';
  if (isPrivacyModeEnabled()) return `${currency} --`;
  return `${currency} ${value.toLocaleString(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function formatPercent(value: number | undefined, signed = false, locale = DEFAULT_LOCALE): string {
  if (value === undefined || value === null || Number.isNaN(value)) return '--';
  if (isPrivacyModeEnabled()) return '--%';
  const sign = signed && value > 0 ? '+' : '';
  return `${sign}${value.toLocaleString(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%`;
}

export function formatCompactCurrency(value: number | undefined, currency = 'CHF', locale = DEFAULT_LOCALE): string {
  if (value === undefined || value === null || Number.isNaN(value)) return '--';
  if (isPrivacyModeEnabled()) return `${currency} --`;
  const amount = Math.abs(value);
  if (amount >= 1_000_000) return `${currency} ${(value / 1_000_000).toLocaleString(locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 })}M`;
  if (amount >= 1_000) return `${currency} ${(value / 1_000).toLocaleString(locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 })}k`;
  return `${currency} ${value.toLocaleString(locale, { maximumFractionDigits: 0 })}`;
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
