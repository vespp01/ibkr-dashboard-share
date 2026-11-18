/** Shared heat scale for the trade calendar and the yearly matrix. */

/** p90 of |value| over the non-zero values; days above it are "outliers". */
export function heatCap(values: number[]): number {
  const abs = values.filter((v) => v !== 0 && Number.isFinite(v)).map((v) => Math.abs(v)).sort((a, b) => a - b);
  if (abs.length === 0) return 1;
  return abs[Math.floor(0.9 * (abs.length - 1))] || abs[abs.length - 1] || 1;
}

/** Background alpha: 0.08 for tiny values up to 0.50 at the cap (sqrt keeps small days visible). */
export function heatAlpha(value: number, cap: number): number {
  if (value === 0 || cap <= 0) return 0;
  return 0.08 + 0.42 * Math.sqrt(Math.min(Math.abs(value), cap) / cap);
}
