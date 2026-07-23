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

export function App() {
  const { t } = useI18n();
  const [activePage, setActivePage] = useState<PageKey>('resumen');
  const [filterMode, setFilterMode] = useState<FilterMode>('period');
  const [period, setPeriod] = useState<PeriodFilters['period']>('all');
  const [year, setYear] = useState<number>(new Date().getFullYear());
  const periodChoices: Array<{ value: PeriodFilters['period']; label: string }> = [
    { value: 'all', label: t('periods.all') },
    { value: 'ytd', label: t('periods.ytd') },
    { value: '1y', label: t('periods.oneYear') },
    { value: '3y', label: t('periods.threeYears') },
    { value: '5y', label: t('periods.fiveYears') },
  ];

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

  const yearOptions = buildYearOptions(coverage.data?.first_date, coverage.data?.last_date);
  const effectiveYear = yearOptions.includes(year) ? year : yearOptions[0] ?? new Date().getFullYear();
  const filters: PeriodFilters = filterMode === 'year' ? { year: effectiveYear } : { period };

  const isOk = health.data?.status === 'ok';
  const daysStale = coverage.data?.days_stale;
  const staleWarn = daysStale !== undefined && daysStale > 30;
  const flexConfigured = pipeline.data?.configured ?? false;
  const latestFlexRun = pipeline.data?.latest_run;
  const flexHealthy = flexConfigured && latestFlexRun?.status === 'success';

  return (
    <div className="app-shell">
      <Sidebar
        active={activePage}
        onChange={setActivePage}
        health={{ ok: isOk, loading: health.isLoading, error: health.isError }}
        flexHealthy={flexHealthy}
        flexConfigured={flexConfigured}
      />

      <Dashboard
        activePage={activePage}
        filters={filters}
        filterMode={filterMode}
        period={period}
        year={effectiveYear}
        yearOptions={yearOptions}
        periodChoices={periodChoices}
        onFilterModeChange={setFilterMode}
        onPeriodChange={setPeriod}
        onYearChange={setYear}
        coverage={coverage.data}
        staleWarn={staleWarn}
        daysStale={daysStale}
      />
    </div>
  );
}
