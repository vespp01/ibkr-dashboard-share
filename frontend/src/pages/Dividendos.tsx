import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { UseQueryResult } from '@tanstack/react-query';
import { api, type PeriodFilters, type PositionRow } from '../lib/api';
import { MissingData } from '../components/MissingData';
import { CountryTable } from '../components/dividends/CountryTable';
import { CumulativeChart } from '../components/dividends/CumulativeChart';
import { HeroCard } from '../components/dividends/HeroCard';
import { HoldingsTable, type HoldingRow } from '../components/dividends/HoldingsTable';
import { IncomeChart, type IncomeMode } from '../components/dividends/IncomeChart';
import { IncomeHeatmap } from '../components/dividends/IncomeHeatmap';
import { UpcomingPayments } from '../components/dividends/UpcomingPayments';
import { COPY, fill } from '../components/dividends/copy';
import { computeTtm } from '../components/dividends/income';
import { formatCurrency, formatNumber, formatPercent, parseLocalDate, periodLabel } from '../lib/format';
import { useI18n } from '../lib/i18n';
import '../styles/dividends.css';

interface DividendsSummary {
  gross_base?: number;
  withholding_base?: number;
  net_base?: number;
}

interface IncomeProjection {
  padi_base?: number;
  monthly_base?: number;
  yield_pct?: number;
  yield_on_nav_pct?: number;
  as_of?: string;
  positions_total?: number;
  positions_covered?: number;
  by_symbol?: HoldingRow[];
}

interface Props {
  filters: PeriodFilters;
  filterMode: 'period' | 'year';
  period: PeriodFilters['period'];
  year: number;
  currency: string;
  /** Shared with Resumen. */
  dividends: UseQueryResult<DividendsSummary>;
  /** Derived from overview; shared with Resumen. */
  incomeProjection?: IncomeProjection;
  overviewIsLoading: boolean;
}

type PositionWithConid = PositionRow & { conid?: number };

