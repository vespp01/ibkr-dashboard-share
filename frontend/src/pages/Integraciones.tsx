import { useQuery } from '@tanstack/react-query';
import { MissingData } from '../components/MissingData';
import { QueryError } from '../components/QueryError';
import {
  api,
  type IntegrationCategory,
  type IntegrationLevel,
  type IntegrationStatus,
} from '../lib/api';
import { useI18n, type Language } from '../lib/i18n';

interface IntegrationsCopy {
  loading: string;
  apiError: string;
  refresh: string;
  updated: string;
  intro: string;
  controlHint: string;
  legendReadOnly: string;
  category: Record<IntegrationCategory, string>;
  categoryHint: Record<IntegrationCategory, string>;
  col: {
    state: string;
    integration: string;
    direction: string;
    mechanism: string;
    control: string;
    detail: string;
  };
  direction: Record<'outbound' | 'inbound' | 'bidirectional', string>;
  controlKind: Record<'env' | 'route' | 'none', string>;
  status: Record<string, string>;
  reason: Record<string, string>;
  asOf: string;
  expiresIn: string;
  expired: string;
  notImplemented: string;
  notRemovable: string;
  none: string;
}

const COPY = {
  es: {
    loading: 'Cargando estado de integraciones',
    apiError: 'No se pudo consultar el estado de integraciones.',
    refresh: 'Actualizar',
    updated: 'Actualizado',
    intro: 'Estado de cada punto de conexión con tus otros proyectos, el broker y los servicios externos. Panel de solo lectura.',
    controlHint: 'Para activar o desactivar una integración, edita su variable en .env y reinicia el backend. Los contratos (endpoints) se apagan simplemente no consumiéndolos.',
    legendReadOnly: 'Solo lectura',
    category: { project: 'Proyectos hermanos', broker: 'Broker (IBKR)', service: 'Servicios externos' },
    categoryHint: {
      project: 'Contratos versionados con tus otros proyectos del ecosistema.',
      broker: 'Conexiones directas con Interactive Brokers.',
      service: 'Servicios de terceros (notificaciones, tipos de cambio).',
    },
    col: { state: 'Estado', integration: 'Integración', direction: 'Dirección', mechanism: 'Mecanismo', control: 'Palanca', detail: 'Detalle' },
    direction: { outbound: 'Saliente', inbound: 'Entrante', bidirectional: 'Bidireccional' },
    controlKind: { env: 'variable de entorno', route: 'endpoint', none: '—' },
    status: {
      ready: 'Operativa', stale: 'Datos antiguos', blocked: 'Sin datos usables',
      not_configured: 'Sin configurar', missing: 'Archivo ausente', invalid: 'Contrato inválido',
      planned: 'Planeada', configured: 'Configurada', enabled: 'Activada', disabled: 'Desactivada',
      expiring: 'Token por caducar', expired: 'Token caducado', on_demand: 'Bajo demanda',
    },
    reason: {
      path_not_configured: 'Define TAX_SCENARIOS_FILE con un archivo tax_sale_scenarios.v1.',
      file_not_found: 'El archivo configurado no existe todavía.',
      file_too_large: 'El archivo supera el límite seguro de 2 MB.',
      file_unreadable: 'El backend no puede leer el archivo configurado.',
      contract_validation_failed: 'El archivo no cumple el contrato versionado.',
      producer_not_implemented: 'Diseñada, pero el productor aún no existe en el dashboard.',
      NO_POSITION_SNAPSHOT: 'Todavía no hay snapshot de posiciones EOD.',
      NAV_MISSING_FOR_POSITION_DATE: 'Falta el NAV para la fecha de posiciones.',
      NO_OPEN_POSITIONS: 'No hay posiciones abiertas.',
      STALE_POSITION_SNAPSHOT: 'El último snapshot supera los 7 días.',
      BASE_CURRENCY_AMBIGUOUS: 'La divisa base es ambigua.',
      MULTIPLE_ACCOUNTS_AGGREGATED: 'Se agregan varias cuentas.',
      pull_failing: 'El último pull de Activity falló; revisa el error en Settings.',
      pull_stale: 'El último pull correcto de Activity es antiguo.',
      POSITION_FX_MISSING: 'Una posición no tiene tipo de cambio a la divisa base.',
      FUTURE_SNAPSHOT_DATE: 'La fecha del snapshot está en el futuro.',
      INVALID_POSITION_IDENTITY: 'Identidad de posición inválida.',
      POSITION_METADATA_NORMALIZED: 'Metadatos de posición normalizados.',
    },
    asOf: 'a',
    expiresIn: 'Caduca en',
    expired: 'Token caducado',
    notImplemented: 'Sin implementar',
    notRemovable: 'Núcleo (no desmontable)',
    none: 'Sin palanca directa',
  },
  en: {
    loading: 'Loading integration status',
    apiError: 'The integration status could not be reached.',
    refresh: 'Refresh',
    updated: 'Updated',
    intro: 'Status of every connection to your other projects, the broker, and external services. Read-only panel.',
    controlHint: 'To enable or disable an integration, edit its variable in .env and restart the backend. Contract endpoints turn off simply by not consuming them.',
    legendReadOnly: 'Read-only',
    category: { project: 'Sibling projects', broker: 'Broker (IBKR)', service: 'External services' },
    categoryHint: {
      project: 'Versioned contracts with your other ecosystem projects.',
      broker: 'Direct connections to Interactive Brokers.',
      service: 'Third-party services (notifications, FX rates).',
    },
    col: { state: 'State', integration: 'Integration', direction: 'Direction', mechanism: 'Mechanism', control: 'Lever', detail: 'Detail' },
    direction: { outbound: 'Outbound', inbound: 'Inbound', bidirectional: 'Bidirectional' },
    controlKind: { env: 'environment variable', route: 'endpoint', none: '—' },
    status: {
      ready: 'Operational', stale: 'Stale data', blocked: 'No usable data',
      not_configured: 'Not configured', missing: 'File missing', invalid: 'Invalid contract',
      planned: 'Planned', configured: 'Configured', enabled: 'Enabled', disabled: 'Disabled',
      expiring: 'Token expiring', expired: 'Token expired', on_demand: 'On demand',
    },
    reason: {
      path_not_configured: 'Set TAX_SCENARIOS_FILE to a tax_sale_scenarios.v1 file.',
      file_not_found: 'The configured file does not exist yet.',
      file_too_large: 'The file exceeds the safe 2 MB limit.',
      file_unreadable: 'The backend cannot read the configured file.',
      contract_validation_failed: 'The file does not match the versioned contract.',
      producer_not_implemented: 'Designed, but the producer does not exist in the dashboard yet.',
      NO_POSITION_SNAPSHOT: 'No EOD position snapshot yet.',
      NAV_MISSING_FOR_POSITION_DATE: 'NAV missing for the position date.',
      NO_OPEN_POSITIONS: 'No open positions.',
      STALE_POSITION_SNAPSHOT: 'The latest snapshot is older than 7 days.',
      BASE_CURRENCY_AMBIGUOUS: 'Base currency is ambiguous.',
      MULTIPLE_ACCOUNTS_AGGREGATED: 'Multiple accounts aggregated.',
      pull_failing: 'The latest Activity pull failed; see the error in Settings.',
      pull_stale: 'The latest successful Activity pull is old.',
      POSITION_FX_MISSING: 'A position has no FX rate to the base currency.',
      FUTURE_SNAPSHOT_DATE: 'The snapshot date is in the future.',
      INVALID_POSITION_IDENTITY: 'Invalid position identity.',
      POSITION_METADATA_NORMALIZED: 'Position metadata normalized.',
    },
    asOf: 'as of',
    expiresIn: 'Expires in',
    expired: 'Token expired',
    notImplemented: 'Not implemented',
    notRemovable: 'Core (not detachable)',
    none: 'No direct lever',
  },
  de: {
    loading: 'Integrationsstatus wird geladen',
    apiError: 'Der Integrationsstatus ist nicht erreichbar.',
    refresh: 'Aktualisieren',
    updated: 'Aktualisiert',
    intro: 'Status jeder Verbindung zu deinen anderen Projekten, dem Broker und externen Diensten. Nur-Lesen-Ansicht.',
    controlHint: 'Zum Aktivieren oder Deaktivieren die Variable in .env aendern und das Backend neu starten. Vertrags-Endpunkte deaktiviert man einfach durch Nichtnutzung.',
    legendReadOnly: 'Nur Lesen',
    category: { project: 'Schwesterprojekte', broker: 'Broker (IBKR)', service: 'Externe Dienste' },
    categoryHint: {
      project: 'Versionierte Vertraege mit deinen anderen Oekosystem-Projekten.',
      broker: 'Direkte Verbindungen zu Interactive Brokers.',
      service: 'Drittanbieter-Dienste (Benachrichtigungen, Wechselkurse).',
    },
    col: { state: 'Status', integration: 'Integration', direction: 'Richtung', mechanism: 'Mechanismus', control: 'Hebel', detail: 'Detail' },
    direction: { outbound: 'Ausgehend', inbound: 'Eingehend', bidirectional: 'Bidirektional' },
    controlKind: { env: 'Umgebungsvariable', route: 'Endpunkt', none: '—' },
    status: {
      ready: 'Betriebsbereit', stale: 'Veraltete Daten', blocked: 'Keine nutzbaren Daten',
      not_configured: 'Nicht konfiguriert', missing: 'Datei fehlt', invalid: 'Ungueltiger Vertrag',
      planned: 'Geplant', configured: 'Konfiguriert', enabled: 'Aktiviert', disabled: 'Deaktiviert',
      expiring: 'Token laeuft ab', expired: 'Token abgelaufen', on_demand: 'Auf Abruf',
    },
    reason: {
      path_not_configured: 'TAX_SCENARIOS_FILE auf eine tax_sale_scenarios.v1-Datei setzen.',
      file_not_found: 'Die konfigurierte Datei existiert noch nicht.',
      file_too_large: 'Die Datei ueberschreitet das sichere Limit von 2 MB.',
      file_unreadable: 'Das Backend kann die konfigurierte Datei nicht lesen.',
      contract_validation_failed: 'Die Datei entspricht nicht dem versionierten Vertrag.',
      producer_not_implemented: 'Entworfen, aber der Produzent existiert im Dashboard noch nicht.',
      NO_POSITION_SNAPSHOT: 'Noch kein EOD-Positions-Snapshot.',
      NAV_MISSING_FOR_POSITION_DATE: 'NAV fuer das Positionsdatum fehlt.',
      NO_OPEN_POSITIONS: 'Keine offenen Positionen.',
      STALE_POSITION_SNAPSHOT: 'Der letzte Snapshot ist aelter als 7 Tage.',
      BASE_CURRENCY_AMBIGUOUS: 'Basiswaehrung ist mehrdeutig.',
      MULTIPLE_ACCOUNTS_AGGREGATED: 'Mehrere Konten aggregiert.',
      pull_failing: 'Der letzte Activity-Pull ist fehlgeschlagen; Fehler in Settings pruefen.',
      pull_stale: 'Der letzte erfolgreiche Activity-Pull ist alt.',
      POSITION_FX_MISSING: 'Einer Position fehlt der Wechselkurs zur Basiswaehrung.',
      FUTURE_SNAPSHOT_DATE: 'Das Snapshot-Datum liegt in der Zukunft.',
      INVALID_POSITION_IDENTITY: 'Ungueltige Positionsidentitaet.',
      POSITION_METADATA_NORMALIZED: 'Positions-Metadaten normalisiert.',
    },
    asOf: 'per',
    expiresIn: 'Laeuft ab in',
    expired: 'Token abgelaufen',
    notImplemented: 'Nicht implementiert',
    notRemovable: 'Kern (nicht abtrennbar)',
    none: 'Kein direkter Hebel',
  },
} satisfies Record<Language, IntegrationsCopy>;

