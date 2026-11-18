import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  api,
  type NotificationCadence,
  type NotificationConfig,
  type NotificationConfigResponse,
  type NotificationFreq,
  type NotificationMessageConfig,
  type NotificationMessageSchema,
} from '../lib/api';
import { describeCron } from '../lib/cron';
import { useI18n } from '../lib/i18n';

const FREQS: NotificationFreq[] = ['off', 'daily', 'weekly', 'biweekly', 'monthly'];
const TIME_PRESETS = ['06:00', '08:00', '12:00', '18:00', '22:00'];

type Messages = NotificationConfig['messages'];

export function NotificationsSettings() {
  const { t } = useI18n();
  const qc = useQueryClient();
  const query = useQuery({
    queryKey: ['config.notifications'],
    queryFn: () => api.notificationConfig(),
  });
  // Local edits only. Until the first edit (and again after a save) the form
  // shows the server config, derived during render — no setState in an effect.
  const [localDraft, setLocalDraft] = useState<Messages | null>(null);
  const draft = localDraft ?? query.data?.config.messages ?? null;

  const save = useMutation({
    mutationFn: () => api.saveNotificationConfig({ messages: draft ?? {} }),
    onSuccess: (data) => {
      qc.setQueryData<NotificationConfigResponse>(['config.notifications'], (prev) =>
        prev ? { ...prev, config: data.config } : prev,
      );
      setLocalDraft(null);
    },
  });
  const test = useMutation({ mutationFn: () => api.testNotification() });

  if (query.isLoading || !query.data || !draft) {
    return (
      <section className="chart-panel settings-panel settings-wide">
        <div className="panel-title">{t('settings.notifications.title')}</div>
        <p className="settings-copy">{t('settings.notifications.loading')}</p>
      </section>
    );
  }

  const { schema, capabilities } = query.data;
  const dirty = JSON.stringify(draft) !== JSON.stringify(query.data.config.messages);

  const updateDraft = (fn: (d: Messages) => Messages) =>
    setLocalDraft((d) => {
      const base = d ?? structuredClone(query.data?.config.messages ?? null);
      return base ? fn(base) : d;
    });
  const patchMessage = (id: string, patch: Partial<NotificationMessageConfig>) =>
    updateDraft((d) => ({ ...d, [id]: { ...d[id], ...patch } }));
  const patchCadence = (id: string, patch: Partial<NotificationCadence>) =>
    updateDraft((d) => ({ ...d, [id]: { ...d[id], cadence: { ...d[id].cadence, ...patch } } }));

  return (
    <section className="chart-panel settings-panel settings-wide">
      <div className="panel-header">
        <div>
          <div className="panel-title">{t('settings.notifications.title')}</div>
          <p className="settings-copy">{t('settings.notifications.description')}</p>
        </div>
        <span className={`settings-pill ${capabilities.telegram_configured ? 'ok' : 'warn'}`}>
          {capabilities.telegram_configured
            ? t('settings.notifications.telegramOn')
            : t('settings.notifications.telegramOff')}
        </span>
      </div>

      {!capabilities.telegram_configured && (
        <div className="settings-callout">
          <p>{t('settings.notifications.telegramHint')}</p>
        </div>
      )}

      <div className="notif-list">
        {schema.messages.map((msg) => (
          <MessageCard
            key={msg.id}
            schema={msg}
            cfg={draft[msg.id]}
            liveOverlay={capabilities.live_overlay_enabled}
            onToggle={(enabled) => patchMessage(msg.id, { enabled })}
            onFreq={(freq) => patchCadence(msg.id, { freq })}
            onWeekday={(weekday) => patchCadence(msg.id, { weekday })}
            onDayOfMonth={(day_of_month) => patchCadence(msg.id, { day_of_month })}
            onTime={(time) => patchCadence(msg.id, { time })}
            onAlertBands={(alert_bands) => patchMessage(msg.id, { alert_bands })}
            onSection={(section, on) => {
              const current = draft[msg.id].sections ?? msg.sections;
              const next = on
                ? msg.sections.filter((s) => current.includes(s) || s === section)
                : current.filter((s) => s !== section);
              patchMessage(msg.id, { sections: next });
            }}
          />
        ))}
      </div>

      <div className="settings-action-row notif-actions">
        <button
          type="button"
          className="settings-link-button primary"
          disabled={!dirty || save.isPending}
          onClick={() => save.mutate()}
        >
          {save.isPending ? t('settings.notifications.saving') : t('settings.notifications.save')}
        </button>
        <button
          type="button"
          className="settings-link-button"
          disabled={!capabilities.telegram_configured || test.isPending}
          onClick={() => test.mutate()}
        >
          {test.isPending ? t('settings.notifications.testing') : t('settings.notifications.sendTest')}
        </button>
        {save.isSuccess && !dirty && (
          <span className="settings-pill ok">{t('settings.notifications.saved')}</span>
        )}
        {save.isError && <span className="settings-pill warn">{t('settings.notifications.saveError')}</span>}
        {test.isSuccess && (
          <span className={`settings-pill ${test.data.sent ? 'ok' : 'warn'}`}>
            {test.data.sent
              ? t('settings.notifications.testSent')
              : t('settings.notifications.testFailed')}
          </span>
        )}
      </div>
    </section>
  );
}

