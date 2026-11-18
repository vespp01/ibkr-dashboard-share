import { useIsFetching, useQuery } from '@tanstack/react-query';
import { CarteraPage } from './Cartera';
import { ComisionesPage } from './Comisiones';
import { DividendosPage } from './Dividendos';
import { FiscalPage } from './Fiscal';
import { IntegracionesPage } from './Integraciones';
import { MargenPage } from './Margen';
import { RentabilidadPage } from './Rentabilidad';
import { ResumenPage } from './Resumen';
import { SettingsPage } from './Settings';
import { TradesPanel } from '../components/TradesPanel';
import { OptionsPanel } from '../components/OptionsPanel';
import { ErrorBoundary } from '../components/ErrorBoundary';
import type { PageKey } from '../components/Sidebar';
import { api, type CommissionsSummary, type DividendsSummary, type PeriodFilters } from '../lib/api';
import { formatAsOf } from '../lib/format';
import { useI18n } from '../lib/i18n';

type FilterMode = 'period' | 'year';

interface Props {
  activePage: PageKey;
  filters: PeriodFilters;
  filterMode: FilterMode;
  period: PeriodFilters['period'];
  year: number;
  yearOptions: number[];
  periodChoices: Array<{ value: PeriodFilters['period']; label: string }>;
  onFilterModeChange: (mode: FilterMode) => void;
  onPeriodChange: (period: PeriodFilters['period']) => void;
  onYearChange: (year: number) => void;
  onNavigate: (page: PageKey) => void;
  coverage?: { first_date?: string; last_date?: string; days_stale?: number; sources?: { trades?: { count?: number }; cash_transactions?: { count?: number } } };
  staleMessage?: string;
}

// Background polls (health, pipeline status…) should not light the fetch bar.
const BACKGROUND_QUERIES = new Set(['health', 'data.coverage', 'pipeline.status', 'tax.scenarios', 'integrations.status']);

const PAGE_TITLES: Record<PageKey, { titleKey: string; subtitleKey: string }> = {
  resumen: { titleKey: 'page.resumen.title', subtitleKey: 'page.resumen.subtitle' },
  cartera: { titleKey: 'page.cartera.title', subtitleKey: 'page.cartera.subtitle' },
  fiscal: { titleKey: 'page.fiscal.title', subtitleKey: 'page.fiscal.subtitle' },
  operaciones: { titleKey: 'page.operaciones.title', subtitleKey: 'page.operaciones.subtitle' },
  opciones: { titleKey: 'page.opciones.title', subtitleKey: 'page.opciones.subtitle' },
  dividendos: { titleKey: 'page.dividendos.title', subtitleKey: 'page.dividendos.subtitle' },
  rentabilidad: { titleKey: 'page.rentabilidad.title', subtitleKey: 'page.rentabilidad.subtitle' },
  comisiones: { titleKey: 'page.comisiones.title', subtitleKey: 'page.comisiones.subtitle' },
  margen: { titleKey: 'page.margen.title', subtitleKey: 'page.margen.subtitle' },
  integraciones: { titleKey: 'page.integraciones.title', subtitleKey: 'page.integraciones.subtitle' },
  settings: { titleKey: 'page.settings.title', subtitleKey: 'page.settings.subtitle' },
};

// Format helpers + chart/period label utilities live in ../lib/format.

