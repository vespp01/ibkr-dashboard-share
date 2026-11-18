import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { MissingData } from '../components/MissingData';
import {
  api,
  type TaxIntegrationResponse,
  type TaxSaleCandidate,
  type TaxSaleScenario,
  type TaxScenarioStatus,
} from '../lib/api';
import { formatCurrency } from '../lib/format';
import { useI18n, type Language } from '../lib/i18n';

interface FiscalCopy {
  connection: string;
  connected: string;
  unavailable: string;
  loading: string;
  apiError: string;
  refresh: string;
  expectedContract: string;
  reason: Record<string, string>;
  status: Record<TaxScenarioStatus | 'expired', string>;
  generated: string;
  taxYear: string;
  residence: string;
  residenceValue: Record<'confirmed' | 'unconfirmed', string>;
  rulePack: string;
  snapshot: string;
  scenarios: string;
  globalBlockers: string;
  globalWarnings: string;
  scenarioBlockers: string;
  scenarioWarnings: string;
  noScenarios: string;
  proceeds: string;
  gainLoss: string;
  taxDelta: string;
  costs: string;
  deviation: string;
  plannedSales: string;
  instrument: string;
  candidateOrigin: string;
  manualOrigin: string;
  trendExitOrigin: string;
  selectedAt: string;
  sourceRef: string;
  quantity: string;
  date: string;
  assumedPrice: string;
  deferredLoss: string;
  audit: string;
  scenarioId: string;
  candidateSet: string;
  documentHash: string;
  noExecution: string;
}

