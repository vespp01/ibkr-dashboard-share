import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { NotificationsSettings } from '../components/NotificationsSettings';
import { QueryError } from '../components/QueryError';
import { DASHBOARD_CARDS, useDashboardLayout } from '../lib/dashboardLayout';
import { parseBackendTimestamp } from '../lib/format';
import { LANGUAGE_OPTIONS, useI18n } from '../lib/i18n';

const FLEX_GUIDE_URL = 'https://www.interactivebrokers.com/campus/ibkr-api-page/flex-web-service/';

export function SettingsPage() {
  const { language, locale, privacyMode, setLanguage, setPrivacyMode, t } = useI18n();
  const { visibility, setCardVisible, resetRecommended, showAll } = useDashboardLayout();
  const pipeline = useQuery({
    queryKey: ['pipeline.status'],
    queryFn: () => api.pipelineStatus(),
  });
  const coverage = useQuery({
    queryKey: ['data.coverage'],
    queryFn: () => api.dataCoverage(),
  });

  const latestRun = pipeline.data?.latest_run;
  const latestRunLabel = latestRun?.fetched_at
    ? (parseBackendTimestamp(latestRun.fetched_at)?.toLocaleString(locale) ?? '--')
    : '--';
  const latestDataLabel = coverage.data?.last_date ?? '--';
  const rowsImported = latestRun?.rows_inserted ?? 0;
  const rowsSkipped = latestRun?.rows_skipped_dup ?? 0;
  // Data age in business days (backend rule), falling back to pull liveness.
  const isStale = pipeline.data?.data_freshness?.data_is_stale ?? pipeline.data?.staleness?.is_stale ?? false;
  const activityConfigured = pipeline.data?.activity_configured ?? pipeline.data?.configured ?? false;

  return (
    <div className="settings-grid">
      {pipeline.isError ? (
        <div className="settings-wide"><QueryError endpoint="/pipeline/status" onRetry={() => pipeline.refetch()} /></div>
      ) : null}
      {coverage.isError ? (
        <div className="settings-wide"><QueryError endpoint="/data/coverage" onRetry={() => coverage.refetch()} /></div>
      ) : null}
      <section className="chart-panel settings-panel">
        <div className="panel-header">
          <div>
            <div className="panel-title">{t('settings.languageTitle')}</div>
            <p className="settings-copy">{t('settings.languageDescription')}</p>
          </div>
          <span className="settings-pill">{locale}</span>
        </div>
        <div className="language-grid" role="group" aria-label={t('settings.languageTitle')}>
          {LANGUAGE_OPTIONS.map((option) => (
            <button
              key={option.value}
              type="button"
              className={`language-option${language === option.value ? ' active' : ''}`}
              onClick={() => setLanguage(option.value)}
              title={option.englishLabel}
            >
              <span>{option.nativeLabel}</span>
              <small>{option.value.toUpperCase()}</small>
            </button>
          ))}
        </div>
        <p className="settings-note">{t('settings.languageHint')}</p>
      </section>

      <section className="chart-panel settings-panel">
        <div className="panel-title">{t('settings.privacyTitle')}</div>
        <div className="settings-toggle-row">
          <div>
            <strong>{t('settings.privacyModeTitle')}</strong>
            <p className="settings-copy">{t('settings.privacyModeDescription')}</p>
          </div>
          <button
            type="button"
            className={`settings-toggle${privacyMode ? ' active' : ''}`}
            role="switch"
            aria-checked={privacyMode}
            onClick={() => setPrivacyMode(!privacyMode)}
          >
            <span aria-hidden="true" />
            {privacyMode ? t('settings.privacyModeOn') : t('settings.privacyModeOff')}
          </button>
        </div>
        <p className="settings-note">{t('settings.privacyModeHint')}</p>
        <ul className="settings-list">
          <li>{t('settings.privacyItems.local')}</li>
          <li>{t('settings.privacyItems.secrets')}</li>
          <li>{t('settings.privacyItems.sample')}</li>
        </ul>
      </section>

      <NotificationsSettings />

      <section className="chart-panel settings-panel settings-wide">
        <div className="panel-header">
          <div>
            <div className="panel-title">{t('settings.ibkrTitle')}</div>
            <p className="settings-copy">{t('settings.ibkrDescription')}</p>
          </div>
          <span className="settings-pill">{t('common.localOnly')}</span>
        </div>
        <ol className="settings-steps">
          <li>{t('settings.steps.token')}</li>
          <li>{t('settings.steps.env')}</li>
          <li>{t('settings.steps.activity')}</li>
          <li>{t('settings.steps.tc')}</li>
          <li>{t('settings.steps.start')}</li>
        </ol>
      </section>

      <section className="chart-panel settings-panel settings-wide">
        <div className="panel-header">
          <div>
            <div className="panel-title">{t('settings.flexTitle')}</div>
            <p className="settings-copy">{t('settings.flexDescription')}</p>
          </div>
          <a className="settings-link-button" href={FLEX_GUIDE_URL} target="_blank" rel="noreferrer">
            {t('settings.openDocs')}
          </a>
        </div>
        <div className="settings-callout">
          <strong>{t('settings.requiredSections')}</strong>
          <p>{t('settings.sections')}</p>
        </div>
      </section>

      <section className="chart-panel settings-panel settings-wide">
        <div className="panel-header">
          <div>
            <div className="panel-title">{t('settings.setupStatusTitle')}</div>
            <p className="settings-copy">{t('settings.setupStatusDescription')}</p>
          </div>
          <span className={`settings-pill ${isStale ? 'warn' : 'ok'}`}>
            {isStale ? t('settings.stale') : t('settings.fresh')}
          </span>
        </div>
        <div className="setup-status-grid">
          <SetupStatusItem
            label={t('settings.activityQuery')}
            value={activityConfigured ? t('common.configured') : t('common.notConfigured')}
            state={activityConfigured ? 'ok' : 'warn'}
          />
          <SetupStatusItem
            label={t('settings.tcQuery')}
            value={pipeline.data?.tc_configured ? t('common.configured') : t('common.optional')}
            state={pipeline.data?.tc_configured ? 'ok' : 'neutral'}
          />
          <SetupStatusItem
            label={t('settings.baseCurrency')}
            value={pipeline.data?.base_currency ?? '--'}
            state={pipeline.data?.base_currency ? 'ok' : 'neutral'}
          />
          <SetupStatusItem
            label={t('settings.tokenExpiry')}
            value={pipeline.data?.token_expiry ?? '--'}
            state={pipeline.data?.token_expiry ? 'ok' : 'warn'}
          />
          <SetupStatusItem
            label={t('settings.latestFlexRun')}
            value={latestRunLabel}
            state={latestRun?.status === 'success' ? 'ok' : latestRun?.status ? 'warn' : 'neutral'}
          />
          <SetupStatusItem
            label={t('settings.latestData')}
            value={latestDataLabel}
            state={coverage.data?.last_date ? 'ok' : 'warn'}
          />
          <SetupStatusItem
            label={t('settings.rowsImported')}
            value={`${rowsImported.toLocaleString(locale)} / ${rowsSkipped.toLocaleString(locale)}`}
            state={latestRun ? 'neutral' : 'warn'}
          />
          <SetupStatusItem
            label={t('settings.databaseRows')}
            value={`trades ${coverage.data?.sources?.trades?.count ?? 0} · cash ${coverage.data?.sources?.cash_transactions?.count ?? 0}`}
            state={coverage.data?.sources?.trades?.count ? 'ok' : 'warn'}
          />
        </div>
      </section>

      <section className="chart-panel settings-panel settings-wide">
        <div className="panel-header">
          <div>
            <div className="panel-title">{t('settings.dashboardCardsTitle')}</div>
            <p className="settings-copy">{t('settings.dashboardCardsDescription')}</p>
          </div>
          <div className="settings-action-row">
            <button type="button" className="settings-link-button" onClick={resetRecommended}>
              {t('settings.resetRecommended')}
            </button>
            <button type="button" className="settings-link-button" onClick={showAll}>
              {t('settings.showAll')}
            </button>
          </div>
        </div>
        <div className="settings-card-list">
          {DASHBOARD_CARDS.map((card) => {
            const active = visibility[card.id] !== false;
            return (
              <button
                key={card.id}
                type="button"
                className={`settings-card-toggle${active ? ' active' : ''}`}
                onClick={() => setCardVisible(card.id, !active)}
                aria-pressed={active}
              >
                <span className="settings-card-check">{active ? 'on' : ''}</span>
                <span>
                  <strong>{t(card.titleKey)}</strong>
                  <small>{t(card.descriptionKey)}</small>
                </span>
              </button>
            );
          })}
        </div>
      </section>

      <section className="chart-panel settings-panel">
        <div className="panel-title">{t('settings.commandsTitle')}</div>
        <pre className="settings-code"><code>{`cp .env.example .env
docker compose up -d --build
docker compose run --rm bootstrap`}</code></pre>
      </section>

      <section className="chart-panel settings-panel">
        <div className="panel-title">{t('settings.healthTitle')}</div>
        <ul className="settings-list compact">
          <li>{t('settings.healthItems.frontend')}</li>
          <li>{t('settings.healthItems.api')}</li>
          <li>{t('settings.healthItems.swagger')}</li>
        </ul>
        <p className="settings-copy">{t('settings.docsDescription')}</p>
      </section>
    </div>
  );
}

function SetupStatusItem({
  label,
  value,
  state,
}: {
  label: string;
  value: string;
  state: 'ok' | 'warn' | 'neutral';
}) {
  return (
    <div className={`setup-status-item ${state}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
