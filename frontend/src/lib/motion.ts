import { useEffect, useRef, useState, useSyncExternalStore } from 'react';

// Motion tokens mirror tokens.css (--dur-*, --ease-*). Keep both in sync.
export const DURATION = { fast: 120, base: 240, slow: 450, line: 700, countUp: 600 } as const;

const REDUCED_QUERY = '(prefers-reduced-motion: reduce)';

function subscribeReduced(onChange: () => void): () => void {
  if (typeof window === 'undefined' || !window.matchMedia) return () => undefined;
  const mql = window.matchMedia(REDUCED_QUERY);
  mql.addEventListener('change', onChange);
  return () => mql.removeEventListener('change', onChange);
}

function getReduced(): boolean {
  if (typeof window === 'undefined' || !window.matchMedia) return false;
  return window.matchMedia(REDUCED_QUERY).matches;
}

/** True when the OS asks for reduced motion. Updates live. */
export function useReducedMotion(): boolean {
  return useSyncExternalStore(subscribeReduced, getReduced, () => false);
}

/** Recharts series animation props: bars grow 450 ms, lines draw 700 ms, off under reduced motion. */
export function seriesAnimation(kind: 'bar' | 'line' | 'area' | 'pie', reduced: boolean, delay = 0) {
  if (reduced) return { isAnimationActive: false } as const;
  return {
    isAnimationActive: true,
    animationBegin: delay,
    animationDuration: kind === 'bar' ? DURATION.slow : DURATION.line,
    animationEasing: 'ease-out',
  } as const;
}

// cubic-bezier(0.16, 1, 0.3, 1) is close to an exponential ease-out.
function easeOutExpo(t: number): number {
  return t >= 1 ? 1 : 1 - Math.pow(2, -10 * t);
}

/**
 * Animate a number toward `target`. The first resolve counts up from 0;
 * later changes morph from the previous value. Returns `target` directly when
 * motion is reduced, disabled (e.g. privacy mode) or the target is missing.
 */
export function useCountUp(target: number | null | undefined, enabled = true, duration: number = DURATION.countUp): number | null | undefined {
  const reduced = useReducedMotion();
  const active = enabled && !reduced && typeof target === 'number' && Number.isFinite(target);
  const [display, setDisplay] = useState<number | null | undefined>(active ? 0 : target);
  const fromRef = useRef<number>(0);
  const shownRef = useRef<number>(0);

  useEffect(() => {
    if (!active) return undefined;
    const from = fromRef.current;
    const to = target as number;
    if (from === to) return undefined;
    let frame = 0;
    const start = performance.now();
    const tick = (now: number) => {
      const progress = Math.min(1, (now - start) / duration);
      const value = from + (to - from) * easeOutExpo(progress);
      shownRef.current = value;
      setDisplay(value);
      if (progress < 1) {
        frame = requestAnimationFrame(tick);
      } else {
        fromRef.current = to;
      }
    };
    frame = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(frame);
      // An interrupted run continues from where it stopped.
      fromRef.current = shownRef.current;
    };
  }, [active, target, duration]);

  // Before the first frame of a run, `display` may still hold the pre-data
  // value (undefined): start from 0 instead of flashing a placeholder.
  return active ? (display ?? 0) : target;
}

export interface ChartTheme {
  grid: string;
  axis: string;
  primary: string;
  positive: string;
  negative: string;
  warning: string;
  muted: string;
  cursorFill: string;
}

const FALLBACK_THEME: ChartTheme = {
  grid: 'rgba(255,255,255,0.05)',
  axis: '#8b88a6',
  primary: '#a855f7',
  positive: '#10b981',
  negative: '#ef4444',
  warning: '#f59e0b',
  muted: '#8b88a6',
  cursorFill: 'rgba(168,85,247,0.06)',
};

let cachedTheme: ChartTheme | null = null;

/** Chart colours read once from the CSS tokens (recharts needs literal colours). */
export function chartTheme(): ChartTheme {
  if (cachedTheme) return cachedTheme;
  if (typeof window === 'undefined' || typeof getComputedStyle === 'undefined') return FALLBACK_THEME;
  const css = getComputedStyle(document.documentElement);
  const read = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback;
  cachedTheme = {
    grid: read('--chart-grid', FALLBACK_THEME.grid),
    axis: read('--chart-axis', FALLBACK_THEME.axis),
    primary: read('--color-primary', FALLBACK_THEME.primary),
    positive: read('--color-positive', FALLBACK_THEME.positive),
    negative: read('--color-negative', FALLBACK_THEME.negative),
    warning: read('--color-warning', FALLBACK_THEME.warning),
    muted: read('--fg-muted', FALLBACK_THEME.muted),
    cursorFill: read('--chart-cursor', FALLBACK_THEME.cursorFill),
  };
  return cachedTheme;
}
