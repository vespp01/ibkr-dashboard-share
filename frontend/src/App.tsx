import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Dashboard } from './pages/Dashboard';
import { Sidebar, type PageKey } from './components/Sidebar';
import { api, type PeriodFilters } from './lib/api';
import { useI18n } from './lib/i18n';

type FilterMode = 'period' | 'year';

function buildYearOptions(firstDate?: string, lastDate?: string): number[] {
  const firstYear = firstDate ? parseInt(firstDate.slice(0, 4), 10) : new Date().getFullYear();
  const lastYear = lastDate ? parseInt(lastDate.slice(0, 4), 10) : new Date().getFullYear();
  const years: number[] = [];
  for (let year = lastYear; year >= firstYear; year -= 1) years.push(year);
  return years;
}

/** True when the data history reaches back at least `years` calendar years. */
function historySpansYears(firstDate: string | undefined, years: number): boolean {
  if (!firstDate) return false;
  const cutoff = new Date();
  cutoff.setFullYear(cutoff.getFullYear() - years);
  return new Date(`${firstDate}T00:00:00`) <= cutoff;
}

export function App() {
  const { t } = useI18n();
  const [activePage, setActivePage] = useState<PageKey>('resumen');
  const [filterMode, setFilterMode] = useState<FilterMode>('period');
  const [period, setPeriod] = useState<PeriodFilters['period']>('all');
  const [year, setYear] = useState<number>(new Date().getFullYear());

  const health = useQuery({
    queryKey: ['health'],
    queryFn: () => api.health(),
    refetchInterval: 60_000,
    retry: 1,
  });

  const coverage = useQuery({
    queryKey: ['data.coverage'],
    queryFn: () => api.dataCoverage(),
    refetchInterval: 300_000,
    retry: 1,
  });

  const pipeline = useQuery({
    queryKey: ['pipeline.status'],
    queryFn: () => api.pipelineStatus(),
    refetchInterval: 60_000,
    retry: 1,
  });

  // Shares the ['tax.scenarios'] cache with the Fiscal page. The Fiscal tab is
  // hidden until OptimizadorFiscal is actually configured (TAX_SCENARIOS_FILE
  // set → status !== 'not_configured'); the not_configured state is documented
  // in the Integraciones tab instead.
  const tax = useQuery({
    queryKey: ['tax.scenarios'],
    queryFn: () => api.taxScenarios(),
    refetchInterval: 60_000,
    retry: 1,
  });

  // Live status of the optional IB Gateway overlay (the sidebar dot). Shares the
  // cache key with the Integraciones page.
  const integrations = useQuery({
    queryKey: ['integrations.status'],
    queryFn: () => api.integrationsStatus(),
    refetchInterval: 300_000,
    retry: 1,
  });
  const overlayLevel = integrations.data?.integrations.find((row) => row.id === 'live_overlay')?.level;
  const liveOverlayLevel = overlayLevel === 'ok' ? 'ok' : overlayLevel === 'warn' ? 'warn' : overlayLevel === 'error' ? 'err' : 'off';

  // 3Y / 5Y chips equal "All" until the history is long enough (B62).
  const periodChoices: Array<{ value: PeriodFilters['period']; label: string }> = [
    { value: 'mtd', label: t('periods.mtd') },
    { value: 'qtd', label: t('periods.qtd') },
    { value: 'ytd', label: t('periods.ytd') },
    { value: '1y', label: t('periods.oneYear') },
    ...(historySpansYears(coverage.data?.first_date, 3) ? [{ value: '3y' as const, label: t('periods.threeYears') }] : []),
    ...(historySpansYears(coverage.data?.first_date, 5) ? [{ value: '5y' as const, label: t('periods.fiveYears') }] : []),
    { value: 'all', label: t('periods.all') },
  ];

  const yearOptions = buildYearOptions(coverage.data?.first_date, coverage.data?.last_date);
  const effectiveYear = yearOptions.includes(year) ? year : yearOptions[0] ?? new Date().getFullYear();
  const filters: PeriodFilters = filterMode === 'year' ? { year: effectiveYear } : { period };

  const isOk = health.data?.status === 'ok';
  const daysStale = coverage.data?.days_stale;
  // Data age (business days) and pull liveness come from the backend's own
  // rules (/pipeline/status). The old 30-calendar-day coverage check missed
  // the 2026-08-17 → 09-12 freeze (26 days); it stays only as a fallback.
  const dataFreshness = pipeline.data?.data_freshness;
  const pullStaleness = pipeline.data?.staleness;
  let staleMessage: string | undefined;
  if (dataFreshness?.data_is_stale) {
    staleMessage = t('dashboard.staleData', {
      date: dataFreshness.last_data_date ?? '?',
      days: dataFreshness.age_business_days ?? '?',
    });
  } else if (pullStaleness?.is_stale) {
    staleMessage = t('dashboard.stalePull', { hours: Math.round(pullStaleness.age_hours ?? 0) });
  } else if (!dataFreshness && daysStale !== undefined && daysStale > 30) {
    staleMessage = t('dashboard.staleWarning', { days: daysStale });
  }
  const flexConfigured = pipeline.data?.configured ?? false;
  const latestFlexRun = pipeline.data?.latest_run;
  const flexHealthy = flexConfigured && latestFlexRun?.status === 'success';
  // Hidden while loading; only shown once confirmed configured.
  const taxConfigured = tax.data ? tax.data.integration_status !== 'not_configured' : false;
  // If the Fiscal tab vanishes while it's open, fall back to the overview.
  const effectiveActivePage: PageKey =
    activePage === 'fiscal' && !taxConfigured ? 'resumen' : activePage;

  return (
    <div className="app-shell">
      <Sidebar
        active={effectiveActivePage}
        onChange={setActivePage}
        health={{ ok: isOk, loading: health.isLoading, error: health.isError }}
        flexHealthy={flexHealthy}
        flexConfigured={flexConfigured}
        taxConfigured={taxConfigured}
        liveOverlayLevel={liveOverlayLevel}
      />

      <Dashboard
        activePage={effectiveActivePage}
        filters={filters}
        filterMode={filterMode}
        period={period}
        year={effectiveYear}
        yearOptions={yearOptions}
        periodChoices={periodChoices}
        onFilterModeChange={setFilterMode}
        onPeriodChange={setPeriod}
        onYearChange={setYear}
        onNavigate={setActivePage}
        coverage={coverage.data}
        staleMessage={staleMessage}
      />
    </div>
  );
}