const CATEGORY_ORDER: IntegrationCategory[] = ['project', 'broker', 'service'];
const DIRECTION_ARROW: Record<'outbound' | 'inbound' | 'bidirectional', string> = {
  outbound: '→',
  inbound: '←',
  bidirectional: '↔',
};

function dotClass(level: IntegrationLevel): string {
  if (level === 'ok') return 'ok';
  if (level === 'warn') return 'warn';
  if (level === 'error') return 'err';
  return 'off';
}

function reasonText(code: string | null | undefined, copy: IntegrationsCopy): string | null {
  if (!code) return null;
  return copy.reason[code] ?? code;
}

function detailLines(row: IntegrationStatus, copy: IntegrationsCopy): string[] {
  const lines: string[] = [];
  if (row.status === 'expired') {
    lines.push(copy.expired);
  } else if (typeof row.days_to_expiry === 'number') {
    lines.push(`${copy.expiresIn}: ${row.days_to_expiry}d`);
  }
  if (row.as_of) {
    lines.push(
      `${copy.asOf} ${row.as_of}${typeof row.days_stale === 'number' ? ` · ${row.days_stale}d` : ''}`,
    );
  }
  const reason = reasonText(row.reason_code, copy);
  if (reason) lines.push(reason);
  if (!row.implemented && row.reason_code !== 'producer_not_implemented') {
    lines.push(copy.notImplemented);
  }
  return lines;
}