const COPY = {
  es: {
    connection: 'Conexión con OptimizadorFiscal',
    connected: 'Contrato validado y disponible.',
    unavailable: 'El motor fiscal no está conectado.',
    loading: 'Cargando escenarios fiscales',
    apiError: 'No se pudo consultar el endpoint fiscal.',
    refresh: 'Actualizar',
    expectedContract: 'Contrato esperado',
    reason: {
      path_not_configured: 'Configura TAX_SCENARIOS_FILE con un archivo local tax_sale_scenarios.v1.',
      file_not_found: 'El archivo configurado no existe todavía.',
      file_too_large: 'El archivo supera el límite seguro de 2 MB.',
      file_unreadable: 'El backend no puede leer el archivo configurado.',
      contract_validation_failed: 'El archivo no cumple el contrato fiscal versionado.',
    },
    status: { draft: 'Borrador', reviewed: 'Revisado', approved: 'Aprobado', blocked: 'Bloqueado', expired: 'Caducado' },
    generated: 'Generado',
    taxYear: 'Ejercicio fiscal',
    residence: 'Residencia',
    residenceValue: { confirmed: 'Confirmada', unconfirmed: 'Sin confirmar' },
    rulePack: 'Paquete de reglas',
    snapshot: 'Snapshot',
    scenarios: 'Escenarios',
    globalBlockers: 'Bloqueos globales',
    globalWarnings: 'Advertencias globales',
    scenarioBlockers: 'Bloqueos del escenario',
    scenarioWarnings: 'Advertencias del escenario',
    noScenarios: 'El contrato es válido, pero no contiene escenarios.',
    proceeds: 'Ingresos estimados',
    gainLoss: 'Ganancia o pérdida',
    taxDelta: 'Impacto fiscal',
    costs: 'Costes estimados',
    deviation: 'Desviación de cartera',
    plannedSales: 'Ventas simuladas',
    instrument: 'Instrumento',
    candidateOrigin: 'Origen del candidato',
    manualOrigin: 'Selección manual',
    trendExitOrigin: 'Salida Método Tendencia',
    selectedAt: 'Seleccionado',
    sourceRef: 'Referencia de origen',
    quantity: 'Cantidad',
    date: 'Fecha asumida',
    assumedPrice: 'Precio asumido',
    deferredLoss: 'Pérdida diferida',
    audit: 'Trazabilidad',
    scenarioId: 'Escenario',
    candidateSet: 'Corte de candidatos',
    documentHash: 'Hash del documento',
    noExecution: 'Solo simulación local. Esta pantalla no crea ni envía órdenes.',
  },
  en: {
    connection: 'OptimizadorFiscal connection',
    connected: 'Contract validated and available.',
    unavailable: 'The tax engine is not connected.',
    loading: 'Loading tax scenarios',
    apiError: 'The tax endpoint could not be reached.',
    refresh: 'Refresh',
    expectedContract: 'Expected contract',
    reason: {
      path_not_configured: 'Configure TAX_SCENARIOS_FILE with a local tax_sale_scenarios.v1 file.',
      file_not_found: 'The configured file does not exist yet.',
      file_too_large: 'The file exceeds the safe 2 MB limit.',
      file_unreadable: 'The backend cannot read the configured file.',
      contract_validation_failed: 'The file does not match the versioned tax contract.',
    },
    status: { draft: 'Draft', reviewed: 'Reviewed', approved: 'Approved', blocked: 'Blocked', expired: 'Expired' },
    generated: 'Generated',
    taxYear: 'Tax year',
    residence: 'Residence',
    residenceValue: { confirmed: 'Confirmed', unconfirmed: 'Unconfirmed' },
    rulePack: 'Rule pack',
    snapshot: 'Snapshot',
    scenarios: 'Scenarios',
    globalBlockers: 'Global blockers',
    globalWarnings: 'Global warnings',
    scenarioBlockers: 'Scenario blockers',
    scenarioWarnings: 'Scenario warnings',
    noScenarios: 'The contract is valid but contains no scenarios.',
    proceeds: 'Estimated proceeds',
    gainLoss: 'Gain or loss',
    taxDelta: 'Tax impact',
    costs: 'Estimated costs',
    deviation: 'Portfolio deviation',
    plannedSales: 'Simulated sales',
    instrument: 'Instrument',
    candidateOrigin: 'Candidate origin',
    manualOrigin: 'Manual selection',
    trendExitOrigin: 'Trend-system exit',
    selectedAt: 'Selected',
    sourceRef: 'Source reference',
    quantity: 'Quantity',
    date: 'Assumed date',
    assumedPrice: 'Assumed price',
    deferredLoss: 'Deferred loss',
    audit: 'Traceability',
    scenarioId: 'Scenario',
    candidateSet: 'Candidate set',
    documentHash: 'Document hash',
    noExecution: 'Local simulation only. This screen cannot create or submit orders.',
  },
  de: {
    connection: 'Verbindung zu OptimizadorFiscal',
    connected: 'Vertrag validiert und verfuegbar.',
    unavailable: 'Die Steuer-Engine ist nicht verbunden.',
    loading: 'Steuerszenarien werden geladen',
    apiError: 'Der Steuer-Endpunkt ist nicht erreichbar.',
    refresh: 'Aktualisieren',
    expectedContract: 'Erwarteter Vertrag',
    reason: {
      path_not_configured: 'TAX_SCENARIOS_FILE mit einer lokalen tax_sale_scenarios.v1-Datei konfigurieren.',
      file_not_found: 'Die konfigurierte Datei ist noch nicht vorhanden.',
      file_too_large: 'Die Datei ueberschreitet das sichere Limit von 2 MB.',
      file_unreadable: 'Das Backend kann die konfigurierte Datei nicht lesen.',
      contract_validation_failed: 'Die Datei entspricht nicht dem versionierten Steuervertrag.',
    },
    status: { draft: 'Entwurf', reviewed: 'Geprueft', approved: 'Genehmigt', blocked: 'Blockiert', expired: 'Abgelaufen' },
    generated: 'Erstellt',
    taxYear: 'Steuerjahr',
    residence: 'Steuerwohnsitz',
    residenceValue: { confirmed: 'Bestaetigt', unconfirmed: 'Nicht bestaetigt' },
    rulePack: 'Regelpaket',
    snapshot: 'Snapshot',
    scenarios: 'Szenarien',
    globalBlockers: 'Globale Blocker',
    globalWarnings: 'Globale Warnungen',
    scenarioBlockers: 'Szenario-Blocker',
    scenarioWarnings: 'Szenario-Warnungen',
    noScenarios: 'Der Vertrag ist gueltig, enthaelt aber keine Szenarien.',
    proceeds: 'Geschaetzter Erloes',
    gainLoss: 'Gewinn oder Verlust',
    taxDelta: 'Steuerwirkung',
    costs: 'Geschaetzte Kosten',
    deviation: 'Portfolioabweichung',
    plannedSales: 'Simulierte Verkaeufe',
    instrument: 'Instrument',
    candidateOrigin: 'Kandidatenquelle',
    manualOrigin: 'Manuelle Auswahl',
    trendExitOrigin: 'Trend-System-Ausstieg',
    selectedAt: 'Ausgewaehlt',
    sourceRef: 'Quellreferenz',
    quantity: 'Menge',
    date: 'Angenommenes Datum',
    assumedPrice: 'Angenommener Preis',
    deferredLoss: 'Aufgeschobener Verlust',
    audit: 'Nachvollziehbarkeit',
    scenarioId: 'Szenario',
    candidateSet: 'Kandidatenmenge',
    documentHash: 'Dokument-Hash',
    noExecution: 'Nur lokale Simulation. Diese Ansicht kann keine Orders erstellen oder senden.',
  },
} satisfies Record<Language, FiscalCopy>;

