import { useMemo } from 'react';
import type { DividendsCountryRow } from '../../lib/api';
import { formatCurrency, formatPercent } from '../../lib/format';
import { useI18n } from '../../lib/i18n';
import { fill, type DividendsCopy } from './copy';
import { mergeCountries, NO_COUNTRY } from './income';

interface Props {
  copy: DividendsCopy;
  currency: string;
  rows: DividendsCountryRow[] | undefined;
  loading: boolean;
  error: boolean;
}

export function CountryTable({ copy, currency, rows, loading, error }: Props) {
  const { locale } = useI18n();
  const merged = useMemo(() => mergeCountries(rows ?? []), [rows]);
  const totalGross = merged.reduce((sum, r) => sum + r.gross, 0);
  const money = (v: number) => formatCurrency(v, '', locale).trim();
  const hasOrphanWht = merged.some((r) => r.gross <= 0 && r.withholding !== 0);

  let body;
  if (loading) {
    body = <div className="skeleton dv-chart-skeleton short" aria-busy="true" />;
  } else if (error) {
    body = <div className="trades-empty error-state">{copy.byCountryError}</div>;
  } else if (merged.length === 0) {
    body = <div className="trades-empty">{copy.noDividends}</div>;
  } else {
    body = (
      <>
        <table className="data-table">
          <thead>
            <tr>
              <th>{copy.country}</th>
              <th className="num">{copy.gross}</th>
              <th className="num">{copy.withholding}</th>
              <th className="num">{copy.net}</th>
              <th className="num">{copy.percentGross}</th>
              <th className="num">{copy.effectiveRate}</th>
            </tr>
          </thead>
          <tbody>
            {merged.map((row) => {
              const orphan = row.gross <= 0 && row.withholding !== 0;
              const share = totalGross > 0 ? (row.gross / totalGross) * 100 : undefined;
              const effective = row.gross > 0 ? (Math.abs(row.withholding) / row.gross) * 100 : undefined;
              return (
                <tr key={row.country}>
                  <td>{row.country === NO_COUNTRY ? copy.noCountry : row.country}{orphan ? <sup aria-label={copy.countryFootnote}> *</sup> : null}</td>
                  <td className="num">{money(row.gross)}</td>
                  <td className={`num${row.withholding !== 0 ? ' pnl-warn' : ''}`}>{money(row.withholding)}</td>
                  <td className="num">{orphan ? '--' : money(row.net)}</td>
                  <td className="num">{formatPercent(share, false, locale)}</td>
                  <td className="num">{formatPercent(effective, false, locale)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {hasOrphanWht ? <p className="dv-note">* {copy.countryFootnote}</p> : null}
      </>
    );
  }

  return (
    <div className="table-panel dv-panel">
      <div className="panel-title">{copy.byCountry}</div>
      <p className="dv-note dv-note-top">{fill(copy.amountsIn, { ccy: currency })}</p>
      {body}
    </div>
  );
}