function IntegrationRow({ row, copy }: { row: IntegrationStatus; copy: IntegrationsCopy }) {
  const lines = detailLines(row, copy);
  return (
    <tr>
      <td>
        <span className="integration-state">
          <span className={`status-dot ${dotClass(row.level)}`} />
          <span>{copy.status[row.status] ?? row.status}</span>
        </span>
      </td>
      <td>
        <span className="position-symbol" data-i18n-exempt="true">{row.name}</span>
        {row.contract && <small><code data-i18n-exempt="true">{row.contract}</code></small>}
        {!row.removable && <small>{copy.notRemovable}</small>}
      </td>
      <td>
        <span title={copy.direction[row.direction]}>
          <span aria-hidden="true">{DIRECTION_ARROW[row.direction]}</span> {copy.direction[row.direction]}
        </span>
      </td>
      <td><small data-i18n-exempt="true">{row.mechanism}</small></td>
      <td>
        {row.control ? (
          <>
            <code data-i18n-exempt="true">{row.control}</code>
            <small>{copy.controlKind[row.control_kind]}</small>
          </>
        ) : (
          <small>{copy.none}</small>
        )}
      </td>
      <td>
        {lines.length > 0
          ? lines.map((line) => <small key={line} data-i18n-exempt="true">{line}</small>)
          : <small>—</small>}
      </td>
    </tr>
  );
}