function amount(value: string | undefined): number | undefined {
  if (value === undefined) return undefined;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function displayDate(value: string, locale: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(parsed);
}

function StatusBadge({ status, copy }: { status: TaxScenarioStatus | 'expired'; copy: FiscalCopy }) {
  return <span className={`tax-status ${status}`}>{copy.status[status]}</span>;
}

function candidateOrigin(candidate: TaxSaleCandidate, copy: FiscalCopy): string {
  if (candidate.selection_source === 'manual') return copy.manualOrigin;
  return `${copy.trendExitOrigin} · ${candidate.signal_kind === 'exit_mp' ? 'MP' : 'LP'}`;
}

function ConnectionState({ data, copy, onRefresh }: {
  data: TaxIntegrationResponse;
  copy: FiscalCopy;
  onRefresh: () => void;
}) {
  const reason = data.reason_code ? copy.reason[data.reason_code] ?? data.reason_code : copy.unavailable;
  return (
    <section className="table-panel tax-connection">
      <div>
        <span className="tax-status blocked">{copy.unavailable}</span>
        <h2>{copy.connection}</h2>
        <p>{reason}</p>
        <small>{copy.expectedContract}: <code>{data.expected_schema_version}</code></small>
      </div>
      <button type="button" onClick={onRefresh}>{copy.refresh}</button>
    </section>
  );
}

function ScenarioDetails({ scenario, copy, locale, candidates }: {
  scenario: TaxSaleScenario;
  copy: FiscalCopy;
  locale: string;
  candidates: Map<string, TaxSaleCandidate>;
}) {
  return (
    <>
      <div className="kpi-grid tax-kpis">
        <div className="kpi-card primary"><span className="label">{copy.taxDelta}</span><span className="value">{formatCurrency(amount(scenario.summary.estimated_tax_delta_eur), 'EUR', locale)}</span></div>
        <div className="kpi-card"><span className="label">{copy.proceeds}</span><span className="value">{formatCurrency(amount(scenario.summary.estimated_proceeds_eur), 'EUR', locale)}</span></div>
        <div className="kpi-card"><span className="label">{copy.gainLoss}</span><span className="value">{formatCurrency(amount(scenario.summary.estimated_gain_loss_eur), 'EUR', locale)}</span></div>
        <div className="kpi-card"><span className="label">{copy.costs}</span><span className="value">{formatCurrency(amount(scenario.summary.estimated_transaction_cost_eur), 'EUR', locale)}</span></div>
        <div className="kpi-card"><span className="label">{copy.deviation}</span><span className="value">{scenario.summary.portfolio_deviation_score}</span></div>
      </div>

      <section className="table-panel">
        <div className="panel-title">{copy.plannedSales}</div>
        <div className="table-scroll">
          <table className="data-table">
            <thead><tr><th>{copy.instrument}</th><th>{copy.candidateOrigin}</th><th>{copy.quantity}</th><th>{copy.date}</th><th>{copy.assumedPrice}</th><th>{copy.gainLoss}</th><th>{copy.deferredLoss}</th></tr></thead>
            <tbody>
              {scenario.sales.map((sale) => {
                const candidate = candidates.get(sale.candidate_id);
                return (
                  <tr key={`${sale.candidate_id}-${sale.assumed_sale_date}`}>
                    <td><span className="position-symbol">{sale.instrument_id}</span><small>FIFO: {sale.fifo_allocations.length}</small></td>
                    <td>
                      {candidate && <strong>{candidateOrigin(candidate, copy)}</strong>}
                      {candidate && <small>{copy.selectedAt}: {displayDate(candidate.selected_at, locale)}</small>}
                      {candidate && <small>{copy.sourceRef}: <code data-i18n-exempt="true">{candidate.source_ref}</code></small>}
                    </td>
                    <td>{sale.quantity}</td>
                    <td>{sale.assumed_sale_date}</td>
                    <td>{formatCurrency(amount(sale.assumed_price), sale.currency, locale)}</td>
                    <td className={(amount(sale.estimated_gain_loss_eur) ?? 0) < 0 ? 'neg' : 'pos'}>{formatCurrency(amount(sale.estimated_gain_loss_eur), 'EUR', locale)}</td>
                    <td>{formatCurrency(amount(sale.deferred_loss_eur), 'EUR', locale)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      {(scenario.blockers.length > 0 || scenario.warnings.length > 0) && (
        <div className="tax-alert-grid">
          {scenario.blockers.length > 0 && <div className="tax-alert blocked"><strong>{copy.scenarioBlockers}</strong><ul>{scenario.blockers.map((item) => <li key={item} data-i18n-exempt="true">{item}</li>)}</ul></div>}
          {scenario.warnings.length > 0 && <div className="tax-alert warning"><strong>{copy.scenarioWarnings}</strong><ul>{scenario.warnings.map((item) => <li key={item} data-i18n-exempt="true">{item}</li>)}</ul></div>}
        </div>
      )}
    </>
  );
}

export function FiscalPage() {
  const { language, locale } = useI18n();
  const copy: FiscalCopy = COPY[language];
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const query = useQuery({
    queryKey: ['tax.scenarios'],
    queryFn: () => api.taxScenarios(),
    refetchInterval: 60_000,
    retry: 1,
  });

  if (query.isLoading) return <MissingData title={copy.loading}>{copy.connection}</MissingData>;
  if (query.isError || !query.data) return <MissingData title={copy.apiError}>{copy.unavailable}</MissingData>;
  if (query.data.integration_status !== 'ready' || !query.data.scenario_set) {
    return <ConnectionState data={query.data} copy={copy} onRefresh={() => { void query.refetch(); }} />;
  }

  const scenarioSet = query.data.scenario_set;
  const selected = scenarioSet.scenarios.find((scenario) => scenario.scenario_id === selectedId) ?? scenarioSet.scenarios[0];
  const candidates = new Map(
    scenarioSet.candidate_set.candidates.map((candidate) => [candidate.candidate_id, candidate]),
  );

  return (
    <div className="tax-page">
      <section className="table-panel tax-overview">
        <div className="tax-overview-heading">
          <div>
            <StatusBadge status={scenarioSet.status} copy={copy} />
            <h2>{copy.connection}</h2>
            <p>{copy.connected}</p>
          </div>
          <button type="button" onClick={() => { void query.refetch(); }}>{copy.refresh}</button>
        </div>
        <div className="tax-meta-grid">
          <div><span>{copy.generated}</span><strong>{displayDate(scenarioSet.generated_at, locale)}</strong></div>
          <div><span>{copy.taxYear}</span><strong>{scenarioSet.tax_profile.tax_year}</strong></div>
          <div><span>{copy.residence}</span><strong>{copy.residenceValue[scenarioSet.tax_profile.residency_status]}</strong></div>
          <div><span>{copy.rulePack}</span><strong>{scenarioSet.rule_pack.id} · {scenarioSet.rule_pack.version}</strong><StatusBadge status={scenarioSet.rule_pack.review_status} copy={copy} /></div>
        </div>
      </section>

      {(scenarioSet.blockers.length > 0 || scenarioSet.warnings.length > 0) && (
        <div className="tax-alert-grid">
          {scenarioSet.blockers.length > 0 && <div className="tax-alert blocked"><strong>{copy.globalBlockers}</strong><ul>{scenarioSet.blockers.map((item) => <li key={item} data-i18n-exempt="true">{item}</li>)}</ul></div>}
          {scenarioSet.warnings.length > 0 && <div className="tax-alert warning"><strong>{copy.globalWarnings}</strong><ul>{scenarioSet.warnings.map((item) => <li key={item} data-i18n-exempt="true">{item}</li>)}</ul></div>}
        </div>
      )}

      <section>
        <div className="panel-title">{copy.scenarios}</div>
        {scenarioSet.scenarios.length === 0 ? (
          <MissingData title={copy.noScenarios}>{copy.noExecution}</MissingData>
        ) : (
          <div className="tax-scenario-grid">
            {scenarioSet.scenarios.map((scenario) => (
              <button
                type="button"
                key={scenario.scenario_id}
                className={`tax-scenario-card${selected?.scenario_id === scenario.scenario_id ? ' active' : ''}`}
                aria-pressed={selected?.scenario_id === scenario.scenario_id}
                onClick={() => setSelectedId(scenario.scenario_id)}
              >
                <span><StatusBadge status={scenario.status} copy={copy} /></span>
                <strong data-i18n-exempt="true">{scenario.objective}</strong>
                <small>{formatCurrency(amount(scenario.summary.estimated_tax_delta_eur), 'EUR', locale)} · {copy.plannedSales}: {scenario.sales.length}</small>
              </button>
            ))}
          </div>
        )}
      </section>

      {selected && <ScenarioDetails scenario={selected} copy={copy} locale={locale} candidates={candidates} />}

      <section className="table-panel tax-audit">
        <div className="panel-title">{copy.audit}</div>
        <dl>
          <div><dt>{copy.scenarioId}</dt><dd><code>{scenarioSet.scenario_set_id}</code></dd></div>
          <div><dt>{copy.candidateSet}</dt><dd><code>{scenarioSet.candidate_set.candidate_set_id}</code></dd></div>
          <div><dt>{copy.snapshot}</dt><dd><code>{scenarioSet.snapshot_id}</code></dd></div>
          <div><dt>{copy.documentHash}</dt><dd><code>{query.data.document_hash}</code></dd></div>
        </dl>
        <p className="tax-no-execution">{copy.noExecution}</p>
      </section>
    </div>
  );
}
