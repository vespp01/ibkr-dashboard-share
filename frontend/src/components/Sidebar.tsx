import type { ReactNode } from 'react';
import { useI18n } from '../lib/i18n';

export type PageKey = 'resumen' | 'cartera' | 'fiscal' | 'operaciones' | 'opciones' | 'dividendos' | 'rentabilidad' | 'comisiones' | 'margen' | 'integraciones' | 'settings';

interface Props {
  active: PageKey;
  onChange: (key: PageKey) => void;
  health: { ok: boolean; loading: boolean; error: boolean };
  flexHealthy: boolean;
  flexConfigured: boolean;
  taxConfigured: boolean;
  /** IB Gateway overlay level from /integrations/status (undefined while loading). */
  liveOverlayLevel?: 'ok' | 'warn' | 'err' | 'off';
}

const Icon = {
  dashboard: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="7" height="9" rx="1.5" />
      <rect x="14" y="3" width="7" height="5" rx="1.5" />
      <rect x="14" y="12" width="7" height="9" rx="1.5" />
      <rect x="3" y="16" width="7" height="5" rx="1.5" />
    </svg>
  ),
  cartera: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 8a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V8Z" />
      <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
      <path d="M3 12h18" />
    </svg>
  ),
  dividendos: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v10M9 10h5a2 2 0 0 1 0 4H8" />
    </svg>
  ),
  rentabilidad: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 17l6-6 4 4 8-8" />
      <path d="M14 7h7v7" />
    </svg>
  ),
  comisiones: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M9 9a3 3 0 1 1 6 0c0 2-3 3-3 3" />
      <path d="M12 17h.01" />
      <circle cx="12" cy="12" r="9" />
    </svg>
  ),
  margen: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
      <path d="M9 12l2 2 4-4" />
    </svg>
  ),
  settings: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h0a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v0a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  ),
  api: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="16 18 22 12 16 6" />
      <polyline points="8 6 2 12 8 18" />
    </svg>
  ),
} as const;

const IconOperaciones = (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="22 7 13.5 15.5 8.5 10.5 2 17" />
    <polyline points="16 7 22 7 22 13" />
  </svg>
);

const IconFiscal = (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M6 2h9l3 3v17l-3-2-3 2-3-2-3 2V2Z" />
    <path d="M9 8h6M9 12h6M9 16h3" />
  </svg>
);

const IconOpciones = (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 12c0-4.97 4.03-9 9-9s9 4.03 9 9-4.03 9-9 9" />
    <path d="M12 7v5l3 3" />
    <path d="M3 12h3" />
  </svg>
);

const IconIntegraciones = (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M9 17H7A5 5 0 0 1 7 7h2" />
    <path d="M15 7h2a5 5 0 0 1 0 10h-2" />
    <path d="M8 12h8" />
  </svg>
);

const NAV: Array<{ key: Exclude<PageKey, 'settings'>; labelKey: string; icon: ReactNode }> = [
  { key: 'resumen', labelKey: 'sidebar.nav.resumen', icon: Icon.dashboard },
  { key: 'cartera', labelKey: 'sidebar.nav.cartera', icon: Icon.cartera },
  { key: 'fiscal', labelKey: 'sidebar.nav.fiscal', icon: IconFiscal },
  { key: 'operaciones', labelKey: 'sidebar.nav.operaciones', icon: IconOperaciones },
  { key: 'opciones', labelKey: 'sidebar.nav.opciones', icon: IconOpciones },
  { key: 'dividendos', labelKey: 'sidebar.nav.dividendos', icon: Icon.dividendos },
  { key: 'rentabilidad', labelKey: 'sidebar.nav.rentabilidad', icon: Icon.rentabilidad },
  { key: 'comisiones', labelKey: 'sidebar.nav.comisiones', icon: Icon.comisiones },
  { key: 'margen', labelKey: 'sidebar.nav.margen', icon: Icon.margen },
  { key: 'integraciones', labelKey: 'sidebar.nav.integraciones', icon: IconIntegraciones },
];

export function Sidebar({ active, onChange, health, flexHealthy, flexConfigured, taxConfigured, liveOverlayLevel = 'off' }: Props) {
  const { t } = useI18n();

  // Hide the Fiscal tab until OptimizadorFiscal is configured.
  const nav = NAV.filter((item) => item.key !== 'fiscal' || taxConfigured);

  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <span className="sidebar-logo" aria-hidden="true">IB</span>
        <span className="sidebar-title">IBKR Dashboard</span>
      </div>

      <nav className="sidebar-nav" aria-label={t('sidebar.mainNavigation')}>
        {nav.map((item) => (
          <button
            key={item.key}
            type="button"
            className={`nav-item${active === item.key ? ' active' : ''}`}
            data-testid={`nav-${item.key}`}
            aria-current={active === item.key ? 'page' : undefined}
            onClick={() => onChange(item.key)}
          >
            <span className="nav-icon">{item.icon}</span>
            <span className="nav-label">{t(item.labelKey)}</span>
          </button>
        ))}
      </nav>

      <div className="sidebar-divider" />

      <div className="sidebar-section">
        <div className="sidebar-section-label">{t('sidebar.sourceStatus')}</div>
        <div className="sidebar-status">
          <div className="status-row" title={t('sidebar.statusTitle.api')}>
            <span className={`status-dot ${health.ok ? 'ok' : health.error ? 'err' : 'off'}`} />
            <span>API</span>
          </div>
          <div className="status-row" title={t('sidebar.statusTitle.flex')}>
            <span className={`status-dot ${flexHealthy ? 'ok' : flexConfigured ? 'warn' : 'off'}`} />
            <span>Flex</span>
          </div>
          <div className="status-row" title={t('sidebar.statusTitle.ibgw')}>
            <span className={`status-dot ${liveOverlayLevel}`} />
            <span>IBGW</span>
          </div>
        </div>
      </div>

      <div className="sidebar-footer">
        <button
          type="button"
          className={`nav-item${active === 'settings' ? ' active' : ''}`}
          data-testid="nav-settings"
          aria-current={active === 'settings' ? 'page' : undefined}
          onClick={() => onChange('settings')}
          title={t('sidebar.nav.settings')}
        >
          <span className="nav-icon">{Icon.settings}</span>
          <span className="nav-label">{t('sidebar.nav.settings')}</span>
        </button>
        <div className="sidebar-version">{t('sidebar.version')}</div>
      </div>
    </aside>
  );
}