function MessageCard({
  schema,
  cfg,
  liveOverlay,
  onToggle,
  onFreq,
  onWeekday,
  onDayOfMonth,
  onTime,
  onAlertBands,
  onSection,
}: {
  schema: NotificationMessageSchema;
  cfg: NotificationMessageConfig;
  liveOverlay: boolean;
  onToggle: (enabled: boolean) => void;
  onFreq: (freq: NotificationFreq) => void;
  onWeekday: (weekday: number) => void;
  onDayOfMonth: (day: number) => void;
  onTime: (time: string) => void;
  onAlertBands: (on: boolean) => void;
  onSection: (section: string, on: boolean) => void;
}) {
  const { t, language } = useI18n();
  const weekdayLabels = t('settings.notifications.weekdaysShort').split(',');
  const unavailable = schema.requires_live_overlay && !liveOverlay;
  const showCadence = schema.schedule_kind !== 'interval';
  const showWeekday = showCadence && (cfg.cadence.freq === 'weekly' || cfg.cadence.freq === 'biweekly');
  const showDom = showCadence && cfg.cadence.freq === 'monthly';
  const showTime = schema.cadence_fields.includes('time') && cfg.cadence.freq !== 'off';
  const selectedSections = cfg.sections ?? schema.sections;
  // A message that needs the live overlay cannot run while it is off, whatever the saved flag says.
  const effectivelyOn = cfg.enabled && !unavailable;

  return (
    <div className={`notif-card${effectivelyOn ? '' : ' is-off'}`}>
      <div className="notif-card-head">
        <div className="notif-card-title">
          <strong>{t(`settings.notifications.msg.${schema.id}.name`)}</strong>
          <small>{t(`settings.notifications.msg.${schema.id}.help`)}</small>
          {unavailable && (
            <span className="notif-note">{t('settings.notifications.requiresOverlayOff')}</span>
          )}
        </div>
        <button
          type="button"
          className={`settings-toggle${effectivelyOn ? ' active' : ''}`}
          role="switch"
          aria-checked={effectivelyOn}
          disabled={unavailable}
          title={unavailable ? t('settings.notifications.requiresOverlayOff') : undefined}
          onClick={() => onToggle(!cfg.enabled)}
        >
          <span aria-hidden="true" />
          {effectivelyOn ? t('settings.notifications.on') : t('settings.notifications.off')}
        </button>
      </div>

      {effectivelyOn && (
        <div className="notif-card-body">
          {schema.schedule_kind === 'interval' && (
            <p className="notif-interval">
              {t('settings.notifications.interval')}:{' '}
              <span title={schema.interval_cron ?? undefined}>
                {(schema.interval_cron && describeCron(schema.interval_cron, language)) || <code>{schema.interval_cron}</code>}
              </span>
            </p>
          )}

          {showCadence && (
            <div className="notif-field">
              <span className="notif-label">{t('settings.notifications.frequency')}</span>
              <div className="notif-seg" role="group">
                {FREQS.map((f) => (
                  <button
                    key={f}
                    type="button"
                    className={`notif-chip${cfg.cadence.freq === f ? ' active' : ''}`}
                    onClick={() => onFreq(f)}
                  >
                    {t(`settings.notifications.freq.${f}`)}
                  </button>
                ))}
              </div>
            </div>
          )}

          {showWeekday && (
            <div className="notif-field">
              <span className="notif-label">{t('settings.notifications.weekday')}</span>
              <div className="notif-seg" role="group">
                {weekdayLabels.map((label, idx) => (
                  <button
                    key={idx}
                    type="button"
                    className={`notif-chip${(cfg.cadence.weekday ?? 0) === idx ? ' active' : ''}`}
                    onClick={() => onWeekday(idx)}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
          )}

          {showDom && (
            <div className="notif-field">
              <span className="notif-label">{t('settings.notifications.dayOfMonth')}</span>
              <div className="notif-stepper">
                <button
                  type="button"
                  className="notif-chip"
                  onClick={() => onDayOfMonth(Math.max(1, (cfg.cadence.day_of_month ?? 1) - 1))}
                  aria-label="-1"
                >
                  −
                </button>
                <span className="notif-stepper-value">{cfg.cadence.day_of_month ?? 1}</span>
                <button
                  type="button"
                  className="notif-chip"
                  onClick={() => onDayOfMonth(Math.min(28, (cfg.cadence.day_of_month ?? 1) + 1))}
                  aria-label="+1"
                >
                  +
                </button>
              </div>
            </div>
          )}

          {showTime && (
            <div className="notif-field">
              <span className="notif-label">{t('settings.notifications.time')}</span>
              <div className="notif-seg" role="group">
                {TIME_PRESETS.map((time) => (
                  <button
                    key={time}
                    type="button"
                    className={`notif-chip${(cfg.cadence.time ?? '08:00') === time ? ' active' : ''}`}
                    onClick={() => onTime(time)}
                  >
                    {time}
                  </button>
                ))}
              </div>
            </div>
          )}

          {schema.supports_sections && (
            <div className="notif-field">
              <span className="notif-label">{t('settings.notifications.sections')}</span>
              <div className="notif-seg" role="group">
                {schema.sections.map((section) => {
                  const on = selectedSections.includes(section);
                  return (
                    <button
                      key={section}
                      type="button"
                      className={`notif-chip${on ? ' active' : ''}`}
                      aria-pressed={on}
                      onClick={() => onSection(section, !on)}
                    >
                      {t(`settings.notifications.section.${section}`)}
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {schema.supports_alert_bands && (
            <label className="notif-inline-toggle">
              <button
                type="button"
                className={`settings-toggle${cfg.alert_bands ? ' active' : ''}`}
                role="switch"
                aria-checked={!!cfg.alert_bands}
                onClick={() => onAlertBands(!cfg.alert_bands)}
              >
                <span aria-hidden="true" />
                {cfg.alert_bands ? t('settings.notifications.on') : t('settings.notifications.off')}
              </button>
              <span className="notif-label">{t('settings.notifications.alertBands')}</span>
            </label>
          )}
        </div>
      )}
    </div>
  );
}
