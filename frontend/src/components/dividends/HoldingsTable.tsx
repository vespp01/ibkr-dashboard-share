import { formatCurrency, formatPercent } from '../../lib/format';
import { useI18n } from '../../lib/i18n';
import { fill, type DividendsCopy } from './copy';

export interface HoldingRow {
  symbol: string;
  conid?: number;
  annual_income_base?: number;
  yield_pct?: number;
}

interface Props {
  copy: DividendsCopy;
  currency: string;
  padi: number | undefined;
  rows: HoldingRow[] | undefined;
  /** conid → cost basis in base currency (IBKR). */
  costByConid: Map<number, number>;
  loading: boolean;
  error: boolean;
}

export function HoldingsTable({ copy, currency, padi, rows, costByConid, loading, error }: Props) {
  const { locale, privacyMode } = useI18n();
  const list = (rows ?? []).filter((r) => (r.annual_income_base ?? 0) > 0).slice(0, 12);
  const fmtMoney = (v: number | undefined) => formatCurrency(v, '', locale).trim();

  let body;
  if (loading) {
    body = <div className="skeleton dv-chart-skeleton short" aria-busy="true" />;
  } else if (error) {
    body = <div className="trades-empty error-state">{copy.holdingsError}</div>;
  } else if (list.length === 0) {
    body = <div className="trades-empty">{copy.holdingsEmpty}</div>;
  } else {
    const top = Math.max(...list.map((r) => r.annual_income_base ?? 0));
    body = (
      <table className="data-table dv-holdings">
        <thead>
          <tr>
            <th>{copy.symbol}</th>
            <th className="num">{copy.annualIncome}</th>
            <th className="num">{copy.yieldValue}</th>
            <th>{copy.shareOfPadi}</th>
            <th className="num">{copy.yoc}</th>
          </tr>
        </thead>
        <tbody>
          {list.map((row) => {
            const income = row.annual_income_base ?? 0;
            const share = padi && padi > 0 ? (income / padi) * 100 : undefined;
            const cost = row.conid !== undefined ? costByConid.get(row.conid) : undefined;
            const yoc = cost && cost > 0 ? (income / cost) * 100 : undefined;
            return (
              <tr key={row.conid ?? row.symbol}>
                <td><strong>{row.symbol}</strong></td>
                <td className="num">{fmtMoney(income)}</td>
                <td className="num">{formatPercent(row.yield_pct, false, locale)}</td>
                <td>
                  <div className="dv-share">
                    <span className="dv-share-bar" aria-hidden="true">
                      {privacyMode ? null : <i style={{ width: `${(income / top) * 100}%` }} />}
                    </span>
                    <span className="num">{formatPercent(share, false, locale)}</span>
                  </div>
                </td>
                <td className="num">{formatPercent(yoc, false, locale)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    );
  }

  return (
    <div className="table-panel dv-panel">
      <div className="panel-title">{copy.holdingsTitle}</div>
      <p className="dv-note dv-note-top">{fill(copy.holdingsNote, { ccy: currency })}</p>
      {body}
    </div>
  );
}