export function Dashboard({
  activePage,
  filters,
  filterMode,
  period,
  year,
  yearOptions,
  periodChoices,
  onFilterModeChange,
  onPeriodChange,
  onYearChange,
  onNavigate,
  staleMessage,
}: Props) {
  const { t } = useI18n();
  const fetching = useIsFetching({
    predicate: (query) => !BACKGROUND_QUERIES.has(String(query.queryKey[0])),
  });

  const overview = useQuery({
    queryKey: ['portfolio.overview', filters],
    queryFn: () => api.portfolioOverview(filters),
  });

  const positions = useQuery({
    queryKey: ['portfolio.positions', filters.account_id],
    queryFn: () => api.portfolioPositions({ account_id: filters.account_id }),
    // Only Cartera and Dividendos read the positions list.
    enabled: activePage === 'cartera' || activePage === 'dividendos',
  });

  // Margin posture — derived from Flex (no IB Gateway). Shared with Resumen
  // and the Margen page.
  const marginQ = useQuery({
    queryKey: ['portfolio.margin'],
    queryFn: () => api.margin(),
    enabled: activePage === 'resumen' || activePage === 'margen' || activePage === 'cartera',
  });

  // Dividends and commissions come embedded in /portfolio/overview (JSON-equal to
  // /dividends/summary and /commissions/summary): same query key, a `select`
  // slice, so the shell no longer fires two duplicate requests per page.
  const dividends = useQuery({
    queryKey: ['portfolio.overview', filters],
    queryFn: () => api.portfolioOverview(filters),
    select: (data): DividendsSummary => data.dividends ?? {},
  });

  const commissions = useQuery({
    queryKey: ['portfolio.overview', filters],
    queryFn: () => api.portfolioOverview(filters),
    select: (data): CommissionsSummary => data.commissions ?? {},
  });

  const currency = (overview.data?.base_currency as string | undefined) ?? 'CHF';
  const nav = overview.data?.nav_base as number | undefined;
  const incomeProjection = overview.data?.income_projection as
    | { as_of?: string; days_stale?: number; padi_base?: number; monthly_base?: number; yield_pct?: number; yield_on_nav_pct?: number; positions_total?: number; positions_covered?: number; coverage_ratio?: number }
    | undefined;
  const consistency = overview.data?.positions_consistency as
    | { market_value_base?: number; nav_positions_base?: number; coverage_ratio?: number }
    | undefined;
  const freshness = overview.data?.freshness as
    | { positions_as_of?: string; positions_days_stale?: number; nav_as_of?: string; nav_days_stale?: number }
    | undefined;
  const asOfLabels = {
    asOf: t('dashboard.asOf'),
    asOfOneDay: t('dashboard.asOfOneDay'),
    asOfDays: t('dashboard.asOfDays'),
  };
  const realizedAsOf = formatAsOf(freshness?.nav_as_of, freshness?.nav_days_stale, asOfLabels);

  const meta = PAGE_TITLES[activePage];
  const showFilters = activePage !== 'settings' && activePage !== 'fiscal' && activePage !== 'integraciones';

  return (
    <main className="main">
      <div className={`fetch-bar${fetching > 0 ? ' active' : ''}`} aria-hidden="true" />
      {/* ─── Page header: title + subtitle + filter chips ─── */}
      <header className="page-header">
        <div className="page-title">
          <h1>{t(meta.titleKey)}</h1>
          <p className="page-subtitle">{t(meta.subtitleKey)}</p>
        </div>

        {showFilters ? (
        <div className="page-filters">
          <div className="segmented-control compact">
            <button aria-pressed={filterMode === 'period'} className={filterMode === 'period' ? 'active' : ''} onClick={() => onFilterModeChange('period')}>
              {t('common.period')}
            </button>
            <button aria-pressed={filterMode === 'year'} className={filterMode === 'year' ? 'active' : ''} onClick={() => onFilterModeChange('year')}>
              {t('common.year')}
            </button>
          </div>
          {filterMode === 'period' ? (
            <div className="filter-chip-grid">
              {periodChoices.map((p) => (
                <button key={p.value} aria-pressed={period === p.value} className={period === p.value ? 'active' : ''} onClick={() => onPeriodChange(p.value)}>
                  {p.label}
                </button>
              ))}
            </div>
          ) : (
            <div className="filter-chip-grid">
              {yearOptions.map((option) => (
                <button key={option} aria-pressed={year === option} className={year === option ? 'active' : ''} onClick={() => onYearChange(option)}>
                  {option}
                </button>
              ))}
            </div>
          )}
        </div>
        ) : null}
      </header>

      {staleMessage ? (
        <div className="warn-box" role="status">
          {staleMessage}
        </div>
      ) : null}

      <ErrorBoundary
        key={activePage}
        title={t('dashboard.pageErrorTitle')}
        body={t('dashboard.pageErrorBody')}
        retryLabel={t('dashboard.retry')}
      >

      {/* ───────────── RESUMEN ───────────── */}
      {activePage === 'resumen' && (
        <ResumenPage
          filters={filters}
          currency={currency}
          nav={nav}
          realizedAsOf={realizedAsOf}
          consistency={consistency}
          overview={overview}
          marginQ={marginQ}
          onFilterModeChange={onFilterModeChange}
          onPeriodChange={onPeriodChange}
          onNavigate={onNavigate}
        />
      )}

      {/* ───────────── CARTERA ───────────── */}
      {activePage === 'cartera' && (
        <CarteraPage filters={filters} currency={currency} positions={positions} marginQ={marginQ} />
      )}

      {/* ───────────── FISCAL ───────────── */}
      {activePage === 'fiscal' && (
        <FiscalPage />
      )}

      {/* ───────────── OPERACIONES ───────────── */}
      {activePage === 'operaciones' && (
        <TradesPanel filters={filters} currency={currency} />
      )}

      {/* ───────────── OPCIONES ───────────── */}
      {activePage === 'opciones' && (
        <OptionsPanel filters={filters} currency={currency} />
      )}

      {/* ───────────── DIVIDENDOS ───────────── */}
      {activePage === 'dividendos' && (
        <DividendosPage
          filters={filters}
          filterMode={filterMode}
          period={period}
          year={year}
          currency={currency}
          dividends={dividends}
          incomeProjection={incomeProjection}
          overviewIsLoading={overview.isLoading}
        />
      )}

      {/* ───────────── RENTABILIDAD ───────────── */}
      {activePage === 'rentabilidad' && (
        <RentabilidadPage filters={filters} currency={currency} />
      )}

      {/* ───────────── COMISIONES ───────────── */}
      {activePage === 'comisiones' && (
        <ComisionesPage filters={filters} currency={currency} commissions={commissions} />
      )}

      {/* ───────────── MARGEN ───────────── */}
      {activePage === 'margen' && (
        <MargenPage filters={filters} currency={currency} marginQ={marginQ} />
      )}

      {/* ───────────── INTEGRACIONES ───────────── */}
      {activePage === 'integraciones' && (
        <IntegracionesPage />
      )}

      {/* ───────────── SETTINGS ───────────── */}
      {activePage === 'settings' && (
        <SettingsPage />
      )}

      </ErrorBoundary>

      <footer className="footer">
        {t('dashboard.footerAdvice')}
        <br />
        {t('dashboard.footerTwr')}
      </footer>
    </main>
  );
}
