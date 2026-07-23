import { useQuery } from '@tanstack/react-query';
import { CarteraPage } from './Cartera';
import { ComisionesPage } from './Comisiones';
import { DividendosPage } from './Dividendos';
import { MargenPage } from './Margen';
import { RentabilidadPage } from './Rentabilidad';
import { ResumenPage } from './Resumen';
import { SettingsPage } from './Settings';
import { TradesPanel } from '../components/TradesPanel';
import { OptionsPanel } from '../components/OptionsPanel';
import type { PageKey } from '../components/Sidebar';
import { api, type PeriodFilters } from '../lib/api';
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
  coverage?: { first_date?: string; last_date?: string; days_stale?: number; sources?: { trades?: { count?: number }; cash_transactions?: { count?: number } } };
  staleWarn?: boolean;
  daysStale?: number;
}

const PAGE_TITLES: Record<PageKey, { titleKey: string; subtitleKey: string }> = {
  resumen: { titleKey: 'page.resumen.title', subtitleKey: 'page.resumen.subtitle' },
  cartera: { titleKey: 'page.cartera.title', subtitleKey: 'page.cartera.subtitle' },
  operaciones: { titleKey: 'page.operaciones.title', subtitleKey: 'page.operaciones.subtitle' },
  opciones: { titleKey: 'page.opciones.title', subtitleKey: 'page.opciones.subtitle' },
  dividendos: { titleKey: 'page.dividendos.title', subtitleKey: 'page.dividendos.subtitle' },
  rentabilidad: { titleKey: 'page.rentabilidad.title', subtitleKey: 'page.rentabilidad.subtitle' },
  comisiones: { titleKey: 'page.comisiones.title', subtitleKey: 'page.comisiones.subtitle' },
  margen: { titleKey: 'page.margen.title', subtitleKey: 'page.margen.subtitle' },
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
  staleWarn,
  daysStale,
}: Props) {
  const { t } = useI18n();

  const overview = useQuery({
    queryKey: ['portfolio.overview', filters],
    queryFn: () => api.portfolioOverview(filters),
  });

  const positions = useQuery({
    queryKey: ['portfolio.positions', filters.account_id],
    queryFn: () => api.portfolioPositions({ account_id: filters.account_id }),
  });

  // Margin posture — derived from Flex (no IB Gateway). Shared with Resumen
  // and the Margen page.
  const marginQ = useQuery({
    queryKey: ['portfolio.margin'],
    queryFn: () => api.margin(),
  });

  const dividends = useQuery({
    queryKey: ['dividends.summary', filters],
    queryFn: () => api.dividendsSummary(filters),
  });

  const commissions = useQuery({
    queryKey: ['commissions.summary', filters],
    queryFn: () => api.commissionsSummary(filters),
  });

  const currency = (overview.data?.base_currency as string | undefined) ?? 'CHF';
  const nav = overview.data?.nav_base as number | undefined;
  const unrealizedMeta = overview.data?.unrealized as
    | { snapshot_date?: string; days_stale?: number; unrealized_pnl_base?: number }
    | undefined;
  const unrealized = unrealizedMeta?.unrealized_pnl_base;
  const dividendsNet = dividends.data?.net_base as number | undefined;
  const commissionsTotal = commissions.data?.total_base as number | undefined;
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
  const unrealizedAsOf = formatAsOf(unrealizedMeta?.snapshot_date, unrealizedMeta?.days_stale, asOfLabels);

  const meta = PAGE_TITLES[activePage];
  const showFilters = activePage !== 'settings';

  return (
    <main className="main">
      {/* ─── Page header: title + subtitle + filter chips ─── */}
      <header className="page-header">
        <div className="page-title">
          <h1>{t(meta.titleKey)}</h1>
          <p className="page-subtitle">{t(meta.subtitleKey)}</p>
        </div>

        {showFilters ? (
        <div className="page-filters">
          <div className="segmented-control compact">
            <button className={filterMode === 'period' ? 'active' : ''} onClick={() => onFilterModeChange('period')}>
              {t('common.period')}
            </button>
            <button className={filterMode === 'year' ? 'active' : ''} onClick={() => onFilterModeChange('year')}>
              {t('common.year')}
            </button>
          </div>
          {filterMode === 'period' ? (
            <div className="filter-chip-grid">
              {periodChoices.map((p) => (
                <button key={p.value} className={period === p.value ? 'active' : ''} onClick={() => onPeriodChange(p.value)}>
                  {p.label}
                </button>
              ))}
            </div>
          ) : (
            <div className="filter-chip-grid">
              {yearOptions.map((option) => (
                <button key={option} className={year === option ? 'active' : ''} onClick={() => onYearChange(option)}>
                  {option}
                </button>
              ))}
            </div>
          )}
        </div>
        ) : null}
      </header>

      {staleWarn ? (
        <div className="warn-box">
          {t('dashboard.staleWarning', { days: daysStale ?? 0 })}
        </div>
      ) : null}

      {/* ───────────── RESUMEN ───────────── */}
      {activePage === 'resumen' && (
        <ResumenPage
          filters={filters}
          currency={currency}
          nav={nav}
          realizedAsOf={realizedAsOf}
          unrealizedAsOf={unrealizedAsOf}
          unrealized={unrealized}
          dividendsNet={dividendsNet}
          commissionsTotal={commissionsTotal}
          incomeProjection={incomeProjection}
          consistency={consistency}
          overview={overview}
          positions={positions}
          dividends={dividends}
          commissions={commissions}
          marginQ={marginQ}
          onFilterModeChange={onFilterModeChange}
          onPeriodChange={onPeriodChange}
        />
      )}

      {/* ───────────── CARTERA ───────────── */}
      {activePage === 'cartera' && (
        <CarteraPage filters={filters} currency={currency} positions={positions} />
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

      {/* ───────────── SETTINGS ───────────── */}
      {activePage === 'settings' && (
        <SettingsPage />
      )}

      <footer className="footer">
        {t('dashboard.footerAdvice')}
        <br />
        {t('dashboard.footerTwr')}
      </footer>
    </main>
  );
}