export function DividendosPage({ filters, currency, dividends, incomeProjection, overviewIsLoading }: Props) {
  const { language, locale } = useI18n();
  const copy = COPY[language];
  const [mode, setMode] = useState<IncomeMode>('month');
  const accountId = filters.account_id;

  const dividendsMonthly = useQuery({
    queryKey: ['dividends.timeseries', filters, 'month'],
    queryFn: () => api.dividendsTimeseries(filters, 'month'),
  });
  const dividendsYearly = useQuery({
    queryKey: ['dividends.timeseries', filters, 'year'],
    queryFn: () => api.dividendsTimeseries(filters, 'year'),
  });
  // Whole history, independent of the page filter: TTM, rolling line, heatmap.
  const allMonthly = useQuery({
    queryKey: ['dividends.timeseries', { period: 'all', account_id: accountId }, 'month'],
    queryFn: () => api.dividendsTimeseries({ period: 'all', account_id: accountId }, 'month'),
  });
  const allDaily = useQuery({
    queryKey: ['dividends.timeseries', { period: 'all', account_id: accountId }, 'day'],
    queryFn: () => api.dividendsTimeseries({ period: 'all', account_id: accountId }, 'day'),
    enabled: mode === 'year',
  });
  const upcoming = useQuery({
    queryKey: ['dividends.upcoming', accountId],
    queryFn: () => api.dividendsUpcoming({ account_id: accountId }),
  });
  const positions = useQuery({
    queryKey: ['portfolio.positions', accountId],
    queryFn: () => api.portfolioPositions({ account_id: accountId }),
  });
  const dividendsByCountry = useQuery({
    queryKey: ['dividends.byCountry', filters],
    queryFn: () => api.dividendsByCountry(filters),
  });

  const costByConid = useMemo(() => {
    const map = new Map<number, number>();
    ((positions.data ?? []) as PositionWithConid[]).forEach((row) => {
      if (row.conid !== undefined && row.cost_basis_money_base !== undefined) {
        map.set(row.conid, (map.get(row.conid) ?? 0) + row.cost_basis_money_base);
      }
    });
    return map;
  }, [positions.data]);

  const ttm = useMemo(() => computeTtm(allMonthly.data ?? []), [allMonthly.data]);

  if (dividends.isError) {
    return <MissingData title={copy.unavailableTitle}>{copy.unavailableBody}</MissingData>;
  }

  const money = (v: number | undefined) => formatCurrency(v, currency, locale);
  const monthYear = new Intl.DateTimeFormat(locale, { month: 'short', year: 'numeric' });
  const dayMonth = new Intl.DateTimeFormat(locale, { day: 'numeric', month: 'short' });

  // TTM card
  const ttmEnd = ttm ? parseLocalDate(`${ttm.endBucket}-01`) : undefined;
  let ttmDelta: string | undefined;
  let ttmKind: 'pos' | 'neg' | 'neutral' = 'neutral';
  if (ttm) {
    if (ttm.yoyPct !== null) {
      ttmDelta = fill(copy.ttmYoy, { pct: formatPercent(ttm.yoyPct, true, locale) });
      ttmKind = ttm.yoyPct >= 0 ? 'pos' : 'neg';
    } else {
      ttmDelta = copy.ttmNoPrior;
    }
  }

  // Upcoming card
  const up = upcoming.data;
  const nextEvent = up?.events[0];
  const nextDelta = upcoming.isError
    ? copy.nextError
    : up && up.totals.count > 0 && nextEvent
      ? fill(copy.nextDelta, { n: formatNumber(up.totals.count, 0, locale), date: dayMonth.format(parseLocalDate(nextEvent.pay_date)) })
      : up
        ? copy.nextNone
        : undefined;

  // Withholding card: average effective rate over the active period.
  const gross = dividends.data?.gross_base;
  const wht = dividends.data?.withholding_base;
  const effective = gross && gross > 0 && wht !== undefined ? (Math.abs(wht) / gross) * 100 : undefined;

  return (
    <div className="dv-page">
      <div className="kpi-grid dv-hero-grid">
        <HeroCard
          primary
          label={ttmEnd ? fill(copy.ttmLabel, { date: monthYear.format(ttmEnd) }) : fill(copy.ttmLabel, { date: '--' })}
          value={ttm?.net}
          format={money}
          delta={ttmDelta}
          deltaKind={ttmKind}
          hint={copy.ttmHint}
          loading={allMonthly.isLoading}
        />
        <HeroCard
          label={copy.nextLabel}
          value={up && up.totals.count > 0 ? up.totals.net_base : undefined}
          format={money}
          delta={nextDelta}
          deltaKind="neutral"
          loading={upcoming.isLoading}
        />
        <HeroCard
          label={copy.padiLabel}
          value={incomeProjection?.padi_base}
          format={money}
          delta={incomeProjection?.yield_pct !== undefined ? fill(copy.padiYield, { pct: formatPercent(incomeProjection.yield_pct, false, locale) }) : undefined}
          deltaKind="pos"
          hint={incomeProjection?.positions_covered !== undefined && incomeProjection?.positions_total !== undefined
            ? fill(copy.padiCoverage, { c: incomeProjection.positions_covered, t: incomeProjection.positions_total })
            : undefined}
          loading={overviewIsLoading}
        />
        <HeroCard
          label={copy.whtLabel}
          value={wht !== undefined ? Math.abs(wht) : undefined}
          format={money}
          delta={effective !== undefined ? fill(copy.whtRate, { pct: formatPercent(effective, false, locale) }) : undefined}
          deltaKind="warn"
          loading={dividends.isLoading}
        />
      </div>

      <div className="dv-split">
        <IncomeChart
          copy={copy}
          currency={currency}
          mode={mode}
          onModeChange={setMode}
          monthly={dividendsMonthly.data}
          yearly={dividendsYearly.data}
          allMonthly={allMonthly.data}
          allDaily={allDaily.data}
          loading={mode === 'month' ? dividendsMonthly.isLoading : dividendsYearly.isLoading}
          error={mode === 'month' ? dividendsMonthly.isError : dividendsYearly.isError}
          exportName={`dividendos-${periodLabel(filters)}-${mode}`}
        />
        <UpcomingPayments copy={copy} data={upcoming.data} loading={upcoming.isLoading} error={upcoming.isError} />
      </div>

      <IncomeHeatmap copy={copy} currency={currency} rows={allMonthly.data} loading={allMonthly.isLoading} error={allMonthly.isError} />

      <div className="dv-split dv-split-even">
        <HoldingsTable
          copy={copy}
          currency={currency}
          padi={incomeProjection?.padi_base}
          rows={incomeProjection?.by_symbol}
          costByConid={costByConid}
          loading={overviewIsLoading}
          error={false}
        />
        <CumulativeChart
          copy={copy}
          currency={currency}
          rows={dividendsMonthly.data}
          loading={dividendsMonthly.isLoading}
          error={dividendsMonthly.isError}
        />
      </div>

      <CountryTable copy={copy} currency={currency} rows={dividendsByCountry.data} loading={dividendsByCountry.isLoading} error={dividendsByCountry.isError} />
    </div>
  );
}