export function IntegracionesPage() {
  const { language, locale } = useI18n();
  const copy: IntegrationsCopy = COPY[language];
  const query = useQuery({
    queryKey: ['integrations.status'],
    queryFn: () => api.integrationsStatus(),
    refetchInterval: 60_000,
    retry: 1,
  });

  if (query.isLoading) return <MissingData title={copy.loading}>{copy.intro}</MissingData>;
  if (query.isError || !query.data) {
    return (
      <>
        <MissingData title={copy.apiError}>{copy.intro}</MissingData>
        <QueryError endpoint="/integrations/status" onRetry={() => { void query.refetch(); }} />
      </>
    );
  }

  const { integrations, generated_at } = query.data;
  const updated = (() => {
    const parsed = new Date(generated_at);
    if (Number.isNaN(parsed.getTime())) return generated_at;
    return new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(parsed);
  })();

  return (
    <div className="integrations-page">
      <section className="info-box">
        <p>{copy.intro}</p>
        <p>{copy.controlHint}</p>
      </section>

      {CATEGORY_ORDER.map((category) => {
        const rows = integrations.filter((row) => row.category === category);
        if (rows.length === 0) return null;
        return (
          <section className="table-panel" key={category}>
            <div className="panel-header">
              <div>
                <div className="panel-title">{copy.category[category]}</div>
                <small className="panel-subtitle">{copy.categoryHint[category]}</small>
              </div>
            </div>
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>{copy.col.state}</th>
                    <th>{copy.col.integration}</th>
                    <th>{copy.col.direction}</th>
                    <th>{copy.col.mechanism}</th>
                    <th>{copy.col.control}</th>
                    <th>{copy.col.detail}</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => <IntegrationRow key={row.id} row={row} copy={copy} />)}
                </tbody>
              </table>
            </div>
          </section>
        );
      })}

      <section className="integrations-footer">
        <span className="integrations-legend">{copy.legendReadOnly}</span>
        <button type="button" onClick={() => { void query.refetch(); }}>{copy.refresh}</button>
        <small>{copy.updated}: {updated}</small>
      </section>
    </div>
  );
}
