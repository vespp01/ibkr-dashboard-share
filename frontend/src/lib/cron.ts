import type { Language } from './i18n';

// APScheduler reads a numeric day-of-week as 0 = Monday ... 6 = Sunday (not the
// POSIX 0 = Sunday), so "1-5" really fires Tuesday to Saturday. Day names are
// unambiguous and are rendered as they are.
const DAY_NAMES: Record<Language, string[]> = {
  es: ['lun', 'mar', 'mié', 'jue', 'vie', 'sáb', 'dom'],
  en: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
  de: ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So'],
};
const NAME_INDEX: Record<string, number> = { mon: 0, tue: 1, wed: 2, thu: 3, fri: 4, sat: 5, sun: 6 };

const WORDS = {
  es: { everyMin: (n: number) => `cada ${n} min`, everyHours: (n: number) => `cada ${n} h`, hourly: 'cada hora', daily: 'cada día', at: 'a las', days: 'solo' },
  en: { everyMin: (n: number) => `every ${n} min`, everyHours: (n: number) => `every ${n} h`, hourly: 'every hour', daily: 'every day', at: 'at', days: 'on' },
  de: { everyMin: (n: number) => `alle ${n} Min`, everyHours: (n: number) => `alle ${n} Std`, hourly: 'jede Stunde', daily: 'taeglich', at: 'um', days: 'nur' },
} as const;

function dayIndex(token: string): number | null {
  const lower = token.toLowerCase();
  if (lower in NAME_INDEX) return NAME_INDEX[lower];
  const n = Number(token);
  return Number.isInteger(n) && n >= 0 && n <= 6 ? n : null;
}

function describeDays(field: string, language: Language): string | null {
  if (field === '*') return null;
  const names = DAY_NAMES[language];
  const parts = field.split(',').map((part) => {
    const [from, to] = part.split('-');
    const a = dayIndex(from);
    const b = to === undefined ? a : dayIndex(to);
    if (a === null || b === null) return null;
    return a === b ? names[a] : `${names[a]}–${names[b]}`;
  });
  return parts.every((p) => p !== null) ? parts.join(', ') : null;
}

/** Plain-language form of a 5-field cron string; null when the shape is not covered. */
export function describeCron(cron: string, language: Language): string | null {
  const fields = cron.trim().split(/\s+/);
  if (fields.length !== 5) return null;
  const [minute, hour, dom, month, dow] = fields;
  if (dom !== '*' || month !== '*') return null;
  const words = WORDS[language];
  const days = describeDays(dow, language);
  if (dow !== '*' && days === null) return null;
  const suffix = days ? `, ${words.days} ${days}` : '';

  const everyMin = minute.match(/^\*\/(\d+)$/);
  if (everyMin && hour === '*') return `${words.everyMin(Number(everyMin[1]))}${suffix}`;
  const everyHours = hour.match(/^\*\/(\d+)$/);
  if (everyHours && /^\d+$/.test(minute)) return `${words.everyHours(Number(everyHours[1]))}${suffix}`;
  if (/^\d+$/.test(minute) && hour === '*') return `${words.hourly}${suffix}`;
  if (/^\d+$/.test(minute) && /^\d+$/.test(hour)) {
    const time = `${hour.padStart(2, '0')}:${minute.padStart(2, '0')}`;
    return `${days ? days : words.daily} ${words.at} ${time}`;
  }
  return null;
}
