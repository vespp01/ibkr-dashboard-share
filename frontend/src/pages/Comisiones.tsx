import { useQuery } from '@tanstack/react-query';
import { api, type PeriodFilters } from '../lib/api';
import type { UseQueryResult } from '@tanstack/react-query';
import { KPICard } from '../components/KPICard';
import { MissingData } from '../components/MissingData';
import { formatCurrency } from '../lib/format';
import { useI18n } from '../lib/i18n';

interface CommissionsSummary {
  total_base?: number;
}

interface Props {
  filters: PeriodFilters;
  currency: string;
  /** Shared with Resumen — passed down so React Query dedupes the call. */
  commissions: UseQueryResult<CommissionsSummary>;
}

const COPY = {
  es: {
    unavailableTitle: 'Comisiones no disponibles',
    unavailableBody: 'Verifica el estado de la base de datos backend.',
    brokerOnly: 'Broker commission (sólo)',
    brokerDelta: 'ibCommission: sin fees ni tax',
    totalCost: 'Total coste operativo',
    totalDelta: 'broker + tx tax + reg fees',
    byCostType: 'Por tipo de coste',
    type: 'Tipo',
    amount: 'Importe',
    percentTotal: '% del total',
    byAssetClass: 'Por clase de activo',
    assetClass: 'Asset Class',
    commission: 'Comisión',
    trades: 'Trades',
    perTrade: 'por trade',
  },
  en: {
    unavailableTitle: 'Fees unavailable',
    unavailableBody: 'Check the backend database status.',
    brokerOnly: 'Broker commission only',
    brokerDelta: 'ibCommission: without fees or tax',
    totalCost: 'Total operating cost',
    totalDelta: 'broker + tx tax + regulatory fees',
    byCostType: 'By cost type',
    type: 'Type',
    amount: 'Amount',
    percentTotal: '% of total',
    byAssetClass: 'By asset class',
    assetClass: 'Asset class',
    commission: 'Commission',
    trades: 'Trades',
    perTrade: 'per trade',
  },
  de: {
    unavailableTitle: 'Gebuehren nicht verfuegbar',
    unavailableBody: 'Pruefe den Status der Backend-Datenbank.',
    brokerOnly: 'Nur Broker-Kommission',
    brokerDelta: 'ibCommission: ohne Fees oder Tax',
    totalCost: 'Gesamte operative Kosten',
    totalDelta: 'Broker + Transaction Tax + Reg Fees',
    byCostType: 'Nach Kostentyp',
    type: 'Typ',
    amount: 'Betrag',
    percentTotal: '% vom Total',
    byAssetClass: 'Nach Assetklasse',
    assetClass: 'Assetklasse',
    commission: 'Kommission',
    trades: 'Trades',
    perTrade: 'pro Trade',
  },
} as const;

export function ComisionesPage({ filters, currency, commissions }: Props) {
  const { language, locale } = useI18n();
  const copy = COPY[language];
  const breakdown = useQuery({
    queryKey: ['commissions.breakdown', filters],
    queryFn: () => api.commissionsBreakdown(filters),
  });

  const commissionsTotal = commissions.data?.total_base as number | undefined;

  if (commissions.isError) {
    return <MissingData title={copy.unavailableTitle}>{copy.unavailableBody}</MissingData>;
  }

  return (
    <>
      <div className="kpi-grid">
        <KPICard
          label={copy.brokerOnly}
          value={formatCurrency(commissionsTotal, currency, locale)}
          delta={copy.brokerDelta}
          deltaKind="neg"
          loading={commissions.isLoading}
        />
        {breakdown.data ? (
          <KPICard
            label={copy.totalCost}
            value={formatCurrency(breakdown.data.total_base, currency, locale)}
            delta={copy.totalDelta}
            deltaKind="neg"
            loading={breakdown.isLoading}
          />
        ) : null}
      </div>

      {breakdown.data && (
        <div className="subsection-grid" style={{ marginTop: 18 }}>
          <div className="chart-panel">
            <div className="panel-header">
              <div className="panel-title">{copy.byCostType}</div>
            </div>
            <table className="data-table">
              <thead><tr><th>{copy.type}</th><th>{copy.amount}</th><th>{copy.percentTotal}</th></tr></thead>
              <tbody>
                {breakdown.data.by_type.map((r) => {
                  const tot = Math.abs(breakdown.data?.total_base ?? 0);
                  const amt = r.amount_base ?? 0;
                  const pct = tot > 0 ? (Math.abs(amt) / tot) * 100 : 0;
                  return (
                    <tr key={r.type}>
                      <td>{r.type}</td>
                      <td className="pnl-neg">{formatCurrency(amt, currency, locale)}</td>
                      <td>{pct.toLocaleString(locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="chart-panel">
            <div className="panel-header">
              <div className="panel-title">{copy.byAssetClass}</div>
            </div>
            <table className="data-table">
              <thead><tr><th>{copy.assetClass}</th><th>{copy.commission}</th><th>{copy.trades}</th><th>{copy.perTrade}</th></tr></thead>
              <tbody>
                {breakdown.data.by_asset_class.map((r) => {
                  const amt = r.amount_base ?? 0;
                  const perTrade = r.trades > 0 ? amt / r.trades : 0;
                  return (
                    <tr key={r.asset_class}>
                      <td>{r.asset_class}</td>
                      <td className="pnl-neg">{formatCurrency(amt, currency, locale)}</td>
                      <td>{r.trades}</td>
                      <td>{formatCurrency(perTrade, currency, locale)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  );
}
